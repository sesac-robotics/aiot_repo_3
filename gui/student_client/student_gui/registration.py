"""자리 등록 흐름 (학생 화면 ⓪): 이름 저장, 등록 창 열기, 바른 자세·방향 측정 완료 처리, Control 전송

- 이름은 학생 노트북 data/seats/seat_N_student.json (git 제외 폴더)에 둔다.
- 바른 자세·방향 측정값은 판정 쪽(monitor/seat_store.py)이 저장하고, Engine 시그널로 끝난 순간을 받는다.
- seat_register가 common/protocol.py에 추가되면 Control(중앙 DB)에도 보낸다 (link.send_register).
"""
import json

from monitor.seat_store import DEFAULT_DIR, open_store
from student_gui.dialogs import RegisterDialog


class Profile:
    """좌석에 등록된 학생 이름 (data/seats/seat_N_student.json)"""

    def __init__(self, seat):
        self.path = DEFAULT_DIR / f"seat_{seat}_student.json"

    def load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8")).get("student_name")
        except (OSError, json.JSONDecodeError):
            return None

    def save(self, name):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"student_name": name}, ensure_ascii=False), encoding="utf-8")


class RegisterFlow:
    """[자리 등록] 버튼 ~ 등록 끝까지. app이 만들고 Engine·평소 화면·Control 연결을 넘겨준다"""

    def __init__(self, seat, need_zones, win, engine, link):
        self.seat, self.need_zones = seat, need_zones
        self.win, self.engine, self.link = win, engine, link
        self.profile = Profile(seat)
        self.name = self.profile.load()
        self.pending_name = None
        self.dialog = None
        self.camera_error = None                 # 카메라·모델을 못 열었으면 그 사유
        self.win.set_name(self.name)
        engine.registered.connect(self.on_registered)
        engine.zones_saved.connect(self.on_zones)

    def needed(self):
        """처음 실행처럼 이름·바른 자세·(학습 얼굴 모델이면) 방향 값 중 빠진 것이 있으면 True"""
        baseline, zones = open_store().load(self.seat)
        return not (self.name and baseline) or (self.need_zones and not zones)

    def open(self):
        if self.dialog is not None:
            self.dialog.raise_()
            return
        self.dialog = RegisterDialog(self.seat, self.name or "", self.win)
        self.dialog.start_requested.connect(self._start)
        self.dialog.next_requested.connect(self.engine.next_zone)
        self.dialog.finished.connect(self._closed)
        self.dialog.show()
        if self.camera_error:
            self.dialog.fail(self.camera_error)

    def fail(self, text):
        """판정 스레드가 끝났다 → 등록을 기다리게 두지 않고 바로 알린다"""
        self.camera_error = text
        if self.dialog is not None:
            self.dialog.fail(text)

    def show_guide(self, s: dict):
        """측정 중이면 판정 스레드의 안내 줄을 등록 창에 보여 준다"""
        if self.dialog is not None and self.dialog.measuring:
            self.dialog.show_guide(s.get("guide", []), s.get("zone_step", False))

    def _start(self, name):
        if self.camera_error:
            self.dialog.fail(self.camera_error)
            return
        self.pending_name = name
        self.engine.register()

    def _closed(self, _):
        self.dialog, self.pending_name = None, None

    def on_registered(self, baseline: dict):
        if not self.pending_name:                # c 키 등 다른 경로로 다시 잰 경우: 이름은 그대로
            return
        self.name = self.pending_name
        self.profile.save(self.name)
        self.win.set_name(self.name)
        _, zones = open_store().load(self.seat)
        sent = self.link.send_register(self.name, baseline, zones)
        if self.dialog is not None:
            where = "중앙 서버에 저장했습니다." if sent else "이 노트북에 저장했습니다."
            more_zones = self.need_zones and not zones
            more = "\n이어서 보는 방향을 잽니다. 안내에 따라 [다음]을 눌러 주세요." if more_zones else ""
            # 방향 측정이 이어지면 [닫기]는 숨기고 안내를 계속 받는다 (닫으면 측정은 계속되는데 안내만 사라짐)
            self.dialog.finish(f"{self.name}님, 바른 자세를 등록했어요. {where}{more}", closable=not more_zones)

    def on_zones(self, zones: dict):
        baseline, _ = open_store().load(self.seat)
        if self.name and baseline:               # 방향까지 끝났으면 방향 값을 담아 다시 보냄
            self.link.send_register(self.name, baseline, zones)
        if self.dialog is not None:
            self.dialog.finish("등록이 모두 끝났어요. [닫기]를 눌러 주세요.")