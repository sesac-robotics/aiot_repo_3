"""강사 화면 ① 홈 · ② 이벤트 창 · ③ 학생 정보 창 (화면 구성도 v3)

홈 = 왼쪽 '좌석도 자리'(좌석도 / 이벤트 창 / 학생 정보 창 / [리포트] 수업 종료 리포트가 번갈아 열림)
   + 오른쪽 이벤트 로그(메인 창구) + 통계 + [질의] AI 조교에게 묻기 (10/22 시안)
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

from . import condition_view as CV               # [세부컨디션] 반 평균 세부 6개
from . import theme as T
from .assistant import AssistantPanel                # [질의] ⑤ AI 조교에게 묻기
from .camera_view import CameraViewPanel             # [수동보기] "1번 좌석 보여줘"
from .report_view import ReportPanel                 # [리포트] ⑥ 수업 종료 리포트
from .store import SEATS, Store
from .video import ClipPlayer, LiveVideo, UdpFrameSource

PAGE_SIZE = 50          # 로그 한 번에 최대 50건


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
    """이벤트 로그 검색. Control(hub)에는 로그 검색 메시지가 없으므로, 이 화면이 켜진 뒤
    받은 이벤트(snapshot·event_update, 닫힌 것 포함) 안에서 찾는다.
    결과 형식은 화면 쪽이 그대로 쓰도록 {"items": [...], "total": n} 으로 콜백에 돌려준다."""

    def __init__(self, store: Store, parent=None):
        super().__init__(parent)
        self.store = store

    def query(self, cb: Callable, kind="", seat=0, student_name="", t_from=0.0, t_to=0.0, offset=0) -> bool:
        items = []
        for e in self.store.events.values():
            if kind and e["kind"] != kind:
                continue
            if seat and e["seat"] != seat:
                continue
            name = self.store.student_name(e["seat"])
            if student_name and student_name not in name:
                continue
            if (t_from and e["detected_at"] < t_from) or (t_to and e["detected_at"] >= t_to):
                continue
            items.append({**e, "student_name": name, "has_clip": False})
        items.sort(key=lambda e: -e["detected_at"])
        cb({"items": items[offset:offset + PAGE_SIZE], "total": len(items)})
        return True

    def reset(self):
        pass


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
        kind = ev["kind"] if ev else T.NO_KIND
        color, bg = T.KIND_COLOR[kind], T.KIND_BG[kind]
        if offline and not ev:                    # 학생 노트북 끊김 = 쓰러짐 알림을 못 받는 자리
            bg = T.OFFLINE_ORANGE
        width = 3 if kind == "fall" else (2 if ev else 1)
        self.setStyleSheet(f"SeatCard {{ background:{bg}; border:{width}px solid {color}; border-radius:12px; }}"
                           "QLabel { background: transparent; border: none; }")
        self.num.setStyleSheet("font-size:22px; font-weight:700; color:#202124;")
        self.name.setText(name or "미등록")
        self.name.setStyleSheet(f"font-size:15px; color:{'#3c4043' if name else '#b0b4b8'};")
        if ev:   # 평상시는 거의 안 보이게, 이벤트가 있을 때만 상태를 크게
            self.state.setText(T.KIND_NAME[kind] + (" · 대기" if ev["status"] == "waiting" else ""))
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
        for k in T.KINDS:
            dot = QLabel("●"); dot.setStyleSheet(f"color:{T.KIND_COLOR[k]};")
            legend.addWidget(dot)
            legend.addWidget(QLabel(f"{T.KIND_NAME[k]} ({P.INCIDENT_PRIORITY[k]}순위)", objectName="muted"))
            legend.addSpacing(10)
        legend.addStretch(1)
        lay.addLayout(legend)

    def refresh(self):
        for s, c in self.cards.items():
            evs = self.store.open_events(s)
            d = self.store.student_device(s)
            c.update_view(self.store.student_name(s), evs[0] if evs else None, max(0, len(evs) - 1),
                          offline=bool(d and not d["online"]))


# ── 2. 이벤트 창 ───────────────────────────────────────────
class EventPanel(QWidget):
    closed = pyqtSignal()

    def __init__(self, store: Store, send: Callable, logs: LogQueries, cam2: UdpFrameSource, clip_root: str):
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
        self.live = LiveVideo(cam2, title="팬틸트 카메라 실시간")
        self.clip = ClipPlayer(clip_root)
        # 녹화 다시 보기: Control 에 clip_request → clip_info 가 오면 main_window 가 on_clip_info 로 넘긴다
        self.clip.request_clip.connect(self._request_clip)
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
        self.b_confirm.clicked.connect(lambda: self.send("confirm", {"event_id": self.event_id}))
        self.b_resolve.clicked.connect(lambda: self.send("resolve", {"event_id": self.event_id}))
        self.b_dismiss.clicked.connect(self._dismiss)
        btns.addStretch(1)
        for b in (self.b_confirm, self.b_resolve, self.b_dismiss):
            b.setMinimumWidth(90); btns.addWidget(b)
        lay.addLayout(btns)

    def _request_clip(self, event_id: int):
        if not self.send("clip_request", {"event_id": event_id}):
            self.clip.on_clip_info({"event_id": event_id, "status": "offline"})

    def _dismiss(self):
        """오감지는 이벤트를 닫고 단계 알림도 멈추므로 한 번 더 묻는다 (완료 버튼 옆이라 잘못 누르기 쉬움)"""
        ev = self._data()
        if not ev:
            return
        warn = "\n\n⚠ 쓰러짐입니다. 학생 상태를 직접 확인했나요?" if ev["kind"] == "fall" else ""
        ans = QMessageBox.question(
            self, "오감지로 닫기",
            f"{ev['seat']}번 좌석 {T.KIND_NAME[ev['kind']]} 이벤트를 잘못 감지된 것으로 닫습니다.{warn}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            self.send("dismiss", {"event_id": self.event_id})

    def open(self, event_id: int):
        self.event_id = event_id
        self._last_live = None
        self.refresh()

    def _data(self) -> dict | None:
        return self.store.events.get(self.event_id)

    def refresh(self):
        ev = self._data()
        if not ev:
            self.title.setText(f"이벤트 #{self.event_id} (정보 없음)")
            return
        kind, is_open = ev["kind"], ev["status"] != "closed"
        name = self.store.student_name(ev["seat"])
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

        status = T.STATUS_NAME.get(ev["status"], ev["status"])
        if ev["status"] == "closed" and ev.get("close_reason"):
            status += f" ({T.CLOSE_NAME.get(ev['close_reason'], ev['close_reason'])})"
        elif ev["status"] == "active" and self.store.queue.get("active") == self.event_id:
            status += " · 카메라가 보는 중"
        self.fields["status"].setText(status)
        self.fields["detected"].setText(T.mdhm(ev["detected_at"]))
        self.fields["elapsed"].setText(T.elapsed(ev["detected_at"], ev["closed_at"] or None))
        stage = ev.get("alert_stage") or "none"
        self.fields["stage"].setText("—" if kind != "fall" else T.STAGE_NAME.get(stage, stage))
        self.fields["confirmed"].setText(T.hms(ev["confirmed_at"]) if ev["confirmed_at"] else "아직 안 함")
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
                   f"{T.CLOSE_NAME.get(it['close_reason']) or T.STATUS_NAME.get(it['status'], '')}"
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
        self.fields["reg"].setText(f"{T.mdhm(s['registered_at'])} 등록" if s.get("registered_at") else "—")
        nbs = s["neighbors"]
        self.fields["nb"].setText(", ".join(f"{n}번 {self.store.student_name(n) or '(미등록)'}" for n in nbs) or "—")

        d = self.store.student_device(self.seat)
        self.dev["net"].setText(self._ok(bool(d["online"]) if d else None))
        # 노트북 카메라·cam1·cam2 상태는 아직 Control 이 Console 에 보내지 않는다 → 정보 없음
        self.dev["cam"].setText(self._ok(None))
        self.dev["cam1"].setText(self._ok(None))
        cam2 = self._ok(None)
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
        self.kind.addItem("전체 종류", "")
        for k in T.KINDS:
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
            unconfirmed = bool(live and status != "closed" and not live["confirmed_at"])
            name = it["student_name"] or "(미등록)"
            cells = [T.mdhm(it["detected_at"]), f"{it['seat']}번", name, T.KIND_NAME[it["kind"]],
                     T.CLOSE_NAME.get(reason, reason) if status == "closed" else T.STATUS_NAME.get(status, status),
                     "▶" if it.get("has_clip") else ""]
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
        self.count.setText(f"{len(self.items)} / {self.total}건 · 굵은 줄 = 확인 안 함 · 이 화면을 켠 뒤 받은 이벤트만")
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
    """③ 반 통계 (반 평균만). 기본은 묶음 2개 막대, [세부 보기] 를 누르면 세부 6개 + 30분 추이 (10/22 시안)."""

    def __init__(self, store: Store, logs: LogQueries):
        super().__init__()
        self.store, self.logs = store, logs
        f, lay = card()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(f)
        head = QHBoxLayout()
        head.addWidget(QLabel("반 통계 (반 평균만)", objectName="cardTitle"))
        self.n = QLabel("학생 —명 · 최근 10초", objectName="muted")
        head.addWidget(self.n)
        head.addStretch(1)
        self.b_detail = QPushButton("세부 보기 ▾", checkable=True, objectName="detailToggle")   # [세부컨디션]
        self.b_detail.toggled.connect(self.on_toggle)
        head.addWidget(self.b_detail)
        lay.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(24)
        self.meters = {g: CV.Meter(CV.GROUP_NAME[g], CV.GROUP_COLOR[g], big=True) for g in ("focus", "posture")}
        for m in self.meters.values():
            row.addWidget(m, 1)
        lay.addLayout(row)

        self.detail = CV.ConditionDetail()                 # 기본 접힘
        self.detail.hide()
        lay.addWidget(self.detail)

        self.today = QLabel("오늘: —", objectName="muted")
        lay.addWidget(self.today)
        self.rest = QLabel(objectName="banner", wordWrap=True)
        self.rest.hide()
        lay.addWidget(self.rest)
        self.rest_timer = QTimer(self, singleShot=True, interval=5 * 60 * 1000)   # 새 권장이 오면 다시 5분
        self.rest_timer.timeout.connect(self.rest.hide)
        store.condition_changed.connect(self.refresh)
        store.rest_suggested.connect(self.on_rest)

    def on_toggle(self, on: bool):
        self.b_detail.setText("세부 접기 ▴" if on else "세부 보기 ▾")
        self.detail.setVisible(on)
        if on:
            self.refresh()

    def refresh(self):
        c = self.store.condition
        if not c:
            return
        self.meters["focus"].set_value(c.get("focus_low_ratio"))
        self.meters["posture"].set_value(c.get("posture_bad_ratio"))
        n = c.get("n", c.get("reporting", 0))     # v1.1 'n' / 현재 hub 'reporting'
        small = 0 < n < CV.SMALL_CLASS
        self.n.setText(f"학생 {n}명 · 최근 10초")
        self.n.setObjectName("smallN" if small else "muted")
        self.n.style().unpolish(self.n); self.n.style().polish(self.n)
        if self.b_detail.isChecked():                    # 접혀 있으면 그리지 않음
            self.detail.refresh(c, self.store.condition_history)

    def refresh_today(self):
        self.logs.query(lambda m: self.today.setText(f"오늘 도움 요청 {m['total']}건"), t_from=today_start())

    def on_rest(self, ratio: float, since: float):
        mins = max(1, int((time.time() - since) // 60))
        self.rest.setText(f"☕ 휴식 권장 — 학생 {ratio * 100:.0f}%가 {mins}분째 집중이 낮습니다")
        self.rest.show()
        self.rest_timer.start()


# ── 홈 페이지 ──────────────────────────────────────────────
class HomePage(QWidget):
    def __init__(self, store: Store, send: Callable, logs: LogQueries, cam2: UdpFrameSource, clip_root: str):
        super().__init__(objectName="page")
        self.store = store
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(16)

        self.seat_area = QStackedWidget()         # 좌석도 자리
        self.seat_map = SeatMap(store)
        self.event_panel = EventPanel(store, send, logs, cam2, clip_root)
        self.student_panel = StudentPanel(store, logs)
        self.report_panel = ReportPanel()                                  # [리포트]
        self.view_panel = CameraViewPanel(cam2, send)                      # [수동보기]
        for w in (self.seat_map, self.event_panel, self.student_panel, self.report_panel, self.view_panel):
            self.seat_area.addWidget(w)
        self.reports: dict[int, dict] = {}                                 # [리포트] report_id -> 최신 report
        lay.addWidget(self.seat_area, 11)

        right = QVBoxLayout()
        right.setSpacing(16)
        self.log = LogPanel(store, logs)
        self.stats = StatsPanel(store, logs)
        self.assistant = AssistantPanel(send)                              # [질의]
        self.assistant.setMinimumHeight(300)
        right.addWidget(self.log, 3)
        right.addWidget(self.stats)
        right.addWidget(self.assistant, 2)
        lay.addLayout(right, 9)

        self.seat_map.seat_clicked.connect(self.open_student)
        self.log.open_event.connect(self.open_event)
        self.student_panel.open_event.connect(self.open_event)
        self.event_panel.closed.connect(self.show_map)
        self.student_panel.closed.connect(self.show_map)
        self.report_panel.closed.connect(self.show_map)                    # [리포트]
        self.view_panel.closed.connect(self.show_map)                      # [수동보기]
        self.assistant.camera_home.connect(lambda: send("view_request", {"seat": None}))
        self.assistant.open_report.connect(self.open_report)
        self.assistant.save_report_pdf.connect(self.save_report_pdf)

    def show_map(self):
        self.event_panel.clip.stop()
        self.log.select(0)
        self.seat_area.setCurrentWidget(self.seat_map)

    def open_event(self, event_id: int):
        self.event_panel.open(event_id)
        self.log.select(event_id)
        self.seat_area.setCurrentWidget(self.event_panel)

    # ── [수동보기] ──
    def on_view_state(self, m: dict):
        """Control 의 view_state. 시작이면 좌석도 자리에 카메라 보기, 끝이면 (보고 있던 경우) 좌석도로."""
        self.view_panel.show_state(m)
        self.assistant.on_view_state(m)
        if m.get("seat"):
            if self.seat_area.currentWidget() is not self.event_panel:      # 이벤트 창을 보는 중이면 가리지 않는다
                self.event_panel.clip.stop()
                self.log.select(0)
                self.seat_area.setCurrentWidget(self.view_panel)
        elif self.seat_area.currentWidget() is self.view_panel:
            self.seat_area.setCurrentWidget(self.seat_map)

    # ── [리포트] ──
    def on_report(self, m: dict):
        """Control 의 report (숫자 먼저 writing → 문장 done). 리포트 화면이 이 리포트를 보고 있으면 바로 갱신."""
        self.reports[m["report_id"]] = m
        self.assistant.on_report(m)
        cur = self.report_panel.report or {}
        if self.seat_area.currentWidget() is self.report_panel and cur.get("report_id") == m["report_id"]:
            self.report_panel.show_report(m)

    def open_report(self, report_id: int):
        m = self.reports.get(report_id)
        if not m:
            return
        self.event_panel.clip.stop()
        self.log.select(0)
        self.report_panel.show_report(m)
        self.seat_area.setCurrentWidget(self.report_panel)

    def save_report_pdf(self, report_id: int):
        self.open_report(report_id)
        self.report_panel.save_pdf()

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
        elif cur is self.view_panel:                                        # [수동보기] 남은 시간
            self.view_panel.tick()
