"""Read the latest source event for the live dashboard.

This is deliberately a small, bounded read. It reports only freshness metadata,
never event properties, visit identifiers, account identifiers, or credentials.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile

from mcp_client import MCPClient, MCPError

SITE = "f56e75d7-ed8c-4ef2-bb90-c1bdb5df035f"
SOURCE = "(lower(referrer_domain) IN ('meigen.ai','www.meigen.ai') OR lower(utm_source) IN ('meigen','meigen.ai'))"
SQL = f"""
SELECT max(created_at) AS latest_event_at, count(*) AS event_count
FROM public.website_event
WHERE website_id='{SITE}' AND {SOURCE}
"""


def check() -> dict:
    checked = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if not os.environ.get("CHAT2DB_MCP_TOKEN"):
        return {"state": "unavailable", "reason": "missing_token", "checked_at": checked}
    temporary = Path(tempfile.mkdtemp(prefix="meigen-freshness-"))
    try:
        with MCPClient(output_dir=temporary, max_calls=8, max_pages=4, timeout=20, deadline_seconds=45, retries=0) as client:
            rows = client.query_rows("FRESHNESS", SQL, max_rows=1)
        row = rows[0] if rows else {}
        latest = row.get("latest_event_at") or None
        count = int(row.get("event_count") or 0)
        if not latest:
            return {"state": "empty", "checked_at": checked, "event_count": count}
        return {"state": "available", "checked_at": checked, "latest_event_at": latest, "event_count": count}
    except (MCPError, OSError, ValueError):
        return {"state": "error", "reason": "freshness_query_failed", "checked_at": checked}
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
