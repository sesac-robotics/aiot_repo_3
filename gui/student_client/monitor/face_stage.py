"""실시간 얼굴 판정 단계: 학습 모델 / 규칙 선택, 영역 캘리브레이션(측정·저장·불러오기), 화면 표시 줄

학습 모델(기본): 영역 기준값이 필요하다. 좌석 저장값(monitor.calib_flow.SeatCalib)이 있으면 불러오고, 없으면 바른 자세 등록 뒤에
  collect.zone_calib.ZoneCalib로 화면 → 강의 공간 한쪽 끝 → 반대쪽 끝 → 책상을 측정(영역마다 Space)해 좌석에 저장한다.
  z 키로 다시 측정한다. 저장 방식(JSON / DB)은 monitor/seat_store.py가 맡는다.
규칙(--face-rule): 기존 detect_face_issues (바른 자세 등록 때의 정면 고개 각도 기준), 영역 측정 없음.
두 방식 모두 결과 이름은 같다: drowsy, yawn, not_forward, no_face.
"""
from collect.face_guide import FACE_CLASSES, FACE_KO
from collect.zone_calib import ZoneCalib
from monitor.display import WHITE
from seat_common.face_judge import FaceThresholds, detect_face_issues


class FaceStage:
    def __init__(self, args, seat_calib):
        self.rule = args.face_rule
        self.fth = FaceThresholds()
        self.seat_calib = seat_calib
        self.zone_calib = ZoneCalib()
        self.zones, self.ens = None, None
        if not self.rule:
            from monitor.face_ensemble import FaceEnsemble       # torch는 학습 모델을 쓸 때만
            self.zones = seat_calib.zones
            self.ens = FaceEnsemble(self.zones or {"screen": {"yaw": 0.0, "pitch": 0.0}},
                                    img_every=args.face_img_every)

    @property
    def needs_clean_frame(self):
        return not self.rule

    def reset(self):
        if self.ens is not None:
            self.ens.reset()

    def restart_zones(self, now):
        """z 키: 영역을 처음부터 다시 측정"""
        if not self.rule:
            self.zones = None
            self.zone_calib.start(now)

    def go(self, now):
        """Space 키: 지금 영역 측정 시작"""
        self.zone_calib.go(now)

    def calibrating(self, now):
        """영역 측정 중이면 True. 학습 모델인데 영역 값이 없으면 여기서 측정을 시작한다"""
        if self.rule:
            return False
        if self.zone_calib.active:
            return True
        if self.zones is None:
            self.zone_calib.start(now)
            return True
        return False

    def calib_step(self, f, now):
        """영역 측정 1프레임 → 화면 안내 줄. 끝나면 저장하고 학습 모델에 적용"""
        if self.zone_calib.add(f, now):
            self.zones = dict(self.zone_calib.zones)
            self.seat_calib.save_zones(self.zones, self.zone_calib.warnings)
            self.ens.set_zones(self.zones)
            for text, _ in self.zone_calib.summary_lines():
                print("  " + text)
            return self.zone_calib.summary_lines()
        return self.zone_calib.lines(now)

    def issues(self, face_res, f, clean, base, now):
        """이번 프레임의 얼굴 문제 목록"""
        if self.rule:
            return detect_face_issues(f, base, self.fth)
        return self.ens.issues(face_res, clean, now)

    def mode_lines(self):
        if self.rule:
            return [("얼굴 판정: 규칙 (MediaPipe만)", WHITE)]
        lines = [(f"얼굴 판정: {self.ens.describe()}  {self.ens.ms:.1f}ms", WHITE)]
        if self.ens.last is not None:
            label, p, with_img = self.ens.last
            tag = "" if with_img else "  (이미지 대기)"
            lines.append((f"      → {FACE_KO[label]} {p:.2f}{tag}", WHITE))
            lines.append((self.parts_line(), WHITE))
        return lines

    def parts_line(self):
        """확인용: 시간 흐름 / 이미지 모델 각각의 1위 판정과 졸음·하품 확률 (결합 전)"""
        d, y = FACE_CLASSES.index("drowsy"), FACE_CLASSES.index("yawn")
        out = []
        for name, q in zip(("시간 흐름", "이미지"), self.ens.parts):
            if q is None:
                out.append(f"{name} -")
                continue
            k = int(q.argmax())
            out.append(f"{name} {FACE_KO[FACE_CLASSES[k]]} {q[k]:.2f} (졸음 {q[d]:.2f} 하품 {q[y]:.2f})")
        return "      " + "  |  ".join(out)