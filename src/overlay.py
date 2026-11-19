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
        frame.subtitle = self.current_text or ""

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

            # Frames are RGB (or UYVY in passthrough), so keep the overlay as RGBA
            rgba = np.array(frame_img, dtype=np.uint8)

            # ROI on even x so it covers whole UYVY pixel pairs
            y1, y2 = box_y, min(height, box_y + box_h + 1)
            x1 = box_x - (box_x % 2)
            x2 = min(width - (width % 2), box_x + box_w + 1 + ((box_x + box_w + 1) % 2))
            self._prepare_blend(rgba[y1:y2, x1:x2])

            self._cache_overlay = rgba
            self._cache_roi = (y1, y2, x1, x2)
            self._cache_text = text
            self._cache_dims = (width, height)

        except Exception as e:
            logger.error(f"Error rendering text overlay: {e}", exc_info=True)
            self._cache_overlay = None

    def _prepare_blend(self, roi_rgba: np.ndarray):
        """Precompute premultiplied overlay + inverse alpha (uint16) for RGB and UYVY blending.

        Done once per text change, so the per-frame blend is one integer multiply-add over the
        subtitle box. UYVY uses BT.601 limited range (inverse of the capture conversion); chroma
        is premultiplied and averaged per pixel pair (4:2:2).
        """
        a = roi_rgba[:, :, 3].astype(np.uint16)
        rgb = roi_rgba[:, :, :3]
        self._rgb_pm = rgb.astype(np.uint16) * a[:, :, None]
        self._rgb_inv = (255 - a)[:, :, None]

        f = rgb.astype(np.float32)
        y = 16.0 + f @ np.array([0.299, 0.587, 0.114], np.float32) * (219.0 / 255.0)
        cb = 128.0 + f @ np.array([-0.168736, -0.331264, 0.5], np.float32) * (224.0 / 255.0)
        cr = 128.0 + f @ np.array([0.5, -0.418688, -0.081312], np.float32) * (224.0 / 255.0)
        af = a.astype(np.float32)
        h, w = a.shape
        pm = np.empty((h, w * 2), np.uint16)
        inv = np.empty((h, w * 2), np.uint16)
        pm[:, 1::2] = np.rint(y * af)
        inv[:, 1::2] = 255 - a
        a_pair = (af[:, 0::2] + af[:, 1::2]) / 2
        pm[:, 0::4] = np.rint((cb[:, 0::2] * af[:, 0::2] + cb[:, 1::2] * af[:, 1::2]) / 2)
        pm[:, 2::4] = np.rint((cr[:, 0::2] * af[:, 0::2] + cr[:, 1::2] * af[:, 1::2]) / 2)
        inv[:, 0::4] = inv[:, 2::4] = np.rint(255 - a_pair)
        self._uyvy_pm, self._uyvy_inv = pm, inv

    @staticmethod
    def _mix(bg: np.ndarray, pm: np.ndarray, inv: np.ndarray) -> np.ndarray:
        # (bg * (255 - a) + fg * a) / 255, rounded; max 255*255 + 127 fits in uint16
        x = bg.astype(np.uint16) * inv + pm + 128
        return ((x + (x >> 8)) >> 8).astype(np.uint8)

    def _blend(self, frame: Frame):
        """Alpha-blend the cached overlay into the frame in place (UYVY in passthrough, else RGB)."""
        if self._cache_overlay is None:
            return
        y1, y2, x1, x2 = self._cache_roi
        if getattr(frame, 'uyvy', None) is not None:
            roi = frame.uyvy[y1:y2, 2 * x1:2 * x2]
            roi[:] = self._mix(roi, self._uyvy_pm, self._uyvy_inv)
        else:
            roi = frame.data[y1:y2, x1:x2]
            roi[:] = self._mix(roi, self._rgb_pm, self._rgb_inv)

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
