"""쓰러짐 알림음 — 강사 화면·학생 화면 공용

Linux는 QApplication.beep()가 소리가 안 나는 경우가 많아 wav를 직접 튼다.
소리 파일을 저장소에 두지 않고 처음 쓸 때 임시 폴더에 만든다 (880Hz 삐-삐-삐).
QtMultimedia가 없거나 소리 장치를 못 열면 beep()로 대신한다.
"""
from __future__ import annotations

import math
import struct
import tempfile
import wave
from pathlib import Path

from PyQt6.QtCore import QObject, QUrl
from PyQt6.QtWidgets import QApplication

try:
    from PyQt6.QtMultimedia import QSoundEffect
except ImportError:                        # pragma: no cover
    QSoundEffect = None


def alert_wav() -> Path:
    path = Path(tempfile.gettempdir()) / "jarvis_fall_alert.wav"
    if not path.exists():
        rate, tone, gap = 22050, 0.18, 0.12
        beep = b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 880 * n / rate)))
                        for n in range(int(rate * tone)))
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes((beep + b"\0\0" * int(rate * gap)) * 3)
    return path


class AlertSound(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.effect = None
        if QSoundEffect is not None:
            self.effect = QSoundEffect(self)
            self.effect.setSource(QUrl.fromLocalFile(str(alert_wav())))

    def play(self):
        if self.effect is not None and self.effect.status() != QSoundEffect.Status.Error:
            self.effect.play()
        else:
            QApplication.beep()
