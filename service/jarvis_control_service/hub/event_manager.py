"""
이벤트 관리 (상세 설계 6장). control 안에서 판단하는 곳은 여기 한 곳이다.
우선순위, 선점, 대기열, 쓰러짐 단계 알림, 만료, 추적 대상을 모두 여기서 정한다.

- 네트워크도 DB 도 모른다. 입력을 받으면 "할 일"(Effects)을 돌려주고,
  hub 가 그것을 실제로 보내고(send) db.py 로 기록한다.
- 시계는 항상 인자로 받는다(now = control 시계 rx_ts). 가짜 시계로 테스트할 수 있다.
- [팬틸트] 팬틸트는 Control 내부 모듈(hub/pantilt)이다. to="pantilt" 인 Send 는
  hub 가 PanTiltService 메서드 호출로 바꿔 실행한다 (TCP 로 보내지 않음).
- [알림문구] 상황이 바뀌는 순간(새 사건·선점, 쓰러짐 15/30초, 일어남, 손 내림, 자리비움 미복귀)마다
  vision_service 에 explain_request {situation, ...} 를 보내 강사용 알림 문구를 다시 만든다.
  응답 explain_text 의 seq 가 마지막 요청과 다르면(늦게 온 옛 응답) 버린다.
- [수동보기] 강사가 "1번 좌석 보여줘" 하면 열린 이벤트가 없을 때만 팬틸트를 그 좌석으로 돌린다 (LED 끔).
  이벤트를 추적 중이면 거절한다. view_hold_s(60초) 뒤 원위치, 그 전에 새 이벤트가 오면 바로 그 이벤트로 넘어간다.
  상태가 바뀌면 Console 에 view_state {seat, until, by} 를 보낸다 (seat=None 이면 끝).
- [녹화] 이벤트 녹화(event_clip)를 언제 시작·끝낼지도 여기서 정한다. to="recorder" 인 Send
  (start {event_id, seat, kind} / stop {event_id}) 를 hub 가 ClipRecorder(hub/clip_recorder.py) 호출과
  db.py 기록으로 바꾼다. 녹화 구간 = 이벤트 확정(detect) ~ 종료(resolve·dismiss·expire·clear).

상태: active(추적 중) / waiting(대기열) / closed(종료) 세 가지뿐.
confirm 은 상태가 아니다. confirmed_at 을 기록하고 단계 알림만 멈춘다.
정렬 기준: (priority, detected_at). 쓰러짐(1) > 질문(2) > 자리비움(3), 같은 등급이면 먼저 온 것부터.
"""

import logging
from .seat_evidence import Evidence
from dataclasses import asdict, dataclass, field

from common.protocol import (CONDITION_DETAIL_KEYS, CONSOLE_NODE, STUDENT_NODES, VISION_NODE,   # [팬틸트] CAMERA_NODE 제거
                             student_node)

log = logging.getLogger("event_manager")

SYSTEM = "system"

PANTILT = "pantilt"                                  # [팬틸트] hub 가 PanTiltService 호출로 바꾸는 목적지
KIND_CODE = {"fall": 1, "question": 2, "away": 3}    # [팬틸트] 팬틸트·LED 는 숫자 코드 (1 빨강·2 파랑·3 초록)
PANTILT_SAFE = 7                                     # [팬틸트] pan_tilt_state.state 7 = SAFE (Uno 통신 오류)
OBS_GAP_S = .8                                       # seat_obs 증거가 이어지는 최대 간격 (이보다 길면 처음부터)
OBS_WARN_S = 10.0                                    # seat_obs 를 못 쓴 이유를 경고하는 주기
AWAY_S = 5.0                                         # 빈자리가 이만큼 이어지면 자리비움 (10/22 시험: 10초 -> 5초, 3초는 오탐 증가)
KIND_KO = {"fall": "쓰러짐", "question": "질문", "away": "자리비움"}   # [수동보기] 거절 안내 문장용
RECORDER = "recorder"                                # [녹화] hub 가 ClipRecorder 호출 + event_clip 기록으로 바꾸는 목적지

# help_event 표에 저장하는 값 (문자열 그대로. 숫자 코드로 바꾸는 일은 db.py 가 한다)
# reg_id·gen 은 db.py 가 채운다. source 는 DB 에 없다 (event_log detect 의 detail 에만).
DB_FIELDS = ("event_id", "seat", "kind", "priority", "status", "detected_at",
             "confirmed_at", "confirmed_by", "closed_at", "close_reason",
             "alert_stage", "hand_down", "explain_text", "explain_mode")


