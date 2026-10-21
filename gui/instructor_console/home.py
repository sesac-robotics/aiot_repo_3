"""강사 화면 ① 홈 · ② 이벤트 창 · ③ 학생 정보 창 (화면 구성도 v3)

홈 = 왼쪽 '좌석도 자리'(좌석도 / 이벤트 창 / 학생 정보 창이 번갈아 열림)
   + 오른쪽 이벤트 로그(메인 창구) + 통계
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Callable

from PyQt6.QtCore import QDate, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QFrame, QGridLayout,
                             QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                             QPushButton, QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from common import protocol as P

from . import theme as T
from .store import SEATS, Store, mask_to_neighbors
from .video import ClipPlayer, LiveVideo

PAGE_SIZE = 50          # log_result 한 번에 최대 50건


def card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame(objectName="card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)
    if title:
        lay.addWidget(QLabel(title, objectName="cardTitle"))
    return f, lay


def today_start() -> float:
    return dt.datetime.combine(dt.date.today(), dt.time()).timestamp()


# ── 로그 검색 요청 관리 ─────────────────────────────────────
class LogQueries(QObject):
    """log_query의 req 번호로 응답을 요청한 곳에 돌려준다. 여러 화면이 동시에 검색해도 섞이지 않게."""

    def __init__(self, send: Callable, store: Store, parent=None):
        super().__init__(parent)
        self.send, self._req = send, 0
        self._waiting: dict[int, Callable] = {}
        self.items: dict[int, dict] = {}       # event_id → 마지막으로 받은 로그 항목 (이벤트 창 보조 정보)
        store.log_result.connect(self._on_result)

    def query(self, cb: Callable, kind=0, seat=0, student_name="", t_from=0.0, t_to=0.0, offset=0) -> bool:
        self._req = 1 if self._req >= 0xFFFF else self._req + 1
        sent = self.send(P.LOG_QUERY, {"req": self._req, "kind": kind, "seat": seat, "student_name": student_name,
                                       "from": t_from, "to": t_to, "offset": offset})
        if sent:                               # 못 보낸 요청의 콜백은 남기지 않는다
            self._waiting[self._req] = cb
        return sent

    def _on_result(self, m: dict):
        for it in m["items"]:
            self.items[it["event_id"]] = it
        cb = self._waiting.pop(m["req"], None)
        if cb:
            cb(m)

    def reset(self):
        """연결이 끊기면 기다리던 검색은 답이 오지 않는다 → 버린다"""
        self._waiting.clear()


# ── 1. 전체 학생 모니터링 (좌석도) ───────────────────────────
class SeatCard(QFrame):
    clicked = pyqtSignal(int)

    def __init__(self, seat: int):
        super().__init__()
        self.seat = seat
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(170, 130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.num = QLabel(f"{seat}번", alignment=Qt.AlignmentFlag.AlignCenter)
        self.name = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.state = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.sub = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        for w in (self.num, self.name, self.state, self.sub):
            lay.addWidget(w)

    def mousePressEvent(self, e):
        self.clicked.emit(self.seat)

    def update_view(self, name: str, ev: dict | None, extra: int, offline: bool = False):
        kind = ev["kind"] if ev else P.KIND_NONE
        color, bg = T.KIND_COLOR[kind], T.KIND_BG[kind]
        if offline and not ev:                    # 학생 노트북 끊김 = 쓰러짐 알림을 못 받는 자리
            bg = T.OFFLINE_ORANGE
        width = 3 if kind == P.KIND_FALL else (2 if ev else 1)
        self.setStyleSheet(f"SeatCard {{ background:{bg}; border:{width}px solid {color}; border-radius:12px; }}"
                           "QLabel { background: transparent; border: none; }")
        self.num.setStyleSheet("font-size:22px; font-weight:700; color:#202124;")
        self.name.setText(name or "미등록")
        self.name.setStyleSheet(f"font-size:15px; color:{'#3c4043' if name else '#b0b4b8'};")
        if ev:   # 평상시는 거의 안 보이게, 이벤트가 있을 때만 상태를 크게
            self.state.setText(T.KIND_NAME[kind] + (" · 대기" if ev["status"] == P.ST_WAITING else ""))
            self.state.setStyleSheet(f"font-size:15px; font-weight:700; color:{color};")
            sub = T.elapsed(ev["detected_at"])
            if ev["confirmed_at"]:
                sub += " · 확인됨"
            if extra:
                sub += f" · 외 {extra}건"
            if offline:
                sub += " · 노트북 끊김"
            self.sub.setText(sub)
        else:
            self.state.setText("")
            self.sub.setText("노트북 연결 끊김 — 알림 못 받음" if offline else "")
        self.sub.setStyleSheet(f"font-size:12px; color:{'#b3261e' if offline else '#5f6368'};")


class SeatMap(QWidget):
    seat_clicked = pyqtSignal(int)

    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        f, lay = card()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(f)
        board = QLabel("칠판 · 강사 자리 · 팬틸트 카메라", alignment=Qt.AlignmentFlag.AlignCenter)
        board.setStyleSheet("background:#eceff1; border-radius:6px; padding:6px; color:#546e7a;")
        lay.addWidget(board)
        grid = QGridLayout()
        grid.setSpacing(14)
        self.cards = {}
        for s in SEATS:                       # 2×2: 1 2 / 3 4
            c = SeatCard(s)
            c.clicked.connect(self.seat_clicked)
            grid.addWidget(c, (s - 1) // 2, (s - 1) % 2)
            self.cards[s] = c
        lay.addLayout(grid, 1)
        legend = QHBoxLayout()
        for k in (P.KIND_FALL, P.KIND_QUESTION, P.KIND_AWAY):
            dot = QLabel("●"); dot.setStyleSheet(f"color:{T.KIND_COLOR[k]};")
            legend.addWidget(dot); legend.addWidget(QLabel(f"{T.KIND_NAME[k]} ({k}순위)", objectName="muted"))
            legend.addSpacing(10)
        legend.addStretch(1)
        lay.addLayout(legend)

    def refresh(self):
        for s, c in self.cards.items():
            evs = self.store.open_events(s)
            d = self.store.devices.get(P.NODE_STUDENTS[s - 1])
            c.update_view(self.store.student_name(s), evs[0] if evs else None, max(0, len(evs) - 1),
                          offline=bool(d and not d["online"]))


# ── 2. 이벤트 창 ───────────────────────────────────────────
class EventPanel(QWidget):
    closed = pyqtSignal()

    def __init__(self, store: Store, send: Callable, logs: LogQueries, live_port: int, clip_root: str):
        super().__init__()
        self.store, self.send, self.logs = store, send, logs
        self.event_id = 0
        f, lay = card()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(f)

        top = QHBoxLayout()
        self.title = QLabel(objectName="cardTitle")
        self.title.setStyleSheet("font-size:17px;")
        close = QPushButton("닫기")
        close.clicked.connect(self.closed)
        top.addWidget(self.title, 1); top.addWidget(close)
        lay.addLayout(top)

        self.video_stack = QStackedWidget()
        self.live = LiveVideo(live_port, cam_id=2, title="팬틸트 카메라 실시간")
        self.clip = ClipPlayer(clip_root)
        self.clip.request_clip.connect(lambda eid: self.send(P.CLIP_REQUEST, {"event_id": eid}))
        store.clip_info.connect(self.clip.on_clip_info)
        self.video_stack.addWidget(self.live)
        self.video_stack.addWidget(self.clip)
        self.video_mode = QLabel(objectName="muted")
        lay.addWidget(self.video_mode)
        lay.addWidget(self.video_stack, 1)

        info = QGridLayout()
        info.setHorizontalSpacing(16)
        self.fields = {}
        for i, (key, label) in enumerate([("status", "상태"), ("detected", "감지 시각"), ("elapsed", "경과"),
                                          ("stage", "알림 단계"), ("confirmed", "확인"), ("closed", "종료")]):
            info.addWidget(QLabel(label, objectName="muted"), i // 3 * 2, i % 3)
            v = QLabel(); v.setStyleSheet("font-weight:600;")
            info.addWidget(v, i // 3 * 2 + 1, i % 3)
            self.fields[key] = v
        lay.addLayout(info)

        self.explain = QLabel(wordWrap=True)
        self.explain.setStyleSheet("background:#f1f3f4; border-radius:8px; padding:10px;")
        lay.addWidget(self.explain)

        btns = QHBoxLayout()
        self.b_confirm = QPushButton("확인", objectName="primary")
        self.b_resolve = QPushButton("완료")
        self.b_dismiss = QPushButton("오감지")
        self.b_confirm.setToolTip("쓰러짐은 단계 알림을 멈추고, 질문은 '응답 중'으로 바뀝니다")
        self.b_dismiss.setToolTip("잘못 감지된 이벤트로 닫습니다")
        self.b_confirm.clicked.connect(lambda: self.send(P.CONFIRM, {"event_id": self.event_id}))
        self.b_resolve.clicked.connect(lambda: self.send(P.RESOLVE, {"event_id": self.event_id}))
        self.b_dismiss.clicked.connect(self._dismiss)
        btns.addStretch(1)
        for b in (self.b_confirm, self.b_resolve, self.b_dismiss):
            b.setMinimumWidth(90); btns.addWidget(b)
        lay.addLayout(btns)

    def _dismiss(self):
        """오감지는 이벤트를 닫고 단계 알림도 멈추므로 한 번 더 묻는다 (완료 버튼 옆이라 잘못 누르기 쉬움)"""
        ev = self._data()
        if not ev:
            return
        warn = "\n\n⚠ 쓰러짐입니다. 학생 상태를 직접 확인했나요?" if ev["kind"] == P.KIND_FALL else ""
        ans = QMessageBox.question(
            self, "오감지로 닫기",
            f"{ev['seat']}번 좌석 {T.KIND_NAME[ev['kind']]} 이벤트를 잘못 감지된 것으로 닫습니다.{warn}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            self.send(P.DISMISS, {"event_id": self.event_id})

    def open(self, event_id: int):
        self.event_id = event_id
        self._last_live = None
        self.refresh()

    def _data(self) -> dict | None:
        ev = self.store.events.get(self.event_id)
        if ev:
            return ev
        it = self.logs.items.get(self.event_id)     # 지난 이벤트는 로그 항목만 있을 수 있다
        if it:
            return {**it, "alert_stage": None, "confirmed_at": 0.0, "explain_text": "", "explain_mode": 0,
                    "_from_log": True}
        return None

    def refresh(self):
        ev = self._data()
        if not ev:
            self.title.setText(f"이벤트 #{self.event_id} (정보 없음)")
            return
        kind, is_open = ev["kind"], ev["status"] != P.ST_CLOSED
        name = self.store.student_name(ev["seat"]) if is_open else ev.get("student_name") or self.store.student_name(ev["seat"])
        self.title.setText(f"{ev['seat']}번 좌석 · {name or '미등록'} · {T.KIND_NAME[kind]}")
        self.title.setStyleSheet(f"font-size:17px; font-weight:700; color:{T.KIND_COLOR[kind]};")

        if is_open != getattr(self, "_last_live", None):   # 진행 중 ↔ 지난 이벤트 전환 때만 영상 바꾸기
            self._last_live = is_open
            if is_open:
                self.clip.stop()
                self.video_stack.setCurrentWidget(self.live)
                self.video_mode.setText("<span style='color:#d93025'>●</span> 진행 중 — 팬틸트 카메라 실시간")
            else:
                self.video_stack.setCurrentWidget(self.clip)
                self.video_mode.setText("지난 이벤트 — 녹화 영상 다시 보기")
                self.clip.load(self.event_id)

        status = T.STATUS_NAME[ev["status"]]
        if ev["status"] == P.ST_CLOSED and ev.get("close_reason"):
            status += f" ({T.CLOSE_NAME[ev['close_reason']]})"
        elif ev["status"] == P.ST_ACTIVE and self.store.queue.get("active") == self.event_id:
            status += " · 카메라가 보는 중"
        self.fields["status"].setText(status)
        self.fields["detected"].setText(T.mdhm(ev["detected_at"]))
        self.fields["elapsed"].setText(T.elapsed(ev["detected_at"], ev["closed_at"] or None))
        self.fields["stage"].setText("—" if ev.get("alert_stage") is None else T.STAGE_NAME[ev["alert_stage"]])
        self.fields["confirmed"].setText(T.hms(ev["confirmed_at"]) if ev["confirmed_at"] else ("—" if ev.get("_from_log") else "아직 안 함"))
        self.fields["closed"].setText(T.hms(ev["closed_at"]))
        if ev["explain_text"]:
            mode = T.EXPLAIN_NAME.get(ev["explain_mode"], "")
            self.explain.setText(f"<b>AI 조교</b> <span style='color:#80868b'>({mode})</span><br>{ev['explain_text']}")
        else:
            self.explain.setText("<b>AI 조교</b><br><span style='color:#80868b'>설명 없음</span>")

        self.b_confirm.setEnabled(is_open and not ev["confirmed_at"])
        self.b_resolve.setEnabled(is_open)
        self.b_dismiss.setEnabled(is_open)


# ── 3. 학생 정보 창 ─────────────────────────────────────────
class StudentPanel(QWidget):
    closed = pyqtSignal()
    open_event = pyqtSignal(int)

    def __init__(self, store: Store, logs: LogQueries):
        super().__init__()
        self.store, self.logs = store, logs
        self.seat = 0
        f, lay = card()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(f)
        top = QHBoxLayout()
        self.title = QLabel(objectName="cardTitle"); self.title.setStyleSheet("font-size:17px;")
        close = QPushButton("닫기"); close.clicked.connect(self.closed)
        top.addWidget(self.title, 1); top.addWidget(close)
        lay.addLayout(top)

        info = QGridLayout()
        self.fields = {}
        for i, (key, label) in enumerate([("name", "이름"), ("state", "현재 상태"),
                                          ("reg", "자리 등록"), ("nb", "옆자리")]):
            info.addWidget(QLabel(label, objectName="muted"), i, 0)
            v = QLabel(); v.setStyleSheet("font-weight:600;")
            info.addWidget(v, i, 1)
            self.fields[key] = v
        info.setColumnStretch(1, 1)
        lay.addLayout(info)

        body = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("오늘 이 좌석의 도움 요청", objectName="cardTitle"))
        self.today = QListWidget()
        self.today.itemClicked.connect(self._item_clicked)
        left.addWidget(self.today, 1)
        note = QLabel("개인 집중·자세 값은 표시하지 않습니다 (학생 노트북 밖으로 나가지 않음)", objectName="muted")
        note.setWordWrap(True)
        left.addWidget(note)
        body.addLayout(left, 3)

        dev_frame = QFrame(); dev_frame.setStyleSheet("background:#f8f9fa; border-radius:8px;")
        dv = QVBoxLayout(dev_frame)
        dv.addWidget(QLabel("장비 상태", objectName="cardTitle"))
        self.dev = {}
        for key, label in [("net", "학생 노트북 네트워크"), ("cam", "노트북 카메라"),
                           ("cam1", "고정 카메라"), ("cam2", "팬틸트 카메라")]:
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            v = QLabel(); row.addStretch(1); row.addWidget(v)
            dv.addLayout(row)
            self.dev[key] = v
        dv.addStretch(1)
        body.addWidget(dev_frame, 2, Qt.AlignmentFlag.AlignBottom)
        lay.addLayout(body, 1)

    def _item_clicked(self, item):
        eid = item.data(Qt.ItemDataRole.UserRole)
        if eid:
            self.open_event.emit(eid)

    def open(self, seat: int):
        self.seat = seat
        self.today.clear()
        self.refresh()
        self.logs.query(self._on_today, seat=seat, t_from=today_start())

    def _on_today(self, m: dict):
        self.today.clear()
        for it in m["items"]:
            txt = (f"{T.mdhm(it['detected_at'])}  {T.KIND_NAME[it['kind']]}  "
                   f"{T.CLOSE_NAME[it['close_reason']] or T.STATUS_NAME[it['status']]}"
                   + ("   ▶ 영상" if it["has_clip"] else ""))
            li = QListWidgetItem(txt)
            li.setForeground(QColor(T.KIND_COLOR[it["kind"]]))
            li.setData(Qt.ItemDataRole.UserRole, it["event_id"])
            self.today.addItem(li)
        if not m["items"]:
            self.today.addItem("오늘은 도움 요청이 없습니다")

    @staticmethod
    def _ok(v: bool | None, ok="정상", bad="끊김") -> str:
        if v is None:
            return "<span style='color:#80868b'>정보 없음</span>"
        return f"<span style='color:#188038'>● {ok}</span>" if v else f"<span style='color:#e8710a'>● {bad}</span>"

    def refresh(self):
        if not self.seat:
            return
        s = self.store.seats[self.seat]
        name = self.store.student_name(self.seat)
        self.title.setText(f"{self.seat}번 좌석 학생 정보")
        self.fields["name"].setText(name or "미등록 (학생 화면에서 [자리 등록] 필요)")
        ev = self.store.seat_top_event(self.seat)
        self.fields["state"].setText(T.KIND_NAME[ev["kind"]] if ev else "일반")
        self.fields["state"].setStyleSheet(f"font-weight:600; color:{T.KIND_COLOR[ev['kind']] if ev else '#202124'};")
        # seat_info에는 등록 시각이 없다 → 등록 번호만 표시 (Interface Spec 보완 필요)
        self.fields["reg"].setText(f"등록 번호 {s['reg_id']}" if s["reg_id"] else "—")
        nbs = mask_to_neighbors(s["neighbors"])
        self.fields["nb"].setText(", ".join(f"{n}번 {self.store.student_name(n) or '(미등록)'}" for n in nbs) or "—")

        d = self.store.devices.get(10 + self.seat)
        if d:
            net = self._ok(bool(d["online"]))
            if d["online"] and d["rtt_ms"]:
                net += f" <span style='color:#80868b'>{d['rtt_ms']}ms</span>"
            self.dev["net"].setText(net)
            self.dev["cam"].setText(self._ok(bool(d["cam_ok"]) if d["online"] else None))
        else:
            self.dev["net"].setText(self._ok(None)); self.dev["cam"].setText(self._ok(None))
        c1, c2 = self.store.devices.get(P.NODE_CAM1), self.store.devices.get(P.NODE_CAM2)
        self.dev["cam1"].setText(self._ok(bool(c1["online"]) if c1 else None))
        cam2 = self._ok(bool(c2["online"]) if c2 else None)
        if not s["has_preset"]:
            cam2 += " <span style='color:#e8710a'>· 각도 미등록</span>"
        self.dev["cam2"].setText(cam2)


# ── 2. 이벤트 로그 (메인 창구) ──────────────────────────────
class LogPanel(QWidget):
    open_event = pyqtSignal(int)
    COLS = ["시각", "좌석", "학생", "종류", "상태", "녹화"]

    def __init__(self, store: Store, logs: LogQueries):
        super().__init__()
        self.store, self.logs = store, logs
        self.items: list[dict] = []
        self.total = 0
        self.selected = 0
        self.params: dict = {}
        f, lay = card("이벤트 로그")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(f)

        # 검색: 이벤트별 · 학생별 · 기간별
        r1 = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.addItem("전체 종류", 0)
        for k in (P.KIND_FALL, P.KIND_QUESTION, P.KIND_AWAY):
            self.kind.addItem(T.KIND_NAME[k], k)
        self.student = QComboBox(editable=True)
        self.student.lineEdit().setPlaceholderText("학생 이름")
        self.student.setMinimumWidth(110)
        r1.addWidget(self.kind); r1.addWidget(self.student, 1)
        lay.addLayout(r1)
        r2 = QHBoxLayout()
        self.use_period = QCheckBox("기간")
        self.d_from = QDateEdit(QDate.currentDate(), calendarPopup=True)
        self.d_to = QDateEdit(QDate.currentDate(), calendarPopup=True)
        for w in (self.d_from, self.d_to):
            w.setDisplayFormat("yyyy-MM-dd"); w.setEnabled(False)
        self.use_period.toggled.connect(self.d_from.setEnabled)
        self.use_period.toggled.connect(self.d_to.setEnabled)
        search = QPushButton("검색", objectName="primary")
        reset = QPushButton("초기화")
        search.clicked.connect(self.search)
        reset.clicked.connect(self.reset)
        self.student.lineEdit().returnPressed.connect(self.search)
        r2.addWidget(self.use_period); r2.addWidget(self.d_from); r2.addWidget(QLabel("~")); r2.addWidget(self.d_to)
        r2.addStretch(1); r2.addWidget(reset); r2.addWidget(search)
        lay.addLayout(r2)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.cellClicked.connect(self._clicked)
        lay.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.count = QLabel(objectName="muted")
        self.more = QPushButton("더 보기")
        self.more.clicked.connect(lambda: self._run(offset=len(self.items)))
        bottom.addWidget(self.count, 1); bottom.addWidget(self.more)
        lay.addLayout(bottom)

        # 이벤트가 바뀌면 첫 페이지를 다시 검색 (몰려올 때 한 번만)
        self.debounce = QTimer(self, singleShot=True, interval=400)
        self.debounce.timeout.connect(lambda: self._run(offset=0, keep=True))

    def search(self):
        kind = self.kind.currentData()
        t_from = t_to = 0.0
        if self.use_period.isChecked():
            a, b = self.d_from.date().toPyDate(), self.d_to.date().toPyDate()
            if a > b:
                a, b = b, a
            t_from = dt.datetime.combine(a, dt.time()).timestamp()
            t_to = dt.datetime.combine(b + dt.timedelta(days=1), dt.time()).timestamp()   # 끝 날짜 포함
        self.params = {"kind": kind, "student_name": self.student.currentText().strip(), "t_from": t_from, "t_to": t_to}
        self._run(offset=0)

    def reset(self):
        self.kind.setCurrentIndex(0)
        self.student.setCurrentText("")
        self.use_period.setChecked(False)
        self.search()

    def _run(self, offset: int, keep: bool = False):
        # keep=True(실시간 갱신)는 첫 페이지만 다시 받고, '더 보기'로 늘린 뒷부분은 _on_result에서 유지
        sent = self.logs.query(lambda m, o=offset, k=keep: self._on_result(m, o, k),
                               offset=offset, **self.params)
        if not sent:
            self.count.setText("Control에 연결되지 않아 검색할 수 없습니다")

    def _on_result(self, m: dict, offset: int, keep: bool = False):
        if offset == 0 and keep:             # 같은 검색 조건의 실시간 갱신 → 뒷부분 유지
            old_tail = self.items[PAGE_SIZE:] if len(self.items) > PAGE_SIZE else []
            new_ids = {it["event_id"] for it in m["items"]}
            self.items = m["items"] + [it for it in old_tail if it["event_id"] not in new_ids]
        elif offset == 0:                    # 새 검색 → 이전 결과는 버린다
            self.items = list(m["items"])
        else:
            self.items += m["items"]
        self.total = m["total"]
        self._render()

    def _render(self):
        self.table.setRowCount(len(self.items))
        bold = QFont(); bold.setBold(True)
        sel_row = -1
        for r, it in enumerate(self.items):
            live = self.store.events.get(it["event_id"])
            status, reason = (live["status"], live["close_reason"]) if live else (it["status"], it["close_reason"])
            unconfirmed = bool(live and status != P.ST_CLOSED and not live["confirmed_at"])
            name = it["student_name"] or "(미등록)"
            cells = [T.mdhm(it["detected_at"]), f"{it['seat']}번", name, T.KIND_NAME[it["kind"]],
                     T.CLOSE_NAME[reason] if status == P.ST_CLOSED else T.STATUS_NAME[status],
                     "▶" if it["has_clip"] else ""]
            for c, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setData(Qt.ItemDataRole.UserRole, it["event_id"])
                if c == 3:
                    cell.setForeground(QColor(T.KIND_COLOR[it["kind"]]))
                if unconfirmed:
                    cell.setFont(bold)
                    cell.setBackground(QColor(T.KIND_BG[it["kind"]]))
                self.table.setItem(r, c, cell)
            if it["event_id"] == self.selected:
                sel_row = r
        if sel_row >= 0:
            self.table.selectRow(sel_row)
        else:
            self.table.clearSelection()
        self.count.setText(f"{len(self.items)} / {self.total}건 · 굵은 줄 = 확인 안 함")
        self.more.setEnabled(len(self.items) < self.total)
        names = sorted({it["student_name"] for it in self.items if it["student_name"]} |
                       {self.store.student_name(s) for s in SEATS if self.store.student_name(s)})
        cur = self.student.currentText()
        self.student.blockSignals(True)
        self.student.clear(); self.student.addItems([""] + names); self.student.setCurrentText(cur)
        self.student.blockSignals(False)

    def _clicked(self, row, _col):
        eid = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self.select(eid)
        self.open_event.emit(eid)

    def select(self, event_id: int):
        """이벤트 창에 열린 이벤트를 로그에서 강조"""
        self.selected = event_id
        self._render()

    def on_event_changed(self):
        self.debounce.start()


# ── 3. 통계 ────────────────────────────────────────────────
class StatsPanel(QWidget):
    def __init__(self, store: Store, logs: LogQueries):
        super().__init__()
        self.store, self.logs = store, logs
        f, lay = card("반 통계")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(f)
        row = QHBoxLayout()
        self.tiles = {}
        for key, label in [("focus", "집중 낮은 학생 비율"), ("posture", "자세 불량 비율"), ("today", "오늘 도움 요청")]:
            box = QVBoxLayout()
            v = QLabel("—"); v.setStyleSheet("font-size:24px; font-weight:700;")
            box.addWidget(v); box.addWidget(QLabel(label, objectName="muted"))
            row.addLayout(box); row.addSpacing(18)
            self.tiles[key] = v
        row.addStretch(1)
        lay.addLayout(row)
        self.n = QLabel("반 평균만 표시 · 개인 값 없음", objectName="muted")
        lay.addWidget(self.n)
        self.rest = QLabel(objectName="banner", wordWrap=True)
        self.rest.hide()
        lay.addWidget(self.rest)
        self.rest_timer = QTimer(self, singleShot=True, interval=5 * 60 * 1000)   # 새 권장이 오면 다시 5분
        self.rest_timer.timeout.connect(self.rest.hide)
        store.condition_changed.connect(self.refresh)
        store.rest_suggested.connect(self.on_rest)

    def refresh(self):
        c = self.store.condition
        if c:
            self.tiles["focus"].setText(f"{c['focus_low_ratio'] * 100:.0f}%")
            self.tiles["posture"].setText(f"{c['posture_bad_ratio'] * 100:.0f}%")
            n = c.get("n", c.get("reporting", 0))     # v1.1 'n' / 현재 hub 'reporting'
            self.n.setText(f"최근 10초 · 학생 {n}명 반 평균 · 개인 값 없음")

    def refresh_today(self):
        self.logs.query(lambda m: self.tiles["today"].setText(f"{m['total']}건"), t_from=today_start())

    def on_rest(self, ratio: float, since: float):
        mins = max(1, int((time.time() - since) // 60))
        self.rest.setText(f"☕ 휴식 권장 — 학생 {ratio * 100:.0f}%가 {mins}분째 집중이 낮습니다")
        self.rest.show()
        self.rest_timer.start()


# ── 홈 페이지 ──────────────────────────────────────────────
class HomePage(QWidget):
    def __init__(self, store: Store, send: Callable, logs: LogQueries, live_port: int, clip_root: str):
        super().__init__(objectName="page")
        self.store = store
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(16)

        self.seat_area = QStackedWidget()         # 좌석도 자리
        self.seat_map = SeatMap(store)
        self.event_panel = EventPanel(store, send, logs, live_port, clip_root)
        self.student_panel = StudentPanel(store, logs)
        for w in (self.seat_map, self.event_panel, self.student_panel):
            self.seat_area.addWidget(w)
        lay.addWidget(self.seat_area, 11)

        right = QVBoxLayout()
        right.setSpacing(16)
        self.log = LogPanel(store, logs)
        self.stats = StatsPanel(store, logs)
        right.addWidget(self.log, 1)
        right.addWidget(self.stats)
        lay.addLayout(right, 9)

        self.seat_map.seat_clicked.connect(self.open_student)
        self.log.open_event.connect(self.open_event)
        self.student_panel.open_event.connect(self.open_event)
        self.event_panel.closed.connect(self.show_map)
        self.student_panel.closed.connect(self.show_map)

    def show_map(self):
        self.event_panel.clip.stop()
        self.log.select(0)
        self.seat_area.setCurrentWidget(self.seat_map)

    def open_event(self, event_id: int):
        self.event_panel.open(event_id)
        self.log.select(event_id)
        self.seat_area.setCurrentWidget(self.event_panel)

    def open_student(self, seat: int):
        self.event_panel.clip.stop()
        self.student_panel.open(seat)
        self.seat_area.setCurrentWidget(self.student_panel)

    def tick(self):
        """1초마다: 경과 시간 갱신"""
        self.seat_map.refresh()
        cur = self.seat_area.currentWidget()
        if cur is self.event_panel:
            self.event_panel.refresh()
        elif cur is self.student_panel:
            self.student_panel.refresh()
