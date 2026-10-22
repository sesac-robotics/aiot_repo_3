"""
이벤트 녹화 (Data Structure v2: event_clip). 팬틸트 카메라(cam2) 영상만, 이벤트 확정 ~ 종료 구간을 MP4 로 저장한다.

- 언제 녹화를 시작·종료할지는 event_manager 가 정한다 (to="recorder" 의 start / stop 할 일).
  hub 가 DB 에 event_clip 행을 만들고(db.py) 이 모듈에 파일 기록만 맡긴다. 여기서는 DB 를 모른다.
- 영상은 hub/video_relay.py 가 받은 cam2 데이터그램의 JPEG 를 feed() 로 넘겨 받는다 (중계와 같은 영상).
- JPEG 디코딩·MP4 인코딩·파일 쓰기는 작업 스레드 하나에서 한다. asyncio 루프(0.1초 tick)는 큐에 넣기만 한다.
- 고정 fps(control.yaml clips.fps, 기본 10)로 쓴다. 들어오는 영상(15fps)은 시각 기준으로 솎아 내고,
  잠깐 끊기면 마지막 장면을 반복해 재생 시간이 실제 시간과 맞게 한다 (MAX_FILL_S 를 넘는 끊김은 채우지 않고 이어 붙인다).
- 열린 이벤트가 여러 개면 같은 cam2 영상을 이벤트마다 따로 저장한다 (이벤트 창에서 이벤트별로 다시 보기).
- 끝나면 결과(ClipResult)를 done 큐에 넣고 notify() 를 부른다. hub 가 루프 스레드에서 drain() 으로 꺼내 DB 를 닫는다.
  프레임을 한 장도 못 쓴 녹화(카메라 꺼짐 등)는 파일을 지우고 frames=0 으로 알린다.
"""

import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("hub.clip")

CODECS = ("avc1", "mp4v")      # 앞에서부터 시도. avc1(H.264)이 안 되는 OpenCV 빌드면 mp4v(MPEG-4)
MAX_FILL_S = 2.0               # 이보다 긴 끊김은 마지막 장면으로 채우지 않는다
MAX_QUEUED_FRAMES = 90         # 작업 스레드가 밀리면 이 이상은 버린다 (약 6초 분량)
STOP_JOIN_S = 5.0


@dataclass
class ClipResult:
    event_id: int
    clip_id: int
    path: Path
    ended_at: float
    frames: int
    duration_s: float
    error: str | None = None


class _Clip:
    """녹화 한 건 (작업 스레드 안에서만 쓴다)."""

    def __init__(self, event_id: int, clip_id: int, path: Path, started_at: float, fps: float):
        self.event_id, self.clip_id, self.path = event_id, clip_id, path
        self.t0 = started_at           # 영상 0초에 해당하는 Control 시각 (긴 끊김 뒤에는 앞으로 당긴다)
        self.fps = fps
        self.writer: cv2.VideoWriter | None = None
        self.size: tuple[int, int] | None = None   # (w, h)
        self.frames = 0
        self.error: str | None = None

    def write(self, img, ts: float, codec_cache: dict):
        if self.error:
            return
        if self.writer is None and not self._open(img, codec_cache):
            return
        if (img.shape[1], img.shape[0]) != self.size:      # 송출 해상도가 중간에 바뀐 경우
            img = cv2.resize(img, self.size, interpolation=cv2.INTER_AREA)
        target = int((ts - self.t0) * self.fps + 1e-6)     # 이 프레임이 들어갈 영상 위치 (프레임 번호, 소수 오차 보정)
        if target < self.frames:                          # 이미 이 시점까지 썼다 (15fps -> 10fps 솎기)
            return
        n = target - self.frames + 1
        max_fill = max(1, int(MAX_FILL_S * self.fps))
        if n > max_fill:                                  # 긴 끊김: 채우지 않고 이어 붙인다
            n = 1
            self.t0 = ts - self.frames / self.fps
        for _ in range(n):
            self.writer.write(img)
        self.frames += n

    def _open(self, img, codec_cache: dict) -> bool:
        h, w = img.shape[:2]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        codecs = [codec_cache["ok"]] if "ok" in codec_cache else list(CODECS)
        for c in codecs:
            wr = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*c), self.fps, (w, h))
            if wr.isOpened():
                if codec_cache.get("ok") != c:
                    codec_cache["ok"] = c
                    log.info("녹화 코덱: %s (%dx%d, %gfps)", c, w, h, self.fps)
                self.writer, self.size = wr, (w, h)
                return True
            wr.release()
        self.error = f"VideoWriter 를 열 수 없음 ({', '.join(codecs)}) - {self.path}"
        log.error("event %d 녹화 실패: %s", self.event_id, self.error)
        return False

    def finish(self, ended_at: float) -> ClipResult:
        if self.writer is not None:
            self.writer.release()
        if self.frames == 0:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
        return ClipResult(self.event_id, self.clip_id, self.path, ended_at, self.frames,
                          self.frames / self.fps, self.error)


