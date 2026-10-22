"""[세부컨디션] 반 평균 세부 6개 그리기 (10/22 시안: 홈 ③ 반 통계 [세부 보기], ⑥ 리포트 세부 항목)

- 묶음 집중 저하 = 졸음 · 하품 · 전방 주시 미흡, 자세 불량 = 앞으로 숙임 · 고개 기울임 · 어깨 비대칭
- 전부 반 평균 (0~1). 좌석·이름 없음. null(None) = 그 항목을 보낸 학생이 적어 값 없음 → "—"
- 비율 = 최근 10초 안에 한 번이라도 감지된 학생 비율
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from common import protocol as P

FOCUS_COLOR, POSTURE_COLOR = "#f9a825", "#bf360c"     # report_view 와 같은 색
GROUP_COLOR = {"focus": FOCUS_COLOR, "posture": POSTURE_COLOR}
GROUP_NAME = {"focus": "집중 저하", "posture": "자세 불량"}
GROUP_KEY = {"focus": "focus_low_ratio", "posture": "posture_bad_ratio"}
SMALL_CLASS = 3            # 보고 학생이 이보다 적으면 "반 평균 = 그 학생 값" 안내 (10/22 결정)
TRACK = "#eceff1"


def pct(x) -> str:
    return "—" if x is None else f"{round(float(x) * 100)}%"


class Bar(QWidget):
    """가로 막대 하나 (0~1). None 이면 빈 막대."""

    def __init__(self, color: str, height: int = 8):
        super().__init__()
        self.color, self.value = color, None
        self.setFixedHeight(height)
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, v):
        self.value = v
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        r = self.height() / 2
        p.setBrush(QColor(TRACK))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), r, r)
        if self.value:
            w = max(self.height(), self.width() * min(1.0, float(self.value)))
            p.setBrush(QColor(self.color))
            p.drawRoundedRect(QRectF(0, 0, w, self.height()), r, r)


class Meter(QWidget):
    """[이름] [막대] [값%] 한 줄. big=True 면 묶음(굵게)."""

    def __init__(self, name: str, color: str, big: bool = False, name_w: int = 64):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        size = 14 if big else 12
        self.name = QLabel(name)
        self.name.setFixedWidth(name_w if big else 92)
        self.name.setStyleSheet(f"font-size:{size}px;" + ("font-weight:700;" if big else "color:#37474f;"))
        self.bar = Bar(color, 14 if big else 8)
        self.val = QLabel("—", alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.val.setFixedWidth(40)
        self.val.setStyleSheet(f"font-size:{size}px;" + ("font-weight:700;" if big else ""))
        lay.addWidget(self.name)
        lay.addWidget(self.bar, 1)
        lay.addWidget(self.val)

    def set_value(self, v):
        self.bar.set_value(v)
        self.val.setText(pct(v))
        self.setAccessibleName(f"{self.name.text()} {pct(v)}")


class Trend(QWidget):
    """최근 30분 5분 평균 막대 (한 항목). None 칸은 비움."""

    def __init__(self, color: str = FOCUS_COLOR):
        super().__init__()
        self.color, self.vals = color, []
        self.setFixedHeight(54)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_values(self, vals: list, color: str):
        self.vals, self.color = vals, color
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.setPen(QColor("#cfd6dd"))
        p.drawLine(0, h - 1, w, h - 1)
        n = len(self.vals) or 1
        gap = 6
        bw = (w - gap * (n - 1)) / n
        for i, v in enumerate(self.vals):
            if v is None:
                continue
            bh = max(1.0, (h - 4) * min(1.0, float(v)))
            p.fillRect(QRectF(i * (bw + gap), h - 1 - bh, bw, bh), QColor(self.color))


def trend_bins(history, key: str, now: float | None = None, minutes: int = 30, bins: int = 6) -> list:
    """history = [(ts, class_condition)] → 5분 평균 bins 개 (값 없으면 None)."""
    now = now or time.time()
    step = minutes * 60 / bins
    out = []
    for i in range(bins):
        t0 = now - minutes * 60 + i * step
        xs = [(c.get("detail") or {}).get(key) for ts, c in history if t0 <= ts < t0 + step]
        xs = [x for x in xs if x is not None]
        out.append(sum(xs) / len(xs) if xs else None)
    return out


def top_item(detail: dict, group: str):
    """묶음 안에서 가장 높은 세부 키 (값이 없거나 전부 0 이면 None)."""
    vals = [(detail.get(k), k) for k in P.CONDITION_DETAIL_GROUP[group] if detail.get(k) is not None]
    if not vals or max(vals)[0] <= 0:
        return None
    return max(vals)[1]


class ConditionDetail(QWidget):
    """홈 ③ [세부 보기] 를 눌렀을 때 펼쳐지는 부분: 묶음별 세부 3개 + 최근 30분 추이."""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.warn = QLabel(objectName="banner", wordWrap=True)       # 보고 학생 1~2명
        self.warn.hide()
        lay.addWidget(self.warn)

        cols = QHBoxLayout()
        cols.setSpacing(18)
        self.meters: dict[str, Meter] = {}
        for group in ("focus", "posture"):
            col = QVBoxLayout()
            col.setSpacing(6)
            head = QLabel(GROUP_NAME[group])
            head.setStyleSheet(f"font-size:12px; font-weight:700; color:{'#8a5a00' if group == 'focus' else '#8f2a0a'};")
            col.addWidget(head)
            for k in P.CONDITION_DETAIL_GROUP[group]:
                m = Meter(P.CONDITION_DETAIL_KO[k], GROUP_COLOR[group])
                self.meters[k] = m
                col.addWidget(m)
            cols.addLayout(col, 1)
        lay.addLayout(cols)
        self.none = QLabel("세부 항목 기록이 없습니다 (학생 프로그램이 묶음 2개만 보내는 중)", objectName="muted",
                           wordWrap=True)
        self.none.hide()
        lay.addWidget(self.none)

        self.trend_title = QLabel(objectName="muted")
        lay.addWidget(self.trend_title)
        self.trend = Trend()
        lay.addWidget(self.trend)
        self.axis = QWidget()
        axis = QHBoxLayout(self.axis)
        axis.setContentsMargins(0, 0, 0, 0)
        self.t_from, self.t_mid, self.t_to = QLabel(objectName="muted"), QLabel(objectName="muted"), QLabel("지금", objectName="muted")
        axis.addWidget(self.t_from)
        axis.addStretch(1)
        axis.addWidget(self.t_mid)
        axis.addStretch(1)
        axis.addWidget(self.t_to)
        lay.addWidget(self.axis)
        lay.addWidget(QLabel("세부 항목도 반 평균 · 좌석·이름 없음 · 비율 = 최근 10초 안에 한 번이라도 감지된 학생 비율",
                             objectName="muted", wordWrap=True))

    def refresh(self, c: dict | None, history, now: float | None = None):
        c = c or {}
        detail = c.get("detail") or {}
        n = c.get("reporting", c.get("n", 0)) or 0
        if 0 < n < SMALL_CLASS:
            self.warn.setText(f"지금 보고하는 학생이 <b>{n}명</b>이라 반 평균이 곧 그 학생"
                              f"{'의' if n == 1 else '들의'} 값에 가깝습니다. 학생이 {SMALL_CLASS}명 이상 모이면 "
                              "이 안내가 사라집니다.")
            self.warn.show()
        else:
            self.warn.hide()
        has = any(v is not None for v in detail.values())
        self.none.setVisible(not has)
        for k, m in self.meters.items():
            m.set_value(detail.get(k))
            m.name.setStyleSheet(m.name.styleSheet().replace("font-weight:700;", ""))
        for group in ("focus", "posture"):                 # 묶음 1위는 굵게
            k = top_item(detail, group)
            if k:
                self.meters[k].name.setStyleSheet(self.meters[k].name.styleSheet() + "font-weight:700;")

        now = now or time.time()
        hist = list(history)
        # 추이 = 최근 30분 동안 가장 높았던 묶음 1위 항목 (집중 저하 먼저)
        avg30 = {}
        for k in P.CONDITION_DETAIL_KEYS:
            xs = [(h.get("detail") or {}).get(k) for ts, h in hist if ts >= now - 1800]
            xs = [x for x in xs if x is not None]
            avg30[k] = sum(xs) / len(xs) if xs else None
        key = top_item(avg30, "focus") or top_item(avg30, "posture")
        if key is None:
            self.trend_title.setText("최근 30분 추이 — 아직 기록이 적습니다")
            self.trend.set_values([], FOCUS_COLOR)
        self.trend.setVisible(key is not None)
        self.axis.setVisible(key is not None)
        if key is not None:
            group = "focus" if key in P.CONDITION_DETAIL_GROUP["focus"] else "posture"
            self.trend_title.setText(f"최근 30분 추이 (5분 평균) · {P.CONDITION_DETAIL_KO[key]} "
                                     f"({GROUP_NAME[group]} 중 가장 높은 항목)")
            vals = trend_bins(hist, key, now)
            self.trend.set_values(vals, GROUP_COLOR[group])
            self.trend.setAccessibleName("최근 30분 " + P.CONDITION_DETAIL_KO[key] + " " +
                                         ", ".join(pct(v) for v in vals))
        self.t_from.setText(time.strftime("%H:%M", time.localtime(now - 1800)))
        self.t_mid.setText(time.strftime("%H:%M", time.localtime(now - 900)))


class ReportDetail(QWidget):
    """⑥ 리포트 '세부 항목' 표: 항목 · 오늘 평균 막대 · 가장 높았던 때. data['condition_detail'] 그대로."""

    def __init__(self):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(5)
        self.rows: dict[str, tuple] = {}
        r = 0
        for group in ("focus", "posture"):
            head = QLabel(GROUP_NAME[group])
            head.setStyleSheet(f"font-size:12px; font-weight:700; color:{'#8a5a00' if group == 'focus' else '#8f2a0a'};")
            self.grid.addWidget(head, r, 0, 1, 4)
            r += 1
            for k in P.CONDITION_DETAIL_GROUP[group]:
                name, bar = QLabel(P.CONDITION_DETAIL_KO[k]), Bar(GROUP_COLOR[group])
                val, peak = QLabel("—"), QLabel(objectName="muted")
                name.setFixedWidth(92)
                val.setFixedWidth(36)
                val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                peak.setFixedWidth(70)
                for c, w in enumerate((name, bar, val, peak)):
                    self.grid.addWidget(w, r, c)
                self.rows[k] = (name, bar, val, peak)
                r += 1
        self.grid.setColumnStretch(1, 1)
        self.empty = QLabel("오늘은 세부 항목 기록이 없습니다", objectName="muted")
        self.grid.addWidget(self.empty, r, 0, 1, 4)

    def set_data(self, det: dict | None):
        det = det or {}
        self.empty.setVisible(not det)
        avg = {k: (det.get(k) or {}).get("avg") for k in P.CONDITION_DETAIL_KEYS}
        tops = {top_item(avg, "focus"), top_item(avg, "posture")}
        for k, (name, bar, val, peak) in self.rows.items():
            d = det.get(k) or {}
            bar.set_value(d.get("avg"))
            val.setText(pct(d.get("avg")))
            peak.setText(f"{d['peak_hour']}시 {pct(d.get('peak'))}" if d.get("peak_hour") is not None else "")
            bold = "font-weight:700;" if k in tops else ""
            name.setStyleSheet(bold)
            val.setStyleSheet(bold)
