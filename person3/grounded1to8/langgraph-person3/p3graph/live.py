"""Local run monitor. Append-only per-run events survive refresh and parallel cards."""
from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

RUNS = Path(__file__).resolve().parents[1] / "runs"
_lock = threading.Lock()
_server = None
_extra_runs: list[Path] = []


def emit(run_id: str, event: str, **data):
    if not re.fullmatch(r"[\w-]+", run_id):
        return
    try:
        with _lock:
            folder = RUNS / run_id
            if not folder.exists() and data.get("node") in ("setup", "intake"):
                return  # no run folder until an agent runs (failed / rejected starts leave nothing)
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / "live.jsonl").open("a") as f:
                f.write(json.dumps({"event": event, "time": time.time(), **data}, default=str) + "\n")
    except OSError:
        pass  # Observability must not break an analysis.


def observer(state, node, agent=None, hid=None, model=None):
    identity = {"id": uuid.uuid4().hex, "node": node, "agent": agent,
                "hid": hid, "model": model}

    def send(event, data=None):
        emit(state["run_id"], event, **{**identity, **(data or {})})

    return send


def event_files():
    """Read all configured run locations; newest means started most recently."""
    files = {}
    for root in [RUNS, *_extra_runs]:
        for path in root.glob("*/live.jsonl"):
            previous = files.get(path.parent.name)
            if previous is None or path.stat().st_mtime > previous.stat().st_mtime:
                files[path.parent.name] = path

    def started(path):
        try:
            with path.open() as stream:
                return float(json.loads(stream.readline())["time"])
        except (OSError, ValueError, KeyError, TypeError):
            return path.stat().st_mtime

    return sorted(files.values(), key=started, reverse=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        code = 200
        if url.path == "/":
            body = Path(__file__).with_name("live.html").read_bytes()
            kind = "text/html; charset=utf-8"
        elif url.path == "/api/runs":
            runs = event_files()
            body = json.dumps([f.parent.name for f in runs]).encode()
            kind = "application/json"
        elif url.path == "/api/events":
            q = parse_qs(url.query)
            run_id = q.get("run", [""])[0]
            if not re.fullmatch(r"[\w-]+", run_id):
                self.send_error(400)
                return
            try:
                offset = max(0, int(q.get("offset", ["0"])[0]))
            except ValueError:
                self.send_error(400)
                return
            events = []
            path = next((p for p in event_files() if p.parent.name == run_id),
                        RUNS / run_id / "live.jsonl")
            if path.exists():
                with path.open("rb") as f:
                    f.seek(offset)
                    for _ in range(2000):
                        line = f.readline()
                        if not line.endswith(b"\n"):
                            break
                        events.append(json.loads(line))
                        offset = f.tell()
            body = json.dumps({"offset": offset, "events": events}).encode()
            kind = "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def ensure_server():
    global _server
    with _lock:
        if _server is not None:
            return
        try:
            _server = ThreadingHTTPServer(("127.0.0.1", int(os.getenv("P3_MONITOR_PORT", "7872"))), Handler)
        except OSError:
            return  # A standalone monitor may already be listening.
        threading.Thread(target=_server.serve_forever, daemon=True).start()


def main():
    parser = argparse.ArgumentParser(description="Watch Langflow agent nodes locally")
    parser.add_argument("--port", type=int, default=int(os.getenv("P3_MONITOR_PORT", "7872")))
    parser.add_argument("--runs", type=Path, action="append", default=[],
                        help="Additional run directory to watch (repeatable)")
    args = parser.parse_args()
    _extra_runs[:] = [path.expanduser().resolve() for path in args.runs]
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Live node monitor: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