class ClipRecorder:
    def __init__(self, fps: float = 10, notify=None):
        """notify() = 녹화가 끝나 done 큐에 결과가 들어갈 때마다 (작업 스레드에서) 부른다."""
        self.fps = float(fps)
        self.notify = notify
        self.q: queue.Queue = queue.Queue()
        self.done: queue.Queue = queue.Queue()
        self.recording: set[int] = set()        # 루프 스레드 기준 녹화 중인 event_id (feed 를 거를지 판단)
        self.dropped = 0
        self._thread = threading.Thread(target=self._run, name="clip-recorder", daemon=True)
        self._thread.start()

    # ---- 루프 스레드에서 부른다 (바로 반환) ------------------------------
    def start(self, event_id: int, clip_id: int, path: Path, now: float):
        self.recording.add(event_id)
        self.q.put(("start", event_id, clip_id, Path(path), now))

    def stop(self, event_id: int, now: float):
        if event_id in self.recording:
            self.recording.discard(event_id)
            self.q.put(("stop", event_id, now))

    def feed(self, jpeg: bytes, now: float):
        """cam2 JPEG 한 장 (video_relay). 녹화 중인 이벤트가 없으면 버린다."""
        if not self.recording:
            return
        if self.q.qsize() > MAX_QUEUED_FRAMES:
            self.dropped += 1
            if self.dropped in (1, 100) or self.dropped % 1000 == 0:
                log.warning("녹화가 밀려 프레임을 버림 (누적 %d장)", self.dropped)
            return
        self.q.put(("frame", jpeg, now))

    def drain(self) -> list[ClipResult]:
        out = []
        while True:
            try:
                out.append(self.done.get_nowait())
            except queue.Empty:
                return out

    def close(self, now: float | None = None) -> list[ClipResult]:
        """종료: 녹화 중인 것을 모두 닫고 결과를 돌려준다 (파일이 깨지지 않게 release 까지 기다린다)."""
        now = time.time() if now is None else now
        for eid in list(self.recording):
            self.stop(eid, now)
        self.q.put(("quit",))
        self._thread.join(STOP_JOIN_S)
        if self._thread.is_alive():
            log.warning("녹화 스레드가 %.0f초 안에 끝나지 않음", STOP_JOIN_S)
        return self.drain()

    # ---- 작업 스레드 -----------------------------------------------------
    def _run(self):
        clips: dict[int, _Clip] = {}
        codec_cache: dict = {}
        while True:
            item = self.q.get()
            op = item[0]
            try:
                if op == "frame":
                    if not clips:
                        continue
                    _, jpeg, ts = item
                    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                    if img is None:
                        continue
                    for c in clips.values():
                        c.write(img, ts, codec_cache)
                elif op == "start":
                    _, eid, clip_id, path, ts = item
                    old = clips.pop(eid, None)
                    if old is not None:                 # 같은 이벤트가 다시 시작 (버그 방지) - 앞의 것을 닫는다
                        self._finished(old.finish(ts))
                    clips[eid] = _Clip(eid, clip_id, path, ts, self.fps)
                    log.info("녹화 시작: event %d -> %s", eid, path)
                elif op == "stop":
                    _, eid, ts = item
                    c = clips.pop(eid, None)
                    if c is not None:
                        self._finished(c.finish(ts))
                elif op == "quit":
                    for c in clips.values():
                        self._finished(c.finish(time.time()))
                    clips.clear()
                    return
            except Exception:                           # 녹화 오류로 Control 이 멈추지 않게
                log.exception("녹화 처리 오류: %s", op)

    def _finished(self, r: ClipResult):
        if r.frames:
            log.info("녹화 저장: event %d %.1f초 (%d프레임) %s", r.event_id, r.duration_s, r.frames, r.path)
        else:
            log.warning("녹화 없음: event %d - cam2 영상을 한 장도 받지 못함%s", r.event_id,
                        f" ({r.error})" if r.error else "")
        self.done.put(r)
        if self.notify:
            try:
                self.notify()
            except Exception:                           # 루프가 이미 닫힌 경우 (종료 중)
                pass
