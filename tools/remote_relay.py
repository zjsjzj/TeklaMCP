#!/usr/bin/env python3
"""远程中继：让运行在 Windows 上的 Tekla 桥主动连过来，代理侧只需本机调用 /call。

用法（Linux/代理侧）：
    python3 tools/remote_relay.py --token <TOKEN> --port 8765
    curl -H "X-Tekla-Token: <TOKEN>" -X POST http://127.0.0.1:8765/call \
         -d '{"tool": "tekla_check_connection", "args": {}}'

Windows 侧（不需要开放入站端口）：
    .venv\\Scripts\\python.exe tools\\tekla_remote_bridge.py --token <TOKEN> \
        --relay http://<linux-ip>:8765

只有本机（127.0.0.1）能提交 /call；/poll 与 /result 需要同一 token。
"""

import argparse
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class Relay:
    def __init__(self, token, timeout=180):
        self.token = token
        self.timeout = timeout
        self.cond = threading.Condition()
        self.job = None
        self.results = {}
        self.bridge_seen = 0.0

    def submit(self, payload):
        with self.cond:
            if self.job is not None:
                raise RuntimeError("已有任务在执行，请串行调用")
            job = {"id": uuid.uuid4().hex,
                   "tool": str(payload.get("tool") or ""),
                   "args": payload.get("args") or {}}
            self.job = job
            self.cond.notify_all()
            deadline = time.time() + self.timeout
            while job["id"] not in self.results:
                remaining = deadline - time.time()
                if remaining <= 0:
                    self.job = None
                    raise TimeoutError(
                        "Windows 桥未在时限内返回；确认 bridge 正在运行且 --relay 指向本机")
                self.cond.wait(remaining)
            result = self.results.pop(job["id"])
            self.job = None
            return result

    def take_job(self, wait):
        with self.cond:
            self.bridge_seen = time.time()
            deadline = time.time() + wait
            while True:
                if self.job is not None and not self.job.get("taken"):
                    self.job["taken"] = True
                    return self.job
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self.cond.wait(remaining)

    def complete(self, job_id, result):
        with self.cond:
            self.results[job_id] = result
            if self.job is not None and self.job.get("id") == job_id:
                self.job = None
            self.cond.notify_all()


class Handler(BaseHTTPRequestHandler):
    server_version = "TeklaRemoteRelay/1.0"

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _token_ok(self):
        supplied = self.headers.get("X-Tekla-Token", "")
        if not supplied:
            query = parse_qs(urlparse(self.path).query)
            supplied = (query.get("token") or [""])[0]
        return supplied == self.server.relay.token

    def _local_only(self):
        return self.client_address[0] in ("127.0.0.1", "::1")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            age = time.time() - self.server.relay.bridge_seen
            self._send(200, {"ok": True,
                             "bridge_seen_seconds_ago": round(age, 1) if age < 1e6 else None})
            return
        if path == "/ping":
            self._send(200, {"ok": True})
            return
        if path == "/poll":
            if not self._token_ok():
                self._send(401, {"ok": False, "error": "bad token"})
                return
            wait = 25.0
            query = parse_qs(urlparse(self.path).query)
            if query.get("wait"):
                try:
                    wait = max(1.0, min(60.0, float(query["wait"][0])))
                except ValueError:
                    pass
            job = self.server.relay.take_job(wait)
            self._send(200, {"job": job})
            return
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/call", "/result"):
            self._send(404, {"ok": False, "error": "not found"})
            return
        if not self._token_ok():
            self._send(401, {"ok": False, "error": "bad token"})
            return
        if path == "/call" and not self._local_only():
            self._send(403, {"ok": False, "error": "call 只允许本机提交"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send(400, {"ok": False, "error": f"bad json: {exc}"})
            return
        if path == "/result":
            job_id = str(payload.get("id") or "")
            if not job_id:
                self._send(400, {"ok": False, "error": "missing id"})
                return
            self.server.relay.complete(job_id, payload.get("result"))
            self._send(200, {"ok": True})
            return
        try:
            result = self.server.relay.submit(payload)
        except TimeoutError as exc:
            self._send(504, {"ok": False, "error": str(exc)})
            return
        except RuntimeError as exc:
            self._send(429, {"ok": False, "error": str(exc)})
            return
        self._send(200, {"ok": True, "result": result})

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


def main():
    parser = argparse.ArgumentParser(description="Tekla 远程中继")
    parser.add_argument("--token", required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.relay = Relay(args.token, args.timeout)
    print(f"relay listening on http://{args.host}:{args.port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
