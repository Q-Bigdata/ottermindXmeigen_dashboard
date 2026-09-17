"""Bounded, auditable Chat2DB reads with verified reconstruction of long JSON cells.

Only backend-owned SQL templates should call this module. The lexical read-only
guard is defense in depth, not a SQL sandbox or a substitute for DB permissions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import binascii
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import tempfile
import time
import uuid
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ENDPOINT = os.environ.get("CHAT2DB_MCP_URL", "http://127.0.0.1:11924/mcp")
PROTOCOL_VERSION = "2024-11-05"
DATA_SOURCE_ID = int(os.environ.get("CHAT2DB_DATASOURCE_ID", "202172"))
DATABASE = "umami"
SCHEMA = "public"
CHUNK_CHARACTERS = 175
CHUNKS_PER_ROW = 64
PAGE_ROWS = 45
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,80}\Z")
MARKER_PATTERN = re.compile(r"mgmcp:[0-9a-f]{32}:[0-9a-f]{32}\Z")


class MCPError(RuntimeError):
    """Contains a safe error code, never credentials or raw server error text."""


class CompletenessError(MCPError):
    pass


class BudgetError(MCPError):
    pass


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CompletenessError("duplicate_json_key")
        result[key] = value
    return result


def strict_json(text: str | bytes) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(CompletenessError("non_finite_json")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CompletenessError("invalid_json") from exc


def readonly_sql(sql: str) -> str:
    """Reject write keywords/multiple statements outside literals and comments."""
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 250_000:
        raise MCPError("invalid_sql_template")
    if "mgmcp:" in sql.lower():
        raise MCPError("reserved_query_marker")
    sql = sql.strip()
    if sql.endswith(";"):
        sql = sql[:-1].rstrip()
    cleaned = []
    index = 0
    while index < len(sql):
        if sql.startswith("--", index):
            end = sql.find("\n", index)
            index = len(sql) if end < 0 else end + 1
            cleaned.append(" ")
        elif sql.startswith("/*", index):
            depth, index = 1, index + 2
            while index < len(sql) and depth:
                if sql.startswith("/*", index):
                    depth, index = depth + 1, index + 2
                elif sql.startswith("*/", index):
                    depth, index = depth - 1, index + 2
                else:
                    index += 1
            if depth:
                raise MCPError("unterminated_sql_comment")
            cleaned.append(" ")
        elif sql[index] in ("'", '"'):
            quote, index = sql[index], index + 1
            while index < len(sql):
                if sql[index] == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        index += 2
                    else:
                        index += 1
                        break
                elif sql[index] == "\\":
                    # Backslash-escape interpretation depends on SQL settings; fail closed.
                    raise MCPError("backslash_sql_literal_not_supported")
                else:
                    index += 1
            else:
                raise MCPError("unterminated_sql_literal")
            cleaned.append(" ")
        else:
            if sql[index] in (";", "$"):
                raise MCPError("multiple_statements_or_dollar_sql_not_supported")
            cleaned.append(sql[index])
            index += 1
    visible = "".join(cleaned)
    words = re.findall(r"[a-zA-Z_][a-zA-Z_0-9]*", visible.lower())
    if not words or words[0] not in ("select", "with") or "select" not in words:
        raise MCPError("select_template_required")
    forbidden = {"insert", "update", "delete", "merge", "drop", "alter", "create", "truncate",
                 "grant", "revoke", "copy", "call", "do", "execute", "prepare", "vacuum", "analyze",
                 "refresh", "set", "reset", "lock", "into", "pg_sleep", "set_config", "nextval",
                 "setval", "dblink", "lo_import", "lo_export", "pg_advisory_lock", "pg_terminate_backend", "pg_cancel_backend"}
    if forbidden.intersection(words):
        raise MCPError("non_readonly_sql_rejected")
    return sql


@dataclass(frozen=True)
class TableResult:
    columns: list[str]
    rows: list[dict[str, str]]
    reported_rows: int
    has_next_page: bool


def parse_table(response: dict[str, Any], *, allow_missing_sql_type: bool = False) -> TableResult:
    """Decode the JSON-string-in-MCP-text table, preserving literal cell content."""
    result = response.get("result", response)
    if not isinstance(result, dict) or result.get("isError") is True:
        raise MCPError("mcp_tool_error")
    content = result.get("content")
    if (not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict)
            or content[0].get("type") != "text" or not isinstance(content[0].get("text"), str)):
        raise CompletenessError("unexpected_mcp_content")
    text = content[0]["text"]
    for _ in range(3):
        if text.lstrip().startswith('"'):
            text = strict_json(text)
            if not isinstance(text, str):
                raise CompletenessError("expected_encoded_text")
        else:
            break
    lines = text.splitlines()
    if sum(line.startswith("## Result ") for line in lines) != 1:
        raise CompletenessError("expected_one_sql_result")
    sql_types = [line for line in lines if line.startswith("sqlType:")]
    if ("success: true" not in lines
            or (sql_types and sql_types != ["sqlType: SELECT"])
            or (not sql_types and not allow_missing_sql_type)):
        raise MCPError("sql_execution_not_successful_select")
    count_lines = [(index, re.fullmatch(r"rows: (\d+), hasNextPage: (true|false)", line))
                   for index, line in enumerate(lines)]
    count_lines = [(index, match) for index, match in count_lines if match]
    if len(count_lines) != 1:
        raise CompletenessError("missing_row_metadata")
    index, match = count_lines[0]
    reported, has_next = int(match[1]), match[2] == "true"
    if has_next:
        raise CompletenessError("server_reports_more_rows")
    remaining = lines[index + 1:]
    if not remaining and reported == 0:
        return TableResult([], [], 0, False)
    if not remaining or not remaining[0].startswith("行号\t"):
        raise CompletenessError("missing_table_header")
    columns = remaining[0].split("\t")[1:]
    if len(columns) != len(set(columns)) or any(not column for column in columns):
        raise CompletenessError("invalid_table_columns")
    rows = []
    for row_number, line in enumerate(remaining[1:], 1):
        fields = line.split("\t")
        if len(fields) != len(columns) + 1 or fields[0] != str(row_number):
            raise CompletenessError("malformed_or_truncated_table")
        rows.append(dict(zip(columns, fields[1:])))
    if len(rows) != reported:
        raise CompletenessError("reported_and_visible_rows_differ")
    if reported >= 50:
        raise CompletenessError("server_row_cap_risk")
    return TableResult(columns, rows, reported, has_next)


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urllib_transport(request: dict[str, Any], headers: dict[str, str], timeout: float) -> HTTPResponse:
    """Never use environment proxies or follow a redirect with the MCP credential."""
    body = json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    req = Request(ENDPOINT, data=body, headers=headers, method="POST")
    with opener.open(req, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise CompletenessError("mcp_response_size_budget_exceeded")
        return HTTPResponse(response.status, {key.lower(): value for key, value in response.headers.items()}, raw)


def _rpc_body(response: HTTPResponse, expected_id: int) -> dict[str, Any]:
    """Accept JSON or an SSE message containing the matching JSON-RPC response."""
    if len(response.body) > MAX_RESPONSE_BYTES:
        raise CompletenessError("mcp_response_size_budget_exceeded")
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" in content_type:
        try:
            text = response.body.decode("utf-8")
        except UnicodeError as exc:
            raise CompletenessError("invalid_sse_encoding") from exc
        messages = []
        for event in re.split(r"\r?\n\r?\n", text):
            data = "\n".join(line[5:].lstrip(" ") for line in event.splitlines() if line.startswith("data:"))
            if data:
                candidate = strict_json(data)
                if isinstance(candidate, dict) and candidate.get("id") == expected_id:
                    messages.append(candidate)
        if len(messages) != 1:
            raise CompletenessError("missing_or_duplicate_sse_response")
        parsed = messages[0]
    else:
        parsed = strict_json(response.body)
    if not isinstance(parsed, dict) or parsed.get("jsonrpc") != "2.0" or parsed.get("id") != expected_id:
        raise CompletenessError("json_rpc_response_id_mismatch")
    if "error" in parsed or "result" not in parsed:
        raise MCPError("json_rpc_error")
    return parsed


class MCPClient:
    def __init__(self, output_dir: str | Path = "analysis", *, max_calls: int = 80, max_pages: int = 32,
                 timeout: float = 55, retries: int = 1, deadline_seconds: float = 240,
                 transport: Callable[[dict[str, Any], dict[str, str], float], HTTPResponse] | None = None) -> None:
        token = os.environ.get("CHAT2DB_MCP_TOKEN")
        if not token:
            raise MCPError("missing_CHAT2DB_MCP_TOKEN")
        if not (1 <= max_calls <= 500 and 1 <= max_pages <= 128 and 1 <= timeout <= 55
                and 0 <= retries <= 2 and 1 <= deadline_seconds <= 900):
            raise BudgetError("invalid_client_budget")
        self._token = token
        self.output_dir = Path(output_dir).resolve()
        for name in ("queries", "results"):
            (self.output_dir / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        self.max_calls, self.max_pages, self.timeout, self.retries = max_calls, max_pages, timeout, retries
        self.deadline = time.monotonic() + deadline_seconds
        self.transport = transport or urllib_transport
        self.calls = 0
        self._rpc_id = 0
        self._session: str | None = None
        self._initialized = False
        self._query_ids: set[str] = set()
        self.run_id = uuid.uuid4().hex
        self._active: dict[str, str] = {}
        self._cleanup_attempted: set[str] = set()
        self._cancelling = False
        self._closed = False
        self._signal_handlers: dict[int, Any] = {}
        self.last_cleanup: dict[str, Any] = {"status": "no_active_queries", "active_remaining": 0}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def install_signal_handlers(self) -> None:
        """Call in the main thread inside `with client`; SIGTERM unwinds through cleanup."""
        def interrupted(signum, frame):
            if self._cancelling:
                return  # Let the bounded cleanup finish; the parent still has a hard kill budget.
            raise SystemExit(128 + signum)
        for signum in (signal.SIGTERM, signal.SIGINT):
            if signum not in self._signal_handlers:
                self._signal_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, interrupted)

    def close(self) -> dict[str, Any]:
        if not self._closed:
            if any(marker not in self._cleanup_attempted for marker in self._active):
                self.cancel_active()
            self._closed = True
            for signum, previous in self._signal_handlers.items():
                signal.signal(signum, previous)
            self._signal_handlers.clear()
        return dict(self.last_cleanup)

    def _write(self, directory: str, filename: str, value: Any, *, plain: bool = False) -> None:
        raw = value if plain else json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)
        if self._token in raw:
            raise MCPError("credential_in_evidence_rejected")
        destination = self.output_dir / directory / filename
        fd, temp = tempfile.mkstemp(prefix=".evidence-", dir=destination.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, destination)
        finally:
            Path(temp).unlink(missing_ok=True)

    def _call(self, method: str, params: dict[str, Any], evidence_id: str, *, notification: bool = False,
              cleanup_deadline: float | None = None) -> dict[str, Any]:
        self._rpc_id += 1
        request = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            request["id"] = self._rpc_id
        # Retrying a SELECT after an HTTP timeout can leave the first expensive
        # statement running. Only handshake traffic may retry automatically.
        retry_limit = self.retries if method != "tools/call" and cleanup_deadline is None else 0
        for attempt in range(retry_limit + 1):
            remaining = (cleanup_deadline if cleanup_deadline is not None else self.deadline) - time.monotonic()
            if (cleanup_deadline is None and self.calls >= self.max_calls) or remaining <= 0:
                raise BudgetError("mcp_call_or_time_budget_exceeded")
            self.calls += 1
            stem = f"{evidence_id}-rpc{self._rpc_id:04d}-try{attempt + 1}"
            self._write("queries", f"{stem}.json", {"requested_at": timestamp(), "endpoint": ENDPOINT, "request": request})
            headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                       "X-Chat2DB-MCP-Token": self._token}
            if self._session:
                headers["Mcp-Session-Id"] = self._session
            if self._initialized:
                headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
            try:
                call_timeout = min(5, remaining) if cleanup_deadline is not None else min(self.timeout, remaining)
                response = self.transport(request, headers, call_timeout)
            except (HTTPError, URLError, TimeoutError, socket.timeout) as exc:
                retryable = retry_limit > 0 and (not isinstance(exc, HTTPError) or exc.code in (502, 503, 504))
                self._write("results", f"{stem}.error.json", {"at": timestamp(), "error": "transport_failure", "retryable": retryable})
                if not retryable or attempt >= retry_limit:
                    raise MCPError("mcp_transport_failure") from None
                continue  # Only initialization/notification and read-only SELECT calls reach here.
            except Exception:
                raise MCPError("mcp_transport_failure") from None
            self._write("results", f"{stem}.raw.json", {"received_at": timestamp(), "http_status": response.status,
                         "content_type": response.headers.get("content-type"),
                         "raw_response": response.body.decode("utf-8", errors="strict")})
            if response.status in (502, 503, 504) and attempt < retry_limit:
                continue
            if not 200 <= response.status < 300:
                raise MCPError("unexpected_http_status")
            if notification:
                if response.body.strip():
                    raise CompletenessError("unexpected_notification_response")
                return {}
            parsed = _rpc_body(response, self._rpc_id)
            if method == "initialize":
                session = response.headers.get("mcp-session-id")
                if session and (len(session) > 512 or "\r" in session or "\n" in session):
                    raise MCPError("invalid_mcp_session")
                self._session = session
            return parsed
        raise MCPError("mcp_retry_exhausted")

    def initialize(self) -> None:
        if self._initialized:
            return
        response = self._call("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                              "clientInfo": {"name": "meigen-analysis-readonly", "version": "1.0"}}, "MCP-INIT")
        result = response["result"]
        if not isinstance(result, dict) or result.get("protocolVersion") != PROTOCOL_VERSION:
            raise MCPError("mcp_protocol_mismatch")
        self._initialized = True
        try:
            self._call("notifications/initialized", {}, "MCP-INITIALIZED", notification=True)
        except Exception:
            self._initialized = False
            raise

    def _begin_query(self, query_id: str, sql: str) -> str:
        if self._closed:
            raise MCPError("client_closed")
        if self._active:
            raise MCPError("previous_query_cleanup_unresolved")
        if not ID_PATTERN.fullmatch(query_id) or query_id in self._query_ids:
            raise MCPError("invalid_or_reused_query_id")
        safe_sql = readonly_sql(sql)
        self._query_ids.add(query_id)
        self._write("queries", f"{query_id}.sql", safe_sql + "\n", plain=True)
        self.initialize()
        return safe_sql

    def _execute(self, query_id: str, sql: str, *, payload_chunks: bool = False) -> TableResult:
        # Validate generated wrappers too; no hidden dynamic statement expansion.
        safe_sql = readonly_sql(sql)
        marker = f"mgmcp:{self.run_id}:{uuid.uuid4().hex}"
        if not MARKER_PATTERN.fullmatch(marker):
            raise MCPError("invalid_internal_marker")
        # Chat2DB classifies a leading comment as a non-query. A one-row VALUES
        # carrier preserves a real SELECT prefix and keeps the nonce as a literal
        # in the activity text even if a parser removes comments. Only q columns
        # are returned, so the marker does not alter the analysis result schema.
        marked_sql = (f"SELECT q.* FROM (VALUES ('{marker}')) AS mgmcp_marker(marker) "
                      f"CROSS JOIN (\n{safe_sql}\n) AS q")
        if payload_chunks:
            marked_sql += " ORDER BY q.part"
        readonly_sql(marked_sql.replace(marker, "internal_generated_marker"))
        self._write("queries", f"{query_id}.sql", marked_sql + "\n", plain=True)
        self._active[marker] = query_id
        try:
            response = self._call("tools/call", {"name": "execute_sql", "arguments": {
                "dataSourceId": DATA_SOURCE_ID, "databaseName": DATABASE, "schemaName": SCHEMA,
                "pageSize": 50, "sql": marked_sql,
            }}, query_id)
            self._active.pop(marker, None)  # A complete tool response means this SQL finished.
            # Chat2DB can omit sqlType even for a completed SELECT. Both the
            # original template and generated wrapper were already read-guarded;
            # accept absent metadata here, but never an explicit non-SELECT type.
            return parse_table(response, allow_missing_sql_type=True)
        except BaseException:
            self.cancel_active()
            raise

    def _cleanup_sql(self, markers: tuple[str, ...], *, cancel: bool) -> str:
        if (not markers or len(markers) > self.max_calls
                or any(not MARKER_PATTERN.fullmatch(marker) or marker not in self._active
                       or not marker.startswith(f"mgmcp:{self.run_id}:") for marker in markers)):
            raise MCPError("unowned_cleanup_marker")
        # Chat2DB can wrap SQL in SELECT * FROM (...), so the exact generated
        # nonce need not start at offset zero. Search only the recorded first
        # 1024 characters. Split the marker literal so the cleanup statement does
        # not itself contain the complete marker it is looking for.
        predicates = " OR ".join(
            f"position(('mgmcp:' || '{marker[len('mgmcp:'):]}') in left(query,1024))>0"
            for marker in markers
        )
        where = ("datname='umami' AND current_database()='umami' AND usename=current_user "
                 "AND pid<>pg_backend_pid() AND state='active' AND (" + predicates + ")")
        if cancel:
            return ("WITH owned_targets AS MATERIALIZED (SELECT pid FROM pg_stat_activity WHERE " + where + ") "
                    "SELECT count(*) AS matched, count(*) FILTER(WHERE pg_cancel_backend(pid)) AS cancel_accepted FROM owned_targets")
        return "SELECT count(*) AS active_remaining FROM pg_stat_activity WHERE " + where

    def cancel_active(self) -> dict[str, Any]:
        """Cancel and verify only this client's generated exact markers; no caller SQL/PID."""
        if self._cancelling:
            return {"status": "unknown", "reason": "cleanup_already_running", "active_remaining": None}
        if not self._active:
            return dict(self.last_cleanup)
        self._cancelling = True
        markers = tuple(self._active)
        self._cleanup_attempted.update(markers)
        cleanup_id = f"CLEANUP-{self.run_id}-{uuid.uuid4().hex[:8]}"
        deadline = time.monotonic() + 10
        summary = {"status": "unknown", "run_id": self.run_id, "queries": list(self._active.values()),
                   "matched": None, "cancel_accepted": None, "active_remaining": None, "at": timestamp()}
        try:
            for cancel in (True, False):
                sql = self._cleanup_sql(markers, cancel=cancel)
                suffix = "cancel" if cancel else "verify"
                self._write("queries", f"{cleanup_id}-{suffix}.sql", sql + "\n", plain=True)
                response = self._call("tools/call", {"name": "execute_sql", "arguments": {
                    "dataSourceId": DATA_SOURCE_ID, "databaseName": DATABASE, "schemaName": SCHEMA,
                    "pageSize": 50, "sql": sql,
                }}, f"{cleanup_id}-{suffix}", cleanup_deadline=deadline)
                # The actual MCP cleanup response omits sqlType. Only this fixed
                # internally generated SELECT may accept that absent metadata;
                # an explicitly non-SELECT response remains an error.
                table = parse_table(response, allow_missing_sql_type=True)
                columns = ["matched", "cancel_accepted"] if cancel else ["active_remaining"]
                if table.columns != columns or len(table.rows) != 1:
                    raise CompletenessError("invalid_cleanup_response")
                values = {key: int(table.rows[0][key]) for key in columns}
                if any(value < 0 for value in values.values()):
                    raise CompletenessError("invalid_cleanup_counts")
                summary.update(values)
                if cancel:
                    if summary["cancel_accepted"] > summary["matched"]:
                        raise CompletenessError("invalid_cleanup_counts")
                    summary["status"] = "cancel_requested"
                elif values["active_remaining"] == 0 and summary["matched"] > 0:
                    summary["status"] = "verified_inactive"
                    for marker in markers:
                        self._active.pop(marker, None)
                elif values["active_remaining"] == 0:
                    # A queued RPC may not have started its SQL yet. Zero matches
                    # alone is not evidence that a timed-out request was cancelled.
                    summary["status"] = "unknown"
                    summary["reason"] = "no_matching_active_query_seen_pending_execution_unknown"
                else:
                    summary["status"] = "still_active"
        except Exception:
            # No server text, SQL, exception repr, IDs or credentials escape here.
            summary["status"] = "unknown"
            summary["reason"] = "cleanup_failed_or_unverified"
        finally:
            self.last_cleanup = summary
            self._cancelling = False
            try:
                self._write("results", f"{cleanup_id}.json", summary)
            except Exception:
                pass
        return dict(summary)

    def query_rows(self, query_id: str, sql: str, *, max_rows: int = PAGE_ROWS) -> list[dict[str, str]]:
        """Small bounded audits; returns cell strings, never assumes invisible rows absent."""
        if not 1 <= max_rows <= PAGE_ROWS:
            raise BudgetError("invalid_audit_row_budget")
        safe_sql = self._begin_query(query_id, sql)
        table = self._execute(f"{query_id}-rows", f"SELECT * FROM (\n{safe_sql}\n) AS audit_rows LIMIT {max_rows + 1}")
        if len(table.rows) > max_rows:
            raise CompletenessError("audit_row_budget_exceeded_use_query_payload")
        if any(len(cell) >= 200 or cell.endswith(("...", "…")) for row in table.rows for cell in row.values()):
            raise CompletenessError("possible_cell_truncation_use_query_payload")
        self._write("results", f"{query_id}.decoded.json", {"query_id": query_id, "rows": table.rows, "row_count": len(table.rows), "complete": True})
        return table.rows

    def query_payload(self, query_id: str, select_sql: str) -> Any:
        """Reconstruct UTF-8 JSON from 64 packed ASCII base64 chunks per table row."""
        safe_sql = self._begin_query(query_id, select_sql)
        chunk_columns = [f"chunk_{index:02d}" for index in range(1, CHUNKS_PER_ROW + 1)]
        numeric_columns = ["characters", "wire_characters", "total_chunks", "total_parts"]
        expected_columns = ["part"] + chunk_columns + numeric_columns + ["body_md5", "wire_md5"]
        chunk_select = ",\n       ".join(
            f"substring(wire FROM (((i-1)*{CHUNKS_PER_ROW}+{index})*{CHUNK_CHARACTERS}+1)::integer FOR {CHUNK_CHARACTERS}) AS {column}"
            for index, column in enumerate(chunk_columns)
        )
        chunks: list[str] = []
        expected = None
        part_count = 0
        for page in range(self.max_pages):
            offset = page * PAGE_ROWS
            wrapper = f"""WITH src AS (
{safe_sql}
), p AS MATERIALIZED (SELECT payload::text AS body FROM src), wire_data AS MATERIALIZED (
 SELECT body,replace(replace(encode(convert_to(body,'UTF8'),'base64'),chr(10),''),chr(13),'') AS wire FROM p
), packet AS MATERIALIZED (
 SELECT wire,length(body) AS characters,length(wire) AS wire_characters,
        ceil(length(wire)/{CHUNK_CHARACTERS}.0)::integer AS total_chunks,
        ceil(length(wire)/{CHUNK_CHARACTERS * CHUNKS_PER_ROW}.0)::integer AS total_parts,
        md5(body) AS body_md5,md5(wire) AS wire_md5
 FROM wire_data
)
SELECT i AS part,
       {chunk_select},
       characters,wire_characters,total_chunks,total_parts,body_md5,wire_md5
FROM packet CROSS JOIN LATERAL generate_series(1,total_parts) AS i
ORDER BY i LIMIT {PAGE_ROWS} OFFSET {offset}"""
            table = self._execute(f"{query_id}-p{page:03d}", wrapper, payload_chunks=True)
            if table.columns != expected_columns or not table.rows:
                raise CompletenessError("missing_payload_chunks_or_columns")
            for row in table.rows:
                try:
                    part = int(row["part"])
                    metadata = {key: int(row[key]) for key in numeric_columns}
                except ValueError as exc:
                    raise CompletenessError("invalid_chunk_metadata") from exc
                metadata.update(body_md5=row["body_md5"], wire_md5=row["wire_md5"])
                if (metadata["characters"] <= 0 or metadata["wire_characters"] <= 0
                        or metadata["wire_characters"] % 4 != 0
                        or metadata["total_chunks"] != math.ceil(metadata["wire_characters"] / CHUNK_CHARACTERS)
                        or metadata["total_parts"] != math.ceil(metadata["total_chunks"] / CHUNKS_PER_ROW)
                        or not re.fullmatch(r"[0-9a-f]{32}", metadata["body_md5"])
                        or not re.fullmatch(r"[0-9a-f]{32}", metadata["wire_md5"])):
                    raise CompletenessError("invalid_payload_size_or_digest")
                if expected is None:
                    expected = metadata
                    if expected["total_parts"] > self.max_pages * PAGE_ROWS:
                        raise BudgetError("payload_page_budget_exceeded")
                if metadata != expected:
                    raise CompletenessError("payload_changed_between_chunks_or_pages")
                if part != part_count + 1 or part > expected["total_parts"]:
                    raise CompletenessError("missing_duplicate_or_out_of_order_chunk")
                for index, column in enumerate(chunk_columns):
                    chunk_index = (part - 1) * CHUNKS_PER_ROW + index
                    expected_length = max(0, min(CHUNK_CHARACTERS, expected["wire_characters"] - chunk_index * CHUNK_CHARACTERS))
                    cell = row[column]
                    if len(cell) != expected_length or not cell.isascii():
                        raise CompletenessError("chunk_character_length_mismatch")
                    if expected_length:
                        if not re.fullmatch(r"[A-Za-z0-9+/=]+", cell):
                            raise CompletenessError("invalid_base64_chunk")
                        chunks.append(cell)
                    elif cell != "":
                        raise CompletenessError("nonempty_tail_chunk")
                part_count += 1
            expected_on_page = min(PAGE_ROWS, expected["total_parts"] - offset)
            if len(table.rows) != expected_on_page:
                raise CompletenessError("visible_page_incomplete")
            if part_count == expected["total_parts"]:
                wire = "".join(chunks)
                if (len(chunks) != expected["total_chunks"] or len(wire) != expected["wire_characters"]
                        or hashlib.md5(wire.encode("ascii")).hexdigest() != expected["wire_md5"]):
                    raise CompletenessError("reassembled_payload_integrity_mismatch")
                try:
                    body_bytes = base64.b64decode(wire, validate=True)
                    body = body_bytes.decode("utf-8", errors="strict")
                except (binascii.Error, UnicodeError, ValueError) as exc:
                    raise CompletenessError("invalid_base64_or_utf8_payload") from exc
                if (base64.b64encode(body_bytes).decode("ascii") != wire
                        or len(body) != expected["characters"]
                        or hashlib.md5(body_bytes).hexdigest() != expected["body_md5"]):
                    raise CompletenessError("reassembled_payload_integrity_mismatch")
                decoded = strict_json(body)
                self._write("results", f"{query_id}.decoded.json", decoded)
                self._write("results", f"{query_id}.integrity.json", {
                    "query_id": query_id, "complete": True, **expected,
                    "parts": part_count, "pages": page + 1, "chunks_per_row": CHUNKS_PER_ROW,
                    "chunk_characters": CHUNK_CHARACTERS, "wire_encoding": "base64_utf8_no_crlf",
                    "body_digest_definition": "MD5 of original JSON UTF-8 bytes",
                    "wire_digest_definition": "MD5 of ASCII base64 wire without CR/LF",
                    "verified_at": timestamp(), "data_source_id": DATA_SOURCE_ID, "database": DATABASE, "schema": SCHEMA,
                })
                return decoded
        raise BudgetError("payload_page_budget_exceeded")
