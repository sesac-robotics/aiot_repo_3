"""시작·등록 흐름: 좌석 저장값 불러오기 / 바른 자세 등록 / 다시 측정 키 (monitor_main.Monitor에 섞어 쓴다)

시작할 때 (--seat 좌석 번호, --register 등록 선택):
  - --register      : 바른 자세 10초 + 방향 측정 → 좌석 저장값 덮어쓰기
  - 저장값 있음     : 불러와서 바로 모니터링
  - 저장값 없음     : 없는 쪽만 자동으로 등록 후 저장 (바른 자세가 먼저, 이어서 방향 측정)
키: c 바른 자세만 다시 등록, z 방향만 다시 측정, Space 방향 측정 시작 — 끝나면 해당 값만 덮어쓴다.
등록 선택은 이후 학생 화면의 등록 버튼(SP-229)이 --register 대신 맡는다.
"""
from monitor.seat_store import open_store


class SeatCalib:
    """좌석 번호 + 저장소. 시작할 때 읽은 값을 들고 있고, 등록이 끝나면 저장한다"""

    def __init__(self, args):
        self.seat, self.store = args.seat, open_store()
        baseline, zones = self.store.load(self.seat)
        if args.register:
            baseline = zones = None
            print(f"좌석 {self.seat}: 등록을 선택해 바른 자세와 방향을 새로 측정합니다")
        self.baseline, self.zones = baseline, zones
        items = (("바른 자세 기준", baseline),) + (() if args.face_rule else (("방향 측정값", zones),))
        for name, v in items:
            print(f"좌석 {self.seat} {name}: " + ("저장값 불러옴" if v else "없음 → 등록 진행"))

    def save_baseline(self, b):
        self.baseline = b
        self.store.save_baseline(self.seat, b)
        print(f"좌석 {self.seat} 바른 자세 기준 저장: {self.store.path(self.seat)}")

    def save_zones(self, zones, warnings=()):
        self.zones = zones
        self.store.save_zones(self.seat, zones, warnings)
        print(f"좌석 {self.seat} 방향 측정값 저장: {self.store.path(self.seat)}")


class CalibFlowMixin:
    """Monitor에 섞어 쓰는 시작·등록 동작 (self.seat_calib, self.calib, self.face_stage 등을 사용)"""

    def start(self, now):
        """프로그램 시작: 바른 자세 저장값이 있으면 그대로 쓰고, 없으면 등록 시작 (방향은 face_stage가 판단)"""
        self.reset_flow()
        if self.seat_calib.baseline:
            self.base = dict(self.seat_calib.baseline)
        else:
            self.calib.start(now)

    def on_baseline(self, b):
        """바른 자세 10초 등록이 끝났을 때"""
        self.base = b
        self.seat_calib.save_baseline(b)

    def reset_flow(self):
        self.smoother.reset()
        self.pause_posture()
        self.face_filter.reset()
        self.face_stage.reset()
        self.hand.reset()

    def recalibrate(self, now):
        """c 키: 바른 자세만 다시 등록"""
        self.reset_flow()
        self.calib.start(now)

    def handle_key(self, key, now):
        """c 바른 자세 다시 등록, z 방향 다시 측정, Space 방향 측정 시작"""
        if key == ord("c"):
            self.recalibrate(now)
        elif key == ord("z"):
            self.face_filter.reset()
            self.face_stage.reset()
            self.face_stage.restart_zones(now)
        elif key == ord(" "):
            self.face_stage.go(now)