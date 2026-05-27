"""Subtitle/text overlay configuration.

Edit values here or use the Subtitle Settings dialog in the GUI.
The running overlay picks up changes applied via the dialog without restart.
"""

# Incremented by the Subtitle Settings dialog on Apply/Save — overlay detects this
_config_version: int = 16

# Text file source
TEXT_FILE: str = r"C:\temp\lines.txt"
TEXT_CHECK_INTERVAL: int = 30  # Check text file for changes every N frames

# Font
TEXT_FONT_PATH: str = r"C:\Windows\Fonts\arial.ttf"
TEXT_FONT_SIZE: int = 70
TEXT_FONT_BOLD: bool = False
TEXT_FONT_ITALIC: bool = False

# Colors (R, G, B)
TEXT_COLOR: tuple = (255, 255, 255)   # White text
TEXT_BG_COLOR: tuple = (0, 0, 0)      # Black background
TEXT_BG_ALPHA: int = 167              # 0 = transparent, 255 = opaque

# Padding inside background box (pixels)
TEXT_BG_PADDING_X: int = 200
TEXT_BG_PADDING_Y: int = 8

# Position
TEXT_OFFSET_BOTTOM: int = 98    # Pixels from bottom edge
TEXT_ALIGN: str = "center"      # "left", "center", or "right"
TEXT_LINE_SPACING: float = 1.2  # Line height multiplier
