"""GUI display for live monitoring."""

import logging
import sys
import queue
import threading
from pathlib import Path
from typing import Optional
import numpy as np
import cv2

try:
    from PyQt6.QtWidgets import (
        QMainWindow, QLabel, QVBoxLayout, QHBoxLayout, QWidget, QStatusBar,
        QCheckBox, QPushButton, QDialog, QFormLayout, QLineEdit, QSpinBox,
        QDoubleSpinBox, QComboBox, QGroupBox, QDialogButtonBox, QColorDialog,
        QFileDialog, QScrollArea, QSlider, QSizePolicy,
    )
    from PyQt6.QtGui import QImage, QPixmap, QFont, QColor
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
    _PYQT6 = True
except ImportError:
    QMainWindow = object
    _PYQT6 = False
    logger_tmp = logging.getLogger(__name__)
    logger_tmp.error("PyQt6 not installed, GUI will not be available")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audio preview thread
# ---------------------------------------------------------------------------

class AudioPreviewThread(threading.Thread):
    """Plays audio from the GUI audio queue through the system output device."""

    def __init__(self, audio_queue: queue.Queue):
        super().__init__(daemon=True, name="audio-preview")
        self._queue = audio_queue
        self._play = False
        self._running = True
        self._sample_rate: Optional[int] = None
        self._channels: int = 2

    def set_playing(self, play: bool):
        self._play = play

    def stop(self):
        self._running = False

    def run(self):
        try:
            import sounddevice as sd
        except ImportError:
            logger.warning("sounddevice not installed — audio preview unavailable. "
                           "Install with: pip install sounddevice")
            # Still drain the queue so it doesn't back up
            self._drain_loop_no_playback()
            return

        stream = None
        try:
            while self._running:
                try:
                    sample = self._queue.get(timeout=0.05)
                except queue.Empty:
                    continue

                if not self._play:
                    continue

                # Lazily open stream on first sample with matching rate
                sr = sample.sample_rate
                if stream is None or self._sample_rate != sr:
                    if stream is not None:
                        stream.close()
                    self._sample_rate = sr
                    try:
                        stream = sd.OutputStream(
                            samplerate=sr,
                            channels=2,
                            dtype='float32',
                            latency='low',
                        )
                        stream.start()
                    except Exception as e:
                        logger.error(f"Failed to open audio output: {e}")
                        stream = None
                        continue

                try:
                    # Mix to stereo float32 — take first two channels
                    data = sample.data
                    nchan = data.shape[1] if data.ndim == 2 else 1
                    if nchan >= 2:
                        stereo = data[:, :2]
                    else:
                        mono = data[:, 0] if data.ndim == 2 else data
                        stereo = np.stack([mono, mono], axis=1)

                    # Normalise to float32 [-1, 1]
                    dt = stereo.dtype
                    if dt == np.int16:
                        audio_f32 = stereo.astype(np.float32) / 32768.0
                    elif dt == np.int32:
                        audio_f32 = stereo.astype(np.float32) / 2147483648.0
                    else:
                        audio_f32 = stereo.astype(np.float32)

                    stream.write(np.ascontiguousarray(audio_f32))
                except Exception as e:
                    logger.debug(f"Audio write error: {e}")

        except Exception as e:
            logger.error(f"Audio preview thread error: {e}", exc_info=True)
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

    def _drain_loop_no_playback(self):
        while self._running:
            try:
                self._queue.get(timeout=0.1)
            except queue.Empty:
                pass


# ---------------------------------------------------------------------------
# Subtitle Settings Dialog
# ---------------------------------------------------------------------------

