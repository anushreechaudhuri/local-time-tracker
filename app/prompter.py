import subprocess
import sys
import logging
from pathlib import Path
from urllib.parse import quote

import config

log = logging.getLogger(__name__)

_PANEL_SCRIPT = str(Path(__file__).parent / "prompt_panel.py")
_PYTHON = sys.executable
_active_panel = None  # track current panel process


def dismiss_active_panel():
    """Kill the currently open panel if any. Called before opening a new one."""
    global _active_panel
    if _active_panel is not None:
        try:
            _active_panel.terminate()
            _active_panel.wait(timeout=2)
        except Exception:
            try:
                _active_panel.kill()
            except Exception:
                pass
        _active_panel = None


def _launch_panel(url, timeout=180):
    """Launch native panel, wait for form submission, return result dict."""
    global _active_panel
    from app.web.server import wait_for_prompt

    # Kill any existing panel first
    dismiss_active_panel()

    proc = subprocess.Popen(
        [_PYTHON, _PANEL_SCRIPT, url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _active_panel = proc

    token = url.split("token=")[1].split("&")[0]
    result = wait_for_prompt(token, timeout=timeout)

    try:
        proc.terminate()
        proc.wait(timeout=2)
    except Exception:
        pass
    _active_panel = None
    return result


def show_log_prompt(projects, tags, ai_projects=None, ai_tags=None, ai_desc="",
                    multitask_warning=False, has_previous=False):
    """Show the log prompt panel. Returns result dict or None."""
    from app.web.server import create_prompt_token

    token = create_prompt_token()
    parts = [f"token={token}", "mode=log"]
    if has_previous:
        parts.append("has_previous=1")
    for p in (ai_projects or []):
        parts.append(f"ai_project={_esc(p)}")
    if ai_desc:
        parts.append(f"ai_desc={_esc(ai_desc)}")
    for t in (ai_tags or []):
        parts.append(f"ai_tag={_esc(t)}")
    if multitask_warning:
        parts.append("multitask_warning=1")
    url = f"http://{config.WEB_HOST}:{config.WEB_PORT}/prompt?" + "&".join(parts)
    return _launch_panel(url)


def show_resume_prompt(last_desc, last_project, projects, tags):
    """Show the resume-from-idle prompt."""
    from app.web.server import create_prompt_token

    token = create_prompt_token()
    parts = [
        f"token={token}",
        "mode=resume",
        f"last_desc={_esc(last_desc)}",
        f"last_project={_esc(last_project)}",
    ]
    url = f"http://{config.WEB_HOST}:{config.WEB_PORT}/prompt?" + "&".join(parts)
    return _launch_panel(url)


def show_multitask_prompt(project_a, project_b, desc, tags, current_split=50):
    """Show the multitask check-in prompt with slider."""
    from app.web.server import create_prompt_token

    token = create_prompt_token()
    parts = [
        f"token={token}",
        "mode=multitask",
        f"mt_project_a={_esc(project_a)}",
        f"mt_project_b={_esc(project_b)}",
        f"mt_desc={_esc(desc)}",
        f"mt_split={current_split}",
    ]
    for t in (tags or []):
        parts.append(f"ai_tag={_esc(t)}")
    url = f"http://{config.WEB_HOST}:{config.WEB_PORT}/prompt?" + "&".join(parts)
    return _launch_panel(url)


def notify(message, title="Time Tracker"):
    try:
        safe_msg = message.replace('"', '\\"')
        safe_title = title.replace('"', '\\"')
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_msg}" with title "{safe_title}"'],
            check=False, capture_output=True, timeout=5,
        )
    except Exception:
        pass


def _esc(s):
    from urllib.parse import quote
    return quote(str(s), safe="")
