import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "timetracker.db"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = "gpt-5-nano"

# Turso cloud database
TURSO_DB_URL = os.getenv("TURSO_DB_URL", "")
TURSO_DB_TOKEN = os.getenv("TURSO_DB_TOKEN", "")

# Prompt interval in seconds (30 minutes)
PROMPT_INTERVAL = 30 * 60

# Multitask prompt interval in seconds (15 minutes)
MULTITASK_PROMPT_INTERVAL = 15 * 60

# Idle threshold in seconds (5 minutes)
IDLE_THRESHOLD = 5 * 60

# Activity poll interval in seconds
POLL_INTERVAL = 10

# Web dashboard
WEB_HOST = "127.0.0.1"
WEB_PORT = 5123

# API cost tracking
MONTHLY_COST_ALERT_USD = 1.00

# Cost per 1M tokens (gpt-5-nano estimates -- update if pricing changes)
COST_PER_1M_INPUT_TOKENS = 0.10
COST_PER_1M_OUTPUT_TOKENS = 0.40

# iCloud sync
ICLOUD_SYNC_DIR = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "TimeTracker"
ICLOUD_SYNC_INTERVAL = 5 * 60

# Default projects and tags -- customize during setup or in the dashboard
SEED_PROJECTS = [
    {"name": "Without task", "color": "#6B7FFF"},
    {"name": "Break", "color": "#9E9E9E"},
    {"name": "Work", "color": "#4CAF50"},
    {"name": "Personal", "color": "#FF9800"},
    {"name": "Side project", "color": "#9C27B0"},
]

SEED_TAGS = [
    {"name": "coding", "color": "#1565C0"},
    {"name": "writing", "color": "#E040FB"},
    {"name": "reading", "color": "#9C27B0"},
    {"name": "communications", "color": "#00BCD4"},
    {"name": "planning", "color": "#FF9800"},
    {"name": "creative", "color": "#FF5722"},
    {"name": "meeting", "color": "#2E7D32"},
    {"name": "admin", "color": "#9E9E9E"},
]
