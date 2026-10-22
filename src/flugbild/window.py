from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import threading

import cv2

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox, QStackedWidget, QVBoxLayout,
    QWidget,
)

from .cutroom import CutRoom
from .learning import Signalsignatur, calibrate
from .media import inspect_video, still_frame, tools_available
from .processor import process_one, video_filters
from .recipe import Arbeitsrezept
from .restoration import process_temporal, restore_frame


COLORS = {
    "canvas": "#f2f3f3", "panel": "#ffffff", "tile": "#e8eaeb",
    "ink": "#2d343a", "muted": "#69737b", "accent": "#eb693d", "line": "#d3d8db",
}


class CompareCanvas(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.original = QPixmap()
        self.treated = QPixmap()
        self.heatmap = QPixmap()
        self.map_visible = False
        self.divider = 50
        self.setMinimumHeight(280)

    def set_images(self, original: Path, treated: Path, heatmap: Path) -> None:
        self.original = QPixmap(str(original))
        self.treated = QPixmap(str(treated))
        self.heatmap = QPixmap(str(heatmap))
        self.map_visible = False
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS["canvas"]))
        if self.original.isNull():
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "Add footage and render a sample")
            return
        image_size = self.original.size().scaled(self.size(), Qt.KeepAspectRatio)
        x = (self.width() - image_size.width()) // 2
        y = (self.height() - image_size.height()) // 2
        area = self.rect().adjusted(x, y, -(self.width() - x - image_size.width()),
                                    -(self.height() - y - image_size.height()))
        painter.drawPixmap(area, self.original)
        choice = self.heatmap if self.map_visible else self.treated
        if not choice.isNull():
            split = x + round(image_size.width() * self.divider / 100)
            painter.save()
            painter.setClipRect(split, y, x + image_size.width() - split, image_size.height())
            painter.drawPixmap(area, choice)
            painter.restore()
            painter.setPen(QColor(COLORS["accent"]))
            painter.drawLine(split, y, split, y + image_size.height())
            painter.fillRect(split - 16, y + 10, 32, 22, QColor(COLORS["accent"]))
            painter.setPen(QColor(COLORS["canvas"]))
            painter.drawText(split - 16, y + 10, 32, 22, Qt.AlignCenter, "↔")


class PreviewWorker(QThread):
    ready = Signal(str, str, str)
    failed = Signal(str)

    def __init__(self, clip: Path, moment: float, recipe: Arbeitsrezept, folder: Path,
                 fingerprint: Signalsignatur | None) -> None:
        super().__init__()
        self.clip, self.moment, self.recipe, self.folder = clip, moment, recipe, folder
        self.fingerprint = fingerprint

    def run(self) -> None:
        try:
            left, right = self.folder / "input.png", self.folder / "probe.png"
            if self.fingerprint:
                capture = cv2.VideoCapture(str(self.clip))
                try:
                    fps = capture.get(cv2.CAP_PROP_FPS)
                    capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, round(self.moment * fps) - 1))
                    ok_prev, previous = capture.read()
                    ok_now, current = capture.read()
                    if not ok_now:
                        current = previous
                    if not ok_prev:
                        raise ValueError("Could not decode preview frame")
                    amount = (self.recipe.farbberuhigung if self.recipe.signalart == "analog"
                              else self.recipe.blockpflege)
                    treated = restore_frame(current, previous, self.recipe.signalart,
                                            amount, self.fingerprint)
                    cv2.imwrite(str(left), current)
                    cv2.imwrite(str(right), treated)
                finally:
                    capture.release()
            else:
                still_frame(self.clip, self.moment, left)
                still_frame(self.clip, self.moment, right, video_filters(self.recipe, preview=True))
            input_pixels = cv2.imread(str(left))
            output_pixels = cv2.imread(str(right))
            if input_pixels is None or output_pixels is None:
                raise ValueError("Could not build the comparison view")
            difference = cv2.absdiff(input_pixels, output_pixels).max(axis=2)
            intensity = (difference.astype("float32") * 5).clip(0, 255).astype("uint8")
            colors = cv2.applyColorMap(intensity, cv2.COLORMAP_HOT)
            overlay = cv2.addWeighted(input_pixels, 0.55, colors, 0.45, 0)
            overlay[intensity < 15] = input_pixels[intensity < 15]
            heatmap = self.folder / "change_map.png"
            cv2.imwrite(str(heatmap), overlay)
            self.ready.emit(str(left), str(right), str(heatmap))
        except Exception as exc:
            self.failed.emit(str(exc))