class SubtitleConfigDialog(QDialog):
    """Non-modal dialog for editing subtitle/overlay settings live."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Subtitle Settings")
        self.setModal(False)
        self.resize(500, 600)

        # Lazy import to avoid circular dependency
        from . import config_subtitles
        self._cfg = config_subtitles

        self._color_text = self._cfg.TEXT_COLOR
        self._color_bg = self._cfg.TEXT_BG_COLOR

        self._build_ui()
        self._load_values()

    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)
        vbox = QVBoxLayout(container)

        vbox.addWidget(self._build_source_group())
        vbox.addWidget(self._build_font_group())
        vbox.addWidget(self._build_appearance_group())
        vbox.addWidget(self._build_position_group())
        vbox.addStretch()

        # Buttons
        btn_box = QHBoxLayout()
        self._btn_apply = QPushButton("Apply")
        self._btn_save = QPushButton("Save to file")
        self._btn_close = QPushButton("Close")
        btn_box.addStretch()
        btn_box.addWidget(self._btn_apply)
        btn_box.addWidget(self._btn_save)
        btn_box.addWidget(self._btn_close)
        root.addLayout(btn_box)

        self._btn_apply.clicked.connect(self._apply)
        self._btn_save.clicked.connect(self._save)
        self._btn_close.clicked.connect(self.close)

    def _build_source_group(self) -> QGroupBox:
        grp = QGroupBox("Text Source")
        form = QFormLayout(grp)

        row = QHBoxLayout()
        self._edit_file = QLineEdit()
        btn_browse_file = QPushButton("…")
        btn_browse_file.setFixedWidth(30)
        btn_browse_file.clicked.connect(self._browse_text_file)
        row.addWidget(self._edit_file)
        row.addWidget(btn_browse_file)
        form.addRow("Text file:", row)

        self._spin_interval = QSpinBox()
        self._spin_interval.setRange(1, 1000)
        self._spin_interval.setSuffix(" frames")
        form.addRow("Check interval:", self._spin_interval)

        return grp

    def _build_font_group(self) -> QGroupBox:
        grp = QGroupBox("Font")
        form = QFormLayout(grp)

        row = QHBoxLayout()
        self._edit_font_path = QLineEdit()
        btn_browse_font = QPushButton("…")
        btn_browse_font.setFixedWidth(30)
        btn_browse_font.clicked.connect(self._browse_font)
        row.addWidget(self._edit_font_path)
        row.addWidget(btn_browse_font)
        form.addRow("Font file:", row)

        self._spin_font_size = QSpinBox()
        self._spin_font_size.setRange(6, 300)
        self._spin_font_size.setSuffix(" px")
        form.addRow("Font size:", self._spin_font_size)

        checks = QHBoxLayout()
        self._chk_bold = QCheckBox("Bold")
        self._chk_italic = QCheckBox("Italic")
        checks.addWidget(self._chk_bold)
        checks.addWidget(self._chk_italic)
        checks.addStretch()
        form.addRow("Style:", checks)

        return grp

    def _build_appearance_group(self) -> QGroupBox:
        grp = QGroupBox("Appearance")
        form = QFormLayout(grp)

        self._btn_text_color = QPushButton()
        self._btn_text_color.clicked.connect(self._pick_text_color)
        form.addRow("Text color:", self._btn_text_color)

        self._btn_bg_color = QPushButton()
        self._btn_bg_color.clicked.connect(self._pick_bg_color)
        form.addRow("Background color:", self._btn_bg_color)

        alpha_row = QHBoxLayout()
        self._slider_alpha = QSlider(Qt.Orientation.Horizontal)
        self._slider_alpha.setRange(0, 255)
        self._lbl_alpha = QLabel("200")
        self._lbl_alpha.setFixedWidth(30)
        self._slider_alpha.valueChanged.connect(
            lambda v: self._lbl_alpha.setText(str(v))
        )
        alpha_row.addWidget(self._slider_alpha)
        alpha_row.addWidget(self._lbl_alpha)
        form.addRow("BG alpha:", alpha_row)

        self._spin_pad_x = QSpinBox()
        self._spin_pad_x.setRange(0, 200)
        self._spin_pad_x.setSuffix(" px")
        form.addRow("Padding X:", self._spin_pad_x)

        self._spin_pad_y = QSpinBox()
        self._spin_pad_y.setRange(0, 200)
        self._spin_pad_y.setSuffix(" px")
        form.addRow("Padding Y:", self._spin_pad_y)

        return grp

    def _build_position_group(self) -> QGroupBox:
        grp = QGroupBox("Position")
        form = QFormLayout(grp)

        self._combo_align = QComboBox()
        self._combo_align.addItems(["left", "center", "right"])
        form.addRow("Alignment:", self._combo_align)

        self._spin_offset = QSpinBox()
        self._spin_offset.setRange(0, 2000)
        self._spin_offset.setSuffix(" px")
        form.addRow("Offset from bottom:", self._spin_offset)

        self._spin_spacing = QDoubleSpinBox()
        self._spin_spacing.setRange(0.5, 5.0)
        self._spin_spacing.setSingleStep(0.1)
        self._spin_spacing.setDecimals(2)
        form.addRow("Line spacing:", self._spin_spacing)

        return grp

    # ------------------------------------------------------------------

    def _load_values(self):
        cfg = self._cfg
        self._edit_file.setText(cfg.TEXT_FILE)
        self._spin_interval.setValue(cfg.TEXT_CHECK_INTERVAL)
        self._edit_font_path.setText(cfg.TEXT_FONT_PATH)
        self._spin_font_size.setValue(cfg.TEXT_FONT_SIZE)
        self._chk_bold.setChecked(cfg.TEXT_FONT_BOLD)
        self._chk_italic.setChecked(cfg.TEXT_FONT_ITALIC)

        self._color_text = tuple(cfg.TEXT_COLOR)
        self._color_bg = tuple(cfg.TEXT_BG_COLOR)
        self._update_color_buttons()

        self._slider_alpha.setValue(cfg.TEXT_BG_ALPHA)
        self._spin_pad_x.setValue(cfg.TEXT_BG_PADDING_X)
        self._spin_pad_y.setValue(cfg.TEXT_BG_PADDING_Y)

        idx = self._combo_align.findText(cfg.TEXT_ALIGN)
        if idx >= 0:
            self._combo_align.setCurrentIndex(idx)
        self._spin_offset.setValue(cfg.TEXT_OFFSET_BOTTOM)
        self._spin_spacing.setValue(cfg.TEXT_LINE_SPACING)

    def _update_color_buttons(self):
        r, g, b = self._color_text
        self._btn_text_color.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); color: {'black' if r+g+b > 380 else 'white'};"
        )
        self._btn_text_color.setText(f"rgb({r}, {g}, {b})")

        r, g, b = self._color_bg
        self._btn_bg_color.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); color: {'black' if r+g+b > 380 else 'white'};"
        )
        self._btn_bg_color.setText(f"rgb({r}, {g}, {b})")

    def _browse_text_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select text file", self._edit_file.text(), "Text files (*.txt);;All files (*)"
        )
        if path:
            self._edit_file.setText(path)

    def _browse_font(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select font file", self._edit_font_path.text(),
            "Font files (*.ttf *.otf);;All files (*)"
        )
        if path:
            self._edit_font_path.setText(path)

    def _pick_text_color(self):
        r, g, b = self._color_text
        color = QColorDialog.getColor(QColor(r, g, b), self, "Text Color")
        if color.isValid():
            self._color_text = (color.red(), color.green(), color.blue())
            self._update_color_buttons()

    def _pick_bg_color(self):
        r, g, b = self._color_bg
        color = QColorDialog.getColor(QColor(r, g, b), self, "Background Color")
        if color.isValid():
            self._color_bg = (color.red(), color.green(), color.blue())
            self._update_color_buttons()

    # ------------------------------------------------------------------

    def _collect_values(self) -> dict:
        return {
            "TEXT_FILE": self._edit_file.text(),
            "TEXT_CHECK_INTERVAL": self._spin_interval.value(),
            "TEXT_FONT_PATH": self._edit_font_path.text(),
            "TEXT_FONT_SIZE": self._spin_font_size.value(),
            "TEXT_FONT_BOLD": self._chk_bold.isChecked(),
            "TEXT_FONT_ITALIC": self._chk_italic.isChecked(),
            "TEXT_COLOR": self._color_text,
            "TEXT_BG_COLOR": self._color_bg,
            "TEXT_BG_ALPHA": self._slider_alpha.value(),
            "TEXT_BG_PADDING_X": self._spin_pad_x.value(),
            "TEXT_BG_PADDING_Y": self._spin_pad_y.value(),
            "TEXT_ALIGN": self._combo_align.currentText(),
            "TEXT_OFFSET_BOTTOM": self._spin_offset.value(),
            "TEXT_LINE_SPACING": self._spin_spacing.value(),
        }

    def _apply(self):
        """Push values into config_subtitles module — overlay picks them up next frame."""
        vals = self._collect_values()
        cfg = self._cfg
        for k, v in vals.items():
            setattr(cfg, k, v)
        cfg._config_version += 1
        logger.info("Subtitle settings applied (version %d)", cfg._config_version)

    def _save(self):
        """Write current values to config_subtitles.py, then apply."""
        self._apply()
        cfg = self._cfg
        # Locate the file via the module's __file__ attribute
        try:
            file_path = Path(cfg.__file__)
        except AttributeError:
            logger.error("Cannot locate config_subtitles.py — save failed")
            return

        content = self._generate_file(cfg)
        try:
            file_path.write_text(content, encoding="utf-8")
            logger.info("Subtitle settings saved to %s", file_path)
        except Exception as e:
            logger.error("Failed to write %s: %s", file_path, e)

    def _generate_file(self, cfg) -> str:
        r_t, g_t, b_t = cfg.TEXT_COLOR
        r_b, g_b, b_b = cfg.TEXT_BG_COLOR
        return (
            '"""Subtitle/text overlay configuration.\n\n'
            'Edit values here or use the Subtitle Settings dialog in the GUI.\n'
            'The running overlay picks up changes applied via the dialog without restart.\n'
            '"""\n\n'
            '# Incremented by the Subtitle Settings dialog on Apply/Save — overlay detects this\n'
            f'_config_version: int = {cfg._config_version}\n\n'
            '# Text file source\n'
            f'TEXT_FILE: str = r"{cfg.TEXT_FILE}"\n'
            f'TEXT_CHECK_INTERVAL: int = {cfg.TEXT_CHECK_INTERVAL}  '
            '# Check text file for changes every N frames\n\n'
            '# Font\n'
            f'TEXT_FONT_PATH: str = r"{cfg.TEXT_FONT_PATH}"\n'
            f'TEXT_FONT_SIZE: int = {cfg.TEXT_FONT_SIZE}\n'
            f'TEXT_FONT_BOLD: bool = {cfg.TEXT_FONT_BOLD}\n'
            f'TEXT_FONT_ITALIC: bool = {cfg.TEXT_FONT_ITALIC}\n\n'
            '# Colors (R, G, B)\n'
            f'TEXT_COLOR: tuple = ({r_t}, {g_t}, {b_t})   # White text\n'
            f'TEXT_BG_COLOR: tuple = ({r_b}, {g_b}, {b_b})      # Black background\n'
            f'TEXT_BG_ALPHA: int = {cfg.TEXT_BG_ALPHA}              # 0 = transparent, 255 = opaque\n\n'
            '# Padding inside background box (pixels)\n'
            f'TEXT_BG_PADDING_X: int = {cfg.TEXT_BG_PADDING_X}\n'
            f'TEXT_BG_PADDING_Y: int = {cfg.TEXT_BG_PADDING_Y}\n\n'
            '# Position\n'
            f'TEXT_OFFSET_BOTTOM: int = {cfg.TEXT_OFFSET_BOTTOM}    # Pixels from bottom edge\n'
            f'TEXT_ALIGN: str = "{cfg.TEXT_ALIGN}"      # "left", "center", or "right"\n'
            f'TEXT_LINE_SPACING: float = {cfg.TEXT_LINE_SPACING}  # Line height multiplier\n'
        )


