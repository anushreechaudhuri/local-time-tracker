import sqlite3
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import contextmanager

import config

# Thread-local persistent connections — avoids opening/closing on every call.
# SQLite in WAL mode supports concurrent readers across threads.
_local = threading.local()


def get_connection():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")  # faster writes, still safe with WAL
        _local.conn = conn
    return conn


@contextmanager
def db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db():
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                color TEXT NOT NULL DEFAULT '#6B7FFF',
                is_default INTEGER DEFAULT 0,
                is_archived INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                color TEXT NOT NULL DEFAULT '#888888',
                is_archived INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS time_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_input TEXT,
                description TEXT,
                project_id INTEGER REFERENCES projects(id),
                start_time TEXT NOT NULL,
                end_time TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS entry_tags (
                entry_id INTEGER REFERENCES time_entries(id) ON DELETE CASCADE,
                tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (entry_id, tag_id)
            );

            CREATE TABLE IF NOT EXISTS api_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT (datetime('now', 'localtime')),
                model TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                estimated_cost_usd REAL DEFAULT 0.0
            );
        """)

    # Migrations for existing DBs
    with db() as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(time_entries)").fetchall()]
        if "raw_input" not in cols:
            conn.execute("ALTER TABLE time_entries ADD COLUMN raw_input TEXT")

        proj_cols = [row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()]
        if "description" not in proj_cols:
            conn.execute("ALTER TABLE projects ADD COLUMN description TEXT DEFAULT ''")

        tag_cols = [row[1] for row in conn.execute("PRAGMA table_info(tags)").fetchall()]
        if "description" not in tag_cols:
            conn.execute("ALTER TABLE tags ADD COLUMN description TEXT DEFAULT ''")

        entry_cols = [row[1] for row in conn.execute("PRAGMA table_info(time_entries)").fetchall()]
        if "log_timezone" not in entry_cols:
            conn.execute("ALTER TABLE time_entries ADD COLUMN log_timezone TEXT DEFAULT 'UTC'")

    _seed_data()


def _seed_data():
    with db() as conn:
        for p in config.SEED_PROJECTS:
            conn.execute(
                "INSERT OR IGNORE INTO projects (name, color, is_default) VALUES (?, ?, ?)",
                (p["name"], p["color"], int(p.get("is_default", False))),
            )
        for t in config.SEED_TAGS:
            conn.execute(
                "INSERT OR IGNORE INTO tags (name, color) VALUES (?, ?)",
                (t["name"], t["color"]),
            )


# --- Projects ---

def get_projects(include_archived=False):
    with db() as conn:
        if include_archived:
            return conn.execute("SELECT * FROM projects ORDER BY is_default DESC, name").fetchall()
        return conn.execute(
            "SELECT * FROM projects WHERE is_archived=0 ORDER BY is_default DESC, name"
        ).fetchall()


def get_project_by_name(name):
    with db() as conn:
        return conn.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()


def add_project(name, color="#6B7FFF"):
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO projects (name, color) VALUES (?, ?)", (name, color))
        return conn.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()


def delete_project(project_id):
    """Delete a project and set its entries' project_id to NULL (uncategorized)."""
    with db() as conn:
        conn.execute("UPDATE time_entries SET project_id=NULL WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))


def update_project(project_id, **kwargs):
    allowed = {"name", "color", "is_archived", "description"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [project_id]
    with db() as conn:
        conn.execute(f"UPDATE projects SET {set_clause} WHERE id=?", values)


# --- Tags ---

def get_tags(include_archived=False):
    with db() as conn:
        if include_archived:
            return conn.execute("SELECT * FROM tags ORDER BY name").fetchall()
        return conn.execute(
            "SELECT * FROM tags WHERE is_archived=0 ORDER BY name"
        ).fetchall()


def get_tag_by_name(name):
    with db() as conn:
        return conn.execute("SELECT * FROM tags WHERE name=?", (name,)).fetchone()


def add_tag(name, color="#888888"):
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO tags (name, color) VALUES (?, ?)", (name, color))
        return conn.execute("SELECT * FROM tags WHERE name=?", (name,)).fetchone()


def update_tag(tag_id, **kwargs):
    allowed = {"name", "color", "is_archived", "description"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [tag_id]
    with db() as conn:
        conn.execute(f"UPDATE tags SET {set_clause} WHERE id=?", values)


# --- Time Entries ---

def create_entry(description, project_id, tag_ids=None, start_time=None, raw_input=None, log_timezone=None):
    start = start_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tz = log_timezone or "UTC"
    with db() as conn:
        cursor = conn.execute(
            "INSERT INTO time_entries (raw_input, description, project_id, start_time, is_active, log_timezone) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (raw_input, description, project_id, start, tz),
        )
        entry_id = cursor.lastrowid
        if tag_ids:
            for tid in tag_ids:
                conn.execute("INSERT OR IGNORE INTO entry_tags (entry_id, tag_id) VALUES (?, ?)", (entry_id, tid))
        return entry_id


def create_split_entries(description, proj_a_id, proj_b_id, tag_ids, start_time, end_time, split_pct, raw_input=None):
    """Create two entries splitting the time period by split_pct (% for project A)."""
    start = datetime.fromisoformat(start_time) if isinstance(start_time, str) else start_time
    end = datetime.fromisoformat(end_time) if isinstance(end_time, str) else end_time
    total = (end - start).total_seconds()
    mid = start + timedelta(seconds=total * split_pct / 100)

    fmt = "%Y-%m-%d %H:%M:%S"
    a_id = create_entry(description, proj_a_id, tag_ids, start.strftime(fmt), raw_input=raw_input)
    update_entry(a_id, end_time=mid.strftime(fmt), is_active=0)

    b_id = create_entry(description, proj_b_id, tag_ids, mid.strftime(fmt), raw_input=raw_input)
    update_entry(b_id, end_time=end.strftime(fmt), is_active=0)

    return a_id, b_id


def delete_tag(tag_id):
    with db() as conn:
        conn.execute("DELETE FROM entry_tags WHERE tag_id=?", (tag_id,))
        conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))


def stop_active_entry():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db() as conn:
        conn.execute(
            "UPDATE time_entries SET is_active=0, end_time=? WHERE is_active=1",
            (now,),
        )


def get_active_entry():
    with db() as conn:
        entry = conn.execute(
            "SELECT te.*, p.name as project_name, p.color as project_color "
            "FROM time_entries te LEFT JOIN projects p ON te.project_id=p.id "
            "WHERE te.is_active=1 ORDER BY te.id DESC LIMIT 1"
        ).fetchone()
        if entry:
            tags = conn.execute(
                "SELECT t.* FROM tags t JOIN entry_tags et ON t.id=et.tag_id WHERE et.entry_id=?",
                (entry["id"],),
            ).fetchall()
            return dict(entry), [dict(t) for t in tags]
    return None, []


def get_entries_for_date(date_str):
    """Get entries for a specific date (YYYY-MM-DD)."""
    with db() as conn:
        entries = conn.execute(
            "SELECT te.*, p.name as project_name, p.color as project_color "
            "FROM time_entries te LEFT JOIN projects p ON te.project_id=p.id "
            "WHERE date(te.start_time)=? ORDER BY te.start_time",
            (date_str,),
        ).fetchall()
        result = []
        for e in entries:
            tags = conn.execute(
                "SELECT t.* FROM tags t JOIN entry_tags et ON t.id=et.tag_id WHERE et.entry_id=?",
                (e["id"],),
            ).fetchall()
            entry_dict = dict(e)
            entry_dict["tags"] = [dict(t) for t in tags]
            result.append(entry_dict)
        return result


def get_entries_for_range(start_date, end_date):
    """Get entries between two dates (inclusive, YYYY-MM-DD)."""
    with db() as conn:
        entries = conn.execute(
            "SELECT te.*, p.name as project_name, p.color as project_color "
            "FROM time_entries te LEFT JOIN projects p ON te.project_id=p.id "
            "WHERE date(te.start_time) BETWEEN ? AND ? ORDER BY te.start_time",
            (start_date, end_date),
        ).fetchall()
        result = []
        for e in entries:
            tags = conn.execute(
                "SELECT t.* FROM tags t JOIN entry_tags et ON t.id=et.tag_id WHERE et.entry_id=?",
                (e["id"],),
            ).fetchall()
            entry_dict = dict(e)
            entry_dict["tags"] = [dict(t) for t in tags]
            result.append(entry_dict)
        return result


def update_entry(entry_id, **kwargs):
    allowed = {"raw_input", "description", "project_id", "start_time", "end_time", "is_active", "log_timezone"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    tag_ids = kwargs.get("tag_ids")

    with db() as conn:
        if fields:
            set_clause = ", ".join(f"{k}=?" for k in fields)
            values = list(fields.values()) + [entry_id]
            conn.execute(f"UPDATE time_entries SET {set_clause} WHERE id=?", values)
        if tag_ids is not None:
            conn.execute("DELETE FROM entry_tags WHERE entry_id=?", (entry_id,))
            for tid in tag_ids:
                conn.execute("INSERT OR IGNORE INTO entry_tags (entry_id, tag_id) VALUES (?, ?)", (entry_id, tid))


def delete_entry(entry_id):
    with db() as conn:
        conn.execute("DELETE FROM entry_tags WHERE entry_id=?", (entry_id,))
        conn.execute("DELETE FROM time_entries WHERE id=?", (entry_id,))


def get_entry_by_id(entry_id):
    with db() as conn:
        entry = conn.execute(
            "SELECT te.*, p.name as project_name, p.color as project_color "
            "FROM time_entries te LEFT JOIN projects p ON te.project_id=p.id "
            "WHERE te.id=?",
            (entry_id,),
        ).fetchone()
        if entry:
            tags = conn.execute(
                "SELECT t.* FROM tags t JOIN entry_tags et ON t.id=et.tag_id WHERE et.entry_id=?",
                (entry_id,),
            ).fetchall()
            return dict(entry), [dict(t) for t in tags]
    return None, []


# --- API Usage ---

def log_api_usage(model, prompt_tokens, completion_tokens, cost_usd):
    with db() as conn:
        conn.execute(
            "INSERT INTO api_usage (model, prompt_tokens, completion_tokens, estimated_cost_usd) "
            "VALUES (?, ?, ?, ?)",
            (model, prompt_tokens, completion_tokens, cost_usd),
        )


def get_monthly_api_cost():
    first_of_month = datetime.now().replace(day=1).strftime("%Y-%m-%d")
    with db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0) as total FROM api_usage WHERE date(timestamp) >= ?",
            (first_of_month,),
        ).fetchone()
        return row["total"] if row else 0.0


