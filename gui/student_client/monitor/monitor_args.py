"""실시간 모니터링 실행 옵션 (monitor.monitor_main, monitor.seat_link_main 공용)

자세: 기본 학습 모델(v6), --rule이면 MediaPipe 규칙만
얼굴: 기본 학습 모델(시간 흐름 ×3 + 이미지 ×2(v2) + 근거 보정, monitor.face_ensemble), --face-rule이면 MediaPipe 규칙만
기준값: 좌석(--seat)별로 바른 자세 기준 + 방향 측정값을 저장·불러오기 (monitor/seat_store.py, monitor/calib_flow.py)
  저장값이 없거나 --register면 시작할 때 등록한다
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
    ap.add_argument("--face-models", default=str(ROOT / "models" / "face" / "v2"),
                    help="얼굴 모델 폴더 (이미지 모델 2개; 시간 흐름 모델이 없으면 models/face/v1 것을 사용). "
                         "기본 v2 = 새 장소 포함 재학습 (10-21 테스트 구성)")
    ap.add_argument("--no-face-evidence", action="store_true",
                    help="눈 감김·입 벌림으로 졸음·하품을 확인하는 보정을 끔 (비교용)")
    ap.add_argument("--stretch-min", type=float, default=50.0,
                    help="이 시간(분) 이어서 앉아 있으면 스트레칭 권유 (SP-228). 시험할 때는 1")
    ap.add_argument("--away-reset-sec", type=float, default=60.0,
                    help="사람이 이 시간(초) 넘게 안 보이면 자리 비움으로 보고 착석 시간을 처음부터")
    ap.add_argument("--seat", type=int, default=1, help="좌석 번호 (기준값 저장 단위)")
    ap.add_argument("--register", action="store_true",
                    help="저장값이 있어도 바른 자세·방향을 새로 등록 (이후 학생 화면의 등록 버튼)")
    return ap


def parse_args():
    return build_parser().parse_args()