# ---------------------------------------------------------------------------
# Video frame label
# ---------------------------------------------------------------------------

class FrameLabel(QLabel):
    """Custom label for displaying video frames."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(640, 480)
        self.setStyleSheet("background-color: black; border: 1px solid gray;")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_image(self, image: np.ndarray):
        if image is None or image.size == 0:
            return
        try:
            h, w = image.shape[:2]
            if image.dtype != np.uint8:
                image = (image * 255).astype(np.uint8)
            q_img = QImage(image.data, w, h, 3 * w, QImage.Format.Format_RGB888)
            pixmap = QPixmap.fromImage(q_img)
            scaled = pixmap.scaledToWidth(self.width(), Qt.TransformationMode.SmoothTransformation)
            self.setPixmap(scaled)
        except Exception as e:
            logger.error(f"Error displaying image: {e}")


# ---------------------------------------------------------------------------
# Signal helper
# ---------------------------------------------------------------------------

class FrameSignal(QObject):
    frame_ready = pyqtSignal(object)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class GUIDisplay(QMainWindow):
    """Main GUI window for live monitoring."""

    def __init__(
        self,
        frame_queue: queue.Queue,
        update_rate: int = 30,
        audio_queue: Optional[queue.Queue] = None,
    ):
        super().__init__()
        self.frame_queue = frame_queue
        self.update_rate = update_rate
        self.current_frame = None
        self.frame_count = 0

        self.signal_emitter = FrameSignal()
        self.signal_emitter.frame_ready.connect(self._update_display)

        # Audio preview
        self._audio_thread: Optional[AudioPreviewThread] = None
        if audio_queue is not None:
            self._audio_thread = AudioPreviewThread(audio_queue)
            self._audio_thread.start()

        self._subtitle_dialog: Optional[SubtitleConfigDialog] = None

        self._setup_ui()

        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self._fetch_frame)
        self.update_timer.start(int(1000 / update_rate))

        logger.info(f"GUI initialized with {update_rate} Hz update rate")

    # ------------------------------------------------------------------

    def _setup_ui(self):
        self.setWindowTitle("FFCapture - Live Monitoring")
        self.setGeometry(100, 100, 1280, 720)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Video frame display
        self.frame_label = FrameLabel()
        layout.addWidget(self.frame_label)

        # Control bar below video
        ctrl = QHBoxLayout()

        self._chk_audio = QCheckBox("Play audio in preview")
        self._chk_audio.setChecked(False)
        self._chk_audio.stateChanged.connect(self._on_audio_toggle)
        ctrl.addWidget(self._chk_audio)

        # Disable audio checkbox when sounddevice is not available
        try:
            import sounddevice  # noqa: F401
        except ImportError:
            self._chk_audio.setEnabled(False)
            self._chk_audio.setToolTip("Install sounddevice to enable audio preview")

        ctrl.addStretch()

        btn_subtitles = QPushButton("Subtitle Settings…")
        btn_subtitles.clicked.connect(self._open_subtitle_dialog)
        ctrl.addWidget(btn_subtitles)

        layout.addLayout(ctrl)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._update_status()

    # ------------------------------------------------------------------

    def _on_audio_toggle(self, state):
        if self._audio_thread is not None:
            playing = (state == Qt.CheckState.Checked.value or state == 2)
            self._audio_thread.set_playing(playing)

    def _open_subtitle_dialog(self):
        if self._subtitle_dialog is None or not self._subtitle_dialog.isVisible():
            self._subtitle_dialog = SubtitleConfigDialog(self)
            self._subtitle_dialog.show()
        else:
            self._subtitle_dialog.raise_()
            self._subtitle_dialog.activateWindow()

    # ------------------------------------------------------------------

    def _fetch_frame(self):
        try:
            while True:
                try:
                    frame = self.frame_queue.get(block=False)
                    self.current_frame = frame
                    self.frame_count += 1
                except queue.Empty:
                    break
            if self.current_frame is not None:
                self.signal_emitter.frame_ready.emit(self.current_frame)
        except Exception as e:
            logger.error(f"Error fetching frame: {e}")

    def _update_display(self, frame):
        try:
            if frame is None:
                return
            frame_data = frame.data
            if frame.format == 'BGR24':
                frame_data = cv2.cvtColor(frame_data, cv2.COLOR_BGR2RGB)
            elif frame.format == 'YUV420P':
                frame_data = cv2.cvtColor(frame_data, cv2.COLOR_YUV2RGB_I420)
            if frame_data.dtype != np.uint8:
                frame_data = np.clip(frame_data * 255, 0, 255).astype(np.uint8)
            self.frame_label.set_image(frame_data)
            self._update_status()
        except Exception as e:
            logger.error(f"Error updating display: {e}")

    def _update_status(self):
        if self.current_frame:
            status_text = (
                f"Frame: {self.frame_count} | "
                f"Resolution: {self.current_frame.width}x{self.current_frame.height} | "
                f"Format: {self.current_frame.format} | "
                f"FPS: {self.current_frame.framerate[0]}/{self.current_frame.framerate[1]}"
            )
        else:
            status_text = "Waiting for frames…"
        self.status_bar.showMessage(status_text)

    def closeEvent(self, event):
        logger.info("Closing GUI")
        self.update_timer.stop()
        if self._audio_thread is not None:
            self._audio_thread.stop()
        if self._subtitle_dialog is not None:
            self._subtitle_dialog.close()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Module-level helpers (unchanged public API)
# ---------------------------------------------------------------------------

_qt_app = None


def create_gui(
    frame_queue: queue.Queue,
    update_rate: int = 30,
    audio_queue: Optional[queue.Queue] = None,
) -> Optional[GUIDisplay]:
    global _qt_app
    if not _PYQT6:
        return None
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer as _QTimer
        _qt_app = QApplication.instance()
        if _qt_app is None:
            _qt_app = QApplication(sys.argv)

        gui = GUIDisplay(frame_queue, update_rate, audio_queue=audio_queue)

        def show_gui():
            try:
                gui.show()
                logger.info("GUI window shown")
            except Exception as e:
                logger.error(f"Failed to show GUI window: {e}", exc_info=True)

        _QTimer.singleShot(100, show_gui)
        logger.info("GUI created and scheduled to show")
        return gui
    except Exception as e:
        logger.error(f"Failed to create GUI: {e}", exc_info=True)
        return None


def get_qt_app():
    global _qt_app
    if _qt_app is not None:
        return _qt_app
    try:
        from PyQt6.QtWidgets import QApplication
        _qt_app = QApplication.instance()
        if _qt_app is None:
            _qt_app = QApplication(sys.argv)
        return _qt_app
    except Exception as e:
        logger.error(f"Failed to get/create QApplication: {e}", exc_info=True)
        return None
