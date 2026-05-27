"""Text overlay engine for video frames — with cached rendering for low CPU usage."""

import logging
import os
import numpy as np
from pathlib import Path
from typing import Optional, Tuple
import cv2
from PIL import Image, ImageDraw, ImageFont

from .capture import Frame

logger = logging.getLogger(__name__)


class TextOverlay:
    """
    Applies a subtitle-style text overlay to video frames.

    Text is read from a file (last line only).  The rendered text is cached as a
    pre-composited BGRA numpy array and only re-rendered when the text or any
    appearance setting changes, so per-frame cost is a single numpy paste.

    Pass the config_subtitles module so that changes applied via the Subtitle
    Settings dialog are picked up live (detected via _config_version).
    """

    def __init__(self, config_sub):
        self._config = config_sub
        self._config_version = -1   # Force first-run apply

        # Runtime values — populated by _apply_config()
        self.text_file: Optional[Path] = None
        self.check_interval: int = 30
        self.font_path: str = ""
        self.font_size: int = 36
        self.color: Tuple = (255, 255, 255)
        self.bg_color: Tuple = (0, 0, 0)
        self.bg_alpha: int = 200
        self.bg_padding_x: int = 20
        self.bg_padding_y: int = 8
        self.offset_bottom: int = 40
        self.align: str = "center"
        self.line_spacing: float = 1.2

        self.current_text: str = ""
        self.last_mtime: Optional[float] = None
        self.frame_count: int = 0

        # Render cache
        self._cache_text: Optional[str] = None
        self._cache_dims: Optional[Tuple[int, int]] = None
        self._cache_overlay: Optional[np.ndarray] = None
        self._cache_roi: Optional[Tuple[int, int, int, int]] = None

        self._font: Optional[ImageFont.FreeTypeFont] = None

        self._apply_config()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(self, frame: Frame) -> Frame:
        """Blend cached subtitle overlay onto frame. Very low CPU per call."""
        # Pick up changes from the settings dialog immediately
        if self._config._config_version != self._config_version:
            self._apply_config()

        if self.frame_count % self.check_interval == 0:
            self._update_text()
        self.frame_count += 1

        if not self.current_text:
            return frame

        if (self.current_text != self._cache_text or
                (frame.width, frame.height) != self._cache_dims):
            self._render_cache(frame.width, frame.height)

        if self._cache_overlay is None:
            return frame

        self._blend(frame)
        return frame

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_config(self):
        """Read current values from the config_subtitles module."""
        cfg = self._config

        new_file = Path(cfg.TEXT_FILE)
        if self.text_file != new_file:
            self.text_file = new_file
            self.last_mtime = None   # Force re-read from new path
            self.current_text = ""

        font_changed = (
            self.font_path != cfg.TEXT_FONT_PATH or
            self.font_size != cfg.TEXT_FONT_SIZE
        )

        self.check_interval = cfg.TEXT_CHECK_INTERVAL
        self.font_path = cfg.TEXT_FONT_PATH
        self.font_size = cfg.TEXT_FONT_SIZE
        self.color = cfg.TEXT_COLOR
        self.bg_color = cfg.TEXT_BG_COLOR
        self.bg_alpha = cfg.TEXT_BG_ALPHA
        self.bg_padding_x = cfg.TEXT_BG_PADDING_X
        self.bg_padding_y = cfg.TEXT_BG_PADDING_Y
        self.offset_bottom = cfg.TEXT_OFFSET_BOTTOM
        self.align = cfg.TEXT_ALIGN
        self.line_spacing = cfg.TEXT_LINE_SPACING
        self._config_version = cfg._config_version

        if font_changed or self._font is None:
            self._font = self._load_font()

        # Invalidate render cache so next frame re-renders with new settings
        self._cache_text = None
        self._cache_dims = None
        self._cache_overlay = None

        if self.text_file and not self.text_file.exists():
            logger.warning(f"Text file does not exist: {self.text_file}")

    def _load_font(self) -> ImageFont.FreeTypeFont:
        for path in [self.font_path,
                     r"C:\Windows\Fonts\arial.ttf",
                     r"C:\Windows\Fonts\segoeui.ttf"]:
            try:
                return ImageFont.truetype(path, self.font_size)
            except (IOError, OSError):
                continue
        logger.warning("No TrueType font found, falling back to PIL default")
        return ImageFont.load_default()

    def _render_cache(self, width: int, height: int):
        """Render text to a full-frame BGRA array. Called only on text/size change."""
        try:
            text = self.current_text
            font = self._font

            spacing = int(self.font_size * (self.line_spacing - 1))
            scratch = Image.new("RGBA", (1, 1))
            draw = ImageDraw.Draw(scratch)
            bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align="center")
            tw = int(bbox[2] - bbox[0])
            th = int(bbox[3] - bbox[1])

            box_w = tw + self.bg_padding_x * 2
            box_h = th + self.bg_padding_y * 2

            if self.align == "center":
                box_x = (width - box_w) // 2
            elif self.align == "right":
                box_x = width - box_w - self.offset_bottom
            else:
                box_x = self.offset_bottom
            box_x = max(0, box_x)

            box_y = height - box_h - self.offset_bottom
            box_y = max(0, box_y)

            frame_img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(frame_img)

            bg_rgba = (*self.bg_color, self.bg_alpha)
            draw.rectangle(
                [box_x, box_y, box_x + box_w, box_y + box_h],
                fill=bg_rgba,
            )

            text_x = box_x + self.bg_padding_x - bbox[0]
            text_y = box_y + self.bg_padding_y - bbox[1]
            draw.multiline_text((text_x, text_y), text, font=font, fill=(*self.color, 255), spacing=spacing, align="center")

            rgba = np.array(frame_img, dtype=np.uint8)
            bgra = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)

            self._cache_overlay = bgra
            self._cache_roi = (box_y, box_y + box_h, box_x, box_x + box_w)
            self._cache_text = text
            self._cache_dims = (width, height)

        except Exception as e:
            logger.error(f"Error rendering text overlay: {e}", exc_info=True)
            self._cache_overlay = None

    def _blend(self, frame: Frame):
        """Alpha-blend cached overlay onto frame data in-place."""
        overlay = self._cache_overlay
        if overlay is None:
            return

        y1, y2, x1, x2 = self._cache_roi

        fg_bgra = overlay[y1:y2, x1:x2]
        alpha = fg_bgra[:, :, 3:4].astype(np.float32) / 255.0
        fg_bgr = fg_bgra[:, :, :3].astype(np.float32)
        bg = frame.data[y1:y2, x1:x2].astype(np.float32)

        frame.data[y1:y2, x1:x2] = (fg_bgr * alpha + bg * (1.0 - alpha)).astype(np.uint8)

    def _update_text(self):
        """Read up to 2 lines from text file, handling UTF-8 BOM."""
        try:
            if not self.text_file or not self.text_file.exists():
                self.current_text = ""
                return

            mtime = os.path.getmtime(self.text_file)
            if self.last_mtime is not None and mtime == self.last_mtime:
                return

            self.last_mtime = mtime

            with open(self.text_file, 'r', encoding='utf-8-sig') as f:
                lines = f.readlines()

            stripped = [l.rstrip('\n\r') for l in lines]
            while stripped and not stripped[-1]:
                stripped.pop()
            self.current_text = '\n'.join(stripped[:2])
            logger.debug(f"Overlay text updated: {stripped[:2]}")

        except PermissionError:
            logger.warning(f"Permission denied reading {self.text_file}")
        except Exception as e:
            logger.error(f"Error reading text file: {e}")
            self.current_text = ""
