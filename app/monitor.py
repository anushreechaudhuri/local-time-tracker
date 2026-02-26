import logging
import subprocess

log = logging.getLogger(__name__)

try:
    from Quartz.CoreGraphics import (
        CGEventSourceSecondsSinceLastEventType,
        kCGEventSourceStateCombinedSessionState,
        kCGAnyInputEventType,
    )
    from Quartz import (
        CGWindowListCopyWindowInfo,
        kCGWindowListOptionOnScreenOnly,
        kCGNullWindowID,
    )
    _HAS_QUARTZ = True
except ImportError:
    _HAS_QUARTZ = False
    log.warning("Quartz unavailable, falling back to ioreg (slower)")


def get_idle_seconds():
    if _HAS_QUARTZ:
        return CGEventSourceSecondsSinceLastEventType(
            kCGEventSourceStateCombinedSessionState,
            kCGAnyInputEventType,
        )
    return _get_idle_ioreg()


def _get_idle_ioreg():
    import re
    try:
        result = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem", "-d", "4"],
            capture_output=True, text=True, timeout=5,
        )
        match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', result.stdout)
        if match:
            return int(match.group(1)) / 1_000_000_000
    except Exception as e:
        log.error("ioreg fallback failed: %s", e)
    return 0


def is_idle(threshold_seconds):
    return get_idle_seconds() >= threshold_seconds


def is_screen_being_recorded():
    """Check if screen is being shared, recorded, or mirrored.

    Checks for:
    - macOS screencaptureui (built-in screen recording toolbar)
    - macOS Screen Sharing (someone viewing your screen remotely)
    - Zoom screen sharing (CptHost process)
    - Known recording apps via on-screen windows
    """
    # Check for screen sharing/recording processes via pgrep
    try:
        result = subprocess.run(
            ["pgrep", "-x", "screencaptureui"],
            capture_output=True, timeout=2,
        )
        if result.returncode == 0:
            return True
    except Exception:
        pass

    # Check for Zoom screen sharing process
    try:
        result = subprocess.run(
            ["pgrep", "-f", "CptHost"],
            capture_output=True, timeout=2,
        )
        if result.returncode == 0:
            return True
    except Exception:
        pass

    # Check on-screen windows for recording/sharing indicators
    if _HAS_QUARTZ:
        try:
            windows = CGWindowListCopyWindowInfo(
                kCGWindowListOptionOnScreenOnly, kCGNullWindowID
            )
            for w in (windows or []):
                owner = (w.get("kCGWindowOwnerName", "") or "").lower()
                name = (w.get("kCGWindowName", "") or "").lower()
                # OBS Studio
                if owner == "obs":
                    return True
                # QuickTime Player recording
                if "quicktime" in owner and "recording" in name:
                    return True
                # Loom
                if "loom" in owner and ("recording" in name or "sharing" in name):
                    return True
        except Exception:
            pass

    return False
