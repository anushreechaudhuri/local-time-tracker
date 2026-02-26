import logging
import threading
import time
import webbrowser
from datetime import datetime

import rumps

import config
from app import database, categorizer, monitor
from app.prompter import show_log_prompt, show_resume_prompt, show_multitask_prompt, notify, dismiss_active_panel

log = logging.getLogger(__name__)

_POLL_ACTIVE = 30
_POLL_IDLE = 60


class TimeTrackerApp(rumps.App):
    def __init__(self):
        super().__init__("TimeTracker", title="\u23F1", quit_button=None)

        self.is_paused = False
        self._was_idle = False
        self._last_prompt_time = time.monotonic()
        self._last_active_entry = None
        self._wake_event = threading.Event()
        self._prompt_lock = threading.Lock()
        self._idle_start = None

        # Multitask state
        self._is_multitasking = False
        self._mt_projects = []      # [(name, id), (name, id)]
        self._mt_tags = []           # [tag_ids]
        self._mt_tag_names = []      # [tag_names]
        self._mt_desc = ""
        self._mt_start = None        # datetime
        self._mt_split = 50          # % for project A

        self.menu = [
            rumps.MenuItem("No active task", callback=None),
            None,
            rumps.MenuItem("Log Entry", callback=self.on_log_entry),
            rumps.MenuItem("Pause", callback=self.on_toggle_pause),
            None,
            rumps.MenuItem("Open Dashboard", callback=self.on_open_dashboard),
            None,
            rumps.MenuItem("Quit", callback=self.on_quit),
        ]

        t = threading.Thread(target=self._run_loop, daemon=True)
        t.start()

    def _get_project_names(self):
        return [dict(p)["name"] for p in database.get_projects()]

    def _get_tag_names(self):
        return [dict(t)["name"] for t in database.get_tags()]

    def _current_prompt_interval(self):
        if self._is_multitasking:
            return config.MULTITASK_PROMPT_INTERVAL
        return config.PROMPT_INTERVAL

    def _run_loop(self):
        while True:
            try:
                if not self.is_paused:
                    idle = monitor.is_idle(config.IDLE_THRESHOLD)

                    if idle and not self._was_idle:
                        self._was_idle = True
                        self._handle_went_idle()
                    elif not idle and self._was_idle:
                        self._was_idle = False
                        self._handle_resumed()
                    elif not idle and not self._was_idle:
                        elapsed = time.monotonic() - self._last_prompt_time
                        if elapsed >= self._current_prompt_interval():
                            self._last_prompt_time = time.monotonic()
                            if self._is_multitasking:
                                self._do_multitask_prompt()
                            else:
                                self._do_prompt()
            except Exception as e:
                log.error("Loop error: %s", e)

            interval = _POLL_IDLE if self._was_idle else _POLL_ACTIVE
            self._wake_event.wait(timeout=interval)
            self._wake_event.clear()

    def _should_suppress_prompt(self):
        try:
            return monitor.is_screen_being_recorded()
        except Exception:
            return False

    def _handle_went_idle(self):
        log.info("Idle detected, auto-pausing")
        self._idle_start = datetime.now()
        if self._is_multitasking:
            self._end_multitask()
        else:
            entry, tags = database.get_active_entry()
            if entry:
                self._last_active_entry = (
                    entry["description"],
                    entry.get("project_name", "Without task"),
                    entry["project_id"],
                    [t["id"] for t in tags],
                )
                database.stop_active_entry()
        self._update_menu_title("Paused (idle)")

    def _handle_resumed(self):
        log.info("Activity resumed")
        self._last_prompt_time = time.monotonic()
        idle_start = getattr(self, "_idle_start", None)

        if self._should_suppress_prompt():
            return
        if self._last_active_entry:
            desc, proj_name, proj_id, tag_ids = self._last_active_entry
            result = show_resume_prompt(
                desc, proj_name,
                self._get_project_names(), self._get_tag_names(),
            )
            if result and result.get("action") == "continue":
                # Log idle time as break if user checked the box
                if result.get("was_break") and idle_start:
                    now = datetime.now()
                    fmt = "%Y-%m-%d %H:%M:%S"
                    break_proj = database.get_project_by_name("Break") or database.add_project("Break", "#9E9E9E")
                    break_id = break_proj["id"] if break_proj else None
                    if break_id:
                        database.create_entry("Break", break_id, [],
                                              idle_start.strftime(fmt))
                        database.update_entry(
                            database.get_active_entry()[0]["id"],
                            end_time=now.strftime(fmt), is_active=0,
                        )
                database.create_entry(desc, proj_id, tag_ids)
                self._update_menu_current(desc, proj_name)
                return
            elif result and result.get("action") == "new":
                # Also log break if checked
                if result.get("was_break") and idle_start:
                    now = datetime.now()
                    fmt = "%Y-%m-%d %H:%M:%S"
                    break_proj = database.get_project_by_name("Break") or database.add_project("Break", "#9E9E9E")
                    break_id = break_proj["id"] if break_proj else None
                    if break_id:
                        database.create_entry("Break", break_id, [],
                                              idle_start.strftime(fmt))
                        database.update_entry(
                            database.get_active_entry()[0]["id"],
                            end_time=now.strftime(fmt), is_active=0,
                        )
                self._do_prompt()
                return
        self._do_prompt()

    def _do_prompt(self):
        if not self._prompt_lock.acquire(blocking=False):
            # A prompt is already showing — dismiss it and infer action
            self._auto_infer_dismissed()
            dismiss_active_panel()
            if not self._prompt_lock.acquire(timeout=3):
                return
        try:
            self._do_prompt_inner()
        finally:
            self._prompt_lock.release()

    def _auto_infer_dismissed(self):
        """When an old dialog is dismissed by a new prompt, infer what happened."""
        if self._last_active_entry:
            # User was active but didn't fill the dialog — assume persisting
            desc, proj_name, proj_id, tag_ids = self._last_active_entry
            database.stop_active_entry()
            database.create_entry(desc, proj_id, tag_ids)
            log.info("Auto-persisted: %s", proj_name)

    def _do_prompt_inner(self):
        if self._should_suppress_prompt():
            return

        projects = self._get_project_names()
        tags = self._get_tag_names()

        # Step 1: Show initial prompt
        result = show_log_prompt(projects, tags, has_previous=self._last_active_entry is not None)
        if not result or result.get("action") == "skip":
            # Dialog was closed (X button) or timed out — auto-persist if active
            if self._last_active_entry:
                desc, proj_name, proj_id, tag_ids = self._last_active_entry
                database.stop_active_entry()
                database.create_entry(desc, proj_id, tag_ids)
            return

        action = result.get("action", "")

        # "Same" — continue previous task
        if action == "same" and self._last_active_entry:
            desc, proj_name, proj_id, tag_ids = self._last_active_entry
            database.stop_active_entry()
            database.create_entry(desc, proj_id, tag_ids)
            self._update_menu_current(desc, proj_name)
            return

        # "Break" — pause tracking and log the break as an entry
        if action == "break":
            database.stop_active_entry()
            break_text = result.get("break_duration", "30 mins")
            break_secs, break_reason = self._parse_break(break_text)

            # Log the break as a "Without task" entry
            break_proj = database.get_project_by_name("Break") or database.add_project("Break", "#9E9E9E")
            break_proj_id = break_proj["id"] if break_proj else 1
            fmt = "%Y-%m-%d %H:%M:%S"
            now = datetime.now()
            desc = f"Break: {break_reason}"
            database.create_entry(desc, break_proj_id, [], now.strftime(fmt), raw_input=break_text)

            self._update_menu_title(f"Break ({break_reason})")
            mins = break_secs // 60
            notify(f"On break: {break_reason} ({mins} min)" if mins else f"On break: {break_reason}")
            self.is_paused = True
            threading.Thread(target=self._break_timer, args=(break_secs,), daemon=True).start()
            return

        raw_input = result.get("description", "")
        user_projects = result.get("projects", [])
        user_tags = result.get("tags", [])
        log_tz = result.get("log_timezone", "UTC")

        # Step 2: AI categorizes/cleans up in the background (no second prompt)
        ai_result = categorizer.categorize(raw_input)
        ai_desc = ai_result["description"]  # cleaned description

        # User's manual selections override AI; AI fills in whatever user left blank
        final_projects = user_projects if user_projects else ai_result["projects"]
        final_tags = user_tags if user_tags else ai_result["tags"]

        if ai_result.get("multitask_warning"):
            notify("Focus on 2 projects max!")

        # Step 3: Handle based on number of projects
        if len(final_projects) >= 2:
            self._start_multitask(final_projects[:2], final_tags, ai_desc, raw_input, log_tz)
        else:
            self._save_single_entry(
                ai_desc,
                final_projects[0] if final_projects else "Without task",
                final_tags,
                raw_input,
                log_tz,
            )

    def _save_single_entry(self, description, project_name, tag_names, raw_input=None, log_tz=None):
        """Resolve names to IDs and save a single entry."""
        project = database.get_project_by_name(project_name)
        if not project:
            project = database.add_project(project_name)
        project_id = project["id"]

        tag_ids = []
        for tn in tag_names:
            tag = database.get_tag_by_name(tn)
            if not tag:
                tag = database.add_tag(tn)
            tag_ids.append(tag["id"])

        database.stop_active_entry()
        database.create_entry(description, project_id, tag_ids, raw_input=raw_input, log_timezone=log_tz)
        self._last_active_entry = (description, project_name, project_id, tag_ids)
        self._update_menu_current(description, project_name)

    def _parse_break(self, text):
        """Use GPT to parse natural language break input into duration + reason."""
        import json as _json
        try:
            from app.categorizer import _get_client
            from app.cost_tracker import track_usage
            client = _get_client()
            response = client.chat.completions.create(
                model=config.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": (
                        "Parse a break description into JSON with:\n"
                        '- "minutes": integer number of minutes for the break\n'
                        '- "reason": short reason (e.g. "lunch", "coffee", "rest"). '
                        'If no reason given, use "break"\n'
                        "Examples: '30 mins' => {\"minutes\":30,\"reason\":\"break\"}, "
                        "'lunch' => {\"minutes\":60,\"reason\":\"lunch\"}, "
                        "'quick coffee' => {\"minutes\":15,\"reason\":\"coffee\"}, "
                        "'1.5 hours nap' => {\"minutes\":90,\"reason\":\"nap\"}\n"
                        "Respond with only valid JSON."
                    )},
                    {"role": "user", "content": text},
                ],
                max_completion_tokens=256,
            )
            usage = response.usage
            track_usage(config.OPENAI_MODEL, usage.prompt_tokens, usage.completion_tokens)
            content = (response.choices[0].message.content or "").strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()
            result = _json.loads(content)
            minutes = int(result.get("minutes", 15))
            reason = result.get("reason", "break")
            return minutes * 60, reason
        except Exception as e:
            log.error("Break parse failed, using fallback: %s", e)
            return self._parse_break_fallback(text)

    def _parse_break_fallback(self, text):
        """Regex fallback if GPT fails."""
        import re
        text = text.lower().strip()
        m = re.search(r'(\d+\.?\d*)\s*(?:-?\s*)(hours?|hrs?|h\b|minutes?|mins?|m\b)', text)
        if m:
            num = float(m.group(1))
            unit = m.group(2)
            secs = int(num * 3600) if unit.startswith('h') else int(num * 60)
            return secs, "break"
        if 'lunch' in text:
            return 3600, "lunch"
        if 'coffee' in text or 'tea' in text:
            return 900, text.strip()
        m = re.search(r'(\d+)', text)
        if m:
            return int(m.group(1)) * 60, "break"
        return 900, "break"

    def _break_timer(self, seconds):
        """Sleep for break duration, then unpause and prompt."""
        actual = seconds
        time.sleep(actual)
        # End the break entry
        database.stop_active_entry()
        self.is_paused = False
        self._last_prompt_time = time.monotonic()
        self._update_menu_title("Back from break")
        self._wake_event.set()
        notify("Break's over!")

    def _start_multitask(self, project_names, tag_names, description, raw_input=None, log_tz=None):
        """Enter multitask mode with 2 projects."""
        self._is_multitasking = True
        self._mt_desc = description
        self._mt_raw_input = raw_input
        self._mt_log_tz = log_tz
        self._mt_tag_names = tag_names
        self._mt_start = datetime.now()
        self._mt_split = 50

        # Resolve project IDs
        self._mt_projects = []
        for pn in project_names[:2]:
            p = database.get_project_by_name(pn)
            if not p:
                p = database.add_project(pn)
            self._mt_projects.append((pn, p["id"]))

        # Resolve tag IDs
        self._mt_tags = []
        for tn in tag_names:
            t = database.get_tag_by_name(tn)
            if not t:
                t = database.add_tag(tn)
            self._mt_tags.append(t["id"])

        database.stop_active_entry()
        self._last_prompt_time = time.monotonic()
        title = f"{self._mt_projects[0][0]} + {self._mt_projects[1][0]}"
        self._update_menu_current(description, title)
        notify(f"Multitasking: {title} (15-min check-ins)")

    def _do_multitask_prompt(self):
        if not self._prompt_lock.acquire(blocking=False):
            return
        try:
            self._do_multitask_prompt_inner()
        finally:
            self._prompt_lock.release()

    def _do_multitask_prompt_inner(self):
        if self._should_suppress_prompt():
            return

        result = show_multitask_prompt(
            self._mt_projects[0][0],
            self._mt_projects[1][0],
            self._mt_desc,
            self._mt_tag_names,
            self._mt_split,
        )

        if not result:
            return

        action = result.get("action", "")

        if action == "multitask_update":
            if result.get("still_multitasking"):
                self._mt_split = result.get("split", 50)
                # Continue multitasking
            else:
                self._end_multitask()
                self._do_prompt_inner()

        elif action == "stopped":
            self._end_multitask()
            self._do_prompt_inner()

    def _end_multitask(self):
        """Finalize multitask period: create split entries."""
        if not self._is_multitasking or not self._mt_start:
            self._is_multitasking = False
            return

        now = datetime.now()
        fmt = "%Y-%m-%d %H:%M:%S"

        database.create_split_entries(
            description=self._mt_desc,
            proj_a_id=self._mt_projects[0][1],
            proj_b_id=self._mt_projects[1][1],
            tag_ids=self._mt_tags,
            start_time=self._mt_start.strftime(fmt),
            end_time=now.strftime(fmt),
            split_pct=self._mt_split,
            raw_input=getattr(self, "_mt_raw_input", None),
        )

        self._is_multitasking = False
        self._mt_projects = []
        self._mt_tags = []
        self._mt_tag_names = []
        self._mt_desc = ""
        self._mt_raw_input = None
        self._mt_start = None
        self._mt_split = 50
        self._last_active_entry = None  # clear so Persist doesn't use stale data

    def _update_menu_current(self, description, project_name):
        short = description[:30] + "..." if len(description) > 30 else description
        self.title = "\u23F1 " + project_name
        for key in list(self.menu.keys()):
            item = self.menu[key]
            if hasattr(item, "title") and (item.title.startswith("No active") or item.title.startswith(">")):
                item.title = f"> {short} ({project_name})"
                break

    def _update_menu_title(self, text):
        self.title = "\u23F1 " + text
        for key in list(self.menu.keys()):
            item = self.menu[key]
            if hasattr(item, "title") and (item.title.startswith("No active") or item.title.startswith(">")):
                item.title = f"> {text}"
                break

    def on_log_entry(self, sender):
        self._wake_event.set()
        threading.Thread(target=self._do_prompt, daemon=True).start()

    def on_toggle_pause(self, sender):
        if self.is_paused:
            self.is_paused = False
            sender.title = "Pause"
            self._last_prompt_time = time.monotonic()
            self._update_menu_title("Resumed")
            self._wake_event.set()
            notify("Time tracking resumed")
        else:
            self.is_paused = True
            sender.title = "Resume"
            if self._is_multitasking:
                self._end_multitask()
            database.stop_active_entry()
            self._update_menu_title("Paused")
            notify("Time tracking paused")

    def on_open_dashboard(self, sender):
        webbrowser.open(f"http://{config.WEB_HOST}:{config.WEB_PORT}")

    def on_quit(self, sender):
        if self._is_multitasking:
            self._end_multitask()
        database.stop_active_entry()
        rumps.quit_application()
