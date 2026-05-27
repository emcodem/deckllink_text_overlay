"""Unit tests for text overlay engine."""

import numpy as np
import sys
from pathlib import Path
import tempfile

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.overlay import TextOverlay
from src.capture import Frame


def test_text_overlay_basic():
    """Test basic text overlay functionality."""
    # Create a temporary text file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write("Test line 1\n")
        f.write("Test line 2\n")
        text_file = f.name

    try:
        # Create overlay engine
        overlay = TextOverlay(text_file)

        # Create a test frame
        frame_data = np.ones((480, 640, 3), dtype=np.uint8) * 128
        frame = Frame(
            data=frame_data,
            format='RGB24',
            width=640,
            height=480,
            framerate=(30, 1),
            timestamp=0.0,
            frame_number=0
        )

        # Apply overlay
        result = overlay.apply(frame)

        assert result is not None
        assert result.data.shape == frame_data.shape
        assert result.frame_number == 0

        # Check that text was read
        assert "Test line 2" in overlay.current_text

        print("✓ Basic overlay test passed")

    finally:
        # Cleanup
        Path(text_file).unlink()


def test_text_file_update():
    """Test that overlay updates when text file changes."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write("Initial text\n")
        text_file = f.name

    try:
        overlay = TextOverlay(text_file, check_interval=1)

        # Initial text
        assert "Initial text" in overlay.current_text

        # Update file
        with open(text_file, 'w') as f:
            f.write("Initial text\n")
            f.write("Updated text\n")

        # Force update
        overlay._update_text()

        assert "Updated text" in overlay.current_text
        print("✓ Text file update test passed")

    finally:
        Path(text_file).unlink()


def test_missing_file():
    """Test behavior when text file is missing."""
    overlay = TextOverlay("/nonexistent/path/file.txt")

    assert overlay.current_text == ""
    print("✓ Missing file test passed")


if __name__ == "__main__":
    test_text_overlay_basic()
    test_text_file_update()
    test_missing_file()
    print("\nAll tests passed!")