@dataclass
class HelpEvent:
    event_id: int
    seat: int
    kind: str                       # fall | question | away
    priority: int
    status: str                     # active | waiting | closed
    detected_at: float
    confirmed_at: float | None = None
    confirmed_by: str | None = None
    closed_at: float | None = None
    close_reason: str | None = None  # resolved | expired | cleared
    alert_stage: str = "none"       # none | neighbor | all
    explain_text: str | None = None
    explain_mode: str | None = None
    source: str | None = None
    hand_down: bool = False         # question end: 손을 내림 (닫지 않음). DB help_event.hand_down
    # ---- 화면 표시용 (DB 에는 없음) ----
    risen: bool = False            # fall end: 일어남 (닫지 않음, 사람이 판단)
    overdue: bool = False           # away 가 away_alert_s 지나도록 미복귀
    explain_seq: int = 0            # [알림문구] 마지막 explain_request 번호 (늦은 응답 거르기)
    explain_situation: str | None = None   # [알림문구] 지금 explain_text 가 설명하는 상황

    @property
    def is_open(self) -> bool:
        return self.status != "closed"

    def sort_key(self):
        return (self.priority, self.detected_at, self.event_id)

    def to_row(self) -> dict:
        """DB help_event 한 행."""
        d = asdict(self)
        return {k: d[k] for k in DB_FIELDS}

    def to_msg(self) -> dict:
        """event_update / snapshot 에 싣는 값 (help_event 한 행 + 표시용 값)."""
        return asdict(self)


@dataclass
class Send:
    to: str
    type: str
    value: dict
    seat: int | None = None


@dataclass
class Effects:
    """한 번의 입력 처리 결과. hub 가 순서대로 실행한다: saves -> logs -> sends."""
    sends: list[Send] = field(default_factory=list)
    saves: list[dict] = field(default_factory=list)     # help_event 행 (insert or replace)
    logs: list[tuple] = field(default_factory=list)     # (event_id, ts, action, actor, detail)

    def __bool__(self):
        return bool(self.sends or self.saves or self.logs)


