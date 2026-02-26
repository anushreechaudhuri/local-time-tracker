import sys
import os
import logging
import threading

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Lower process priority so we never compete with foreground apps
try:
    os.nice(10)
except OSError:
    pass

import config
from app import database
from app.menubar import TimeTrackerApp

# Minimal logging — WARNING level by default, only file output
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(config.BASE_DIR / "timetracker.log")],
)
# Suppress Flask/Werkzeug request-level logs
logging.getLogger("werkzeug").setLevel(logging.ERROR)

log = logging.getLogger(__name__)


def run_web_server():
    from app.web.server import create_app  # lazy import — saves ~20ms startup
    flask_app = create_app()
    flask_app.run(
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        debug=False,
        use_reloader=False,
    )


def warm_panel():
    """Pre-launch and immediately kill a panel subprocess to cache pyobjc imports in the OS."""
    import subprocess
    from pathlib import Path
    panel_script = str(Path(__file__).parent / "prompt_panel.py")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "from AppKit import NSApplication,NSPanel,NSScreen,NSFloatingWindowLevel,NSColor;"
             "from WebKit import WKWebView,WKWebViewConfiguration;"
             "from Foundation import NSURL,NSURLRequest,NSMakeRect"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=15)
    except Exception:
        pass


def main():
    database.init_db()

    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    # Pre-warm pyobjc imports so first panel opens fast
    threading.Thread(target=warm_panel, daemon=True).start()

    # Start iCloud sync
    from app.sync import start_sync_thread
    start_sync_thread()

    app = TimeTrackerApp()
    app.run()


if __name__ == "__main__":
    main()
