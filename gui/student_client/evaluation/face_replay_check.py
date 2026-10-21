"""실시간 얼굴 판정 재생 확인: 저장된 세션을 실시간 모듈(monitor.face_ensemble)에 프레임 순서대로 넣어
최종 평가(evaluation.face_compare의 F)와 판정이 같은지, 1프레임 처리 시간이 얼마인지 확인한다.

gui/student_client에서 실행 (웹캠 불필요):
  python -m evaluation.face_replay_check                       # 테스트 세션(_test) 전체
  python -m evaluation.face_replay_check --session newplace_g0 # 이름 일부로 세션 하나만
  python -m evaluation.face_replay_check --limit 3000          # 앞쪽 3000프레임만 (빠른 확인)

비교 기준
  - 평가(일괄): 시간 흐름 묶음을 수집 단계 구간 안에서만 만든다 (구간이 바뀌면 다시 채움)
  - 실시간(재생): 구간을 모르므로 이어서 쌓는다. 세션이 바뀌거나 1초 넘게 끊기면 비운다
  → 단계가 바뀐 직후 약 1초 동안은 판정이 다를 수 있다. 그 외 구간의 일치율이 높아야 정상
비교 프레임은 얼굴 이미지가 있는 프레임 (평가와 같음). 이미지는 저장된 crop을 그대로 쓴다 (3프레임마다 갱신).
"""
import argparse
import time

import numpy as np
from PIL import Image

from collect.face_guide import FACE_CLASSES, FACE_KO
from evaluation.eval_metrics import balanced_acc, confusion
from evaluation.face_compare import image_model_probs, seq_ensemble_probs
from learning.face_dataset import load_face_splits, zone_gate
from monitor.face_ensemble import DEFAULT_CNNS, DEFAULT_SEQS, MODEL_DIR, RESET_GAP_SEC, FaceEnsemble, default_paths


def parse_args():
    root = MODEL_DIR.parents[2]
    ap = argparse.ArgumentParser(description="실시간 얼굴 판정 재생 확인")
    ap.add_argument("--data", default=str(root / "data" / "face"))
    ap.add_argument("--session", default="", help="테스트 세션 이름 일부 (비우면 테스트 세션 전체)")
    ap.add_argument("--seq", nargs="+", default=None, help="시간 흐름 모델 (기본: models/face/v1의 3개)")
    ap.add_argument("--cnn", nargs="+", default=None, help="이미지 모델 (기본: models/face/v1의 2개)")
    ap.add_argument("--img-weight", type=float, default=0.7)
    ap.add_argument("--gate-yaw", type=float, default=5.0)
    ap.add_argument("--limit", type=int, default=0, help="앞쪽 N프레임만 (0 = 전체)")
    return ap.parse_args()


def load_test(args):
    _, _, test = load_face_splits(args.data, None)
    keep = [i for i, s in enumerate(test.sessions) if args.session in s]
    if args.limit:
        keep = keep[:args.limit]
    if not keep:
        raise SystemExit("확인할 테스트 프레임이 없습니다. --session을 확인하세요.")
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
    split = load_test(args)
    ens = FaceEnsemble({"screen": {"yaw": 0.0, "pitch": 0.0}}, seqs, cnns, args.img_weight, args.gate_yaw)
    print(f"재생 확인: {len(split)} 프레임 (세션 {len(set(split.sessions))}개), {ens.describe()}")

    idx, pb = batch_predict(split, seqs, cnns, args.img_weight, args.gate_yaw, ens.device)
    pr, ms, with_img = replay(split, ens)
    pred_b, pred_r, y = pb.argmax(1), pr[idx].argmax(1), split.y[idx]
    n = len(FACE_CLASSES)

    seg_start = np.r_[True, (np.diff(y) != 0) | (np.array(split.sessions)[idx][1:] != np.array(split.sessions)[idx][:-1])]
    near = np.zeros(len(idx), bool)                       # 단계가 바뀐 직후 1초 이내
    t_img = split.t[idx]
    last = t_img[0]
    for j in range(len(idx)):
        last = t_img[j] if seg_start[j] else last
        near[j] = t_img[j] - last <= 1.0

    same = pred_b == pred_r
    print(f"\n비교 프레임 (얼굴 이미지 있음): {len(idx)}")
    print(f"판정 일치율: 전체 {same.mean():.1%} | 단계 전환 1초 이후 {same[~near].mean():.1%} "
          f"| 전환 직후 1초 {same[near].mean():.1%}")
    print(f"균형 정확도: 평가(일괄) {balanced_acc(confusion(y, pred_b, n)):.3f} | "
          f"실시간(재생) {balanced_acc(confusion(y, pred_r, n)):.3f}")
    diff = np.abs(pb - pr[idx]).max(1)
    print(f"확률 최대 차이 (전환 1초 이후): 중앙값 {np.median(diff[~near]):.4f}, 최대 {diff[~near].max():.4f}")
    print("클래스별 일치율: " + ", ".join(
        f"{FACE_KO[c]} {same[y == k].mean():.1%}" for k, c in enumerate(FACE_CLASSES) if (y == k).any()))

    print(f"\n1프레임 처리 시간 ({ens.device}, 얼굴 검출 제외)")
    print(f"  시간 흐름만: 평균 {ms[~with_img].mean():.1f} ms")
    print(f"  시간 흐름 + 이미지: 평균 {ms[with_img].mean():.1f} ms")
    print(f"  이미지 3프레임마다 갱신 시 평균: {ms.mean():.1f} ms")


if __name__ == "__main__":
    main()