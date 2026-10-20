"""착석자 모니터링 (실시간): 자세 + 얼굴 → 지속 시간 필터 → 화면 표시

자세 판정은 기본으로 최종 구성(v6)을 쓴다 (monitor/posture_ensemble.py):
  수치 MLP + 이미지 앙상블 6개(배경 제거 3, 실루엣 3) → 가중 결합(이미지 0.6) → 목 꺾임 보정
--rule을 주면 MediaPipe 규칙만으로 판정한다 (mediapipe_only 기준선과 같은 방식, 비교용).
얼굴 판정은 기본으로 학습 모델(시간 흐름 ×3 + 이미지 ×2, monitor/face_stage.py)을 쓰고, --face-rule이면 규칙만.
시작 순서: 바른 자세 10초 → 영역 캘리브레이션(학습 모델이고 --zones 파일이 없을 때, 영역마다 Space) → 모니터링.
손 듦(팔꿈치가 어깨 위, 손목이 귀 위)이면 자세 판정과 얼굴 일부를 멈춘다 (common/hand_raise.py).

프로젝트 루트에서 실행:
  python -m monitor.monitor_main
  python -m monitor.monitor_main --posture-hold 30 --show-input   # 실사용 확정 시간, 모델 입력 이미지 표시
  python -m monitor.monitor_main --img-every 3                    # 느릴 때 이미지 판정을 3프레임마다
  python -m monitor.monitor_main --rule --face-rule               # 자세·얼굴 모두 규칙만 (비교용)
옵션 전체는 monitor/monitor_args.py
키: c 기준 자세 다시 저장, z 영역 다시 측정, Space 영역 측정 시작, ESC 종료
"""
import time

import cv2

from seat_common.face_features import FaceDetector, compute_face_features, draw_face_points
from seat_common.face_judge import FACE_LABELS, FaceThresholds, make_face_filter
from seat_common.hand_raise import FACE_PAUSED, HandRaise, draw_arm_points
from seat_common.korean_text import TextRenderer
from seat_common.posture_features import FEATURE_NAMES, PoseDetector, Smoother, compute_features, draw_key_points
from seat_common.posture_judge import LABELS, Calibrator, DurationFilter, Thresholds, compute_deltas, detect_issues
from monitor.display import RED, WHITE, YELLOW, paste_inputs, pending_text, posture_line, status_line, value_lines
from monitor.face_stage import FaceStage
from monitor.monitor_args import parse_args

CALIB_KEYS = FEATURE_NAMES + ("yaw", "pitch")      # 자세 기준 + 정면 고개 각도


def load_ensemble(args):
    if args.rule:
        return None
    from monitor.posture_ensemble import DEFAULT_CNNS, DEFAULT_MLP, PostureEnsemble   # torch는 모델을 쓸 때만
    cnns = DEFAULT_CNNS if args.cnn is None else args.cnn
    return PostureEnsemble(args.mlp or DEFAULT_MLP, cnns, args.img_weight, img_every=args.img_every)


