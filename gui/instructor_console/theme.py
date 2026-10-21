"""화면 구성도 v3의 색·라벨 (화면 표시용 이름은 여기 한 곳에만)"""
from __future__ import annotations

import time

from common import protocol as P

# 색 = 우선순위 (빨강 쓰러짐 > 파랑 질문 > 노랑 자리비움 > 흐린 회색 평상시)
KIND_COLOR = {P.KIND_FALL: "#d93025", P.KIND_QUESTION: "#1a73e8", P.KIND_AWAY: "#f9ab00", P.KIND_NONE: "#c4c8cc"}
KIND_BG = {P.KIND_FALL: "#fdecea", P.KIND_QUESTION: "#e8f0fe", P.KIND_AWAY: "#fef7e0", P.KIND_NONE: "#fafbfc"}
KIND_NAME = {P.KIND_FALL: "쓰러짐", P.KIND_QUESTION: "질문", P.KIND_AWAY: "자리비움", P.KIND_NONE: "일반"}
OFFLINE_ORANGE = "#fbe3cf"

STATUS_NAME = {P.ST_ACTIVE: "대응 중", P.ST_WAITING: "대기", P.ST_CLOSED: "종료"}
CLOSE_NAME = {P.CLOSE_NONE: "", P.CLOSE_RESOLVED: "완료", P.CLOSE_FALSE_ALARM: "오감지",
              P.CLOSE_EXPIRED: "만료", P.CLOSE_CLEARED: "해제"}
STAGE_NAME = {P.STAGE_NONE: "알림 전", P.STAGE_NEIGHBOR: "옆자리 알림", P.STAGE_ALL: "반 전체 알림",
              P.STAGE_LONG: "장시간 자리비움"}
EXPLAIN_NAME = {P.EXPLAIN_NONE: "", P.EXPLAIN_GEMINI: "Gemini", P.EXPLAIN_OLLAMA: "Ollama",
                P.EXPLAIN_DEFAULT: "기본 문구"}
DEVICE_KIND_NAME = {P.DEV_SERVER: "서버", P.DEV_CAMERA: "카메라", P.DEV_PANTILT: "팬틸트",
                    P.DEV_STUDENT: "학생 노트북", P.DEV_CONSOLE: "강사 화면"}


def node_name(node: int) -> str:
    """device_update에는 이름이 없어서 노드 번호로 표시 이름을 만든다 (Data Structure v2 device.node)"""
    fixed = {P.NODE_CONTROL: "Control 서버", P.NODE_VISION: "Vision 서비스", P.NODE_PANTILT: "팬틸트",
             P.NODE_CONSOLE: "강사 화면", P.NODE_CAM1: "고정 카메라 (cam1)", P.NODE_CAM2: "팬틸트 카메라 (cam2)"}
    if node in fixed:
        return fixed[node]
    if node in P.NODE_STUDENTS:
        return f"학생 노트북 {node - 10}"
    return f"노드 {node}"


def hms(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "—"


def mdhm(ts: float) -> str:
    if not ts:
        return "—"
    lt = time.localtime(ts)
    today = time.localtime()
    if lt.tm_yday == today.tm_yday and lt.tm_year == today.tm_year:
        return time.strftime("%H:%M:%S", lt)
    return time.strftime("%m/%d %H:%M", lt)


def elapsed(since: float, until: float | None = None) -> str:
    if not since:
        return "—"
    s = int(max(0, (until or time.time()) - since))
    return f"{s}초" if s < 60 else f"{s // 60}분 {s % 60:02d}초"


STYLE = """
QWidget { font-size: 13px; color: #202124; }
QMainWindow, #page { background: #f4f5f7; }
#card { background: #ffffff; border: 1px solid #e1e4e8; border-radius: 10px; }
#cardTitle { font-size: 14px; font-weight: 600; color: #3c4043; }
#muted { color: #80868b; }
#sideBar { background: #263238; }
#sideBar QToolButton { color: #cfd8dc; background: transparent; border: none; border-radius: 8px;
                      padding: 10px 6px; font-size: 13px; }
#sideBar QToolButton:checked { background: #37474f; color: #ffffff; font-weight: 600; }
#sideBar QToolButton:hover { background: #31424a; }
#banner { background: #fff4e5; border: 1px solid #f9ab00; border-radius: 8px; padding: 6px 10px; }
#offlineBanner { background: #fdecea; border: 1px solid #d93025; border-radius: 8px; padding: 6px 10px; color: #b3261e; }
QPushButton { padding: 6px 12px; border-radius: 6px; border: 1px solid #c4c8cc; background: #fff; }
QPushButton:hover { background: #f1f3f4; }
QPushButton:disabled { color: #b0b4b8; background: #f6f7f8; }
QPushButton#primary { background: #1a73e8; color: white; border-color: #1a73e8; }
QPushButton#danger  { background: #d93025; color: white; border-color: #d93025; }
QPushButton#primary:disabled, QPushButton#danger:disabled { background: #f6f7f8; color: #b0b4b8; border-color: #e1e4e8; }
QTableWidget { background: #fff; gridline-color: #eceff1; border: none; selection-background-color: #e8f0fe;
               selection-color: #202124; }
QHeaderView::section { background: #f8f9fa; border: none; border-bottom: 1px solid #e1e4e8; padding: 4px; color: #5f6368; }
"""
