"""FFCapture main entry point."""

import logging
import sys
import signal
import time
from pathlib import Path

# Add parent directory to path so src can be imported
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.logger import setup_logging
from src.pipeline import Pipeline
from src import config
from src.outputs import UDPMonoOutput, TSFileOutput

logger = logging.getLogger(__name__)


def create_outputs():
    """Create encoding output instances from config."""
    logger.info(f"create_outputs() called with {len(config.OUTPUTS)} outputs in config")
    outputs = []
    for output_config in config.OUTPUTS:
        output_type = output_config.get("type")
        try:
            if output_type == "udp_mono":
                channels = output_config.get("channels", [1, 2])
                url = output_config.get("url", "udp://127.0.0.1:12345")
                output = UDPMonoOutput(channels=channels, url=url)
                outputs.append(output)
                logger.info(f"Created UDP mono output: channels={channels}, url={url}")
            elif output_type == "ts_file":
                path = output_config.get("path", r"C:\temp\monitoring.ts")
                output = TSFileOutput(path=path)
                outputs.append(output)
                logger.info(f"Created TS file output: path={path}")
            else:
                logger.warning(f"Unknown output type: {output_type}")
        except Exception as e:
            logger.error(f"Failed to create output {output_type}: {e}", exc_info=True)
    return outputs


def main():
    """Main application entry point."""
    # Setup logging
    setup_logging()

    logger.info("=" * 60)
    logger.info("FFCapture Starting")
    logger.info("=" * 60)
    logger.info(f"Configuration:")
    logger.info(f"  - Capture device: {config.CAPTURE_DEVICE_INDEX}")
    logger.info(f"  - Playout device: {config.PLAYOUT_DEVICE_INDEX}")
    logger.info(f"  - Text file: {config.TEXT_FILE}")
    logger.info(f"  - Simulate hardware: {config.SIMULATE_HARDWARE}")
    if config.SIMULATE_HARDWARE and config.SIMULATION_INPUT_FILE:
        logger.info(f"  - Input file: {config.SIMULATION_INPUT_FILE}")

    try:
        # Create encoding outputs from config
        logger.info("Creating outputs from config...")
        outputs = create_outputs()
        logger.info(f"create_outputs() returned {len(outputs)} output(s)")

        if outputs:
            logger.info(f"Initialized {len(outputs)} encoding output(s)")
        else:
            logger.warning("No outputs configured")

        # Create and start pipeline
        logger.info(f"Creating pipeline with {len(outputs)} outputs...")
        pipeline = Pipeline(outputs=outputs)
        logger.info("Pipeline created, starting...")
        pipeline.start()

        # Setup signal handlers for graceful shutdown
        def signal_handler(sig, frame):
            logger.info("Received interrupt signal, shutting down...")
            pipeline.stop()

            # Stop Qt event loop if GUI is running
            try:
                from PyQt6.QtWidgets import QApplication
                app = QApplication.instance()
                if app:
                    app.quit()
            except:
                pass
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)

        # Run Qt event loop if GUI is available, otherwise keep main thread alive
        logger.info(f"GUI available: {pipeline.gui is not None}")
        if pipeline.gui:
            try:
                from src.gui import get_qt_app
                app = get_qt_app()
                logger.info(f"QApplication instance: {app}")
                if app:
                    logger.info("Starting Qt event loop")
                    logger.info(f"GUI window should be visible. Press Ctrl+C to exit.")
                    result = app.exec()
                    logger.info(f"Qt event loop ended with result: {result}")
                else:
                    logger.error("No QApplication instance found!")
                    raise RuntimeError("QApplication not available")
            except Exception as e:
                logger.error(f"Failed to run Qt event loop: {e}", exc_info=True)
                logger.info("Falling back to non-GUI mode")
                # Fallback: keep main thread alive without GUI
                try:
                    while pipeline.is_running:
                        time.sleep(0.1)
                except KeyboardInterrupt:
                    logger.info("Interrupted by user")
                    pipeline.stop()
        else:
            # No GUI, keep main thread alive
            logger.warning("No GUI available, running in headless mode")
            try:
                while pipeline.is_running:
                    time.sleep(0.1)
            except KeyboardInterrupt:
                logger.info("Interrupted by user")
                pipeline.stop()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
