"""실시간 얼굴 판정 재생 확인: 저장된 세션을 실시간 모듈(monitor.face_ensemble)에 프레임 순서대로 넣어
평가(evaluation.face_compare의 F, 보정 없음)와 실시간 판정(근거 보정 포함)을 같은 프레임·같은 지표로 비교한다.

gui/student_client에서 실행 (웹캠 불필요):
  python -m evaluation.face_replay_check                                  # 테스트 세션(_test) 전체
  python -m evaluation.face_replay_check --val-sessions val               # 검증 세션
  python -m evaluation.face_replay_check --val-sessions val --cnn models/face/v2/cnn_ls_erase.pt models/face/v2/cnn_ls_erase_s1.pt
  python -m evaluation.face_replay_check --no-evidence                    # 근거 보정 끔 (계산 일치 확인용)

실시간(재생)은 구간을 모르므로 시간 흐름 묶음을 이어서 쌓는다 (세션이 바뀌거나 1초 넘게 끊기면 비움).
→ 보정을 끄면 평가와 판정이 거의 같아야 하고 (10-20 확인 99.8%), 보정을 켜면 그 차이가 보정의 효과다.
비교 프레임은 얼굴 이미지가 있는 프레임. 이미지는 저장된 crop을 그대로 쓴다 (3프레임마다 갱신).
"""
import argparse
import time

import numpy as np
from PIL import Image

from evaluation.face_compare import image_model_probs, report, seq_ensemble_probs
from evaluation.face_events import events_report
from learning.face_dataset import load_face_splits, zone_gate
from monitor.face_ensemble import DEFAULT_CNNS, DEFAULT_SEQS, MODEL_DIR, RESET_GAP_SEC, FaceEnsemble, default_paths
from monitor.face_evidence import EvidenceGate


def parse_args():
    root = MODEL_DIR.parents[2]
    ap = argparse.ArgumentParser(description="실시간 얼굴 판정 재생 확인")
    ap.add_argument("--data", default=str(root / "data" / "face"))
    ap.add_argument("--session", default="", help="세션 이름 일부로 하나만 (비우면 전체)")
    ap.add_argument("--val-sessions", nargs="+", default=None, help="테스트 대신 이 검증 세션으로 확인")
    ap.add_argument("--no-evidence", action="store_true", help="근거 보정 끔")
    ap.add_argument("--eye-min", type=float, default=0.35, help="졸음 인정 눈 감김 하한")
    ap.add_argument("--jaw-min", type=float, default=0.15, help="하품 인정 입 벌림 하한")
    ap.add_argument("--seq", nargs="+", default=None, help="시간 흐름 모델 (기본: models/face/v1의 3개)")
    ap.add_argument("--cnn", nargs="+", default=None, help="이미지 모델 (기본: models/face/v1의 2개)")
    ap.add_argument("--img-weight", type=float, default=0.7)
    ap.add_argument("--gate-yaw", type=float, default=5.0)
    ap.add_argument("--limit", type=int, default=0, help="앞쪽 N프레임만 (0 = 전체)")
    return ap.parse_args()


def load_split(args):
    _, val, test = load_face_splits(args.data, args.val_sessions)
    test = val if args.val_sessions else test
    keep = [i for i, s in enumerate(test.sessions) if args.session in s]
    if args.limit:
        keep = keep[:args.limit]
    if not keep:
        raise SystemExit("확인할 프레임이 없습니다. --session / --val-sessions를 확인하세요.")
    return test.subset(keep)


def batch_predict(split, seqs, cnns, w, gate_yaw, device):
    """평가와 같은 방식 → 이미지 프레임 번호, F 확률"""
    idx = np.array([i for i, p in enumerate(split.images) if p])
    pt = seq_ensemble_probs(split, seqs, device)[idx]
    pc = zone_gate(image_model_probs(split.subset(idx), cnns, device), split.X[idx], yaw_tol=gate_yaw)
    return idx, (1 - w) * pt + w * pc


def replay(split, ens):
    """실시간 모듈에 프레임 순서대로 넣기 → (N, 5) 확률, 프레임별 처리 시간(ms), 이미지 포함 여부"""
    probs, ms, with_img = [], [], []
    prev_s, prev_t = None, None
    for i in range(len(split)):
        s, t = split.sessions[i], float(split.t[i])
        if s != prev_s or (prev_t is not None and t - prev_t > RESET_GAP_SEC):
            ens.reset()
        prev_s, prev_t = s, t
        img = Image.open(split.images[i]).convert("RGB") if split.images[i] else None
        t0 = time.perf_counter()
        probs.append(ens.update(split.X[i], img, t))
        ms.append((time.perf_counter() - t0) * 1000)
        with_img.append(img is not None)
    return np.array(probs), np.array(ms), np.array(with_img)


def main():
    args = parse_args()
    seqs = args.seq or default_paths(DEFAULT_SEQS)
    cnns = args.cnn or default_paths(DEFAULT_CNNS)
    split = load_split(args)
    gate = None if args.no_evidence else EvidenceGate(eye_min=args.eye_min, jaw_min=args.jaw_min)
    ens = FaceEnsemble({"screen": {"yaw": 0.0, "pitch": 0.0}}, seqs, cnns, args.img_weight, args.gate_yaw,
                       evidence=gate)
    print(f"재생 확인: {len(split)} 프레임 (세션 {len(set(split.sessions))}개), {ens.describe()}")
    if gate is not None:
        print(gate.describe())

    idx, pb = batch_predict(split, seqs, cnns, args.img_weight, args.gate_yaw, ens.device)
    pr, ms, with_img = replay(split, ens)
    pred_b, pred_r = pb.argmax(1), pr[idx].argmax(1)
    print(f"\n판정 일치율 (평가 vs 실시간): {(pred_b == pred_r).mean():.1%}  (이미지 있는 프레임 {len(idx)})")
    tag = "근거 보정" if gate is not None else "보정 없음"
    preds = {"F 평가 (일괄, 보정 없음)": pred_b, f"실시간 재생 ({tag})": pred_r}
    sub = split.subset(idx)
    print(report(sub, preds, "재생 확인", f"T = {len(seqs)}개, C = {', '.join(cnns)}"))
    print(events_report(sub, preds))

    print(f"\n1프레임 처리 시간 ({ens.device}, 얼굴 검출 제외)")
    print(f"  시간 흐름만: 평균 {ms[~with_img].mean():.1f} ms | 시간 흐름 + 이미지: 평균 {ms[with_img].mean():.1f} ms")


if __name__ == "__main__":
    main()