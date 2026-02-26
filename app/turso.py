"""Turso database client using the HTTP API.

Syncs local SQLite to Turso and provides a query interface for the Vercel deployment.
Uses httpx (already installed via openai dependency).
"""

import json
import logging
import httpx
import config

log = logging.getLogger(__name__)

TURSO_URL = config.TURSO_DB_URL.replace("libsql://", "https://") if config.TURSO_DB_URL else ""
TURSO_TOKEN = config.TURSO_DB_TOKEN


def _headers():
    return {"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"}


def execute(sql, params=None):
    """Execute a single SQL statement on Turso. Returns rows as list of dicts."""
    if not TURSO_URL or not TURSO_TOKEN:
        return []
    try:
        body = {
            "requests": [
                {"type": "execute", "stmt": {"sql": sql, "args": _format_params(params)}},
                {"type": "close"},
            ]
        }
        resp = httpx.post(f"{TURSO_URL}/v2/pipeline", headers=_headers(), json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("results", [{}])[0].get("response", {}).get("result", {})
        cols = [c["name"] for c in result.get("cols", [])]
        rows = []
        for row in result.get("rows", []):
            rows.append({col: _extract_value(val) for col, val in zip(cols, row)})
        return rows
    except Exception as e:
        log.error("Turso query failed: %s", e)
        return []


def execute_batch(statements):
    """Execute multiple SQL statements in a single request."""
    if not TURSO_URL or not TURSO_TOKEN:
        return
    try:
        requests = [{"type": "execute", "stmt": {"sql": sql, "args": _format_params(params)}}
                    for sql, params in statements]
        requests.append({"type": "close"})
        resp = httpx.post(f"{TURSO_URL}/v2/pipeline", headers=_headers(), json={"requests": requests}, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        log.error("Turso batch failed: %s", e)


def _format_params(params):
    if not params:
        return []
    return [{"type": "text" if isinstance(v, str) else "integer" if isinstance(v, int) else
             "float" if isinstance(v, float) else "null" if v is None else "text",
             "value": str(v) if v is not None else None}
            for v in params]


def _extract_value(val):
    if val.get("type") == "null":
        return None
    if val.get("type") == "integer":
        return int(val["value"])
    if val.get("type") == "float":
        return float(val["value"])
    return val.get("value")


def sync_local_to_turso():
    """Push the entire local SQLite database to Turso (full sync)."""
    import sqlite3
    if not TURSO_URL or not TURSO_TOKEN:
        return

    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row

    # Create tables
    schema_sql = [
        ("CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, name TEXT UNIQUE, color TEXT, is_default INTEGER DEFAULT 0, is_archived INTEGER DEFAULT 0, description TEXT DEFAULT '')", None),
        ("CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY, name TEXT UNIQUE, color TEXT, is_archived INTEGER DEFAULT 0, description TEXT DEFAULT '')", None),
        ("CREATE TABLE IF NOT EXISTS time_entries (id INTEGER PRIMARY KEY, raw_input TEXT, description TEXT, project_id INTEGER, start_time TEXT, end_time TEXT, is_active INTEGER DEFAULT 0, log_timezone TEXT DEFAULT 'UTC', created_at TEXT)", None),
        ("CREATE TABLE IF NOT EXISTS entry_tags (entry_id INTEGER, tag_id INTEGER, PRIMARY KEY (entry_id, tag_id))", None),
        ("CREATE TABLE IF NOT EXISTS api_usage (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, estimated_cost_usd REAL)", None),
    ]
    execute_batch(schema_sql)

    # Sync projects
    stmts = []
    for row in conn.execute("SELECT * FROM projects").fetchall():
        stmts.append((
            "INSERT OR REPLACE INTO projects (id, name, color, is_default, is_archived, description) VALUES (?, ?, ?, ?, ?, ?)",
            [row["id"], row["name"], row["color"], row["is_default"], row["is_archived"], row["description"] or ""],
        ))

    # Sync tags
    for row in conn.execute("SELECT * FROM tags").fetchall():
        stmts.append((
            "INSERT OR REPLACE INTO tags (id, name, color, is_archived, description) VALUES (?, ?, ?, ?, ?)",
            [row["id"], row["name"], row["color"], row["is_archived"], row["description"] or ""],
        ))

    # Sync entries
    for row in conn.execute("SELECT * FROM time_entries").fetchall():
        stmts.append((
            "INSERT OR REPLACE INTO time_entries (id, raw_input, description, project_id, start_time, end_time, is_active, log_timezone, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [row["id"], row["raw_input"], row["description"], row["project_id"],
             row["start_time"], row["end_time"], row["is_active"],
             row["log_timezone"] if "log_timezone" in row.keys() else "UTC",
             row["created_at"]],
        ))

    # Sync entry_tags
    for row in conn.execute("SELECT * FROM entry_tags").fetchall():
        stmts.append((
            "INSERT OR REPLACE INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
            [row["entry_id"], row["tag_id"]],
        ))

    conn.close()

    # Send in batches of 50
    for i in range(0, len(stmts), 50):
        execute_batch(stmts[i:i+50])

    log.info("Synced %d statements to Turso", len(stmts))
