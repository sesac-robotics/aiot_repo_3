"""착석자 모니터링 + control(hub) 전송: 웹캠 판정 결과를 N초마다 condition_summary 로 보내고, 쓰러짐 알림을 받는다

student_client 폴더에서 실행 (control 서버는 먼저 service/jarvis_control_service/run_hub.py 로 켜 둔다):
  python -m monitor.seat_link_main --seat 1                       # control IP·포트는 config/common.yaml
  python -m monitor.seat_link_main --seat 1 --server 127.0.0.1    # 같은 PC에서 시험할 때
monitor.monitor_main의 옵션(--rule, --img-every 등)도 그대로 쓸 수 있다.
판정은 monitor_main.Monitor를 그대로 쓰고, 전송은 seat_link/control_link.py(바이너리 프레임 v3, common/protocol.py)가 맡는다.
  - 보내는 값: {"focus_low": 졸음·하품·전방 주시 미흡 중 하나라도, "posture_bad": 앞으로 숙임} (monitor/condition.py to_summary)
  - 쓰러짐 알림(fall_alert)이 오면 화면에 띄우고, f 또는 Enter 로 확인(fall_confirm)한다
키: c 기준 자세 다시 저장, z 영역 다시 측정, Space 영역 측정 시작, f/Enter 쓰러짐 알림 확인, ESC 종료
"""
import argparse
import logging
import sys
import time

import cv2

from monitor.condition import ConditionLatch, to_summary
from monitor.display import RED, WHITE, YELLOW
from monitor.monitor_args import parse_args
from monitor.monitor_main import Monitor
from seat_common.korean_text import TextRenderer
from seat_link.control_link import VALID_SEATS, StudentLink

ON_KO = {"drowsy": "졸음", "yawn": "하품", "not_forward": "전방 주시 미흡", "forward_lean": "앞으로 숙임"}
STAGE_KO = {"neighbor": "옆자리", "all": "전체"}
CONFIRM_KEYS = (ord("f"), 13, 10)                   # f, Enter


def link_args():
    ap = argparse.ArgumentParser(description="착석자 모니터링 + control 전송", add_help=False)
    ap.add_argument("--server", default=None, help="control IP (기본: config/common.yaml 의 control_host)")
    ap.add_argument("--port", type=int, default=None, help="control 포트 (기본: config/common.yaml 의 control_port)")
    ap.add_argument("--seat", type=int, default=1, choices=VALID_SEATS)
    ap.add_argument("--interval", type=float, default=10.0,
                    help="전송 주기 (초). control 은 30초 넘은 값을 집계에서 빼므로 30 미만으로")
    link, rest = ap.parse_known_args()
    if not 0 < link.interval < 30:
        ap.error("--interval 은 0 초과 30 미만이어야 합니다")
    sys.argv = [sys.argv[0]] + rest                 # 나머지는 monitor_main 옵션으로 넘김
    args = parse_args()
    args.seat = link.seat                           # 전송 좌석 번호 = 기준값 저장 좌석 번호
    return link, args


def link_lines(link, last):
    state = "연결됨" if link.connected else "끊김 (재접속 중)"
    on = ", ".join(ON_KO[k] for k in ON_KO if last.get(k)) if last else "-"
    color = WHITE if link.connected else YELLOW
    lines = [(f"control {link.host}:{link.port} {link.node} {state} | 보냄 {link.sent} | 마지막: {on or '해당 없음'}",
              color)]
    if link.alert_seat is not None:
        stage = STAGE_KO.get(link.alert_stage, link.alert_stage or "")
        lines.append((f"!! {link.alert_seat}번 좌석 쓰러짐 알림 ({stage}) - f 또는 Enter 로 확인", RED))
    return lines


def main():
    link_opt, args = link_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger("client").setLevel(logging.WARNING)   # 2초마다 나오는 재접속 시도 로그는 숨김
    text = TextRenderer(size=18)
    monitor = Monitor(args)
    latch = ConditionLatch()
    link = StudentLink(link_opt.seat, link_opt.server, link_opt.port)
    link.start()
    src = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        link.stop()
        raise SystemExit(f"카메라를 열 수 없습니다: {args.source}")
    monitor.start(time.monotonic())
    fps, t_prev = 0.0, time.monotonic()
    t_send, last = t_prev, {}
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            now = time.monotonic()
            fps = 0.9 * fps + 0.1 / max(now - t_prev, 1e-3)
            t_prev = now
            link.set_fps(fps)
            lines = monitor.step(frame, now)
            latch.update(monitor.state)
            if now - t_send >= link_opt.interval:
                last = latch.snapshot()
                link.send_summary(**to_summary(last))
                t_send = now
            lines.extend(link_lines(link, last))
            lines.append((f"c: 기준 자세 다시 저장  |  z: 영역 다시 측정  |  f/Enter: 알림 확인  |  ESC: 종료  |  {fps:.0f} fps",
                          WHITE))
            text.draw_lines(frame, lines)
            cv2.imshow("seat monitor (link)", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                break
            if key in CONFIRM_KEYS and link.alert_seat is not None:
                link.confirm_fall()
                continue
            monitor.handle_key(key, now)
    finally:
        link.stop()
        cap.release()
        monitor.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
