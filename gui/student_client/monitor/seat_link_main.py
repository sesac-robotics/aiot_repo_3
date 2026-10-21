"""착석자 모니터링 + 바이너리 TCP 전송 (임시): 웹캠 판정 결과(졸음·하품·전방 주시 미흡·앞으로 숙임)를 N초마다 패킷으로 보낸다

student_client 폴더에서 실행 (먼저 수신기를 켠다: python -m seat_link.test_receiver --port 6000):
  python -m monitor.seat_link_main --server 127.0.0.1 --port 6000 --seat 1 --interval 2
monitor.monitor_main의 옵션(--rule, --img-every 등)도 그대로 쓸 수 있다.
판정은 monitor_main.Monitor를 그대로 쓰고, 패킷 형식은 seat_link/packet.py가 맡는다.
키: c 기준 자세 다시 저장, z 영역 다시 측정, Space 영역 측정 시작, ESC 종료
"""
import argparse
import logging
import sys
import time

import cv2

from monitor.condition import ConditionLatch
from monitor.display import WHITE, YELLOW
from monitor.monitor_args import parse_args
from monitor.monitor_main import Monitor
from seat_common.korean_text import TextRenderer
from seat_link import packet
from seat_link.sender import PacketSender

ON_KO = {"drowsy": "졸음", "yawn": "하품", "not_forward": "전방 주시 미흡", "forward_lean": "앞으로 숙임"}


def link_args():
    ap = argparse.ArgumentParser(description="착석자 모니터링 + 바이너리 TCP 전송", add_help=False)
    ap.add_argument("--server", default="127.0.0.1", help="받는 쪽 IP")
    ap.add_argument("--port", type=int, default=6000)
    ap.add_argument("--seat", type=int, default=1)
    ap.add_argument("--interval", type=float, default=10.0, help="전송 주기 (초)")
    link, rest = ap.parse_known_args()
    sys.argv = [sys.argv[0]] + rest                 # 나머지는 monitor_main 옵션으로 넘김
    args = parse_args()
    args.seat = link.seat                           # 전송 좌석 번호 = 기준값 저장 좌석 번호
    return link, args


def link_line(sender, last, link):
    state = "연결됨" if sender.connected else "끊김 (재접속 중)"
    on = ", ".join(ON_KO[k] for k in ON_KO if last.get(k)) if last else "-"
    color = WHITE if sender.connected else YELLOW
    return (f"전송 {link.server}:{link.port} 좌석 {link.seat} {state} | 보냄 {sender.sent} | 마지막: {on or '해당 없음'}",
            color)


def main():
    link, args = link_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    text = TextRenderer(size=18)
    monitor = Monitor(args)
    latch = ConditionLatch()
    sender = PacketSender(link.server, link.port)
    sender.start()
    src = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
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
            lines = monitor.step(frame, now)
            latch.update(monitor.state)
            if now - t_send >= link.interval:
                last = latch.snapshot()
                sender.send(packet.encode(link.seat, last))
                t_send = now
            lines.append(link_line(sender, last, link))
            lines.append((f"c: 기준 자세 다시 저장  |  z: 영역 다시 측정  |  ESC: 종료  |  {fps:.0f} fps", WHITE))
            text.draw_lines(frame, lines)
            cv2.imshow("seat monitor (link)", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                break
            monitor.handle_key(key, now)
    finally:
        sender.stop()
        cap.release()
        monitor.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()