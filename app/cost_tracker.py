import logging
import subprocess
from datetime import datetime

import config
from app import database

log = logging.getLogger(__name__)


def track_usage(model, prompt_tokens, completion_tokens):
    """Log an API call and check cost thresholds."""
    cost = (
        (prompt_tokens / 1_000_000) * config.COST_PER_1M_INPUT_TOKENS
        + (completion_tokens / 1_000_000) * config.COST_PER_1M_OUTPUT_TOKENS
    )
    database.log_api_usage(model, prompt_tokens, completion_tokens, cost)

    monthly_total = database.get_monthly_api_cost()
    _check_threshold(monthly_total)


def _check_threshold(monthly_total):
    """Alert if monthly cost exceeds or is projected to exceed the threshold."""
    if monthly_total >= config.MONTHLY_COST_ALERT_USD:
        _notify(f"API cost alert: ${monthly_total:.2f} this month (limit: ${config.MONTHLY_COST_ALERT_USD:.2f})")
        return

    # Project cost for the rest of the month
    now = datetime.now()
    day_of_month = now.day
    if day_of_month >= 2 and monthly_total > 0:
        days_in_month = 30  # rough estimate
        projected = monthly_total * (days_in_month / day_of_month)
        if projected >= config.MONTHLY_COST_ALERT_USD:
            _notify(
                f"API cost projected to reach ${projected:.2f} this month "
                f"(current: ${monthly_total:.2f}, limit: ${config.MONTHLY_COST_ALERT_USD:.2f})"
            )


def _notify(message):
    """Show a macOS notification."""
    log.warning(message)
    try:
        subprocess.run(
            [
                "osascript", "-e",
                f'display notification "{message}" with title "Time Tracker" subtitle "Cost Alert"',
            ],
            check=False,
            capture_output=True,
        )
    except Exception:
        pass
