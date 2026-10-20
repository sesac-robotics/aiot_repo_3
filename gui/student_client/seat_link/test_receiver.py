"""시험용 수신 서버: 학생 노드가 보낸 바이너리 상태 패킷을 받아 해석해 터미널에 출력 (임시, 받는 쪽이 정해지면 교체)

student_client 폴더에서 실행:
  python -m seat_link.test_receiver --port 6000
여러 학생 노드가 동시에 접속해도 된다 (접속마다 스레드 하나).
"""
import argparse
import socketserver
import time

from seat_link.packet import FIELDS, PacketReader

SHORT = {"drowsy": "졸음", "yawn": "하품", "not_forward": "전방주시미흡", "forward_lean": "앞으로숙임"}


def describe(p: dict) -> str:
    on = [SHORT[k] for k in FIELDS if p["flags"][k]]
    bits = " ".join(f"{k}={int(p['flags'][k])}" for k in FIELDS)
    sent = time.strftime("%H:%M:%S", time.localtime(p["ts"]))
    return f"좌석 {p['seat']} | {', '.join(on) or '해당 없음'} | {bits} | 보낸 시각 {sent}"


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        print(f"[접속] {peer}", flush=True)
        reader = PacketReader()
        while True:
            chunk = self.request.recv(1024)
            if not chunk:
                break
            for p in reader.feed(chunk):
                print(f"[{time.strftime('%H:%M:%S')}] {peer} {describe(p)}", flush=True)
            while reader.errors:
                print(f"[오류] {peer} {reader.errors.pop(0)}", flush=True)
        print(f"[끊김] {peer}", flush=True)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser(description="시험용 학생 상태 패킷 수신기")
    ap.add_argument("--host", default="0.0.0.0", help="받을 주소 (같은 PC만이면 127.0.0.1)")
    ap.add_argument("--port", type=int, default=6000)
    args = ap.parse_args()
    with Server((args.host, args.port), Handler) as srv:
        print(f"수신 대기: {args.host}:{args.port}  (Ctrl+C 종료)", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()