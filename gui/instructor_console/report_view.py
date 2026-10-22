"""강사 화면 ⑥ 수업 종료 리포트 (좌석도 자리에 열림, 10/22 시안)

Control 이 보내는 report {report_id, status, data, summary, suggestions, mode, took_s, fallback} 를 그린다.
- 숫자(타일·좌석별·반 평균 막대·세부 항목·장비)는 data 그대로 = control 이 DB 에서 집계 ("기록에서 집계")
- 문장(오늘 요약·살펴볼 점)만 LLM ("AI 작성"). status=writing 이면 "쓰는 중"
- 좌석 번호만, 반 평균만 (학생 이름·개인 값 없음)
- [PDF 저장] [인쇄]: 아래 page 위젯을 그대로 그린다 (화면 = 종이)
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QMarginsF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPageLayout, QPageSize, QPainter, QPdfWriter
from PyQt6.QtWidgets import (QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                             QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from . import theme as T
from .condition_view import ReportDetail          # [세부컨디션]

FOCUS_COLOR, POSTURE_COLOR = "#f9a825", "#bf360c"   # 밝기가 달라 색약에도 구분됨
AI_TAG, DATA_TAG = "AI 작성", "기록에서 집계"


def _tag(text: str, ai: bool) -> QLabel:
    return QLabel(text, objectName="tagAI" if ai else "tagData")


def _box(obj: str = "repBox") -> tuple[QFrame, QVBoxLayout]:
    f = QFrame(objectName=obj)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(6)
    return f, lay


class BarChart(QWidget):
    """시간대별 반 평균 (집중 저하 · 자세 불량) 막대. 개인 값 없음."""

    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_rows(self, rows: list[dict]):
        self.rows = rows or []
        self.setAccessibleName("시간대별 반 평균: " + ", ".join(
            f"{r['hour']}시 집중 저하 {round((r.get('focus_low') or 0) * 100)}% 자세 불량 "
            f"{round((r.get('posture_bad') or 0) * 100)}%" for r in self.rows))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        base = h - 22
        p.setPen(QColor("#cfd6dd"))
        p.drawLine(0, base, w, base)
        if not self.rows:
            p.setPen(QColor("#5f6b76"))
            p.drawText(QRectF(0, 0, w, base), Qt.AlignmentFlag.AlignCenter, "오늘은 반 집계가 없습니다")
            return
        n = len(self.rows)
        slot = w / n
        bw = min(18.0, slot / 3)
        top = 8
        for i, r in enumerate(self.rows):
            cx = slot * i + slot / 2
            for j, (val, color) in enumerate(((r.get("focus_low") or 0, FOCUS_COLOR),
                                             (r.get("posture_bad") or 0, POSTURE_COLOR))):
                bh = max(1.0, (base - top) * min(1.0, float(val)))
                x = cx - bw - 1.5 if j == 0 else cx + 1.5
                p.fillRect(QRectF(x, base - bh, bw, bh), QColor(color))
            p.setPen(QColor("#4a5560"))
            p.drawText(QRectF(slot * i, base + 3, slot, 18), Qt.AlignmentFlag.AlignCenter, f"{r['hour']}시")


class ReportPanel(QWidget):
    closed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.report: dict | None = None
        f = QFrame(objectName="reportFrame")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(f)
        lay = QVBoxLayout(f)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(16, 10, 16, 10)
        bar.addWidget(QLabel("수업 종료 리포트", objectName="cardTitle"))
        bar.addStretch(1)
        self.b_pdf = QPushButton("PDF 저장")
        self.b_print = QPushButton("인쇄")
        close = QPushButton("닫기 → 좌석도", objectName="dark")
        self.b_pdf.clicked.connect(lambda: self.save_pdf())
        self.b_print.clicked.connect(self.print_report)
        close.clicked.connect(self.closed)
        for b in (self.b_pdf, self.b_print, close):
            bar.addWidget(b)
        lay.addLayout(bar)

        scroll = QScrollArea(widgetResizable=True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.page = QWidget(objectName="reportPage")       # 이 위젯이 그대로 PDF·인쇄가 된다
        scroll.setWidget(self.page)
        lay.addWidget(scroll, 1)
        p = QVBoxLayout(self.page)
        p.setContentsMargins(20, 14, 20, 16)
        p.setSpacing(12)

        self.title = QLabel(objectName="repTitle")
        self.sub = QLabel(objectName="muted")
        p.addWidget(self.title)
        p.addWidget(self.sub)

        # 오늘 요약 (AI)
        box, bl = _box("repAI")
        head = QHBoxLayout()
        head.addWidget(QLabel("오늘 요약", objectName="repHead"))
        self.sum_tag = _tag(AI_TAG, True)
        head.addWidget(self.sum_tag)
        head.addStretch(1)
        self.sum_meta = QLabel(objectName="muted")
        head.addWidget(self.sum_meta)
        bl.addLayout(head)
        self.summary = QLabel(wordWrap=True, objectName="repText")
        bl.addWidget(self.summary)
        p.addWidget(box)

        # 숫자로 보기 (기록)
        head = QHBoxLayout()
        head.addWidget(QLabel("숫자로 보기", objectName="repHead"))
        head.addWidget(_tag(DATA_TAG, False))
        head.addStretch(1)
        p.addLayout(head)
        tiles = QGridLayout()
        tiles.setSpacing(10)
        self.tiles = []
        for i in range(4):
            t, tl = _box()
            cap, val, sub = QLabel(objectName="muted"), QLabel(objectName="repBig"), QLabel(wordWrap=True, objectName="repSmall")
            for w in (cap, val, sub):
                tl.addWidget(w)
            tiles.addWidget(t, 0, i)
            self.tiles.append((cap, val, sub))
        p.addLayout(tiles)

        # 좌석별 · 반 평균 흐름
        row = QHBoxLayout()
        row.setSpacing(12)
        seat_box, sl = _box()
        sh = QHBoxLayout()
        sh.addWidget(QLabel("좌석별 도움 요청", objectName="repHead"))
        sh.addWidget(QLabel("좌석 번호로만 표기", objectName="muted"))
        sh.addStretch(1)
        sl.addLayout(sh)
        board = QLabel("칠판 · 강사 자리", alignment=Qt.AlignmentFlag.AlignCenter, objectName="repBoard")
        sl.addWidget(board)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.seat_cells = {}
        for s in (1, 2, 3, 4):
            c, cl = _box("repSeat")
            head_l, body_l = QLabel(objectName="repSeatHead"), QLabel(wordWrap=True, objectName="repSmall")
            cl.addWidget(head_l)
            cl.addWidget(body_l)
            grid.addWidget(c, (s - 1) // 2, (s - 1) % 2)
            self.seat_cells[s] = (head_l, body_l)
        sl.addLayout(grid)
        row.addWidget(seat_box, 1)

        chart_box, cbl = _box()
        ch = QHBoxLayout()
        ch.addWidget(QLabel("반 평균 흐름", objectName="repHead"))
        ch.addWidget(QLabel("개인 값 없음", objectName="muted"))
        ch.addStretch(1)
        cbl.addLayout(ch)
        legend = QLabel(f"<span style='color:{FOCUS_COLOR}'>■</span> 집중 저하 &nbsp; "
                        f"<span style='color:{POSTURE_COLOR}'>■</span> 자세 불량", objectName="repSmall")
        cbl.addWidget(legend)
        self.chart = BarChart()
        cbl.addWidget(self.chart, 1)
        row.addWidget(chart_box, 1)
        p.addLayout(row)

        # [세부컨디션] 세부 항목 (오늘 평균 · 가장 높았던 때)
        det_box, dbl = _box()
        dh = QHBoxLayout()
        dh.addWidget(QLabel("세부 항목", objectName="repHead"))
        dh.addWidget(_tag(DATA_TAG, False))
        dh.addStretch(1)
        dh.addWidget(QLabel("오늘 평균 · 가장 높았던 때 · 굵게 = 묶음 안에서 가장 높은 항목", objectName="muted"))
        dbl.addLayout(dh)
        self.detail = ReportDetail()
        dbl.addWidget(self.detail)
        p.addWidget(det_box)

        # 장비
        dev, dl = _box()
        self.devices = QLabel(wordWrap=True, objectName="repText")
        dl.addWidget(self.devices)
        p.addWidget(dev)

        # 다음 수업에 살펴볼 점 (AI)
        box, bl = _box("repAI")
        head = QHBoxLayout()
        head.addWidget(QLabel("다음 수업에 살펴볼 점", objectName="repHead"))
        self.sug_tag = _tag(AI_TAG, True)
        head.addWidget(self.sug_tag)
        head.addStretch(1)
        bl.addLayout(head)
        self.suggestions = QLabel(wordWrap=True, objectName="repText", textFormat=Qt.TextFormat.RichText)
        bl.addWidget(self.suggestions)
        p.addWidget(box)

        self.footer = QLabel("좌석 번호로만 표기 · 개인 집중·자세 값은 포함하지 않음 · 숫자는 기록에서 직접 집계하고, "
                             "AI 는 요약과 살펴볼 점만 작성", wordWrap=True, objectName="muted")
        p.addWidget(self.footer)
        p.addStretch(1)
        self.show_report({"status": "writing", "data": {}})

    # ── 그리기 ──
    def show_report(self, m: dict):
        self.report = m
        d = m.get("data") or {}
        k = d.get("by_kind") or {}
        res = d.get("by_result") or {}
        done = m.get("status") in ("done", "error")
        self.title.setText("수업 종료 리포트")
        self.sub.setText(f"{d.get('date', '—')} (오늘 하루) · {d.get('generated_at', '—')} 생성 · "
                         "강사가 [수업 종료 리포트]를 눌러 만듦")

        ai = m.get("mode") in ("gemini", "ollama")
        tag = AI_TAG if ai or not done else ("규칙으로 작성" if m.get("mode") == "default" else "AI 없음")
        for t in (self.sum_tag, self.sug_tag):
            t.setText(tag)
            t.setObjectName("tagAI" if ai or not done else "tagData")
            t.style().unpolish(t); t.style().polish(t)
        if not done:
            self.summary.setText("AI 조교가 오늘 기록을 읽고 요약을 쓰는 중입니다…")
            self.sum_meta.setText("")
        else:
            self.summary.setText(m.get("summary") or "요약이 없습니다.")
            meta = {"gemini": "Gemini", "ollama": "Ollama"}.get(m.get("mode"), "")
            if meta and isinstance(m.get("took_s"), (int, float)):
                meta += f" · {m['took_s']:.1f}초"
            if m.get("fallback") and not ai:
                meta = "AI 를 쓰지 못함: " + " / ".join(m["fallback"])[:80]
            self.sum_meta.setText(meta)

        case = d.get("max_confirm_case") or {}
        fa = d.get("false_alarms") or []
        tiles = [
            ("도움 요청", f"{d.get('total', 0)}건",
             f"쓰러짐 {k.get('fall', 0)} · 질문 {k.get('question', 0)} · 자리비움 {k.get('away', 0)}"),
            ("평균 확인 시간", d.get("avg_confirm") or "—",
             f"가장 길었던 때 {d['max_confirm']} ({case.get('seat')}번 {T.KIND_NAME.get(case.get('kind'), '')} "
             f"{case.get('at', '')})" if d.get("max_confirm") else "확인한 이벤트 없음"),
            ("평균 처리 시간", d.get("avg_resolve") or "—", "감지 → 완료"),
            ("오감지 · 만료", f"{res.get('false_alarm', 0)} · {res.get('expired', 0)}",
             ("오감지: " + ", ".join(f"{x['seat']}번 {T.KIND_NAME.get(x['kind'], '')} {x['at']}" for x in fa[:2]))
             if fa else "오감지 없음"),
        ]
        for (cap, val, sub), (c, v, s) in zip(self.tiles, tiles):
            cap.setText(c); val.setText(v); sub.setText(s)

        seats = d.get("by_seat") or {}
        for s, (head, body) in self.seat_cells.items():
            v = seats.get(str(s)) or {}
            head.setText(f"{s}번 · {v.get('total', 0)}건")
            parts = []
            for kind in T.KINDS:
                if v.get(kind):
                    parts.append(f"<span style='color:{T.KIND_COLOR[kind]}'>{T.KIND_NAME[kind]} {v[kind]}</span>")
            if v.get("false_alarm"):
                parts.append(f"<span style='color:#5f6b76'>(오감지 {v['false_alarm']})</span>")
            body.setText(" · ".join(parts) or "<span style='color:#5f6b76'>없음</span>")
        self.chart.set_rows(d.get("hourly_condition") or [])
        self.detail.set_data(d.get("condition_detail"))

        outs = d.get("device_outages") or []
        if outs:
            self.devices.setText("<b>장비</b> &nbsp; 끊김 " + f"{len(outs)}번 — " + "; ".join(
                f"{o['name']} ({o['from']}~{o['to']}, {o['duration']})" + (f" · {o['note']}" if o.get("note") else "")
                for o in outs))
        else:
            self.devices.setText("<b>장비</b> &nbsp; 오늘 끊긴 장비 없음")

        sugs = m.get("suggestions") or []
        if not done:
            self.suggestions.setText("<span style='color:#5f6b76'>쓰는 중…</span>")
        elif sugs:
            self.suggestions.setText("".join(f"• {s}<br>" for s in sugs))
        else:
            self.suggestions.setText("<span style='color:#5f6b76'>특별히 살펴볼 점이 없습니다.</span>")
        busy = not done
        self.b_pdf.setEnabled(not busy)
        self.b_print.setEnabled(not busy)

    # ── PDF · 인쇄 ──
    def _paint(self, painter: QPainter, page_rect: QRectF):
        """page 위젯을 종이 폭에 맞춰 그린다 (한 장에 들어가게 필요하면 더 줄임)."""
        w, h = self.page.width(), self.page.sizeHint().height()
        h = max(h, self.page.height())
        scale = min(page_rect.width() / w, page_rect.height() / h)
        painter.translate(page_rect.left(), page_rect.top())
        painter.scale(scale, scale)
        self.page.render(painter)

    def default_pdf_name(self) -> str:
        d = (self.report or {}).get("data") or {}
        return f"수업리포트_{d.get('date', time.strftime('%Y-%m-%d'))}.pdf"

    def save_pdf(self, path: str | None = None) -> str | None:
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "리포트 PDF 저장", self.default_pdf_name(), "PDF (*.pdf)")
            if not path:
                return None
        writer = QPdfWriter(path)
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setPageMargins(QMarginsF(12, 12, 12, 12), QPageLayout.Unit.Millimeter)
        writer.setResolution(110)
        painter = QPainter(writer)
        self._paint(painter, QRectF(painter.viewport()))
        painter.end()
        return path

    def print_report(self):
        try:
            from PyQt6.QtPrintSupport import QPrintDialog, QPrinter
        except ImportError:
            QMessageBox.information(self, "인쇄", "이 PC 에서는 인쇄 모듈을 쓸 수 없습니다. PDF 로 저장해 주세요.")
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        if QPrintDialog(printer, self).exec():
            painter = QPainter(printer)
            self._paint(painter, QRectF(painter.viewport()))
            painter.end()