def get_api_usage_daily(days=30):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with db() as conn:
        return conn.execute(
            "SELECT date(timestamp) as day, SUM(estimated_cost_usd) as cost, "
            "SUM(prompt_tokens) as input_tok, SUM(completion_tokens) as output_tok "
            "FROM api_usage WHERE date(timestamp) >= ? GROUP BY date(timestamp) ORDER BY day",
            (cutoff,),
        ).fetchall()


# --- Summary helpers ---

def get_weekly_summary():
    """Returns summary data for the past 7 days."""
    today = datetime.now().date()
    week_ago = today - timedelta(days=6)
    entries = get_entries_for_range(week_ago.isoformat(), today.isoformat())

    total_seconds = 0
    by_project = {}
    by_tag = {}
    by_day = {}

    for e in entries:
        start = datetime.fromisoformat(e["start_time"])
        end = datetime.fromisoformat(e["end_time"]) if e["end_time"] else datetime.now()
        dur = (end - start).total_seconds()
        total_seconds += dur

        proj = e.get("project_name") or "Without task"
        by_project[proj] = by_project.get(proj, 0) + dur

        day_key = start.strftime("%Y-%m-%d")
        by_day[day_key] = by_day.get(day_key, 0) + dur

        for t in e.get("tags", []):
            by_tag[t["name"]] = by_tag.get(t["name"], 0) + dur

    return {
        "total_hours": round(total_seconds / 3600, 1),
        "by_project": {k: round(v / 3600, 1) for k, v in sorted(by_project.items(), key=lambda x: -x[1])},
        "by_tag": {k: round(v / 3600, 1) for k, v in sorted(by_tag.items(), key=lambda x: -x[1])},
        "by_day": {k: round(v / 3600, 1) for k, v in sorted(by_day.items())},
        "start_date": week_ago.isoformat(),
        "end_date": today.isoformat(),
    }


def export_all_entries_csv():
    """Export all entries as CSV string."""
    import csv
    import io
    with db() as conn:
        entries = conn.execute(
            "SELECT te.id, te.raw_input, te.description, p.name as project, "
            "te.start_time, te.end_time, te.log_timezone, te.created_at "
            "FROM time_entries te LEFT JOIN projects p ON te.project_id=p.id "
            "ORDER BY te.start_time"
        ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "raw_input", "description", "project", "tags", "start_time_utc", "end_time_utc", "log_timezone", "created_at"])
    for e in entries:
        with db() as conn:
            tags = conn.execute(
                "SELECT t.name FROM tags t JOIN entry_tags et ON t.id=et.tag_id WHERE et.entry_id=?",
                (e["id"],),
            ).fetchall()
        tag_str = ", ".join(t["name"] for t in tags)
        writer.writerow([e["id"], e["raw_input"], e["description"], e["project"],
                         tag_str, e["start_time"], e["end_time"], e.get("log_timezone", "UTC"), e["created_at"]])
    return output.getvalue()