class ExportWorker(QThread):
    advanced = Signal(int, int, float)
    finished_item = Signal(int, bool, str)
    failed = Signal(str)

    def __init__(self, clips: list[Path], folder: Path, recipe: Arbeitsrezept,
                 cuts: dict[Path, tuple[float, float]],
                 fingerprint: Signalsignatur | None) -> None:
        super().__init__()
        self.clips, self.folder, self.recipe = clips, folder, recipe
        self.cuts, self.fingerprint = cuts, fingerprint
        self.stop = threading.Event()

    def run(self) -> None:
        try:
            for index, clip in enumerate(self.clips):
                if self.stop.is_set():
                    break
                metadata = inspect_video(clip)
                start, end = self.cuts.get(clip, (0.0, 0.0))
                clip_recipe = replace(self.recipe, schnittbeginn=start, schnittende=end)
                candidate = self.folder / f"{clip.stem}_flugbild.mp4"
                number = 2
                while candidate.exists() or candidate.resolve() == clip.resolve():
                    candidate = self.folder / f"{clip.stem}_flugbild_{number}.mp4"
                    number += 1
                notify = lambda fraction, i=index: self.advanced.emit(i, len(self.clips), fraction)
                if self.fingerprint:
                    outcome = process_temporal(clip, candidate, clip_recipe, metadata,
                                               self.fingerprint, notify, self.stop)
                else:
                    outcome = process_one(clip, candidate, clip_recipe, metadata,
                                          notify, self.stop)
                self.finished_item.emit(index, outcome.success, outcome.message)
                if not outcome.success and self.stop.is_set():
                    break
        except Exception as exc:
            self.failed.emit(str(exc))


class LearningWorker(QThread):
    sampled = Signal(int, int)
    learned = Signal(object)
    failed = Signal(str)

    def __init__(self, folder: Path, signalart: str, seconds: int) -> None:
        super().__init__()
        self.folder, self.signalart, self.seconds = folder, signalart, seconds
        self.stop = threading.Event()

    def run(self) -> None:
        try:
            result = calibrate(self.folder, self.signalart, self.seconds,
                               lambda count, limit: self.sampled.emit(count, limit), self.stop)
            if not self.stop.is_set():
                self.learned.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class QueueRow(QFrame):
    """Compact clip entry with its own remove action."""

    def __init__(self, clip: Path, remove) -> None:
        super().__init__()
        self.setObjectName("queueRow")
        line = QHBoxLayout(self)
        line.setContentsMargins(12, 8, 9, 8)
        line.setSpacing(10)
        labels = QVBoxLayout()
        labels.setSpacing(2)
        self.title = QLabel(clip.name)
        self.title.setObjectName("queueTitle")
        self.detail = QLabel(str(clip.parent))
        self.detail.setObjectName("queueDetail")
        self.detail.setToolTip(str(clip))
        labels.addWidget(self.title)
        labels.addWidget(self.detail)
        line.addLayout(labels, 1)
        self.remove_button = QPushButton("×")
        self.remove_button.setObjectName("queueRemove")
        self.remove_button.setAccessibleName(f"Remove {clip.name} from queue")
        self.remove_button.setToolTip(f"Remove {clip.name} from queue")
        self.remove_button.setFixedSize(32, 32)
        self.remove_button.clicked.connect(remove)
        line.addWidget(self.remove_button)


