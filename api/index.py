"""Vercel serverless entry point. Reads from Turso instead of local SQLite."""

import os
import sys
import json
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request, jsonify, Response

import httpx

# --- Turso query helpers ---

def _get_turso_url():
    return os.getenv("TURSO_DB_URL", "").strip().replace("libsql://", "https://")

def _get_turso_token():
    return os.getenv("TURSO_DB_TOKEN", "").strip()

def _headers():
    return {"Authorization": f"Bearer {_get_turso_token()}", "Content-Type": "application/json"}

def query(sql, params=None):
    args = []
    if params:
        for v in params:
            if v is None:
                args.append({"type": "null", "value": None})
            elif isinstance(v, int):
                args.append({"type": "integer", "value": str(v)})
            elif isinstance(v, float):
                args.append({"type": "float", "value": str(v)})
            else:
                args.append({"type": "text", "value": str(v)})

    body = {"requests": [
        {"type": "execute", "stmt": {"sql": sql, "args": args}},
        {"type": "close"},
    ]}
    resp = httpx.post(f"{_get_turso_url()}/v2/pipeline", headers=_headers(), json=body, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    result = data.get("results", [{}])[0].get("response", {}).get("result", {})
    cols = [c["name"] for c in result.get("cols", [])]
    rows = []
    for row in result.get("rows", []):
        d = {}
        for col, val in zip(cols, row):
            if val.get("type") == "null":
                d[col] = None
            elif val.get("type") == "integer":
                d[col] = int(val["value"])
            elif val.get("type") == "float":
                d[col] = float(val["value"])
            else:
                d[col] = val.get("value")
        rows.append(d)
    return rows


# --- Flask app ---

from pathlib import Path
BASE = Path(__file__).parent.parent

app = Flask(
    __name__,
    template_folder=str(BASE / "app" / "web" / "templates"),
    static_folder=str(BASE / "app" / "web" / "static"),
)


@app.route("/")
def index():
    date_str = request.args.get("date", datetime.utcnow().strftime("%Y-%m-%d"))
    view = request.args.get("view", "day")

    if view == "week":
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        start = dt - timedelta(days=dt.weekday())
        end = start + timedelta(days=6)
        entries = _get_entries_range(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
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
        entries = _get_entries_range(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        date_label = dt.strftime("%B %Y")
        prev_date = (start - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%d")
        nm = start.month + 1 if start.month < 12 else 1
        ny = start.year if start.month < 12 else start.year + 1
        next_date = start.replace(year=ny, month=nm).strftime("%Y-%m-%d")
    else:
        entries = _get_entries_date(date_str)
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        date_label = dt.strftime("%A, %B %d, %Y")
        prev_date = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
        next_date = (dt + timedelta(days=1)).strftime("%Y-%m-%d")

    total_seconds = 0
    project_totals = {}
    tag_totals = {}
    for e in entries:
        start_t = datetime.fromisoformat(e["start_time"])
        end_t = datetime.fromisoformat(e["end_time"]) if e["end_time"] else datetime.utcnow()
        dur = (end_t - start_t).total_seconds()
        total_seconds += dur
        proj = e.get("project_name") or "Uncategorized"
        proj_color = e.get("project_color") or "#6B7FFF"
        project_totals[proj] = {"seconds": project_totals.get(proj, {}).get("seconds", 0) + dur, "color": proj_color}
        for t in e.get("tags", []):
            tag_totals[t["name"]] = {"seconds": tag_totals.get(t["name"], {}).get("seconds", 0) + dur, "color": t["color"]}

    projects = query("SELECT * FROM projects ORDER BY name")
    tags = query("SELECT * FROM tags WHERE is_archived=0 ORDER BY name")

    return render_template("dashboard.html",
        entries=entries, date_str=date_str, date_label=date_label,
        view=view, prev_date=prev_date, next_date=next_date,
        total_seconds=total_seconds, project_totals=project_totals,
        tag_totals=tag_totals, projects=projects, tags=tags,
        today=datetime.utcnow().strftime("%Y-%m-%d"),
    )


@app.route("/settings")
def settings_page():
    projects = query("SELECT * FROM projects ORDER BY name")
    tags = query("SELECT * FROM tags ORDER BY name")
    monthly_cost = 0
    api_usage = []
    try:
        first = datetime.utcnow().replace(day=1).strftime("%Y-%m-%d")
        cost_row = query("SELECT COALESCE(SUM(estimated_cost_usd), 0) as total FROM api_usage WHERE date(timestamp) >= ?", [first])
        monthly_cost = cost_row[0]["total"] if cost_row else 0
    except Exception:
        pass
    return render_template("settings.html",
        projects=projects, tags=tags, monthly_cost=monthly_cost,
        api_usage=api_usage, cost_limit=1.0,
    )


@app.route("/api/summary/weekly")
def api_weekly_summary():
    today = datetime.utcnow().date()
    week_ago = today - timedelta(days=6)
    entries = _get_entries_range(week_ago.isoformat(), today.isoformat())
    total = 0; by_project = {}; by_tag = {}; by_day = {}
    for e in entries:
        s = datetime.fromisoformat(e["start_time"])
        en = datetime.fromisoformat(e["end_time"]) if e["end_time"] else datetime.utcnow()
        d = (en - s).total_seconds(); total += d
        p = e.get("project_name") or "Uncategorized"
        by_project[p] = by_project.get(p, 0) + d
        dk = s.strftime("%Y-%m-%d"); by_day[dk] = by_day.get(dk, 0) + d
        for t in e.get("tags", []):
            by_tag[t["name"]] = by_tag.get(t["name"], 0) + d
    return jsonify({
        "total_hours": round(total/3600, 1),
        "by_project": {k: round(v/3600, 1) for k, v in sorted(by_project.items(), key=lambda x: -x[1])},
        "by_tag": {k: round(v/3600, 1) for k, v in sorted(by_tag.items(), key=lambda x: -x[1])},
        "by_day": {k: round(v/3600, 1) for k, v in sorted(by_day.items())},
        "start_date": week_ago.isoformat(), "end_date": today.isoformat(),
    })


@app.route("/api/export/csv")
def api_export_csv():
    entries = query(
        "SELECT te.id, te.raw_input, te.description, p.name as project, "
        "te.start_time, te.end_time, te.log_timezone, te.created_at "
        "FROM time_entries te LEFT JOIN projects p ON te.project_id=p.id ORDER BY te.start_time"
    )
    import csv, io
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id","raw_input","description","project","tags","start_time_utc","end_time_utc","log_timezone","created_at"])
    for e in entries:
        tag_rows = query("SELECT t.name FROM tags t JOIN entry_tags et ON t.id=et.tag_id WHERE et.entry_id=?", [e["id"]])
        tags_str = ", ".join(r["name"] for r in tag_rows)
        w.writerow([e["id"], e["raw_input"], e["description"], e["project"],
                     tags_str, e["start_time"], e["end_time"], e.get("log_timezone","UTC"), e["created_at"]])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=timetracker_export.csv"})


# Template filters
@app.template_filter("duration")
def duration_filter(seconds):
    if not seconds: return "0m"
    h = int(seconds // 3600); m = int((seconds % 3600) // 60)
    return f"{h}h {m}m" if h > 0 else f"{m}m"

@app.template_filter("time_only")
def time_only_filter(dt_str):
    if not dt_str: return "now"
    try: return datetime.fromisoformat(dt_str).strftime("%H:%M")
    except: return dt_str


# --- Helpers ---

def _get_entries_date(date_str):
    rows = query(
        "SELECT te.*, p.name as project_name, p.color as project_color "
        "FROM time_entries te LEFT JOIN projects p ON te.project_id=p.id "
        "WHERE date(te.start_time)=? ORDER BY te.start_time", [date_str])
    return _attach_tags(rows)

def _get_entries_range(start, end):
    rows = query(
        "SELECT te.*, p.name as project_name, p.color as project_color "
        "FROM time_entries te LEFT JOIN projects p ON te.project_id=p.id "
        "WHERE date(te.start_time) BETWEEN ? AND ? ORDER BY te.start_time", [start, end])
    return _attach_tags(rows)

def _attach_tags(entries):
    for e in entries:
        tags = query("SELECT t.* FROM tags t JOIN entry_tags et ON t.id=et.tag_id WHERE et.entry_id=?", [e["id"]])
        e["tags"] = tags
    return entries
