"""강사 화면 ⑤ AI 조교에게 묻기 (홈 오른쪽 아래, 10/22 시안)

- 질문 → Control 에 ask {ask_id, text} → (vision LLM ⇄ control 도구) → ask_answer {ask_id, text, mode, tools, took_s, fallback}
- [수업 종료 리포트] → report_request {report_id} → report {status: writing → done}
  리포트 카드의 [리포트 열기] 를 누르면 좌석도 자리에 ⑥ 리포트 화면이 열린다 (home.py).
- 조회만 한다. 학생은 좌석 번호로만 답한다 (이름은 vision·control 이 다루지 않음).
"""
from __future__ import annotations

import itertools
import time
from typing import Callable

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
                             QVBoxLayout, QWidget)

ASK_TIMEOUT_MS = 45_000          # vision 의 질문 전체 대기(30초) + 여유
REPORT_TIMEOUT_MS = 60_000

QUICK = ["지금 대기 중인 요청?", "오늘 평균 확인 시간"]
TOOL_LABEL = {"get_open_events": "열린 이벤트", "search_events": "이벤트 검색", "get_event_timeline": "처리 과정",
              "get_stats": "통계", "get_device_status": "장비 상태", "get_class_condition": "반 평균"}
ARG_LABEL = {"fall": "쓰러짐", "question": "질문", "away": "자리비움", "today": "오늘", "false_alarm": "오감지",
             "resolved": "완료", "open": "처리 중", "closed": "종료", "unconfirmed": "미확인"}
MODE_LABEL = {"gemini": "Gemini", "ollama": "Ollama", "rule": "정해진 답", "default": "답 못 함",
              "error": "연결 안 됨", "none": "AI 없음"}

_ids = itertools.count(int(time.time()) % 100000 * 10)


def tool_chip_text(t: dict) -> str:
    """{"name":"search_events","args":{"kind":"fall","since":"today"},"summary":"2건"} → "이벤트 검색 · 쓰러짐 · 오늘 → 2건" """
    parts = [TOOL_LABEL.get(t.get("name"), t.get("name", "?"))]
    for k, v in (t.get("args") or {}).items():
        if k == "seat":
            parts.append(f"{v}번 좌석")
        elif k in ("kind", "status") or v == "today":
            parts.append(ARG_LABEL.get(v, str(v)))
        elif k == "minutes":
            parts.append(f"최근 {v}분")
        elif k == "event_id":
            parts.append(f"#{v}")
    return " · ".join(parts) + (f" → {t['summary']}" if t.get("summary") else "")


def mode_text(m: dict) -> str:
    mode = MODE_LABEL.get(m.get("mode"), m.get("mode") or "")
    took = m.get("took_s")
    return f"{mode} · {took:.1f}초" if isinstance(took, (int, float)) and m.get("mode") not in ("rule", "error") else mode


class _Bubble(QLabel):
    """질문 말풍선 (오른쪽)"""

    def __init__(self, text: str):
        super().__init__(text, objectName="askQ", wordWrap=len(text) > 24)   # 짧은 질문은 한 줄로
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setMaximumWidth(360)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)


class AnswerCard(QFrame):
    """AI 답 카드: 본문 + 근거 칩 + 모델·시간. 답이 오기 전엔 '확인하는 중'."""

    def __init__(self):
        super().__init__(objectName="askA")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        self.body = QLabel("AI 조교가 확인하는 중…", wordWrap=True, objectName="muted")
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.body)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        lay.addLayout(self.chips)
        self.warn = QLabel(wordWrap=True, objectName="askWarn")
        self.warn.hide()
        lay.addWidget(self.warn)

    def set_answer(self, m: dict):
        self.body.setObjectName("")
        self.body.setStyleSheet("font-size:14px; color:#202124;")
        self.body.setText(m.get("text") or "(빈 답)")
        while self.chips.count():
            it = self.chips.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        tools = m.get("tools") or []
        if tools:
            self.chips.addWidget(QLabel("근거", objectName="muted"))
        for t in tools[:3]:
            chip = QLabel(tool_chip_text(t), objectName="askChip")
            chip.setToolTip(str(t.get("args") or {}))
            self.chips.addWidget(chip)
        self.chips.addStretch(1)
        self.chips.addWidget(QLabel(mode_text(m), objectName="muted"))
        reasons = m.get("fallback") or []
        if reasons and m.get("mode") not in ("gemini", "rule"):
            self.warn.setText("앞 단계를 못 씀: " + " / ".join(reasons)[:200])
            self.warn.show()

    def set_failed(self, text: str):
        self.body.setText(text)
        self.body.setStyleSheet("font-size:14px; color:#b3261e;")


