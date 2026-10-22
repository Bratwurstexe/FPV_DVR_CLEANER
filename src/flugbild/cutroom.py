"""A compact, independently designed per-clip cut room."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QPainter
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    QWidget,
)


class CutTrack(QWidget):
    """One timeline with two handles and a visible retained span."""

    def __init__(self) -> None:
        super().__init__()
        self.full = 1.0
        self.entry = 0.0
        self.exit = 1.0
        self.head = 0.0
        self.grabbed = ""
        self.setMinimumHeight(72)
        self.setMouseTracking(True)

    def _x(self, moment: float) -> int:
        return 18 + round((self.width() - 36) * moment / max(self.full, 0.001))

    def _time(self, x: int) -> float:
        return max(0.0, min(self.full, (x - 18) / max(1, self.width() - 36) * self.full))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.fillRect(18, 20, self.width() - 36, 32, QColor("#d6dadc"))
        painter.fillRect(self._x(self.entry), 20, self._x(self.exit) - self._x(self.entry),
                         32, QColor("#f7bda7"))
        for second in range(0, int(self.full) + 1, max(1, int(self.full / 10))):
            x = self._x(second)
            painter.setPen(QColor("#6d777e"))
            painter.drawLine(x, 53, x, 59)
        painter.setPen(QColor("#eb693d"))
        for moment in (self.entry, self.exit):
            x = self._x(moment)
            painter.setBrush(QColor("#eb693d"))
            painter.drawRoundedRect(x - 5, 15, 10, 42, 3, 3)
        painter.setPen(QColor("#30373d"))
        painter.drawLine(self._x(self.head), 8, self._x(self.head), 64)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        x = int(event.position().x())
        if abs(x - self._x(self.entry)) < 13:
            self.grabbed = "entry"
        elif abs(x - self._x(self.exit)) < 13:
            self.grabbed = "exit"
        else:
            self.grabbed = "head"
        self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self.grabbed:
            return
        moment = self._time(int(event.position().x()))
        if self.grabbed == "entry":
            self.entry = min(moment, self.exit - 0.1)
        elif self.grabbed == "exit":
            self.exit = max(moment, self.entry + 0.1)
        else:
            self.head = moment
            if hasattr(self, "seek"):
                self.seek(moment)
        if hasattr(self, "changed"):
            self.changed()
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self.grabbed = ""


class CutRoom(QDialog):
    def __init__(self, clip: Path, length: float, start: float, end: float, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Cut room · {clip.name}")
        self.resize(850, 650)
        self.setStyleSheet("""
            QDialog { background: #f2f3f3; color: #2d343a; font: 11pt 'Inter'; }
            QPushButton { background: #e8eaeb; color: #2d343a; border: 1px solid #d3d8db;
                          border-radius: 7px; padding: 8px 12px; }
            QLabel { color: #2d343a; }
        """)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(QLabel("CUT ROOM  /  Set the section before processing"))

        self.screen = QVideoWidget()
        self.screen.setMinimumHeight(380)
        layout.addWidget(self.screen, 1)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.screen)
        self.player.setSource(QUrl.fromLocalFile(str(clip)))

        controls = QHBoxLayout()
        self.play_button = QPushButton("Play / pause")
        self.play_button.clicked.connect(self._toggle)
        controls.addWidget(self.play_button)
        self.position_label = QLabel("00:00")
        controls.addWidget(self.position_label)
        controls.addStretch()
        self.range_label = QLabel()
        controls.addWidget(self.range_label)
        layout.addLayout(controls)

        self.track = CutTrack()
        self.track.full = max(0.1, length)
        self.track.entry = min(start, length - 0.1)
        self.track.exit = min(end or length, length)
        self.track.seek = lambda moment: self.player.setPosition(round(moment * 1000))
        self.track.changed = self._update_labels
        layout.addWidget(self.track)
        layout.addWidget(QLabel("Drag the orange handles to set in and out. Click elsewhere to seek."))

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.player.positionChanged.connect(self._position_changed)
        self._update_labels()

    def _toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _position_changed(self, milliseconds: int) -> None:
        seconds = milliseconds / 1000
        self.track.head = seconds
        self.track.update()
        self.position_label.setText(self._clock(seconds))
        if seconds >= self.track.exit and self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()

    def _update_labels(self) -> None:
        self.range_label.setText(f"IN {self._clock(self.track.entry)}  ·  OUT {self._clock(self.track.exit)}")

    @staticmethod
    def _clock(seconds: float) -> str:
        return f"{int(seconds // 60):02}:{seconds % 60:05.2f}"

    def accept(self) -> None:
        self.player.stop()
        super().accept()

    def reject(self) -> None:
        self.player.stop()
        super().reject()
