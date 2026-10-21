"""화면 구성도 v3의 색·라벨 (화면 표시용 이름은 여기 한 곳에만)

값은 hub(event_manager)가 보내는 문자열 그대로 쓴다 (common/protocol.py 기준).
kind: fall | question | away, status: active | waiting | closed …
"""
from __future__ import annotations

import time

from common import protocol as P

KINDS = ("fall", "question", "away")          # 우선순위 순 (P.INCIDENT_PRIORITY)
NO_KIND = "none"                              # 이벤트 없는 평상시 (화면 표시용)

# 색 = 우선순위 (빨강 쓰러짐 > 파랑 질문 > 노랑 자리비움 > 흐린 회색 평상시)
KIND_COLOR = {"fall": "#d93025", "question": "#1a73e8", "away": "#f9ab00", NO_KIND: "#c4c8cc"}
KIND_BG = {"fall": "#fdecea", "question": "#e8f0fe", "away": "#fef7e0", NO_KIND: "#fafbfc"}
KIND_NAME = {"fall": "쓰러짐", "question": "질문", "away": "자리비움", NO_KIND: "일반"}
OFFLINE_ORANGE = "#fbe3cf"

STATUS_NAME = {"active": "대응 중", "waiting": "대기", "closed": "종료"}
CLOSE_NAME = {None: "", "resolved": "완료", "expired": "만료", "cleared": "해제"}
STAGE_NAME = {"none": "알림 전", "neighbor": "옆자리 알림", "all": "반 전체 알림"}
EXPLAIN_NAME = {None: "", "gemini": "Gemini", "ollama": "Ollama", "default": "기본 문구"}

# device_update 의 node 는 노드 이름 문자열. "pantilt" 는 hub 안의 팬틸트 모듈
PANTILT_NODE = "pantilt"
NODE_LABEL = {P.CONTROL_NODE: "Control 서버", P.VISION_NODE: "Vision 서비스", PANTILT_NODE: "팬틸트",
              P.CONSOLE_NODE: "강사 화면"}
NODE_KIND = {P.CONTROL_NODE: "서버", P.VISION_NODE: "서버", PANTILT_NODE: "팬틸트",
             P.CONSOLE_NODE: "강사 화면"}


def node_name(node: str) -> str:
    seat = P.student_seat(node)
    if seat:
        return f"학생 노트북 {seat}"
    return NODE_LABEL.get(node, node)


def node_kind(node: str) -> str:
    return "학생 노트북" if P.student_seat(node) else NODE_KIND.get(node, "")


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