class ReportCard(QFrame):
    """[수업 종료 리포트] 카드: 숫자 한 줄 + [리포트 열기] [PDF 저장]"""
    open_clicked = pyqtSignal(int)
    pdf_clicked = pyqtSignal(int)

    def __init__(self, report_id: int):
        super().__init__(objectName="reportCard")
        self.report_id = report_id
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        top = QHBoxLayout()
        self.title = QLabel("오늘 기록을 모으는 중…", objectName="reportCardTitle")
        self.meta = QLabel(objectName="muted")
        top.addWidget(self.title, 1)
        top.addWidget(self.meta)
        lay.addLayout(top)
        self.line = QLabel(wordWrap=True)
        self.line.setStyleSheet("font-size:13px; color:#2f3a44;")
        lay.addWidget(self.line)
        btns = QHBoxLayout()
        self.b_open = QPushButton("리포트 열기", objectName="purple")
        self.b_pdf = QPushButton("PDF 저장")
        self.b_open.clicked.connect(lambda: self.open_clicked.emit(self.report_id))
        self.b_pdf.clicked.connect(lambda: self.pdf_clicked.emit(self.report_id))
        for b in (self.b_open, self.b_pdf):
            b.setEnabled(False)
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)

    def set_report(self, m: dict):
        d = m.get("data") or {}
        k = d.get("by_kind") or {}
        fa = (d.get("by_result") or {}).get("false_alarm", 0)
        if m.get("status") == "error":
            self.title.setText("리포트를 만들지 못했습니다")
            self.line.setText(m.get("summary") or "")
            return
        self.title.setText("오늘 수업 리포트가 준비되었습니다" if m.get("status") == "done"
                           else "숫자 준비됨 · AI 조교가 요약을 쓰는 중…")
        self.line.setText(f"도움 요청 {d.get('total', 0)}건 (쓰러짐 {k.get('fall', 0)} · 질문 {k.get('question', 0)} · "
                          f"자리비움 {k.get('away', 0)}) · 평균 확인 {d.get('avg_confirm') or '—'} · 오감지 {fa}건")
        self.meta.setText(mode_text(m) if m.get("status") == "done" else "")
        self.b_open.setEnabled(True)
        self.b_pdf.setEnabled(True)

    def set_failed(self, text: str):
        self.title.setText(text)