class EventManager:
    def __init__(self, cfg: dict, next_event_id: int = 1, open_events=()):
        """cfg = load_control_config() 결과. open_events = 재시작 전 열려 있던 HelpEvent 들."""
        self.seats: dict[int, dict] = cfg["seats"]
        self.priority: dict[str, int] = cfg["priority"]
        t = cfg["timers"]
        self.fall_neighbor_s = float(t["fall_neighbor_s"])
        self.fall_all_s = float(t["fall_all_s"])
        self.question_expire_s = float(t["question_expire_s"])
        self.away_alert_s = float(t["away_alert_s"])
        self.class_condition_s = float(t.get("class_condition_s", 10))
        self.view_hold_s = float(t.get("view_hold_s", 60))     # [수동보기] 강사가 고른 좌석을 비추는 시간
        # [세부컨디션] 세부 항목 비율을 낼 최소 학생 수 (그 항목을 보낸 학생 기준)
        self.detail_min_students = int(cfg.get("condition", {}).get("detail_min_students", 1))
        # [팬틸트] nudge_min_interval, nudge_gain 삭제 — P 제어(보정량 계산)는 팬틸트 모듈이 한다

        self.next_id = next_event_id
        self.events: dict[int, HelpEvent] = {}           # 열린 이벤트만
        self.active_id: int | None = None
        self.devices: dict[str, dict] = {}               # node -> {online, note}
        self.conditions: dict[int, tuple[dict, float]] = {}   # seat -> (condition_summary, rx_ts)
        self._last_class_sent: float | None = None
        self._last_queue: tuple | None = None
        self.gen = 0                                     # [팬틸트] 추적 대상(active 사건)이 바뀔 때마다 +1
        self._target_id: int | None = None               # [팬틸트] 마지막으로 팬틸트에 보낸 active 사건 (좌석 대신)
        self.pan_tilt: dict = {}                         # [팬틸트] 마지막 pan_tilt_state {state, pan, tilt, gen, error}
        self._seat_states = {}
        self._obs_session = None
        self._obs_frame = -1
        self._obs_last_rx: float | None = None           # 마지막 seat_obs 를 받은 Control 시각
        self._obs_drops = {'clock': 0, 'slow': 0}        # 경고 사이에 쌓인 개수
        self._obs_worst = {'clock': 0.0, 'slow': 0.0}    # 그 사이 가장 큰 시계 차이 / 관측 간격 (초)
        self._obs_warned_at = None
        self.view: dict | None = None                    # [수동보기] {"seat", "until", "by"} — 이벤트가 없을 때만
        self._fx = Effects()

        for ev in open_events:
            self.events[ev.event_id] = ev
            if ev.status == "active":
                self.active_id = ev.event_id             # [팬틸트] _target 은 비워 둠 → start() 에서 팬틸트에 다시 보냄
            self.next_id = max(self.next_id, ev.event_id + 1)

    def start(self, now: float) -> Effects:              # [팬틸트] 추가
        """hub 가 시작 직후 한 번 호출. 복구된 active 사건 좌석으로 팬틸트·Vision 을 맞춘다.
        [녹화] 재시작 전에 열려 있던 이벤트는 녹화를 새로 시작한다 (끊긴 녹화는 hub 가 DB 에서 닫는다)."""
        for ev in self._sorted_open():                                  # [녹화]
            self._record_start(ev)                                      # [녹화]
        self._reschedule(now)
        return self._flush()

    # =====================================================================
    # 입력
    # =====================================================================
    def on_seat_obs(self, value, now):
        # Observations are not incidents. Only this central process confirms them.
        age = now - value['captured_at']
        if not -.2 <= age <= .8:
            # 촬영 PC 와 Control 시계가 어긋나면(-0.2~0.8초 밖) 관측이 전부 버려져 사건이 안 생긴다.
            # 조용히 버리면 원인을 찾기 어려우므로 몇 개를 버렸는지 10초마다 알린다.
            self._obs_drop(now, 'clock', age)
            return Effects()
        if self._obs_last_rx is not None and now - self._obs_last_rx > OBS_GAP_S:
            # 관측 간격이 증거 간격(0.8초)보다 길면 손들기·자리비움 판정이 매번 처음부터 다시 시작된다
            # (Vision 분석이 약 1.25 fps 보다 느림). 사건이 안 생기는 이유를 알린다.
            self._obs_drop(now, 'slow', now - self._obs_last_rx)
        self._obs_last_rx = now
        if value['session'] != self._obs_session:
            self._obs_session, self._obs_frame = value['session'], -1
            self._seat_states.clear()
        if value['frame_id'] <= self._obs_frame:
            return Effects()
        self._obs_frame = value['frame_id']
        out = Effects()
        def incident(kind, seat, state):
            fx = self.on_incident(kind, seat, state, now, source='vision_service:seat_obs')
            out.sends.extend(fx.sends)
            out.saves.extend(fx.saves)
            out.logs.extend(fx.logs)
        for row in value['seats']:
            seat = row['seat']
            s = self._seat_states.setdefault(seat, {
                'seen': False, 'armed': True, 'down_sent': False,
                **{k: Evidence(OBS_GAP_S) for k in ('up', 'down', 'away', 'back')}})
            p, h = row['present'], row['hand_up']
            if p is True:
                s['seen'] = True
            if s['up'].update(h if p is True else None, now, 2.):
                if s['armed']:
                    incident('question', seat, 'start')
                    s['armed'], s['down_sent'] = False, False
            if s['down'].update(None if h is None else not h, now, .6):
                if not s['down_sent'] and self._find_open(seat, 'question'):
                    incident('question', seat, 'end')
                s['armed'], s['down_sent'] = True, True
            absent = None if p is None or not s['seen'] else not p
            if s['away'].update(absent, now, AWAY_S) and not self._find_open(seat, 'away'):
                incident('away', seat, 'start')
            if s['back'].update(p, now, 2.) and self._find_open(seat, 'away'):
                incident('away', seat, 'end')
        return out

    def _obs_drop(self, now, why, amount):
        """seat_obs 를 쓰지 못한 이유를 모아 OBS_WARN_S 마다 한 줄로 경고한다 (판정에는 영향 없음)."""
        self._obs_drops[why] += 1
        self._obs_worst[why] = max(self._obs_worst[why], abs(amount))
        if self._obs_warned_at is not None and now - self._obs_warned_at < OBS_WARN_S:
            return
        self._obs_warned_at = now
        if self._obs_drops['clock']:
            log.warning("seat_obs %d건 버림: 촬영 시각과 Control 시계 차이 최대 %.2f초 (허용 -0.2~0.8초). "
                        "카메라 PC 와 Control PC 의 시계를 맞추세요 (NTP)",
                        self._obs_drops['clock'], self._obs_worst['clock'])
        if self._obs_drops['slow']:
            log.warning("seat_obs 간격 최대 %.2f초 (%d회): Vision 분석이 너무 느려 손들기·자리비움이 확정되지 않습니다 "
                        "(관측 간격 %.1f초 이하 = 약 %.2f fps 이상 필요)",
                        self._obs_worst['slow'], self._obs_drops['slow'], OBS_GAP_S, 1 / OBS_GAP_S)
        self._obs_drops = {'clock': 0, 'slow': 0}
        self._obs_worst = {'clock': 0.0, 'slow': 0.0}

    def on_incident(self, kind: str, seat: int, state: str, now: float,
                    source: str | None = None) -> Effects:
        """vision_service 의 fall / question / away start·end."""
        ev = self._find_open(seat, kind)
        if state == "start":
            if ev is not None:
                log.debug("중복 무시: %s seat=%s (event %d)", kind, seat, ev.event_id)
                if ev.hand_down or ev.risen:          # 다시 손을 듦 / 다시 쓰러짐
                    ev.hand_down = ev.risen = False
                    self._update(ev)
                    self._explain(ev, now, "again")    # [알림문구]
                return self._flush()
            ev = HelpEvent(event_id=self.next_id, seat=seat, kind=kind,
                           priority=self.priority[kind], status="waiting",
                           detected_at=now, source=source)
            self.next_id += 1
            self.events[ev.event_id] = ev
            prev_active = self.active                 # [알림문구] 선점 여부를 알려 주려고
            self._save(ev)
            self._log(ev, now, "detect", SYSTEM, {"source": source})
            self._record_start(ev)                  # [녹화] 확정 시각부터 cam2 녹화
            self._reschedule(now)                   # active 가 되면 거기서 event_update 를 보냄
            if ev.status == "waiting":
                self._log(ev, now, "wait", SYSTEM)
                self._update(ev)
            preempted = prev_active if (prev_active is not None and ev.status == "active") else None
            self._explain(ev, now, "detect",                                      # [알림문구]
                          {"preempted_kind": preempted.kind, "preempted_seat": preempted.seat}
                          if preempted else None)
            return self._flush()

        # ---- end ----
        if ev is None:
            return self._flush()
        if kind == "question":                 # 닫지 않는다. 손 내림 표시만 (자동 제거는 논의 필요)
            if not ev.hand_down:
                ev.hand_down = True
                self._update(ev)
                self._explain(ev, now, "hand_down")    # [알림문구]
        elif kind == "fall":                   # 자동으로 닫지 않는다. "일어남" 표시만
            if not ev.risen:
                ev.risen = True
                self._update(ev)
                if ev.confirmed_at is None:           # [알림문구] 확인 전이면 "일어났지만 확인 필요"
                    self._explain(ev, now, "fall_risen")
        elif kind == "away":                   # 복귀 -> 종료
            self._close(ev, now, "cleared", "clear", SYSTEM)
            self._reschedule(now)
        return self._flush()

    def on_confirm(self, event_id: int, now: float, by: str) -> Effects:
        """강사 화면의 "확인". 상태는 그대로, 단계 알림만 멈춘다."""
        ev = self.events.get(event_id)
        if ev is None:
            log.warning("confirm: 열린 이벤트 %s 없음", event_id)
            return self._flush()
        self._confirm(ev, now, by)
        return self._flush()

    def on_fall_confirm(self, target_seat: int, now: float, by: str) -> Effects:
        """학생 노트북의 확인 버튼."""
        ev = self._find_open(target_seat, "fall")
        if ev is None:
            log.warning("fall_confirm: seat %s 의 열린 쓰러짐 없음", target_seat)
            return self._flush()
        self._confirm(ev, now, by)
        return self._flush()

    def on_resolve(self, event_id: int, now: float, by: str) -> Effects:
        """강사 화면의 "완료". 이벤트 종료 후 다음 대상."""
        return self._end_by_instructor(event_id, now, by, "resolved", "resolve")

    def on_dismiss(self, event_id: int, now: float, by: str) -> Effects:
        """강사 화면의 "오감지". 잘못 감지된 이벤트로 닫는다 (close_reason false_alarm). 나머지는 완료와 같다."""
        return self._end_by_instructor(event_id, now, by, "false_alarm", "dismiss")

    def _end_by_instructor(self, event_id: int, now: float, by: str, reason: str, action: str) -> Effects:
        ev = self.events.get(event_id)
        if ev is None:
            log.warning("%s: 열린 이벤트 %s 없음", action, event_id)
            return self._flush()
        if ev.kind == "fall" and ev.alert_stage != "none" and ev.confirmed_at is None:
            self._cancel_fall_alert(ev)          # 알림 받은 학생 화면의 알림을 내린다
        self._close(ev, now, reason, action, by)
        self._reschedule(now)
        return self._flush()

    # ---- [수동보기] 강사가 고른 좌석 비추기 ----
    def on_view_request(self, seat: int | None, now: float, by: str) -> tuple[Effects, dict]:
        """seat=None 이면 원위치. (할 일, 결과) 를 돌려준다. 결과는 AI 조교·화면이 그대로 안내에 쓴다:
        {"ok": True, "seat", "hold_s"} / {"ok": False, "reason": "tracking"|"pantilt_off"|"bad_seat", ...}"""
        if seat is None:
            if self.view is None:
                return self._flush(), {"ok": True, "home": True, "note": "이미 원위치(수동 보기 없음)"}
            self._end_view("request")
            self._reschedule(now)
            return self._flush(), {"ok": True, "home": True}
        if seat not in self.seats:
            return self._flush(), {"ok": False, "reason": "bad_seat", "message": f"{seat}번 좌석은 없습니다 (1~4)"}
        cur = self.active
        if cur is not None:                       # 이벤트 추적이 먼저 (10/22 결정: 추적 중이면 거절)
            return self._flush(), {"ok": False, "reason": "tracking",
                                   "tracking": {"event_id": cur.event_id, "seat": cur.seat, "kind": cur.kind},
                                   "message": f"지금 {cur.seat}번 좌석 {KIND_KO.get(cur.kind, cur.kind)} 이벤트를 추적 중이라 "
                                              "카메라를 돌릴 수 없습니다"}
        pt = self.devices.get(PANTILT)
        if pt is not None and not pt["online"]:
            return self._flush(), {"ok": False, "reason": "pantilt_off", "message": "팬틸트가 동작하지 않습니다 (SAFE)"}
        self.view = {"seat": seat, "until": now + self.view_hold_s, "by": by}
        log.info("수동 보기: %s번 좌석 (%s, %.0f초)", seat, by, self.view_hold_s)
        self._send(CONSOLE_NODE, "view_state", dict(self.view))
        self._reschedule(now)
        return self._flush(), {"ok": True, "seat": seat, "hold_s": self.view_hold_s}

    def _end_view(self, why: str):
        log.info("수동 보기 끝: %s번 좌석 (%s)", self.view["seat"] if self.view else None, why)
        self.view = None
        self._send(CONSOLE_NODE, "view_state", {"seat": None, "until": None, "by": None, "why": why})

    def on_explain_text(self, event_id: int, text: str, mode: str | None, now: float,
                        seq: int | None = None) -> Effects:
        ev = self.events.get(event_id)
        if ev is None:
            return self._flush()
        if seq is not None and seq != ev.explain_seq:    # [알림문구] 더 새 요청이 나간 뒤 늦게 온 옛 응답
            log.info("explain_text 무시: event %s seq %s (최신 %s)", event_id, seq, ev.explain_seq)
            return self._flush()
        ev.explain_text, ev.explain_mode = text, mode
        self._save(ev)
        self._update(ev)
        return self._flush()

    def on_track_obs(self, value: dict, now: float) -> Effects:   # [팬틸트] on_track_offset(nudge) 대체
        """Vision track_obs {gen, frame_id, captured_at, status, cx, cy} 를 팬틸트로 넘긴다.
        보정량 계산(P 제어)은 팬틸트가 한다. 지난 gen 의 좌표는 버린다."""
        if self.active is None or value.get("gen") != self.gen:
            return self._flush()
        self._send(PANTILT, "track", {
            "gen": self.gen, "cx": value.get("cx", .5), "cy": value.get("cy", .5),
            "captured_at": value["captured_at"],          # cam2 촬영 시각 — 바꾸지 않고 그대로
            "frame_id": value["frame_id"],
            "found": value.get("status") == "TRACKED"})
        return self._flush()

    def on_condition_summary(self, seat: int, value: dict, now: float) -> Effects:
        self.conditions[seat] = (value, now)
        return self._flush()

    def on_node_online(self, node: str, online: bool, now: float) -> Effects:
        """노드 접속·끊김. 바뀌었을 때만 device_update."""
        prev = self.devices.get(node)
        if prev is not None and prev["online"] == online:
            return self._flush()
        note = ""
        if not online:
            if node == VISION_NODE:
                note = "감지 중단"
            else:                               # [팬틸트] CAMERA_NODE 분기 삭제 (팬틸트는 on_pantilt_state)
                note = "연결 끊김"
        self.devices[node] = {"node": node, "online": online, "note": note}
        self._send(CONSOLE_NODE, "device_update", dict(self.devices[node]))
        if node == VISION_NODE:                                          # [팬틸트]
            self._send(PANTILT, "vision_online", {"online": online})    # [팬틸트] 끊기면 추적 멈추고 그 자리 유지
            if online:                          # 다시 붙은 Vision 에 지금 추적 대상을 알려 준다
                ev = self.active
                self._send(VISION_NODE, "track_target",
                           {"seat": ev.seat if ev else None, "gen": self.gen})   # [팬틸트] gen 추가
        return self._flush()

    # ---- [팬틸트] 팬틸트 → Control (hub 가 on_event 를 Control 루프로 넘겨 호출) ----
    def on_pantilt_state(self, fields: dict, now: float) -> Effects:   # [팬틸트] 추가
        """pan_tilt_state {state, pan, tilt, gen, error}. SAFE 면 Console 장비 상태에 '추적 중단'."""
        self.pan_tilt = fields
        online = fields["state"] != PANTILT_SAFE
        prev = self.devices.get(PANTILT)
        if prev is None or prev["online"] != online:
            self.devices[PANTILT] = {"node": PANTILT, "online": online,
                                     "note": "" if online else "추적 중단 (알림은 계속)"}
            self._send(CONSOLE_NODE, "device_update", dict(self.devices[PANTILT]))
        return self._flush()

    def on_preset_saved(self, fields: dict, now: float) -> Effects:    # [팬틸트] 추가
        """preset_saved {seat, pan, tilt}. 저장 직후와 시작 직후(저장된 좌석마다) 온다."""
        self._send("db", "seat_preset", {"seat": fields["seat"], "has_preset": 1})   # hub 가 seat.has_preset 갱신
        self._send(CONSOLE_NODE, "preset_saved", dict(fields))
        return self._flush()

    # ---- [팬틸트] Console 프리셋 등록 → 팬틸트 ----
    def on_pan_tilt_jog(self, dpan: int, dtilt: int, now: float) -> Effects:   # [팬틸트] 추가
        self._send(PANTILT, "jog", {"dpan": dpan, "dtilt": dtilt})   # IDLE·HOLD_SEAT 에서만 동작
        return self._flush()

    def on_preset_save(self, seat: int, now: float) -> Effects:      # [팬틸트] 추가
        self._send(PANTILT, "save", {"seat": seat})                   # 끝나면 on_preset_saved 로 돌아옴
        return self._flush()

    def snapshot(self) -> dict:
        """instructor_console 이 접속·재접속할 때 보내는 전체 상태."""
        return {"events": [e.to_msg() for e in self._sorted_open()],
                "queue": self._queue_value(),
                "devices": list(self.devices.values())}

    # =====================================================================
    # tick (0.1초마다, control 시계)
    # =====================================================================
    def tick(self, now: float) -> Effects:
        changed = False
        for ev in list(self.events.values()):
            age = now - ev.detected_at
            # 1. 쓰러짐 단계 알림
            if ev.kind == "fall" and ev.confirmed_at is None:
                if age >= self.fall_all_s and ev.alert_stage != "all":
                    self._fall_alert(ev, now, "all")
                elif age >= self.fall_neighbor_s and ev.alert_stage == "none":
                    self._fall_alert(ev, now, "neighbor")
            # 2. 질문 만료 (확인되지 않은 질문만)
            elif ev.kind == "question" and ev.confirmed_at is None \
                    and age >= self.question_expire_s:
                self._close(ev, now, "expired", "expire", SYSTEM)
                changed = True
            # 3. 자리비움 미복귀 -> 강사에게 다시 알림 (한 번)
            elif ev.kind == "away" and not ev.overdue and age >= self.away_alert_s:
                ev.overdue = True
                self._log(ev, now, "alert", SYSTEM, {"stage": "away_overdue"})
                self._update(ev)
                self._explain(ev, now, "away_overdue")  # [알림문구]
        # 4. [수동보기] 시간이 다 되면 원위치
        if self.view is not None and now >= self.view["until"]:
            self._end_view("timeout")
            changed = True
        # 5. 대기열 맨 앞이 바뀌었으면 교체
        if changed:
            self._reschedule(now)
        # 반 집계 (class_condition_s 마다)
        if self._last_class_sent is None:
            self._last_class_sent = now
        elif now - self._last_class_sent >= self.class_condition_s:
            self._last_class_sent = now
            self._send_class_condition(now)
        return self._flush()

    # =====================================================================
    # 내부
    # =====================================================================
    @property
    def active(self) -> HelpEvent | None:
        return self.events.get(self.active_id) if self.active_id is not None else None

    def _find_open(self, seat: int, kind: str) -> HelpEvent | None:
        for ev in self.events.values():
            if ev.seat == seat and ev.kind == kind:
                return ev
        return None

    def _sorted_open(self) -> list[HelpEvent]:
        return sorted(self.events.values(), key=HelpEvent.sort_key)

    def _queue_value(self) -> dict:
        return {"active": self.active_id,
                "waiting": [e.event_id for e in self._sorted_open() if e.status == "waiting"]}

    def _reschedule(self, now: float):
        """대기열 맨 앞이 지금 active 와 다르면 바꾼다(선점 포함)."""
        order = self._sorted_open()
        head = order[0] if order else None
        cur = self.active
        if head is not cur:
            if cur is not None:                       # 선점당함 -> 대기열로
                cur.status = "waiting"
                self._save(cur)
                self._log(cur, now, "wait", SYSTEM, {"preempted_by": head.event_id})
                self._update(cur)
            if head is not None:
                head.status = "active"
                self.active_id = head.event_id
                self._save(head)
                self._log(head, now, "preempt" if cur is not None else "activate", SYSTEM,
                          {"preempted": cur.event_id} if cur is not None else None)
                self._update(head)
            else:
                self.active_id = None
        # [팬틸트] 추적 대상(active 사건)이 바뀌었으면 gen+1 후 팬틸트와 Vision 에 알린다.
        # 좌석이 아니라 사건으로 비교한다: 같은 좌석에서 질문 → 쓰러짐으로 바뀌어도 LED·gen 이 바뀌어야 함
        if head is not None and self.view is not None:                       # [수동보기] 이벤트가 오면 수동 보기는 끝
            self._end_view("event")
        target_id = head.event_id if head is not None else (                 # [팬틸트]
            ("view", self.view["seat"]) if self.view else None)              # [수동보기] 이벤트 없으면 수동 좌석
        if target_id != self._target_id:                                     # [팬틸트]
            self._target_id = target_id                                      # [팬틸트]
            self.gen += 1                                                    # [팬틸트]
            if head is None and self.view is not None:                       # [수동보기] LED 끈 채로 좌석 이동
                self._send(PANTILT, "preset", {"gen": self.gen, "seat": self.view["seat"], "kind": 0})
                self._send(VISION_NODE, "track_target", {"seat": self.view["seat"], "gen": self.gen})
            elif head is not None:                                           # [팬틸트]
                self._send(PANTILT, "preset", {"gen": self.gen, "seat": head.seat,
                                               "kind": KIND_CODE[head.kind]})   # [팬틸트] 좌석 이동 + LED
                self._send(VISION_NODE, "track_target", {"seat": head.seat, "gen": self.gen})   # [팬틸트] gen 추가
            else:                                                            # [팬틸트]
                self._send(PANTILT, "home", {"gen": self.gen})               # [팬틸트] HOME, LED 끔
                self._send(VISION_NODE, "track_target", {"seat": None, "gen": self.gen})        # [팬틸트]
        q = self._queue_value()
        key = (q["active"], tuple(q["waiting"]))
        if key != self._last_queue:
            self._last_queue = key
            self._send(CONSOLE_NODE, "queue_state", q)

    def _confirm(self, ev: HelpEvent, now: float, by: str):
        if ev.confirmed_at is not None:
            return
        ev.confirmed_at, ev.confirmed_by = now, by
        self._save(ev)
        self._log(ev, now, "confirm", by)
        self._update(ev)
        if ev.kind == "fall" and ev.alert_stage != "none":
            self._cancel_fall_alert(ev)

    def _close(self, ev: HelpEvent, now: float, reason: str, action: str, actor: str):
        ev.status, ev.closed_at, ev.close_reason = "closed", now, reason
        if self.active_id == ev.event_id:
            self.active_id = None
        self.events.pop(ev.event_id, None)
        self._save(ev)
        self._log(ev, now, action, actor)
        self._update(ev)
        self._send(RECORDER, "stop", {"event_id": ev.event_id})        # [녹화] 완료·오감지·만료·복귀 = 녹화 끝

    def _fall_alert(self, ev: HelpEvent, now: float, stage: str):
        ev.alert_stage = stage
        self._save(ev)
        self._log(ev, now, "alert", SYSTEM, {"stage": stage})
        self._update(ev)
        self._send_students(self._alert_targets(ev), "fall_alert",
                            {"target_seat": ev.seat, "stage": stage})
        self._explain(ev, now, "fall_" + stage)                       # [알림문구] fall_neighbor / fall_all

    def _alert_targets(self, ev: HelpEvent) -> list[str]:
        """지금 alert_stage 에서 fall_alert 를 받은 학생 노드들."""
        if ev.alert_stage == "neighbor":
            return [student_node(n) for n in self.seats.get(ev.seat, {}).get("neighbors", [])]
        if ev.alert_stage == "all":
            return list(STUDENT_NODES)
        return []

    def _explain(self, ev: HelpEvent, now: float, situation: str, extra: dict | None = None):   # [알림문구]
        """vision_service 에 지금 상황의 알림 문구를 요청한다.
        situation: detect | again | fall_neighbor | fall_all | fall_risen | hand_down | away_overdue"""
        ev.explain_seq += 1
        ev.explain_situation = situation
        waiting = [e.event_id for e in self._sorted_open() if e.status == "waiting"]
        value = {
            "event_id": ev.event_id, "kind": ev.kind, "seat": ev.seat,
            "situation": situation, "seq": ev.explain_seq,
            "status": ev.status,                                      # active(추적 중) | waiting
            "queue_pos": waiting.index(ev.event_id) + 1 if ev.event_id in waiting else 0,
            "elapsed_s": round(now - ev.detected_at, 1),
            "waiting_count": len(waiting),
            "alert_stage": ev.alert_stage,
            "confirmed": ev.confirmed_at is not None,
        }
        if extra:
            value.update(extra)
        self._send(VISION_NODE, "explain_request", value)

    def _record_start(self, ev: HelpEvent):                            # [녹화]
        self._send(RECORDER, "start", {"event_id": ev.event_id, "seat": ev.seat, "kind": ev.kind})

    def _cancel_fall_alert(self, ev: HelpEvent):
        self._send_students(self._alert_targets(ev), "fall_alert_cancel", {"target_seat": ev.seat})

    def _send_class_condition(self, now: float):
        fresh = [v for v, ts in self.conditions.values() if now - ts <= 3 * self.class_condition_s]
        if not fresh:
            return
        n = len(fresh)
        # [세부컨디션] 항목마다 그 키를 보낸 학생만 분모로 센다 (옛 클라이언트는 묶음 2개만 보냄)
        detail = {}
        for k in CONDITION_DETAIL_KEYS:
            have = [v[k] for v in fresh if isinstance(v.get(k), bool)]
            detail[k] = (round(sum(have) / len(have), 2)
                         if have and len(have) >= self.detail_min_students else None)
        self._send(CONSOLE_NODE, "class_condition", {
            "focus_low_ratio": round(sum(bool(v.get("focus_low")) for v in fresh) / n, 2),
            "posture_bad_ratio": round(sum(bool(v.get("posture_bad")) for v in fresh) / n, 2),
            "reporting": n,
            "detail": detail})

    # ---- 할 일 쌓기 -----------------------------------------------------
    def _send(self, to: str, type_: str, value: dict, seat=None):
        self._fx.sends.append(Send(to, type_, value, seat))

    def _send_students(self, nodes, type_: str, value: dict):
        for node in nodes:
            self._send(node, type_, dict(value))

    def _update(self, ev: HelpEvent):
        self._send(CONSOLE_NODE, "event_update", ev.to_msg(), ev.seat)

    def _save(self, ev: HelpEvent):
        self._fx.saves.append(ev.to_row())

    def _log(self, ev: HelpEvent, now: float, action: str, actor: str, detail=None):
        self._fx.logs.append((ev.event_id, now, action, actor, detail))

    def _flush(self) -> Effects:
        fx, self._fx = self._fx, Effects()
        return fx