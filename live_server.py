#!/usr/bin/env python3
"""Loopback-only HTTP service for atomically refreshed MeiGen reports.

The UI is optional and is served from ./report. The refresh runner is a sibling
script; credentials are inherited through the service environment and are never
included in HTTP responses or command-line arguments by this module.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

UTC = timezone.utc
MAX_REQUEST_BYTES = 4096
PHASE_LABELS = {
    "starting": "准备更新", "load_cache": "读取上一批完整数据",
    "extract_events": "读取新增访问与事件", "restore_visits": "补全新增访问路径",
    "extract_entries": "读取新增入口参数", "account_context": "读取访问身份上下文",
    "normalize": "整理访问与行为节点", "segments": "计算需求、流量和分层",
    "branches": "计算流程断点与分岔", "account_identity": "关联注册账户",
    "account_history": "读取账户回访与复用", "account_payments": "读取付费记录",
    "account_analysis": "计算回访、复用和付费分析", "assemble": "汇总完整报告",
    "validate": "校验本批报告", "publish": "发布完整数据快照",
    "complete": "更新完成", "cancelled": "本次更新已取消", "failed": "本次更新失败",
}


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected an ISO timestamp with a timezone.")
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Expected an ISO timestamp with a timezone.") from exc
    if dt.tzinfo is None:
        raise ValueError("Timestamp must include its timezone.")
    return dt.astimezone(UTC)


def validate_report(report: object, cutoff: datetime | None = None) -> dict:
    """Return public cache metadata after validating the agreed report schema."""
    if not isinstance(report, dict):
        raise ValueError("Report must be a JSON object.")
    generation = report.get("generation_id")
    if not isinstance(generation, str) or not generation.strip() or len(generation) > 200:
        raise ValueError("Report generation_id is missing or invalid.")
    generated = parse_timestamp(report.get("generated_at"))
    window = report.get("window")
    if not isinstance(window, dict):
        raise ValueError("Report window is missing.")
    start, end = parse_timestamp(window.get("start")), parse_timestamp(window.get("end"))
    if start >= end:
        raise ValueError("Report window.start must precede window.end.")
    if cutoff is not None and end != cutoff:
        raise ValueError("Report window.end does not match the requested cutoff.")
    try:
        if not isinstance(window.get("timezone"), str):
            raise ValueError("Missing timezone.")
        ZoneInfo(window["timezone"])
    except Exception as exc:
        raise ValueError("Report window.timezone is invalid.") from exc
    if not isinstance(report.get("data"), (dict, list)):
        raise ValueError("Report data must be an object or array.")
    if not isinstance(report.get("findings"), (dict, list)):
        raise ValueError("Report findings must be an object or array.")
    return {"available": True, "generation_id": generation, "generated_at": iso_utc(generated),
            "window": {"start": window["start"], "end": window["end"], "timezone": window["timezone"]}}


class RefreshManager:
    def __init__(self, root: Path, runner: Path | None = None, timeout_seconds: float = 1800,
                 cutoff_lag_seconds: int = 120, auto_interval_seconds: float = 300):
        self.root = root.resolve()
        self.runner = (runner or self.root / "refresh_pipeline.py").resolve()
        self.report_path = self.root / "data" / "report.json"
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.root / "data" / "live_settings.json"
        self.history_path = self.root / "data" / "refresh_history.jsonl"
        self.timeout_seconds = timeout_seconds
        self.cutoff_lag_seconds = cutoff_lag_seconds
        self.auto_interval_seconds = auto_interval_seconds
        self.lock = threading.RLock()
        self.worker: threading.Thread | None = None
        self.process: subprocess.Popen | None = None
        self.progress_path: Path | None = None
        self.closed = False
        self.refresh = {"state": "idle", "request_id": None, "requested_cutoff": None,
                        "started_at": None, "finished_at": None, "error": None,
                        "trigger": None, "cancel_requested": False, "phase": None,
                        "phase_label": None, "progress_updated_at": None, "elapsed_seconds": 0}
        self._started_monotonic: float | None = None
        self._cached_stat = None
        self._cached_metadata = {"available": False, "generation_id": None, "generated_at": None, "window": None}
        self.settings = {"auto_enabled": False, "auto_interval_seconds": auto_interval_seconds,
                         "updated_at": iso_utc(utc_now())}
        try:
            saved = json.loads(self.settings_path.read_text())
            self.settings["auto_enabled"] = saved.get("auto_enabled") is True
            if isinstance(saved.get("updated_at"), str):
                self.settings["updated_at"] = saved["updated_at"]
        except (OSError, ValueError, AttributeError):
            pass
        self.next_auto_at = utc_now() + timedelta(seconds=auto_interval_seconds) if self.settings["auto_enabled"] else None
        self._scheduler_stop = threading.Event()
        self.scheduler = threading.Thread(target=self._schedule, daemon=True, name="meigen-auto-refresh")
        self.scheduler.start()

    def _schedule(self) -> None:
        # A single service timer owns automatic refreshes across all browser tabs.
        while not self._scheduler_stop.wait(min(1, self.auto_interval_seconds)):
            with self.lock:
                due = (not self.closed and self.settings["auto_enabled"] and self.next_auto_at is not None
                       and utc_now() >= self.next_auto_at and self.refresh["state"] not in {"running", "cancelling"})
            if due:
                self.start_refresh(trigger="auto")

    def get_settings(self) -> dict:
        with self.lock:
            return {**self.settings, "next_auto_at": iso_utc(self.next_auto_at) if self.next_auto_at else None}

    def update_settings(self, auto_enabled: object) -> tuple[int, dict]:
        if not isinstance(auto_enabled, bool):
            return 400, {"error": {"code": "invalid_settings", "message": "auto_enabled must be a boolean."}}
        with self.lock:
            updated = {**self.settings, "auto_enabled": auto_enabled, "updated_at": iso_utc(utc_now())}
            try:
                atomic_json(self.settings_path, updated)
            except OSError:
                return 500, {"error": {"code": "settings_save_failed", "message": "Could not save automatic refresh settings."}}
            self.settings = updated
            self.next_auto_at = utc_now() + timedelta(seconds=self.auto_interval_seconds) if auto_enabled else None
            return 200, {"settings": self.get_settings(), "status": self.status()}

    def cache_metadata(self) -> dict:
        with self.lock:
            try:
                st = self.report_path.stat()
                marker = (st.st_mtime_ns, st.st_size, st.st_ino)
            except FileNotFoundError:
                return {"available": False, "generation_id": None, "generated_at": None, "window": None}
            if marker != self._cached_stat:
                try:
                    metadata = validate_report(json.loads(self.report_path.read_bytes()))
                except (OSError, ValueError, TypeError):
                    return {"available": False, "generation_id": None, "generated_at": None,
                            "window": None, "error": "invalid_cached_report"}
                self._cached_stat, self._cached_metadata = marker, metadata
            return json.loads(json.dumps(self._cached_metadata))

    def _read_progress(self) -> None:
        if not self.progress_path or self.refresh["state"] not in {"running", "cancelling"}:
            return
        try:
            value = json.loads(self.progress_path.read_text())
            phase = value.get("phase")
            if value.get("request_id") != self.refresh["request_id"] or phase not in PHASE_LABELS:
                return
            updated = iso_utc(parse_timestamp(value.get("updated_at")))
            if self.refresh.get("progress_updated_at") and updated < self.refresh["progress_updated_at"]:
                return
            self.refresh.update(phase=phase, phase_label=PHASE_LABELS[phase], progress_updated_at=updated)
            self.refresh.pop("units", None)
            # Only controlled numeric progress is exposed, never subprocess text.
            done, total = value.get("completed"), value.get("total")
            if type(done) is int and type(total) is int and 0 <= done <= total:
                self.refresh["units"] = {"completed": done, "total": total}
        except (OSError, ValueError, AttributeError):
            pass

    def status(self) -> dict:
        with self.lock:
            self._read_progress()
            report = self.cache_metadata()
            refresh = json.loads(json.dumps(self.refresh))
            if refresh["state"] in {"running", "cancelling"} and self._started_monotonic is not None:
                refresh["elapsed_seconds"] = max(0, int(time.monotonic() - self._started_monotonic))
            return {"server_time": iso_utc(utc_now()), "refresh": refresh, "report": report,
                    "settings": self.get_settings(),
                    "serving_previous_report": bool(report["available"] and refresh["state"] in {"running", "cancelling", "cancelled", "failed"}),
                    "refresh_policy": {"default_cutoff_lag_seconds": self.cutoff_lag_seconds,
                                       "suggested_ui_interval_seconds": self.auto_interval_seconds,
                                       "automatic_server_schedule": True, "timeout_seconds": self.timeout_seconds}}

    def _audit(self, event: str, **extra) -> None:
        # Safe fixed fields only: never URLs, environment, SQL, or runner output.
        record = {"at": iso_utc(utc_now()), "event": event, "request_id": self.refresh.get("request_id"),
                  "trigger": self.refresh.get("trigger"), **extra}
        try:
            with self.history_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        except OSError:
            pass

    def start_refresh(self, cutoff_value: object = None, trigger: str = "manual") -> tuple[int, dict]:
        if trigger not in {"manual", "auto"}:
            return 400, {"error": {"code": "invalid_trigger", "message": "trigger must be manual or auto."}}
        try:
            cutoff = parse_timestamp(cutoff_value) if cutoff_value is not None else utc_now() - timedelta(seconds=self.cutoff_lag_seconds)
            cutoff = cutoff.replace(microsecond=0)
        except ValueError as exc:
            return 400, {"error": {"code": "invalid_cutoff", "message": str(exc)}}
        if cutoff > utc_now():
            return 400, {"error": {"code": "future_cutoff", "message": "cutoff must not be in the future."}}
        with self.lock:
            if self.closed:
                return 503, {"error": {"code": "service_stopping", "message": "Service is stopping."}}
            if trigger == "auto" and not self.settings["auto_enabled"]:
                self._audit("rejected", trigger="auto", error_code="auto_refresh_disabled")
                return 409, {"error": {"code": "auto_refresh_disabled", "message": "Automatic refresh is disabled."}, "status": self.status()}
            if self.refresh["state"] in {"running", "cancelling"}:
                return 409, {"error": {"code": "refresh_already_running", "message": "A refresh is already running."}, "status": self.status()}
            if trigger == "auto" and self.next_auto_at and utc_now() < self.next_auto_at:
                return 409, {"error": {"code": "auto_refresh_not_due", "message": "The next automatic refresh is not due yet."}, "status": self.status()}
            request_id = uuid.uuid4().hex
            now = iso_utc(utc_now())
            self.refresh = {"state": "running", "request_id": request_id, "requested_cutoff": iso_utc(cutoff),
                            "started_at": now, "finished_at": None, "error": None, "trigger": trigger,
                            "cancel_requested": False, "phase": "starting", "phase_label": PHASE_LABELS["starting"],
                            "progress_updated_at": now, "elapsed_seconds": 0}
            self._started_monotonic = time.monotonic()
            self.next_auto_at = None
            self._audit("started", requested_cutoff=iso_utc(cutoff))
            self.worker = threading.Thread(target=self._run, args=(request_id, cutoff), daemon=True,
                                           name="meigen-report-refresh")
            self.worker.start()
            return 202, {"accepted": True, "request_id": request_id, "cutoff": iso_utc(cutoff), "status": self.status()}

    def cancel_refresh(self) -> tuple[int, dict]:
        with self.lock:
            if self.refresh["state"] not in {"running", "cancelling"}:
                return 200, {"accepted": False, "status": self.status()}
            self.refresh.update(state="cancelling", cancel_requested=True)
            self._audit("cancel_requested")
            # The owning worker observes this flag and terminates its own process group.
            return 202, {"accepted": True, "status": self.status()}

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait(timeout=5)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass

    def _run(self, request_id: str, cutoff: datetime) -> None:
        output_path = None
        progress_path = None
        failure = None
        success = None
        published = False
        try:
            if not self.runner.is_file():
                failure = {"code": "runner_missing", "message": "refresh_pipeline.py is not available."}
                return
            fd, temporary = tempfile.mkstemp(prefix=".report-refresh-", suffix=".json", dir=self.report_path.parent)
            os.close(fd)
            output_path = Path(temporary)
            progress_path = output_path.with_suffix(".progress.json")
            command = [sys.executable, str(self.runner), "--cutoff", iso_utc(cutoff), "--output", str(output_path)]
            env = {**os.environ, "MEIGEN_PROGRESS_PATH": str(progress_path), "MEIGEN_REFRESH_REQUEST_ID": request_id}
            with self.lock:
                self.progress_path = progress_path
                if self.closed or self.refresh["cancel_requested"]:
                    return
                process = subprocess.Popen(command, cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           start_new_session=(os.name == "posix"))
                self.process = process
            while True:
                with self.lock:
                    should_cancel = self.closed or self.refresh["cancel_requested"]
                if should_cancel:
                    self._terminate(process)
                    return
                remaining = self.timeout_seconds - (time.monotonic() - self._started_monotonic)
                if remaining <= 0:
                    self._terminate(process)
                    failure = {"code": "refresh_timeout", "message": f"Refresh exceeded the {self.timeout_seconds:g}-second time limit."}
                    return
                try:
                    exit_code = process.wait(timeout=min(.25, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if exit_code != 0:
                failure = {"code": "runner_failed", "message": "Refresh pipeline failed.", "exit_code": exit_code}
                return
            with self.lock:
                self._read_progress()
                self.refresh.update(phase="validate", phase_label=PHASE_LABELS["validate"], progress_updated_at=iso_utc(utc_now()))
            try:
                payload = output_path.read_bytes()
                parsed = json.loads(payload)
                success = validate_report(parsed, cutoff)
            except (ValueError, TypeError, OSError):
                failure = {"code": "invalid_report", "message": "Refresh produced an invalid report or mismatched cutoff."}
                return
            with self.lock:
                if self.closed or self.refresh["cancel_requested"]:
                    return
                self.refresh.update(phase="publish", phase_label=PHASE_LABELS["publish"], progress_updated_at=iso_utc(utc_now()))
                # The runner changes no current source cache. Readers and future
                # refreshes both select this complete generation through report.json.
                with output_path.open("rb") as handle:
                    os.fsync(handle.fileno())
                os.replace(output_path, self.report_path)
                output_path = None
                published = True
                self._cached_stat = None
                self.refresh["state"] = "succeeded"
        except OSError:
            failure = {"code": "refresh_io_error", "message": "Refresh could not start or publish its report."}
        except Exception:
            failure = {"code": "refresh_internal_error", "message": "Refresh failed unexpectedly."}
        finally:
            for path in (output_path, progress_path):
                if path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
            with self.lock:
                if self.refresh["request_id"] == request_id:
                    cancelled = not published and (self.closed or self.refresh["cancel_requested"])
                    state = "succeeded" if published else "cancelled" if cancelled else "failed"
                    phase = "complete" if published else "cancelled" if cancelled else "failed"
                    self.process = None
                    self.progress_path = None
                    self.refresh.update(state=state, finished_at=iso_utc(utc_now()),
                                        error=None if cancelled or published else failure or {"code": "refresh_internal_error", "message": "Refresh did not complete."},
                                        phase=phase, phase_label=PHASE_LABELS[phase], progress_updated_at=iso_utc(utc_now()),
                                        elapsed_seconds=max(0, int(time.monotonic()-self._started_monotonic)))
                    self.refresh.pop("units", None)
                    if published:
                        self.refresh["generation_id"] = success["generation_id"]
                    self.next_auto_at = utc_now() + timedelta(seconds=self.auto_interval_seconds) if self.settings["auto_enabled"] and not self.closed else None
                    self._audit("finished", state=state, elapsed_seconds=self.refresh["elapsed_seconds"],
                                error_code=(self.refresh.get("error") or {}).get("code"), generation_id=self.refresh.get("generation_id"))

    def close(self) -> None:
        with self.lock:
            self.closed = True
            if self.refresh["state"] in {"running", "cancelling"}:
                self.refresh.update(cancel_requested=True, state="cancelling")
            worker = self.worker
        self._scheduler_stop.set()
        self.scheduler.join(timeout=2)
        if worker is not None and worker.is_alive():
            worker.join(timeout=12)

class ReportHandler(SimpleHTTPRequestHandler):
    server_version = "MeiGenLocalReport/1.0"

    def __init__(self, *args, manager: RefreshManager, **kwargs):
        self.manager = manager
        self.ui_root = (manager.root / "report").resolve()
        super().__init__(*args, directory=str(self.ui_root), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        # Request paths and queries may carry unintended secrets. Log only
        # the method and response status, never URL/body/runner output.
        status = str(args[1]) if len(args) > 1 and str(args[1]).isdigit() else "-"
        print(f"{iso_utc(utc_now())} {self.command} {status}", file=sys.stderr, flush=True)

    def _local_request(self) -> bool:
        host = self.headers.get("Host", "")
        try:
            parsed = urlparse("http://" + host)
            port = parsed.port or 80
        except ValueError:
            return False
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or port != self.server.server_port:
            return False
        origin = self.headers.get("Origin")
        if origin:
            try:
                source = urlparse(origin)
                if source.scheme != "http" or source.hostname not in {"localhost", "127.0.0.1", "::1"} or (source.port or 80) != self.server.server_port:
                    return False
            except ValueError:
                return False
        return True

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_GET(self) -> None:
        if not self._local_request():
            self._json(403, {"error": {"code": "local_origin_required", "message": "Use this service from its localhost URL."}})
            return
        path = urlparse(self.path).path
        if path == "/api/status":
            self._json(200, self.manager.status())
            return
        if path == "/api/settings":
            self._json(200, {"settings": self.manager.get_settings()})
            return
        if path == "/api/report":
            with self.manager.lock:
                metadata = self.manager.cache_metadata()
                try:
                    payload = self.manager.report_path.read_bytes() if metadata["available"] else None
                except OSError:
                    payload = None
            if payload is None:
                self._json(503, {"error": {"code": "report_unavailable", "message": "No successfully generated report is available yet."}, "status": self.manager.status()})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            return
        if path.startswith("/api/"):
            self._json(404, {"error": {"code": "not_found", "message": "Unknown API endpoint."}})
            return
        self._static(path)

    def do_HEAD(self) -> None:
        self.do_GET()

    def _static(self, path: str) -> None:
        relative = unquote(path).lstrip("/")
        candidate = (self.ui_root / relative).resolve()
        try:
            candidate.relative_to(self.ui_root)
        except ValueError:
            self._json(404, {"error": {"code": "not_found", "message": "File not found."}})
            return
        if candidate.is_dir():
            candidate = (candidate / "index.html").resolve()
            try:
                candidate.relative_to(self.ui_root)
            except ValueError:
                self._json(404, {"error": {"code": "not_found", "message": "File not found."}})
                return
        if not candidate.is_file():
            message = "The report UI has not been created yet. API endpoints are available." if path == "/" else "File not found."
            self._json(404, {"error": {"code": "ui_unavailable" if path == "/" else "not_found", "message": message}})
            return
        try:
            payload = candidate.read_bytes()
        except OSError:
            self._json(404, {"error": {"code": "not_found", "message": "File not found."}})
            return
        self.send_response(200)
        mime = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") or mime in {"application/javascript", "application/json"} else ""))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_POST(self) -> None:
        if not self._local_request():
            self._json(403, {"error": {"code": "local_origin_required", "message": "Use this service from its localhost URL."}})
            return
        path = urlparse(self.path).path
        if path not in {"/api/refresh", "/api/refresh/cancel", "/api/settings"}:
            self._json(404, {"error": {"code": "not_found", "message": "Unknown API endpoint."}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": {"code": "invalid_request", "message": "Invalid Content-Length."}})
            return
        if length < 0 or length > MAX_REQUEST_BYTES or self.headers.get("Transfer-Encoding"):
            self._json(413, {"error": {"code": "invalid_request", "message": "Request body is too large or uses unsupported encoding."}})
            return
        try:
            body = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(body, dict):
                raise ValueError("Expected JSON object")
        except (ValueError, UnicodeError):
            self._json(400, {"error": {"code": "invalid_json", "message": "Request body must be a JSON object."}})
            return
        if path == "/api/settings":
            code, data = self.manager.update_settings(body.get("auto_enabled"))
        elif path == "/api/refresh/cancel":
            code, data = self.manager.cancel_refresh()
        elif body.get("trigger") not in {"manual", "auto"}:
            # Legacy tabs submitted {} every five minutes. Reject these so a
            # stale page cannot bypass the shared automatic refresh switch.
            code, data = 400, {"error": {"code": "require_refresh_trigger", "message": "Choose an explicit manual or auto refresh trigger."}}
        else:
            code, data = self.manager.start_refresh(body.get("cutoff"), trigger=body["trigger"])
        self._json(code, data)


def make_server(root: Path, host: str = "127.0.0.1", port: int = 8765, **manager_options) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("This report service only binds to localhost/127.0.0.1.")
    manager = RefreshManager(root, **manager_options)
    server = ThreadingHTTPServer((host, port), partial(ReportHandler, manager=manager))
    server.daemon_threads = True
    server.manager = manager
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--runner", type=Path, default=None, help="Defaults to <root>/refresh_pipeline.py")
    parser.add_argument("--timeout", type=float, default=1800, help="Maximum pipeline duration in seconds")
    parser.add_argument("--cutoff-lag", type=int, default=120, help="Seconds behind now for automatic cutoffs")
    args = parser.parse_args()
    if args.timeout <= 0 or args.cutoff_lag < 0:
        parser.error("timeout must be positive and cutoff-lag must not be negative")
    server = make_server(args.root, args.host, args.port, runner=args.runner,
                         timeout_seconds=args.timeout, cutoff_lag_seconds=args.cutoff_lag)
    def stop_service(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_service)
    print(f"MeiGen local report: http://{args.host}:{server.server_port}", flush=True)
    print("APIs: GET /api/report, GET /api/status, GET/POST /api/settings, POST /api/refresh, POST /api/refresh/cancel", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.manager.close()
        server.server_close()


if __name__ == "__main__":
    main()
