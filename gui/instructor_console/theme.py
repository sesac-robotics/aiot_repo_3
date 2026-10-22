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
CLOSE_NAME = {None: "", "resolved": "완료", "false_alarm": "오감지", "expired": "만료", "cleared": "해제"}
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
#assistantCard { background: #ffffff; border: 2px solid #8e24aa; border-radius: 10px; }
#askInner, #askViewport { background: #ffffff; }
#askQ { background: #ede7f6; color: #2a1a3a; border-radius: 12px; padding: 7px 12px; font-size: 13px; }
#askA { background: #fafbfc; border: 1px solid #e3e7eb; border-radius: 12px; }
#askChip { color: #4a148c; background: #f3e5f5; border: 1px solid #ce93d8; border-radius: 10px; padding: 1px 8px; font-size: 12px; }
#askWarn { color: #b06000; font-size: 12px; }
#askInput { border: 1.5px solid #b39ddb; border-radius: 8px; padding: 7px 10px; font-size: 14px; }
#reportCard { background: #fbf7fd; border: 1.5px solid #8e24aa; border-radius: 10px; }
#reportCardTitle { color: #4a148c; font-weight: 700; font-size: 14px; }
QPushButton#purple { background: #6a1b9a; color: white; border-color: #6a1b9a; font-weight: 600; }
QPushButton#purple:disabled { background: #f6f7f8; color: #b0b4b8; border-color: #e1e4e8; }
QPushButton#purpleChip { background: #f3e5f5; color: #4a148c; border: 1.5px solid #6a1b9a; border-radius: 13px;
                         padding: 4px 12px; font-weight: 700; }
QPushButton#chipBtn { border-radius: 13px; padding: 4px 12px; color: #37474f; }
QPushButton#dark { background: #37474f; color: white; border-color: #37474f; font-weight: 600; }
#reportFrame { background: #ffffff; border: 2px solid #8e24aa; border-radius: 10px; }
#reportPage { background: #ffffff; }
#repTitle { font-size: 20px; font-weight: 700; }
#repHead { font-size: 14px; font-weight: 700; }
#repText { font-size: 14px; color: #202124; }
#repSmall { font-size: 12px; color: #4a5560; }
#repBig { font-size: 24px; font-weight: 700; }
#repBox { background: #ffffff; border: 1px solid #e3e7eb; border-radius: 8px; }
#repAI { background: #f6f0fa; border: none; border-radius: 8px; }
#repSeat { background: #ffffff; border: 1px solid #e3e7eb; border-radius: 6px; }
#repSeatHead { font-size: 13px; font-weight: 700; }
#repBoard { background: #eef1f4; border-radius: 4px; font-size: 11px; color: #4a5560; padding: 2px; }
#tagAI { color: #4a148c; border: 1px solid #ab47bc; border-radius: 8px; padding: 0 7px; font-size: 11px; font-weight: 700; }
#tagData { color: #37474f; border: 1px solid #90a4ae; border-radius: 8px; padding: 0 7px; font-size: 11px; font-weight: 700; }
QHeaderView::section { background: #f8f9fa; border: none; border-bottom: 1px solid #e1e4e8; padding: 4px; color: #5f6368; }
"""