class AssistantPanel(QWidget):
    open_report = pyqtSignal(int)        # report_id → 좌석도 자리에 리포트
    save_report_pdf = pyqtSignal(int)

    def __init__(self, send: Callable[[str, dict], bool]):
        super().__init__()
        self.send = send
        self.answers: dict[int, AnswerCard] = {}
        self.report_cards: dict[int, ReportCard] = {}

        f = QFrame(objectName="assistantCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(f)
        lay = QVBoxLayout(f)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(QLabel("AI 조교에게 묻기", objectName="cardTitle"))
        head.addStretch(1)
        head.addWidget(QLabel("조회만 · 좌석 번호로 답함", objectName="muted"))
        lay.addLayout(head)

        self.scroll = QScrollArea(widgetResizable=True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setObjectName("askViewport")             # 배경은 theme.STYLE (자식에 번지지 않게)
        inner = QWidget(objectName="askInner")
        self.msgs = QVBoxLayout(inner)
        self.msgs.setContentsMargins(0, 0, 4, 0)
        self.msgs.setSpacing(8)
        self.msgs.addStretch(1)                       # 대화는 아래에서부터 쌓인다
        self.hint = QLabel("예) 오늘 쓰러짐 몇 번 있었어? · 3번 좌석 오늘 기록 · 장비 다 연결돼 있어?",
                           objectName="muted", wordWrap=True)
        self.msgs.addWidget(self.hint)
        self.scroll.setWidget(inner)
        lay.addWidget(self.scroll, 1)

        quick = QHBoxLayout()
        quick.setSpacing(6)
        b = QPushButton("수업 종료 리포트", objectName="purpleChip")
        b.setToolTip("오늘 하루 기록으로 리포트를 만듭니다 (좌석 번호·반 평균만)")
        b.clicked.connect(self.request_report)
        quick.addWidget(b)
        for q in QUICK:
            qb = QPushButton(q, objectName="chipBtn")
            qb.clicked.connect(lambda _=False, t=q: self.ask(t))
            quick.addWidget(qb)
        quick.addStretch(1)
        lay.addLayout(quick)

        row = QHBoxLayout()
        self.input = QLineEdit(placeholderText="궁금한 것을 물어보세요 (예: 3번 좌석 오늘 기록)", objectName="askInput")
        self.input.setMaxLength(500)
        self.input.returnPressed.connect(self._send_input)
        go = QPushButton("보내기", objectName="purple")
        go.clicked.connect(self._send_input)
        row.addWidget(self.input, 1)
        row.addWidget(go)
        lay.addLayout(row)

    # ── 대화 ──
    def _add(self, w: QWidget, right: bool = False):
        self.hint.hide()
        if right:
            box = QHBoxLayout()
            box.addStretch(1)
            box.addWidget(w)
            self.msgs.addLayout(box)
        else:
            self.msgs.addWidget(w)
        QTimer.singleShot(30, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()))

    def _send_input(self):
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.ask(text)

    def ask(self, text: str):
        ask_id = next(_ids)
        self._add(_Bubble(text), right=True)
        card = AnswerCard()
        self.answers[ask_id] = card
        self._add(card)
        if not self.send("ask", {"ask_id": ask_id, "text": text}):
            card.set_failed("Control 에 연결되지 않아 보내지 못했습니다.")
            return
        QTimer.singleShot(ASK_TIMEOUT_MS, lambda: self._timeout(ask_id))

    def _timeout(self, ask_id: int):
        card = self.answers.pop(ask_id, None)
        if card is not None:
            card.set_failed("답이 오지 않았습니다. vision_service 가 켜져 있는지 확인해 주세요.")

    def on_answer(self, m: dict):
        card = self.answers.pop(m.get("ask_id"), None)
        if card is not None:                          # 이 화면이 보낸 질문의 답만
            card.set_answer(m)
            QTimer.singleShot(30, lambda: self.scroll.verticalScrollBar().setValue(
                self.scroll.verticalScrollBar().maximum()))

    # ── 리포트 ──
    def request_report(self):
        rid = next(_ids)
        self._add(_Bubble("[수업 종료 리포트]"), right=True)
        card = ReportCard(rid)
        card.open_clicked.connect(self.open_report)
        card.pdf_clicked.connect(self.save_report_pdf)
        self.report_cards[rid] = card
        self._add(card)
        if not self.send("report_request", {"report_id": rid}):
            card.set_failed("Control 에 연결되지 않아 만들지 못했습니다")
            return
        QTimer.singleShot(REPORT_TIMEOUT_MS, lambda: self._report_timeout(rid))

    def _report_timeout(self, rid: int):
        card = self.report_cards.get(rid)
        if card is not None and not card.b_open.isEnabled():
            card.set_failed("리포트가 오지 않았습니다 (Control 확인)")

    def on_report(self, m: dict):
        card = self.report_cards.get(m.get("report_id"))
        if card is not None:
            card.set_report(m)