class Monitor:
    """프레임 1장 처리: 검출 → 특징 → (캘리브레이션 | 판정) → 표시할 줄"""

    def __init__(self, args):
        self.ensemble = load_ensemble(args)
        need_mask = self.ensemble is not None and bool(self.ensemble.cnns)
        self.pose = PoseDetector(args.pose_model, masks=need_mask)
        self.face = FaceDetector(args.face_model)
        self.smoother = Smoother(alpha=0.3)
        self.calib = Calibrator(seconds=args.calib_sec, keys=CALIB_KEYS)
        self.pth = Thresholds(hold_sec=args.posture_hold)
        self.face_stage = FaceStage(args)
        self.posture_filter = DurationFilter(self.pth.hold_sec, self.pth.release_sec)
        self.face_filter = make_face_filter(FaceThresholds())
        self.hand = HandRaise()
        self.plabels = LABELS if self.ensemble is None else self.ensemble.labels
        self.show_input = args.show_input and need_mask
        self.base = None
        self.state = {"present": False, "calibrating": False, "face": (), "posture": ()}   # 확정 상태 (전송용)

    def judge_posture(self, d, clean, lms):
        if self.ensemble is None:
            return detect_issues(d, self.pth)
        return self.ensemble.issues(d, clean, lms, self.pose.mask)

    def mode_lines(self):
        face = self.face_stage.mode_lines()
        if self.ensemble is None:
            return [("자세 판정: 규칙 (MediaPipe만)", WHITE)] + face
        label, p, by_angle = self.ensemble.last
        tag = "  (목 꺾임 보정)" if by_angle else ""
        now_line = "      → 손 듦: 판정 멈춤" if self.hand.active else f"      → {self.plabels[label]} {p:.2f}{tag}"
        return [(f"자세 판정: {self.ensemble.describe()}", WHITE), (now_line, WHITE)] + face

    def pause_posture(self):
        """자세 판정 흐름을 비움 (확인 중 시간, 결합 확률 평활). 다시 시작하면 처음부터"""
        self.posture_filter.reset()
        if self.ensemble is not None:
            self.ensemble.reset()

    def recalibrate(self, now):
        self.smoother.reset()
        self.pause_posture()
        self.face_filter.reset()
        self.face_stage.reset()
        self.hand.reset()
        self.calib.start(now)

    def handle_key(self, key, now):
        """c 바른 자세 다시 등록, z 영역 다시 측정, Space 영역 측정 시작"""
        if key == ord("c"):
            self.recalibrate(now)
        elif key == ord("z"):
            self.face_filter.reset()
            self.face_stage.reset()
            self.face_stage.restart_zones(now)
        elif key == ord(" "):
            self.face_stage.go(now)

    def step(self, frame, now):
        h, w = frame.shape[:2]
        need_clean = self.ensemble is not None or self.face_stage.needs_clean_frame
        clean = frame.copy() if need_clean else frame   # 점을 그리기 전 원본 (이미지 모델 입력)
        pose_lms = self.pose.detect(frame)
        face_res = self.face.detect(frame)
        p_raw = compute_features(pose_lms, w, h)
        p = self.smoother.update(p_raw)
        f = compute_face_features(face_res)
        hand = self.hand.update(pose_lms, now)
        draw_key_points(frame, pose_lms)
        draw_arm_points(frame, pose_lms, hand)
        draw_face_points(frame, face_res)

        self.state = {"present": not (p_raw is None and f is None), "calibrating": self.calib.active,
                      "face": (), "posture": ()}
        if p_raw is None and f is None:                 # 사람 없음
            self.posture_filter.reset()
            self.face_filter.reset()
            if self.calib.active:
                self.calib.add(None, now)
            return [("자리 비움: 사람이 화면에 없습니다", YELLOW)]

        if self.calib.active:
            sample = {**p, "yaw": f["yaw"], "pitch": f["pitch"]} if (p_raw and f) else None
            b = self.calib.add(sample, now)
            if b is not None:
                self.base = b
            lines = [(f"기준 자세 저장 중: 허리를 펴고 화면 정면을 보세요  {self.calib.remaining(now):.1f}초", YELLOW)]
            if self.calib.message:
                lines.append((self.calib.message, RED))
            return lines
        if self.face_stage.calibrating(now):            # 영역 캘리브레이션 (학습 모델, 영역 값이 없을 때)
            self.state["calibrating"] = True
            return self.face_stage.calib_step(f, now)

        face_issues = self.face_stage.issues(face_res, f, clean, self.base, now)
        if hand:                                        # 손 듦: 고개 돌림·팔에 가려짐은 판정하지 않음
            face_issues = [k for k in face_issues if k not in FACE_PAUSED]
        face_conf = self.face_filter.update(face_issues, now)
        d = compute_deltas(p, self.base) if p_raw is not None else None
        if hand:                                        # 손 듦: 자세 판정 멈춤 (내리면 처음부터)
            self.pause_posture()
        posture_issues = self.judge_posture(d, clean, pose_lms) if (d is not None and not hand) else []
        posture_conf = self.posture_filter.update(posture_issues, now)
        self.state.update(face=tuple(face_conf), posture=tuple(posture_conf))
        if self.show_input:
            paste_inputs(frame, self.ensemble.inputs)

        lines = [status_line("얼굴", face_conf, FACE_LABELS),
                 posture_line(hand, p_raw is not None, posture_conf, self.plabels)]
        pending = (pending_text(face_issues, self.face_filter, FACE_LABELS, now)
                   + pending_text(posture_issues, self.posture_filter, self.plabels, now))
        if pending:
            lines.append(("확인 중: " + ", ".join(pending), YELLOW))
        return lines + value_lines(f, self.base, d) + self.mode_lines()

    def close(self):
        self.pose.close()
        self.face.close()


def main():
    args = parse_args()
    text = TextRenderer(size=18)
    monitor = Monitor(args)
    src = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise SystemExit(f"카메라를 열 수 없습니다: {args.source}")
    monitor.recalibrate(time.monotonic())
    fps, t_prev = 0.0, time.monotonic()

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            now = time.monotonic()
            fps = 0.9 * fps + 0.1 / max(now - t_prev, 1e-3)
            t_prev = now
            lines = monitor.step(frame, now)
            lines.append((f"c: 기준 자세 다시 저장  |  z: 영역 다시 측정  |  ESC: 종료  |  {fps:.0f} fps", WHITE))
            text.draw_lines(frame, lines)
            cv2.imshow("seat monitor", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                break
            monitor.handle_key(key, now)
    finally:
        cap.release()
        monitor.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()