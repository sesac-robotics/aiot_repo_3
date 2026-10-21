"""Instructor Console 실행

    python gui/instructor_console/run_console.py [--host 127.0.0.1] [--port 5000]

Console은 Control과 같은 강사 노트북에서 돈다. 포트 기본값은 config/common.yaml 의 control_port.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                       # 저장소 루트 (common/protocol.py가 있는 곳)
sys.path[:0] = [str(ROOT), str(HERE.parent)]  # common, instructor_console 패키지

from PyQt6.QtWidgets import QApplication     # noqa: E402

from common.config import load_config        # noqa: E402
from common.protocol import CONSOLE_VIDEO_PORTS  # noqa: E402
from instructor_console.main_window import MainWindow  # noqa: E402
from instructor_console.theme import STYLE   # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Jarvis 강사 화면")
    # Console은 Control과 같은 노트북 → host 기본은 127.0.0.1, 포트는 다른 노드처럼 config/common.yaml
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None, help="Control TCP 포트 (기본: config/common.yaml 의 control_port)")
    ap.add_argument("--cam1-port", type=int, default=CONSOLE_VIDEO_PORTS[1],
                    help="전체 화면 실시간 영상 UDP 포트 (Control 이 camera_streamer 영상을 넘겨 줌)")
    ap.add_argument("--cam2-port", type=int, default=CONSOLE_VIDEO_PORTS[2],
                    help="이벤트 창 실시간 영상 UDP 포트")
    ap.add_argument("--clip-root", default=str(ROOT / "service" / "jarvis_DB"),
                    help="event_clip.path(clips/{event_id}.mp4)의 기준 폴더")
    args = ap.parse_args()
    port = args.port or int(load_config()["control_port"])

    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    w = MainWindow(args.host, port, args.cam1_port, args.cam2_port, args.clip_root)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
