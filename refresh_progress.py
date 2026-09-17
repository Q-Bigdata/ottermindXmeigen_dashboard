"""Controlled stage telemetry for the local report refresh (no query or secrets)."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path


def progress(phase, completed=None, total=None):
    target = os.environ.get('MEIGEN_PROGRESS_PATH')
    request_id = os.environ.get('MEIGEN_REFRESH_REQUEST_ID')
    if not target or not request_id:
        return
    value = {'request_id': request_id, 'phase': phase,
             'updated_at': datetime.now(timezone.utc).isoformat()}
    if completed is not None and total is not None:
        value.update(completed=completed, total=total)
    path = Path(target)
    temporary = path.with_name(path.name + '.next')
    temporary.write_text(json.dumps(value, separators=(',', ':')))
    temporary.replace(path)
