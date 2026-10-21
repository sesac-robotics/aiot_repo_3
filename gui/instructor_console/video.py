"""영상 위젯 — 실시간(UDP, IF-01 형식)과 녹화 다시 보기(event_clip 파일)

⚠ 실시간 영상을 Console이 어디서 받는지는 Interface Spec v1.1에 아직 없다.
  cam2 :5002는 Vision Service가 이미 받고 있어서 같은 포트를 Console이 또 열 수 없다.
  여기서는 IF-01과 같은 패킷 형식을 '별도 포트'(기본 cam1 5011, cam2 5012)로 받도록 만들어 두었다.
  Camera Streamer가 두 곳으로 보내거나 Vision이 중계하도록 규격을 정하면 포트만 맞추면 된다.
"""
from __future__ import annotations

import os
import struct
import time

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QImage, QPixmap
from PyQt6.QtNetwork import QHostAddress, QUdpSocket
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QStackedLayout, QVBoxLayout, QWidget

from common import protocol as P

UDP_HEADER = struct.Struct(">B3xId")     # cam_id, 예약 3바이트, frame_id, captured_at (16바이트)

try:                                       # QtMultimedia가 없는 환경에서도 화면은 뜨게
    from PyQt6.QtMultimedia import QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    HAS_MULTIMEDIA = True
except ImportError:                        # pragma: no cover
    HAS_MULTIMEDIA = False


class LiveVideo(QLabel):
    """IF-01 형식 UDP(헤더 16바이트 + JPEG)를 받아 그리는 위젯"""

    def __init__(self, port: int, cam_id: int, title: str, parent=None):
        super().__init__(parent)
        self.port, self.cam_id, self.title = port, cam_id, title
        self.last_frame_id = -1
        self.last_rx = 0.0
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 200)
        self.setStyleSheet("background:#1f2428; color:#9aa0a6; border-radius:8px;")
        self._pix: QPixmap | None = None
        self.sock = QUdpSocket(self)
        self.bound = self.sock.bind(QHostAddress(QHostAddress.SpecialAddress.AnyIPv4), port,
                                    QUdpSocket.BindFlag.ShareAddress | QUdpSocket.BindFlag.ReuseAddressHint)
        self.sock.readyRead.connect(self._on_datagram)
        self.watch = QTimer(self, interval=1000)
        self.watch.timeout.connect(self._check_alive)
        self.watch.start()
        self._check_alive()

    def _on_datagram(self):
        latest = None
        while self.sock.hasPendingDatagrams():
            data = bytes(self.sock.receiveDatagram().data().data())
            if len(data) <= UDP_HEADER.size:
                continue
            cam_id, frame_id, _captured = UDP_HEADER.unpack_from(data)
            if cam_id != self.cam_id:
                continue
            now = time.time()
            # 순서가 뒤집힌 옛 프레임은 버린다 (IF-01 규칙). 단 1초 넘게 끊겼다 오면
            # Streamer가 다시 켜져 frame_id가 처음부터 다시 시작한 것이므로 받는다.
            if frame_id < self.last_frame_id and self.last_frame_id - frame_id < 1000 \
                    and now - self.last_rx < 1.0:
                continue
            self.last_frame_id = frame_id
            self.last_rx = now
            latest = data
        # 한 번에 여러 장이 쌓였으면 마지막 것만, 화면에 보일 때만 그린다 (숨은 탭은 디코딩 안 함)
        if latest is None or not self.isVisible():
            return
        img = QImage.fromData(latest[UDP_HEADER.size:], "JPG")
        if not img.isNull():
            self._pix = QPixmap.fromImage(img)
            self._render()

    def _render(self):
        if self._pix:
            self.setPixmap(self._pix.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._render()

    def _check_alive(self):
        if time.time() - self.last_rx > 2.0:
            self._pix = None
            self.clear()
            why = f"UDP :{self.port} 대기 중" if self.bound else f"UDP :{self.port} 열 수 없음 (다른 프로그램이 사용 중)"
            self.setText(f"{self.title}\n영상 없음 — {why}")


class ClipPlayer(QWidget):
    """녹화 다시 보기. Console은 Control과 같은 노트북이라 clip_info.path 파일을 직접 연다."""

    request_clip = pyqtSignal(int)

    def __init__(self, clip_root: str, parent=None):
        super().__init__(parent)
        self.clip_root = clip_root
        self.event_id = 0
        self.path = ""
        self.msg = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.msg.setStyleSheet("background:#1f2428; color:#9aa0a6; border-radius:8px;")
        self.msg.setMinimumSize(320, 200)
        self.open_btn = QPushButton("외부 플레이어로 열기")
        self.open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.path)))
        self.open_btn.hide()

        self.stack = QStackedLayout()
        self.stack.addWidget(self.msg)
        if HAS_MULTIMEDIA:
            self.video = QVideoWidget()
            self.player = QMediaPlayer(self)
            self.player.setVideoOutput(self.video)
            self.stack.addWidget(self.video)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self.stack, 1)
        row = QHBoxLayout()
        self.replay_btn = QPushButton("처음부터")
        self.replay_btn.clicked.connect(self._replay)
        self.replay_btn.hide()
        row.addStretch(1); row.addWidget(self.replay_btn); row.addWidget(self.open_btn)
        lay.addLayout(row)

    def load(self, event_id: int):
        self.stop()
        self.event_id = event_id
        self._show_msg("녹화 정보를 불러오는 중…")
        self.request_clip.emit(event_id)

    def on_clip_info(self, m: dict):
        if m["event_id"] != self.event_id:
            return
        st = m["status"]
        if st == P.CLIP_NONE:
            return self._show_msg("이 이벤트는 녹화가 없습니다")
        if st == P.CLIP_DELETED:
            return self._show_msg("보관 기간이 지나 녹화가 삭제되었습니다")
        if st == P.CLIP_RECORDING:
            return self._show_msg("아직 녹화 중입니다 — 이벤트가 끝나면 다시 볼 수 있습니다")
        path = m["path"]
        self.path = path if os.path.isabs(path) else os.path.join(self.clip_root, path)
        if not os.path.exists(self.path):
            return self._show_msg(f"녹화 파일을 찾을 수 없습니다\n{self.path}")
        self.open_btn.show()
        if HAS_MULTIMEDIA:
            self.stack.setCurrentIndex(1)
            self.replay_btn.show()
            self.player.setSource(QUrl.fromLocalFile(self.path))
            self.player.play()
        else:
            self._show_msg("이 PC에 QtMultimedia가 없어 화면 안에서 재생할 수 없습니다")

    def _replay(self):
        if HAS_MULTIMEDIA:
            self.player.setPosition(0); self.player.play()

    def _show_msg(self, text):
        self.stack.setCurrentIndex(0)
        self.msg.setText(text)
        self.replay_btn.hide()
        if text.startswith("녹화 정보") or not self.path:
            self.open_btn.hide()

    def stop(self):
        self.path = ""
        self.open_btn.hide()
        if HAS_MULTIMEDIA:
            self.player.stop()
