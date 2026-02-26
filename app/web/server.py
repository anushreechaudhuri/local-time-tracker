import json
import threading
import uuid
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for, Response

import config
from app import database

# Shared state for prompt results. The menubar opens /prompt in a browser,
# the page posts to /api/prompt/submit, and the menubar polls /api/prompt/result.
_prompt_results = {}  # token -> result dict
_prompt_events = {}   # token -> threading.Event


def create_prompt_token():
    token = uuid.uuid4().hex[:12]
    _prompt_events[token] = threading.Event()
    return token


def wait_for_prompt(token, timeout=180):
    """Block until the user submits the prompt page, or timeout."""
    event = _prompt_events.get(token)
    if event and event.wait(timeout=timeout):
        result = _prompt_results.pop(token, None)
        _prompt_events.pop(token, None)
        return result
    _prompt_results.pop(token, None)
    _prompt_events.pop(token, None)
    return None


def create_app():
    app = Flask(
        __name__,
        template_folder=str(config.BASE_DIR / "app" / "web" / "templates"),
        static_folder=str(config.BASE_DIR / "app" / "web" / "static"),
    )

    # --- Page routes ---

    @app.route("/")
    def index():
        date_str = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
        view = request.args.get("view", "day")

        if view == "week":
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            start = dt - timedelta(days=dt.weekday())  # Monday
            end = start + timedelta(days=6)
            entries = database.get_entries_for_range(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            date_label = f"{start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}"
            prev_date = (start - timedelta(days=7)).strftime("%Y-%m-%d")
            next_date = (start + timedelta(days=7)).strftime("%Y-%m-%d")
        elif view == "month":
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            start = dt.replace(day=1)
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1) - timedelta(days=1)
            else:
                end = start.replace(month=start.month + 1) - timedelta(days=1)
            entries = database.get_entries_for_range(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            date_label = dt.strftime("%B %Y")
            prev_date = (start - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%d")
            next_month = start.month + 1 if start.month < 12 else 1
            next_year = start.year if start.month < 12 else start.year + 1
            next_date = start.replace(year=next_year, month=next_month).strftime("%Y-%m-%d")
        else:
            entries = database.get_entries_for_date(date_str)
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            date_label = dt.strftime("%A, %B %d, %Y")
            prev_date = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
            next_date = (dt + timedelta(days=1)).strftime("%Y-%m-%d")

        # Compute totals
        total_seconds = 0
        project_totals = {}
        tag_totals = {}
        for e in entries:
            start_t = datetime.fromisoformat(e["start_time"])
            end_t = datetime.fromisoformat(e["end_time"]) if e["end_time"] else datetime.now()
            dur = (end_t - start_t).total_seconds()
            total_seconds += dur
            proj = e.get("project_name") or "Without task"
            proj_color = e.get("project_color") or "#6B7FFF"
            project_totals[proj] = {
                "seconds": project_totals.get(proj, {}).get("seconds", 0) + dur,
                "color": proj_color,
            }
            for t in e.get("tags", []):
                tag_totals[t["name"]] = {
                    "seconds": tag_totals.get(t["name"], {}).get("seconds", 0) + dur,
                    "color": t["color"],
                }

        projects = database.get_projects()
        tags = database.get_tags()

        return render_template(
            "dashboard.html",
            entries=entries,
            date_str=date_str,
            date_label=date_label,
            view=view,
            prev_date=prev_date,
            next_date=next_date,
            total_seconds=total_seconds,
            project_totals=project_totals,
            tag_totals=tag_totals,
            projects=projects,
            tags=tags,
            today=datetime.now().strftime("%Y-%m-%d"),
        )

    @app.route("/settings")
    def settings_page():
        projects = database.get_projects(include_archived=True)
        tags = database.get_tags(include_archived=True)
        monthly_cost = database.get_monthly_api_cost()
        api_usage = database.get_api_usage_daily()
        return render_template(
            "settings.html",
            projects=projects,
            tags=tags,
            monthly_cost=monthly_cost,
            api_usage=api_usage,
            cost_limit=config.MONTHLY_COST_ALERT_USD,
        )

    @app.route("/prompt")
    def prompt_page():
        """Prompt page opened by the menu bar app."""
        token = request.args.get("token", "")
        mode = request.args.get("mode", "log")
        ai_projects = request.args.getlist("ai_project")
        ai_tags = request.args.getlist("ai_tag")
        ai_desc = request.args.get("ai_desc", "")
        last_desc = request.args.get("last_desc", "")
        last_project = request.args.get("last_project", "")
        multitask_warning = request.args.get("multitask_warning", "") == "1"

        # Multitask mode params
        mt_project_a = request.args.get("mt_project_a", "")
        mt_project_b = request.args.get("mt_project_b", "")
        mt_desc = request.args.get("mt_desc", "")
        mt_split = request.args.get("mt_split", "50")
        has_previous = request.args.get("has_previous", "") == "1"

        projects = database.get_projects()
        tags = database.get_tags()

        return render_template(
            "prompt.html",
            token=token,
            mode=mode,
            projects=projects,
            tags=tags,
            ai_projects=ai_projects,
            ai_tags=ai_tags,
            ai_desc=ai_desc,
            last_desc=last_desc,
            last_project=last_project,
            multitask_warning=multitask_warning,
            mt_project_a=mt_project_a,
            mt_project_b=mt_project_b,
            mt_desc=mt_desc,
            mt_split=mt_split,
            has_previous=has_previous,
        )

    @app.route("/api/prompt/submit", methods=["POST"])
    def api_prompt_submit():
        data = request.json
        token = data.get("token", "")

        # Handle undo: delete the most recent entry
        if data.get("action") == "undo":
            entry, _ = database.get_active_entry()
            if entry:
                database.delete_entry(entry["id"])
            else:
                # Delete the last entry by ID
                with database.db() as conn:
                    last = conn.execute("SELECT id FROM time_entries ORDER BY id DESC LIMIT 1").fetchone()
                    if last:
                        database.delete_entry(last["id"])
            return jsonify({"ok": True, "undone": True})

        if token in _prompt_events:
            _prompt_results[token] = data
            _prompt_events[token].set()
        return jsonify({"ok": True})

    # --- API routes ---

    @app.route("/api/entries", methods=["POST"])
    def api_create_entry():
        data = request.json
        database.stop_active_entry()
        entry_id = database.create_entry(
            description=data.get("description", ""),
            project_id=data.get("project_id"),
            tag_ids=data.get("tag_ids", []),
            start_time=data.get("start_time"),
        )
        return jsonify({"id": entry_id}), 201

    @app.route("/api/entries/<int:entry_id>", methods=["PUT"])
    def api_update_entry(entry_id):
        data = request.json
        database.update_entry(entry_id, **data)
        return jsonify({"ok": True})

    @app.route("/api/entries/<int:entry_id>", methods=["DELETE"])
    def api_delete_entry(entry_id):
        database.delete_entry(entry_id)
        return jsonify({"ok": True})

    @app.route("/api/entries/stop", methods=["POST"])
    def api_stop_entry():
        database.stop_active_entry()
        return jsonify({"ok": True})

    @app.route("/api/projects", methods=["POST"])
    def api_add_project():
        data = request.json
        p = database.add_project(data["name"], data.get("color", "#6B7FFF"))
        return jsonify(dict(p)), 201

    @app.route("/api/projects/<int:pid>", methods=["PUT"])
    def api_update_project(pid):
        database.update_project(pid, **request.json)
        return jsonify({"ok": True})

    @app.route("/api/projects/<int:pid>", methods=["DELETE"])
    def api_delete_project(pid):
        database.delete_project(pid)
        return jsonify({"ok": True})

    @app.route("/api/tags", methods=["POST"])
    def api_add_tag():
        data = request.json
        t = database.add_tag(data["name"], data.get("color", "#888888"))
        return jsonify(dict(t)), 201

    @app.route("/api/tags/<int:tid>", methods=["PUT"])
    def api_update_tag(tid):
        database.update_tag(tid, **request.json)
        return jsonify({"ok": True})

    @app.route("/api/tags/<int:tid>", methods=["DELETE"])
    def api_delete_tag(tid):
        database.delete_tag(tid)
        return jsonify({"ok": True})

    @app.route("/api/summary/weekly")
    def api_weekly_summary():
        """Returns weekly summary JSON for n8n integration."""
        summary = database.get_weekly_summary()
        return jsonify(summary)

    @app.route("/api/costs")
    def api_costs():
        monthly = database.get_monthly_api_cost()
        daily = [dict(r) for r in database.get_api_usage_daily()]
        return jsonify({"monthly_total": monthly, "daily": daily})

    @app.route("/api/export/csv")
    def api_export_csv():
        csv_data = database.export_all_entries_csv()
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=timetracker_export.csv"},
        )

    # --- Template filters ---

    @app.template_filter("duration")
    def duration_filter(seconds):
        if not seconds:
            return "0m"
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        if h > 0:
            return f"{h}h {m}m"
        return f"{m}m"

    @app.template_filter("time_only")
    def time_only_filter(dt_str):
        if not dt_str:
            return "now"
        try:
            return datetime.fromisoformat(dt_str).strftime("%H:%M")
        except Exception:
            return dt_str

    return app
