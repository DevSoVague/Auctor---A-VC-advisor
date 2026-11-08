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


def emit(run_id: str, event: str, **data):
    if not re.fullmatch(r"[\w-]+", run_id):
        return
    try:
        with _lock:
            folder = RUNS / run_id
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
            runs = sorted(RUNS.glob("*/live.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
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
            path = RUNS / run_id / "live.jsonl"
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
            _server = ThreadingHTTPServer(("127.0.0.1", int(os.getenv("P3_MONITOR_PORT", "7862"))), Handler)
        except OSError:
            return  # A standalone monitor may already be listening.
        threading.Thread(target=_server.serve_forever, daemon=True).start()


def main():
    parser = argparse.ArgumentParser(description="Watch Langflow agent nodes locally")
    parser.add_argument("--port", type=int, default=int(os.getenv("P3_MONITOR_PORT", "7862")))
    args = parser.parse_args()
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