class Workbench(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FPV DVR Cleaner")
        self.resize(1320, 810)
        self.setMinimumSize(980, 650)
        self.clips: list[Path] = []
        self.queue_rows: list[QueueRow] = []
        self.cuts: dict[Path, tuple[float, float]] = {}
        self.fingerprint: Signalsignatur | None = None
        self.preview_job: PreviewWorker | None = None
        self.export_job: ExportWorker | None = None
        self.learning_job: LearningWorker | None = None
        self.scratch = tempfile.TemporaryDirectory(prefix="flugbild-")
        self.setAcceptDrops(True)
        self._build()
        if not tools_available():
            QMessageBox.warning(self, "Tools missing", "FFmpeg and ffprobe must be on PATH.")

    def _label(self, title: str, style: str = "") -> QLabel:
        item = QLabel(title)
        if style:
            item.setObjectName(style)
        return item

    def _button(self, title: str, action, primary: bool = False) -> QPushButton:
        button = QPushButton(title)
        button.setProperty("primary", primary)
        button.clicked.connect(action)
        return button

    def _card(self) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(11)
        return card, layout

    def _slider(self, title: str, start: int, parent: QVBoxLayout) -> QSlider:
        row = QHBoxLayout()
        row.addWidget(self._label(title))
        value = self._label(str(start), "value")
        row.addStretch()
        row.addWidget(value)
        parent.addLayout(row)
        control = QSlider(Qt.Horizontal)
        control.setRange(0, 100)
        control.setValue(start)
        control.valueChanged.connect(lambda number: value.setText(str(number)))
        parent.addWidget(control)
        return control

    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(22)
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(220)
        rail_box = QVBoxLayout(rail)
        rail_box.setContentsMargins(18, 23, 18, 23)
        rail_box.setSpacing(12)
        rail_box.addWidget(self._label("FPV DVR\nCLEANER", "railbrand"))
        rail_box.addSpacing(28)
        self.pages = QStackedWidget()
        self.nav_buttons: list[QPushButton] = []
        for index, title in enumerate(("Library", "Signal lab", "Model room", "Deliver")):
            button = self._button(title, lambda checked=False, number=index: self._navigate(number))
            button.setObjectName("navigation")
            rail_box.addWidget(button)
            self.nav_buttons.append(button)
        rail_box.addStretch()
        rail_box.addWidget(self._label("Your footage stays local.", "railfoot"))
        outer.addWidget(rail)
        outer.addWidget(self.pages, 1)

        library_page = QWidget()
        library_box = QVBoxLayout(library_page)
        library_box.addWidget(self._label("Library", "headline"))
        library_box.addWidget(self._label("Collect and cut the moments worth keeping.", "muted"))
        library_columns = QHBoxLayout()
        library_box.addLayout(library_columns, 1)
        self.pages.addWidget(library_page)

        signal_page = QWidget()
        signal_page_box = QVBoxLayout(signal_page)
        signal_page_box.addWidget(self._label("Signal lab", "headline"))
        signal_page_box.addWidget(self._label("Tune the noise profile and inspect a frame.", "muted"))
        self.pages.addWidget(signal_page)

        model_page = QWidget()
        model_page_box = QVBoxLayout(model_page)
        model_page_box.addWidget(self._label("Model room", "headline"))
        model_page_box.addWidget(self._label("Build a local signal fingerprint from your own damaged recordings.", "muted"))
        self.pages.addWidget(model_page)

        export_page = QWidget()
        export_page_box = QVBoxLayout(export_page)
        export_page_box.addWidget(self._label("Deliver", "headline"))
        export_page_box.addWidget(self._label("Choose a destination and render your queue.", "muted"))
        self.pages.addWidget(export_page)

        left, left_box = self._card()
        left.setMinimumWidth(350)
        left_box.addWidget(self._label("FOOTAGE QUEUE", "eyebrow"))
        queue_heading = QHBoxLayout()
        queue_heading.addWidget(self._label("Your clips", "section"))
        queue_heading.addStretch()
        self.queue_count = self._label("0 clips", "value")
        queue_heading.addWidget(self.queue_count)
        left_box.addLayout(queue_heading)
        self.listing = QListWidget()
        self.listing.currentRowChanged.connect(self._selection_changed)
        left_box.addWidget(self.listing, 1)
        queue_actions = QHBoxLayout()
        self.add_button = self._button("+ Add footage", self.add_clips, True)
        queue_actions.addWidget(self.add_button, 1)
        self.cut_button = self._button("Open cut room", self.open_cut_room)
        self.cut_button.setEnabled(False)
        queue_actions.addWidget(self.cut_button, 1)
        left_box.addLayout(queue_actions)
        self.meta = self._label("No footage yet", "muted")
        self.meta.setWordWrap(True)
        left_box.addWidget(self.meta)
        library_columns.addWidget(left, 2)

        welcome, welcome_box = self._card()
        welcome_box.addStretch()
        welcome_box.addWidget(self._label("WORKFLOW", "eyebrow"))
        welcome_box.addWidget(self._label("From footage\nto final cut.", "feature"))
        note = self._label("01  Add analog or HDZero clips\n\n02  Trim each clip in Cut room\n\n03  Tune and check a frame\n\n04  Export the queue", "muted")
        note.setWordWrap(True)
        welcome_box.addWidget(note)
        self.queue_hint = self._label("Add footage to begin", "value")
        welcome_box.addWidget(self.queue_hint)
        welcome_box.addWidget(self._button("Explore signal lab  →", lambda: self._navigate(1), True))
        welcome_box.addStretch()
        library_columns.addWidget(welcome, 1)

        center, center_box = self._card()
        center_box.addWidget(self._label("IMAGE CHECK", "eyebrow"))
        title_row = QHBoxLayout()
        title_row.addWidget(self._label("Before / after", "section"))
        title_row.addStretch()
        self.map_button = self._button("Show change map", self._toggle_map)
        self.map_button.setEnabled(False)
        title_row.addWidget(self.map_button)
        title_row.addWidget(self._button("Render sample", self.preview))
        center_box.addLayout(title_row)
        self.canvas = CompareCanvas()
        center_box.addWidget(self.canvas, 1)
        self.split = QSlider(Qt.Horizontal)
        self.split.setRange(0, 100)
        self.split.setValue(50)
        self.split.valueChanged.connect(self._move_divider)
        center_box.addWidget(self.split)
        moment_row = QHBoxLayout()
        moment_row.addWidget(self._label("Frame"))
        self.moment = QSlider(Qt.Horizontal)
        self.moment.setRange(0, 100)
        self.moment.setValue(20)
        moment_row.addWidget(self.moment, 1)
        self.moment_label = self._label("20 %", "value")
        self.moment.valueChanged.connect(lambda n: self.moment_label.setText(f"{n} %"))
        moment_row.addWidget(self.moment_label)
        center_box.addLayout(moment_row)
        self.status_line = self._label("Ready", "muted")
        center_box.addWidget(self.status_line)
        signal_columns = QHBoxLayout()
        signal_columns.setSpacing(16)
        signal_columns.addWidget(center, 2)
        signal_page_box.addLayout(signal_columns, 1)

        signal_card, signal_box = self._card()
        signal_box.addWidget(self._label("PROCESSING DESK", "eyebrow"))
        signal_box.addWidget(self._label("What needs care?", "section"))
        self.signal_mode = QComboBox()
        self.signal_mode.addItem("Analog · color noise", "analog")
        self.signal_mode.addItem("HDZero · block edges", "digital")
        self.signal_mode.addItem("Neutral · finishing only", "neutral")
        self.signal_mode.currentIndexChanged.connect(self._mode_changed)
        signal_box.addWidget(self.signal_mode)
        self.farbberuhigung = self._slider("Color calm", 35, signal_box)
        self.blockpflege = self._slider("Block care", 35, signal_box)
        self.kantendetail = self._slider("Edge presence", 15, signal_box)
        self.bewegungsspur = self._slider("Motion trace", 0, signal_box)
        signal_card.setMinimumWidth(280)
        signal_columns.addWidget(signal_card, 1)

        model_card, model_box = self._card()
        model_box.addWidget(self._label("LOCAL LEARNING", "eyebrow"))
        model_box.addWidget(self._label("Your signal model", "section"))
        model_box.addWidget(self._label("Measure a folder of damaged footage for the selected signal type.", "muted"))
        model_box.addWidget(self._label("Keep Analog and HDZero clips in separate folders.", "muted"))
        self.learning_folder = QLineEdit()
        self.learning_folder.setPlaceholderText("Choose an Analog footage folder")
        model_box.addWidget(self.learning_folder)
        model_box.addWidget(self._button("Choose footage folder", self.choose_learning_folder))
        self.learning_minutes = QSpinBox()
        self.learning_minutes.setRange(1, 60)
        self.learning_minutes.setValue(5)
        self.learning_minutes.setSuffix(" min learning time")
        model_box.addWidget(self.learning_minutes)
        self.learn_button = self._button("Train signal model", self.start_learning, True)
        model_box.addWidget(self.learn_button)
        self.model_label = self._label("No model loaded", "muted")
        self.model_label.setWordWrap(True)
        model_box.addWidget(self.model_label)
        model_box.addWidget(self._button("Load model", self.load_model))
        model_page_box.addWidget(model_card)
        model_page_box.addStretch()

        export_card, export_box = self._card()
        export_box.addWidget(self._label("RENDER SETTINGS", "eyebrow"))
        export_box.addWidget(self._label("Size & delivery", "section"))
        self.delivery_summary = self._label("0 clips ready to export", "muted")
        export_box.addWidget(self.delivery_summary)
        export_box.addWidget(self._label("Output resolution"))
        self.magnification_choice = QComboBox()
        self.magnification_choice.addItem("Source size", 100)
        self.magnification_choice.addItem("1.5 × resize", 150)
        self.magnification_choice.addItem("2 × resize", 200)
        export_box.addWidget(self.magnification_choice)
        export_box.addWidget(self._label("Caption at bottom"))
        self.lower_caption_entry = QLineEdit()
        self.lower_caption_entry.setPlaceholderText("Optional text")
        export_box.addWidget(self.lower_caption_entry)
        self.compression_grade_spin = QSpinBox()
        self.compression_grade_spin.setRange(14, 32)
        self.compression_grade_spin.setValue(20)
        self.compression_grade_spin.setPrefix("Quality  ")
        export_box.addWidget(self._label("Compression quality"))
        export_box.addWidget(self.compression_grade_spin)
        self.size_limit = QSpinBox()
        self.size_limit.setRange(0, 10000)
        self.size_limit.setSuffix(" MB")
        self.size_limit.setSpecialValueText("No size cap")
        export_box.addWidget(self._label("Target file size"))
        export_box.addWidget(self.size_limit)
        export_box.addWidget(self._label("Output folder"))
        self.folder = QLineEdit(str(Path.home() / "Videos" / "FPV DVR Cleaner"))
        export_box.addWidget(self.folder)
        export_box.addWidget(self._button("Choose output folder", self.choose_folder))
        self.export_button = self._button("Export queue", self.start_export, True)
        export_box.addWidget(self.export_button)
        self.cancel_button = self._button("Cancel run", self.cancel_export)
        self.cancel_button.setEnabled(False)
        export_box.addWidget(self.cancel_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        export_box.addWidget(self.progress)
        self.export_status = self._label("Ready to export", "muted")
        export_box.addWidget(self.export_status)
        export_page_box.addWidget(export_card)
        export_page_box.addStretch()
        self._mode_changed()

        self._navigate(0)

        self.setStyleSheet(f"""
            QWidget {{ background: {COLORS['canvas']}; color: {COLORS['ink']}; font: 11pt 'Inter'; }}
            QFrame#rail {{ background: #353c42; border-radius: 18px; }}
            QFrame#card {{ background: {COLORS['panel']}; border: 1px solid {COLORS['line']}; border-radius: 18px; }}
            QLabel {{ background: transparent; }}
            QLabel#railbrand {{ color: #ffffff; background: #353c42; font-size: 17pt; font-weight: 900; }}
            QLabel#railfoot {{ color: #c2c8cb; background: #353c42; font-size: 9pt; }}
            QLabel#eyebrow {{ color: {COLORS['accent']}; font-size: 9pt; font-weight: 800; letter-spacing: 2px; }}
            QLabel#headline {{ font-size: 29pt; font-weight: 800; }}
            QLabel#feature {{ font-size: 27pt; font-weight: 800; }}
            QLabel#section {{ font-size: 18pt; font-weight: 700; }}
            QLabel#muted {{ color: {COLORS['muted']}; font-size: 9pt; }}
            QLabel#value {{ color: {COLORS['accent']}; font-weight: 700; }}
            QPushButton {{ background: {COLORS['tile']}; border: 1px solid {COLORS['line']}; border-radius: 11px; padding: 10px 14px; }}
            QPushButton:hover {{ border-color: {COLORS['accent']}; }}
            QPushButton[primary=true] {{ background: {COLORS['accent']}; color: #fffaf2; font-weight: 800; border: 0; }}
            QPushButton#navigation {{ background: #353c42; color: #f3f4f4; border: 0; text-align: left; padding: 14px; font-weight: 700; }}
            QPushButton#navigation[selected=true] {{ background: {COLORS['accent']}; color: #fffaf2; }}
            QPushButton:disabled {{ color: {COLORS['muted']}; }}
            QListWidget, QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox {{ background: {COLORS['tile']}; border: 1px solid {COLORS['line']}; border-radius: 7px; padding: 7px; }}
            QComboBox {{ min-height: 24px; }}
            QListWidget {{ background: #f4f5f5; padding: 6px; }}
            QListWidget::item {{ padding: 0; border: 0; }}
            QFrame#queueRow {{ background: #ffffff; border: 1px solid {COLORS['line']}; border-radius: 10px; }}
            QFrame#queueRow[selected=true] {{ background: #fff1eb; border-color: {COLORS['accent']}; }}
            QLabel#queueTitle {{ color: {COLORS['ink']}; font-weight: 700; }}
            QLabel#queueDetail {{ color: {COLORS['muted']}; font-size: 8pt; }}
            QPushButton#queueRemove {{ background: transparent; color: {COLORS['muted']}; border: 0; font-size: 18pt; padding: 0; }}
            QPushButton#queueRemove:hover {{ background: #fde4dc; color: #b83c1d; }}
            QScrollArea {{ border: 0; }}
            QSlider::groove:horizontal {{ background: {COLORS['line']}; height: 5px; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: {COLORS['accent']}; width: 15px; margin: -5px 0; border-radius: 7px; }}
            QProgressBar {{ border: 0; background: {COLORS['tile']}; border-radius: 5px; text-align: center; }}
            QProgressBar::chunk {{ background: {COLORS['accent']}; border-radius: 5px; }}
        """)

    def _navigate(self, number: int) -> None:
        self.pages.setCurrentIndex(number)
        for index, button in enumerate(self.nav_buttons):
            button.setProperty("selected", index == number)
            button.style().unpolish(button)
            button.style().polish(button)

    def _mode_changed(self) -> None:
        mode = self.signal_mode.currentData()
        self.farbberuhigung.setEnabled(mode == "analog")
        self.blockpflege.setEnabled(mode == "digital")
        if hasattr(self, "learning_folder"):
            name = "HDZero" if mode == "digital" else "Analog"
            self.learning_folder.setPlaceholderText(f"Choose a {name} footage folder")

    def _move_divider(self, number: int) -> None:
        self.canvas.divider = number
        self.canvas.update()

    def _selection_changed(self, number: int) -> None:
        for index, widget in enumerate(self.queue_rows):
            widget.setProperty("selected", index == number)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.cut_button.setEnabled(number >= 0 and not self._export_running())
        if number < 0 or number >= len(self.clips):
            self.meta.setText("No footage yet")
            return
        try:
            info = inspect_video(self.clips[number])
            start, end = self.cuts.get(self.clips[number], (0.0, info.duration))
            self.meta.setText(f"{info.width} × {info.height}  ·  {info.duration:.1f} s\n"
                              f"{'audio' if info.has_audio else 'silent'} · cut {start:.1f}–{end:.1f} s")
        except Exception as exc:
            self.meta.setText(str(exc))
        self.canvas.original = QPixmap()
        self.canvas.treated = QPixmap()
        self.canvas.heatmap = QPixmap()
        self.map_button.setEnabled(False)
        self.canvas.update()

    def _recipe(self) -> Arbeitsrezept:
        recipe = Arbeitsrezept(signalart=self.signal_mode.currentData(), farbberuhigung=self.farbberuhigung.value(),
                        blockpflege=self.blockpflege.value(), kantendetail=self.kantendetail.value(),
                        bewegungsspur=self.bewegungsspur.value(),
                        vergroesserung_prozent=self.magnification_choice.currentData(),
                        untertiteltext=self.lower_caption_entry.text(),
                        kompressionswert=self.compression_grade_spin.value(),
                        dateiziel_mb=self.size_limit.value())
        recipe.validate()
        return recipe

    def add_clips(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(self, "Choose footage", "",
                                                "Videos (*.mp4 *.mov *.mkv *.avi *.ts *.m4v);;All files (*)")
        self._add_paths([Path(name) for name in names])

    def _add_paths(self, paths: list[Path]) -> None:
        if self._export_running():
            return
        for item in paths:
            if item.is_file() and item not in self.clips:
                self.clips.append(item)
                entry = QListWidgetItem()
                entry.setSizeHint(QSize(0, 64))
                self.listing.addItem(entry)
                row_widget = QueueRow(item, lambda checked=False, clip=item: self.remove_clip(clip))
                self.listing.setItemWidget(entry, row_widget)
                self.queue_rows.append(row_widget)
        self._update_queue_summary()
        if self.listing.currentRow() < 0 and self.clips:
            self.listing.setCurrentRow(0)

    def _export_running(self) -> bool:
        return bool(self.export_job and self.export_job.isRunning())

    def _update_queue_summary(self) -> None:
        count = len(self.clips)
        self.queue_count.setText(f"{count} clip{'s' if count != 1 else ''}")
        self.queue_hint.setText("Ready to trim" if count else "Add footage to begin")
        self.delivery_summary.setText(f"{count} clip{'s' if count != 1 else ''} ready to export")

    def remove_clip(self, clip: Path | None = None) -> None:
        if self._export_running():
            return
        row = self.clips.index(clip) if clip in self.clips else self.listing.currentRow()
        if row < 0 or row >= len(self.clips):
            return
        self.listing.blockSignals(True)
        entry = self.listing.takeItem(row)
        del entry
        removed = self.clips.pop(row)
        self.queue_rows.pop(row)
        self.cuts.pop(removed, None)
        self.listing.blockSignals(False)
        next_row = min(row, len(self.clips) - 1)
        self.listing.setCurrentRow(next_row)
        self._selection_changed(next_row)
        self._update_queue_summary()

    def open_cut_room(self) -> None:
        row = self.listing.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select footage", "Add and select a clip first.")
            return
        clip = self.clips[row]
        try:
            metadata = inspect_video(clip)
            start, end = self.cuts.get(clip, (0.0, metadata.duration))
            room = CutRoom(clip, metadata.duration, start, end, self)
            if room.exec():
                self.cuts[clip] = (room.track.entry, room.track.exit)
                self._selection_changed(row)
        except Exception as exc:
            self._show_error(str(exc))

    def choose_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose output folder", self.folder.text())
        if selected:
            self.folder.setText(selected)

    def choose_learning_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose damaged footage folder",
                                                    self.learning_folder.text())
        if selected:
            self.learning_folder.setText(selected)

    def start_learning(self) -> None:
        if self.learning_job and self.learning_job.isRunning():
            self.learning_job.stop.set()
            self.model_label.setText("Stopping training …")
            return
        signalart = self.signal_mode.currentData()
        if signalart == "neutral":
            QMessageBox.information(self, "Choose signal", "Select Analog or HDZero before learning.")
            return
        try:
            if not self.learning_folder.text().strip():
                raise ValueError("Choose a folder with matching damaged footage")
            folder = Path(self.learning_folder.text()).expanduser()
            self.learning_job = LearningWorker(folder, signalart, self.learning_minutes.value() * 60)
            self.learning_job.sampled.connect(self._learning_progress)
            self.learning_job.learned.connect(self._learning_ready)
            self.learning_job.failed.connect(self._show_error)
            self.learning_job.finished.connect(lambda: self.learn_button.setText("Train signal model"))
            self.learn_button.setText("Stop training")
            self.model_label.setText("Sampling footage …")
            self.learning_job.start()
        except Exception as exc:
            self._show_error(str(exc))

    def _learning_progress(self, count: int, limit: int) -> None:
        self.model_label.setText(f"Measured {count} frames · up to {limit // 60} min")

    def _learning_ready(self, fingerprint: Signalsignatur) -> None:
        self.fingerprint = fingerprint
        store = Path.home() / ".flugbild" / f"{fingerprint.signalart}-signal.json"
        fingerprint.save(store)
        self.model_label.setText(f"{fingerprint.signalart.title()} model · {fingerprint.stichproben} frames "
                                 f"from {fingerprint.quelldateien} files\nSaved: {store}")

    def load_model(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Load signal model", str(Path.home() / ".flugbild"),
                                              "Signal models (*.json)")
        if not name:
            return
        try:
            self.fingerprint = Signalsignatur.load(Path(name))
            match = self.signal_mode.findData(self.fingerprint.signalart)
            if match >= 0:
                self.signal_mode.setCurrentIndex(match)
            self.model_label.setText(f"{self.fingerprint.signalart.title()} model · "
                                     f"{self.fingerprint.stichproben} frames")
        except Exception as exc:
            self._show_error(str(exc))

    def preview(self) -> None:
        row = self.listing.currentRow()
        if row < 0:
            QMessageBox.information(self, "No footage", "Add a video first.")
            return
        if self.preview_job and self.preview_job.isRunning():
            return
        try:
            info = inspect_video(self.clips[row])
            recipe = self._recipe()
            moment = min(info.duration * self.moment.value() / 100, max(0, info.duration - 0.1))
            self.status_line.setText("Rendering sample …")
            active_model = self.fingerprint if self.fingerprint and self.fingerprint.signalart == recipe.signalart else None
            self.preview_job = PreviewWorker(self.clips[row], moment, recipe,
                                             Path(self.scratch.name), active_model)
            self.preview_job.ready.connect(self._preview_ready)
            self.preview_job.failed.connect(self._show_error)
            self.preview_job.start()
        except Exception as exc:
            self._show_error(str(exc))

    def _preview_ready(self, left: str, right: str, heatmap: str) -> None:
        self.canvas.set_images(Path(left), Path(right), Path(heatmap))
        self.map_button.setEnabled(True)
        self.map_button.setText("Show change map")
        self.status_line.setText("Sample ready · move the divider to compare")

    def _toggle_map(self) -> None:
        self.canvas.map_visible = not self.canvas.map_visible
        self.canvas.update()
        self.map_button.setText("Show result" if self.canvas.map_visible else "Show change map")

    def start_export(self) -> None:
        if not self.clips:
            QMessageBox.information(self, "No footage", "Add videos first.")
            return
        try:
            recipe = self._recipe()
            destination = Path(self.folder.text()).expanduser()
            if not self.folder.text().strip():
                raise ValueError("Choose an output folder")
            destination.mkdir(parents=True, exist_ok=True)
            for clip in self.clips:
                from .processor import output_duration
                start, end = self.cuts.get(clip, (0.0, 0.0))
                output_duration(inspect_video(clip), replace(recipe, schnittbeginn=start, schnittende=end))
            active_model = self.fingerprint if self.fingerprint and self.fingerprint.signalart == recipe.signalart else None
            self.export_job = ExportWorker(self.clips.copy(), destination, recipe, self.cuts.copy(), active_model)
            self.export_job.advanced.connect(self._export_progress)
            self.export_job.finished_item.connect(self._export_item)
            self.export_job.failed.connect(self._show_error)
            self.export_job.finished.connect(self._export_done)
            self.export_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.progress.setValue(0)
            self.export_status.setText(f"Preparing {len(self.clips)} clips…")
            self.add_button.setEnabled(False)
            self.cut_button.setEnabled(False)
            for row_widget in self.queue_rows:
                row_widget.remove_button.setEnabled(False)
            self.export_job.start()
        except Exception as exc:
            self._show_error(str(exc))

    def _export_progress(self, index: int, count: int, fraction: float) -> None:
        self.progress.setValue(round((index + fraction) * 100 / count))
        self.export_status.setText(f"Rendering clip {index + 1} of {count} · {fraction:.0%}")
        self.status_line.setText(f"Clip {index + 1} of {count} · {fraction:.0%}")

    def _export_item(self, index: int, success: bool, message: str) -> None:
        if index < len(self.queue_rows):
            self.queue_rows[index].title.setText(
                f"{'✓' if success else '✕'}  {self.clips[index].name}")
        if not success:
            self.export_status.setText("Export failed: " + message[:120])
            self._show_error(message)

    def _export_done(self) -> None:
        self.export_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.add_button.setEnabled(True)
        self.cut_button.setEnabled(self.listing.currentRow() >= 0)
        for row_widget in self.queue_rows:
            row_widget.remove_button.setEnabled(True)
        if self.export_job and self.export_job.stop.is_set():
            self.status_line.setText("Run canceled")
            self.export_status.setText("Export canceled")
        else:
            self.status_line.setText("Run complete")
            self.export_status.setText("Export complete")

    def cancel_export(self) -> None:
        if self.export_job:
            self.export_job.stop.set()

    def _show_error(self, message: str) -> None:
        self.status_line.setText("Error: " + message[:140])
        QMessageBox.warning(self, "Processing", message)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        self._add_paths([Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()])

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.export_job and self.export_job.isRunning():
            self.export_job.stop.set()
            self.export_job.wait(12000)
            if self.export_job.isRunning():
                event.ignore()
                return
        if self.preview_job and self.preview_job.isRunning():
            self.preview_job.wait(12000)
            if self.preview_job.isRunning():
                event.ignore()
                return
        if self.learning_job and self.learning_job.isRunning():
            self.learning_job.stop.set()
            self.learning_job.wait(12000)
            if self.learning_job.isRunning():
                event.ignore()
                return
        self.scratch.cleanup()
        event.accept()


def launch() -> None:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("FPV DVR Cleaner")
    font_path = Path(__file__).parent / "assets" / "Inter.ttf"
    QFontDatabase.addApplicationFont(str(font_path))
    window = Workbench()
    window.show()
    app.exec()
