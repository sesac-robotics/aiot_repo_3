"""실시간 얼굴 판정 (학습 모델): 시간 흐름 모델 ×3 + 얼굴 이미지 모델 ×2 → 영역 연결 → 결합 → 5클래스

최종 테스트에서 평가한 구성을 그대로 쓴다 (evaluation.face_compare --seq … --cnn … --zone-gate --img-weight 0.7).
  - 시간 흐름: 최근 30프레임의 수치 56개 → 1D-CNN 3개 확률 평균
    버퍼가 30프레임보다 짧으면 가장 오래된 프레임을 반복해 채운다 (학습 때 구간 앞쪽 채우기와 같음)
    얼굴이 RESET_GAP_SEC 넘게 안 보이면 버퍼를 비운다 (미검출 프레임은 버퍼에 넣지 않음)
  - 이미지: 얼굴 crop → 이미지 모델 2개 확률 평균, img_every 프레임마다 갱신 (그 사이는 직전 값 사용)
  - 영역 연결: 고개가 화면·강의 공간 범위 안(허용 5°)이면 이미지의 '영역 밖' 확률을 '앞 보기'로
  - 결합 = (1 − w) × 시간 흐름 + w × 영역 연결(이미지), w = 0.7. 이미지 값이 아직 없으면 시간 흐름만
출력은 기존 규칙 판정(seat_common.face_judge.detect_face_issues)과 같은 이름으로 맞춘다:
  졸음 → drowsy, 하품 → yawn, 영역 밖 → not_forward, 얼굴 없음 → no_face, 앞 보기·책상 보기 → 문제 없음
지속 시간 필터는 기존 make_face_filter를 그대로 쓴다 (졸음 1.5초, 하품 1초, 영역 밖 2초, 해제 1초 — 평가와 같음).
"""
import time
from collections import deque
from pathlib import Path

import numpy as np
import torch

from collect.face_guide import FACE_CLASSES
from learning.face_dataset import zone_gate
from learning.face_image_train import face_transform, load_face_image_model
from learning.face_seq_train import load_seq_model
from monitor.face_inputs import face_image, numeric_inputs

MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "face" / "v1"
DEFAULT_SEQS = ("seq.pt", "seq_s1.pt", "seq_s2.pt")
DEFAULT_CNNS = ("cnn_ls_erase.pt", "cnn_ls_erase_s1.pt")
ISSUE = {"drowsy": "drowsy", "yawn": "yawn", "away": "not_forward"}   # 5클래스 → 기존 문제 이름
RESET_GAP_SEC = 1.0


def default_paths(names, model_dir=MODEL_DIR):
    paths = [Path(model_dir) / n for n in names]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise FileNotFoundError(f"얼굴 모델 파일이 없습니다: {missing[0]}")
    return [str(p) for p in paths]


class FaceEnsemble:
    def __init__(self, zones, seqs=None, cnns=None, img_weight=0.7, gate_yaw=5.0, img_every=1, device=None):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.seq_paths = list(seqs) if seqs else default_paths(DEFAULT_SEQS)
        self.cnn_paths = list(cnns) if cnns is not None else default_paths(DEFAULT_CNNS)
        self.seqs = [load_seq_model(p, self.device) for p in self.seq_paths]       # (model, mean, std, win)
        wins = {s[3] for s in self.seqs}
        if len(wins) != 1:
            raise ValueError(f"시간 흐름 모델의 묶음 길이가 서로 다릅니다: {sorted(wins)}")
        self.win = wins.pop()
        self.cnns = [load_face_image_model(p, self.device)[0] for p in self.cnn_paths]
        self.tf = face_transform(False)
        self.w, self.gate_yaw, self.img_every = img_weight, gate_yaw, max(1, img_every)
        self.zones = zones
        self.reset()

    def reset(self):
        """버퍼·이미지 확률을 비움 (기준 다시 등록, 자리 비움 등)"""
        self.buf = deque(maxlen=self.win)
        self.pc = None              # 마지막 이미지 확률 (영역 연결 전)
        self.n = 0                  # 얼굴이 있던 프레임 수 (이미지 갱신 주기용)
        self.last_t = None
        self.last = None            # (클래스 이름, 확률, 이미지 반영 여부)
        self.parts = (None, None)   # 마지막 (시간 흐름 확률, 영역 연결 후 이미지 확률) — 화면 확인용
        self.ms = 0.0               # 마지막 판정 시간 (밀리초)

    def set_zones(self, zones):
        self.zones = zones
        self.reset()

    def describe(self):
        img = f" + 이미지 ×{len(self.cnns)}" if self.cnns else ""
        return f"학습 모델 (시간 흐름 ×{len(self.seqs)}{img}, 이미지 {self.w:.1f}, {self.device})"

    @torch.no_grad()
    def _seq_probs(self):
        frames = list(self.buf)
        frames = [frames[0]] * (self.win - len(frames)) + frames            # 앞쪽 채우기
        raw = np.stack(frames)
        out = []
        for model, mean, std, _ in self.seqs:
            xb = torch.from_numpy(((raw - mean) / std).astype(np.float32))[None].to(self.device)
            out.append(torch.softmax(model(xb), 1)[0].cpu().numpy())
        return np.mean(out, axis=0)

    @torch.no_grad()
    def _image_probs(self, img):
        xb = self.tf(img)[None].to(self.device)
        return np.mean([torch.softmax(m(xb), 1)[0].cpu().numpy() for m in self.cnns], axis=0)

    def update(self, x, img, now):
        """수치 56개(None = 얼굴 없음) + 얼굴 이미지(None = 이번에 갱신 안 함) → 5클래스 확률, 얼굴이 없으면 None"""
        if x is None:
            if self.last_t is not None and now - self.last_t > RESET_GAP_SEC:
                self.reset()
            return None
        t0 = time.perf_counter()
        self.buf.append(x)
        self.last_t = now
        pt = self._seq_probs()
        if self.cnns and img is not None:
            self.pc = self._image_probs(img)
        self.n += 1
        if self.pc is None:
            p, pc = pt, None
        else:
            pc = zone_gate(self.pc[None], x[None], yaw_tol=self.gate_yaw)[0]
            p = (1 - self.w) * pt + self.w * pc
        self.parts = (pt, pc)
        k = int(p.argmax())
        self.last = (FACE_CLASSES[k], float(p[k]), self.pc is not None)
        self.ms = (time.perf_counter() - t0) * 1000
        return p

    def wants_image(self):
        """이번 프레임에 이미지 모델을 돌릴 차례인가 (crop 계산을 아끼기 위해 먼저 확인)"""
        return bool(self.cnns) and (self.pc is None or self.n % self.img_every == 0)

    def issues(self, result, frame_bgr, now):
        """검출 결과 + 원본 프레임 → 이번 프레임의 얼굴 문제 목록 (detect_face_issues와 같은 형식)"""
        x = numeric_inputs(result, self.zones)
        if x is None:
            self.update(None, None, now)
            return ["no_face"]
        img = face_image(frame_bgr, result) if self.wants_image() else None
        p = self.update(x, img, now)
        issue = ISSUE.get(FACE_CLASSES[int(p.argmax())])
        return [issue] if issue else []