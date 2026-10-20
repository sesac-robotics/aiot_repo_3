"""실시간 모니터링 실행 옵션 (monitor.monitor_main, monitor.seat_link_main 공용)

자세: 기본 학습 모델(v6), --rule이면 MediaPipe 규칙만
얼굴: 기본 학습 모델(시간 흐름 ×3 + 이미지 ×2, monitor.face_ensemble), --face-rule이면 MediaPipe 규칙만
영역 기준값: --zones 파일이 있으면 불러오고, 없으면 시작할 때 측정해 그 파일에 저장 (학습 모델일 때만)
"""
import argparse
from pathlib import Path

from seat_common.posture_judge import Thresholds

ROOT = Path(__file__).resolve().parents[1]


def build_parser():
    ap = argparse.ArgumentParser(description="착석자 모니터링 (자세 + 얼굴)")
    ap.add_argument("--source", default="0", help="웹캠 번호 또는 스트림 URL")
    ap.add_argument("--pose-model", default=str(ROOT / "assets" / "pose_landmarker_lite.task"))
    ap.add_argument("--face-model", default=str(ROOT / "assets" / "face_landmarker.task"))
    ap.add_argument("--calib-sec", type=float, default=10.0, help="시작 시 바른 자세 기준 등록 시간 (초, SR_007)")
    ap.add_argument("--posture-hold", type=float, default=Thresholds.hold_sec,
                    help="자세 문제 확정 시간(초), 실사용 30 이상 권장")
    ap.add_argument("--rule", action="store_true", help="학습 모델 없이 MediaPipe 규칙으로 자세 판정")
    ap.add_argument("--mlp", default="", help="수치 MLP (기본: models/v6/posture_mlp.pt)")
    ap.add_argument("--cnn", nargs="*", default=None, help="이미지 모델들 (기본: models/v6의 6개, 비우면 수치만)")
    ap.add_argument("--img-weight", type=float, default=0.6, help="자세 이미지 비중 w")
    ap.add_argument("--img-every", type=int, default=1, help="자세 이미지 판정을 N프레임마다 갱신")
    ap.add_argument("--show-input", action="store_true", help="이미지 모델 입력(배경 제거·실루엣)을 화면에 표시")
    ap.add_argument("--face-rule", action="store_true", help="학습 모델 없이 MediaPipe 규칙으로 얼굴 판정 (비교용)")
    ap.add_argument("--face-img-every", type=int, default=3,
                    help="얼굴 이미지 판정을 N프레임마다 갱신 (수집 때 3프레임마다 저장한 것과 같게)")
    ap.add_argument("--zones", default=str(ROOT / "data" / "zones" / "seat_zones.json"),
                    help="영역 기준값 파일 (없으면 시작할 때 측정해서 저장)")
    return ap


def parse_args():
    return build_parser().parse_args()