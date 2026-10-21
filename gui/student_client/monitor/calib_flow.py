"""시작·등록 흐름: 좌석 저장값 불러오기 / 바른 자세 등록 / 다시 측정 키 (monitor_main.Monitor에 섞어 쓴다)

시작할 때 (--seat 좌석 번호, --register 등록 선택):
  - --register      : 바른 자세 10초 + 방향 측정 → 좌석 저장값 덮어쓰기
  - 저장값 있음     : 불러와서 바로 모니터링
  - 저장값 없음     : 없는 쪽만 자동으로 등록 후 저장 (바른 자세가 먼저, 이어서 방향 측정)
키: c 바른 자세만 다시 등록, z 방향만 다시 측정, Space 방향 측정 시작 — 끝나면 해당 값만 덮어쓴다.
학생 화면(student_gui)은 키 대신 아래 공개 함수만 쓴다 (Monitor 내부 이름에 기대지 않게):
  register(now) 바른 자세 등록 시작, next_zone(now) 방향 측정 다음 단계, zone_step 방향 측정 중인지,
  labels() 얼굴·자세 한글 이름, seat_calib.on_baseline_saved / on_zones_saved 저장이 끝났을 때 부를 함수
"""
from monitor.seat_store import open_store
from seat_common.face_judge import FACE_LABELS


class SeatCalib:
    """좌석 번호 + 저장소. 시작할 때 읽은 값을 들고 있고, 등록이 끝나면 저장한다"""

    def __init__(self, args):
        self.seat, self.store = args.seat, open_store()
        self.on_baseline_saved = None          # 저장 끝 알림 (학생 화면): f(baseline dict)
        self.on_zones_saved = None             # 저장 끝 알림 (학생 화면): f(zones dict)
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
        if self.on_baseline_saved:
            self.on_baseline_saved(dict(b))

    def save_zones(self, zones, warnings=()):
        self.zones = zones
        self.store.save_zones(self.seat, zones, warnings)
        print(f"좌석 {self.seat} 방향 측정값 저장: {self.store.path(self.seat)}")
        if self.on_zones_saved:
            self.on_zones_saved(dict(zones))


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

    # ---- 학생 화면용 공개 함수 --------------------------------------------------
    def register(self, now):
        """[자리 등록]: 바른 자세 10초 등록 시작 (방향 측정값이 없으면 끝난 뒤 이어서 측정)"""
        self.recalibrate(now)

    def next_zone(self, now):
        """[다음]: 방향 측정 다음 단계 (= Space)"""
        self.face_stage.go(now)

    @property
    def zone_step(self):
        """방향 측정 중이면 True ([다음] 버튼 표시)"""
        return self.face_stage.zone_calib.active

    def labels(self):
        """(얼굴 문제 이름 → 한글, 자세 문제 이름 → 한글)"""
        return dict(FACE_LABELS), dict(self.plabels)