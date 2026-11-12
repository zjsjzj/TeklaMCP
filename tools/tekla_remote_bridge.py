#!/usr/bin/env python3
"""Tekla 远程桥：把本机 Tekla MCP 工具暴露给远程代理，让远程代理直接操作 Tekla。

⚠️ 这个脚本会把「可写模型」的 MCP 工具暴露到网络上，只在你信任的局域网临时使用：
   运行前设置一个足够长的 --token，用完 Ctrl+C 关掉。

在装有 Tekla 的 Windows 机器上运行（放在 tekla_server.py 的上一级目录）：

    .venv\\Scripts\\python.exe tools\\tekla_remote_bridge.py --self-test
    .venv\\Scripts\\python.exe tools\\tekla_remote_bridge.py --token <TOKEN> --relay http://10.10.10.20:8765

三种模式：
  --self-test            只检查工具注册与 Tekla 连接，退出（不监听、不修改模型）
  --token ...            监听 0.0.0.0:8765，远程 POST /call {"tool": "...", "args": {...}}
  --token ... --relay U  主动连接中继 U 轮询任务（不需要开放入站端口）

调用示例：
  curl -H "X-Tekla-Token: $TOKEN" -X POST http://<windows-ip>:8765/call \
       -d "{\\"tool\\":\\"tekla_check_connection\\",\\"args\\":{}}"
"""

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import tekla_server as ts  # noqa: E402

MAX_BODY = 2 * 1024 * 1024


def tool_registry():
    """只暴露公开的 tekla_* 工具，避免远程调用到内部函数。"""
    registry = {}
    for name in dir(ts):
        if not name.startswith("tekla_"):
            continue
        fn = getattr(ts, name)
        if callable(fn):
            registry[name] = fn
    return registry


def _read_file_diag(args):
    """只读诊断：返回文件大小与首尾字节，用于判断导出文件的真实格式。"""
    path = str(args.get("path") or "")
    try:
        n = max(1, min(int(args.get("n") or 64), 4096))
    except (TypeError, ValueError):
        n = 64
    if not path or not os.path.isfile(path):
        return {"ok": False, "error": f"file not found: {path}"}
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(n)
        f.seek(max(0, size - n))
        tail = f.read(n)
    return {"ok": True, "path": path, "size": size,
            "head_hex": head.hex(), "head_repr": repr(head),
            "tail_hex": tail.hex(), "tail_repr": repr(tail)}


def _list_printers_diag(args):
    """只读诊断：列出 Windows 已安装打印机，用于找 PDF 打印机。"""
    try:
        import clr
        clr.AddReference("System.Drawing")
        from System.Drawing.Printing import PrinterSettings
        printers = [str(p) for p in PrinterSettings.InstalledPrinters]
        default = ""
        try:
            default = str(PrinterSettings().PrinterName)
        except Exception:
            pass
        return {"ok": True, "printers": printers, "default": default}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def call_tool(payload):
    tool = str(payload.get("tool") or "")
    args = payload.get("args") or {}
    if tool == "__read_file__":
        return _read_file_diag(args)
    if tool == "__list_printers__":
        return _list_printers_diag(args)
    registry = tool_registry()
    if tool not in registry:
        return {"ok": False, "error": f"未知工具 {tool!r}", "tools": sorted(registry)}
    if not isinstance(args, dict):
        return {"ok": False, "error": "args 必须是 JSON 对象"}
    try:
        result = registry[tool](**args)
        return {"ok": True, "tool": tool, "result": result}
    except Exception as exc:  # noqa: BLE001 - 远程调用要把异常带回调用方
        return {"ok": False, "tool": tool,
                "error": f"{type(exc).__name__}: {exc}"}


class _Handler(BaseHTTPRequestHandler):
    server_version = "TeklaRemoteBridge/1.0"

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        token = self.server.token
        if not token:
            return False
        supplied = self.headers.get("X-Tekla-Token", "")
        if not supplied:
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            for part in query.split("&"):
                if part.startswith("token="):
                    supplied = part.split("=", 1)[1]
        return supplied == token

    def do_GET(self):
        if self.path.split("?", 1)[0] in ("/ping", "/health"):
            self._send(200, {"ok": True, "service": "tekla-remote-bridge",
                             "tools": len(tool_registry())})
            return
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/call":
            self._send(404, {"ok": False, "error": "not found"})
            return
        if not self._authorized():
            self._send(401, {"ok": False, "error": "bad or missing token"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._send(400, {"ok": False, "error": "empty or too large body"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send(400, {"ok": False, "error": f"bad json: {exc}"})
            return
        self._send(200, call_tool(payload))

    def log_message(self, fmt, *args):  # 日志写 stderr，避免污染任何 JSON 输出
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def serve(token, host, port):
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.token = token
    print(f"Tekla remote bridge listening on http://{host}:{port} "
          f"({len(tool_registry())} tools)", flush=True)
    for name in _lan_addresses():
        print(f"  LAN: http://{name}:{port}/call", flush=True)
    httpd.serve_forever()


def _lan_addresses():
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))
        addresses.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    return sorted(a for a in addresses if not a.startswith("127."))


def relay_loop(relay, token, poll_seconds=35):
    """主动向中继轮询任务，避免 Windows 入站防火墙。"""
    base = relay.rstrip("/")
    print(f"Tekla remote bridge polling relay {base} "
          f"({len(tool_registry())} tools)", flush=True)
    while True:
        try:
            request = urllib.request.Request(
                f"{base}/poll?token={token}", method="GET")
            with urllib.request.urlopen(request, timeout=poll_seconds + 10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            sys.stderr.write(f"relay poll failed: {exc}\n")
            time.sleep(3)
            continue
        job = payload.get("job")
        if not job:
            continue
        result = call_tool(job)
        body = json.dumps({"id": job.get("id"), "result": result},
                          ensure_ascii=False).encode("utf-8")
        try:
            request = urllib.request.Request(
                f"{base}/result?token={token}", data=body, method="POST",
                headers={"Content-Type": "application/json; charset=utf-8"})
            urllib.request.urlopen(request, timeout=20).read()
        except (urllib.error.URLError, TimeoutError) as exc:
            sys.stderr.write(f"relay result failed: {exc}\n")


def self_test():
    registry = tool_registry()
    print(f"tekla_server {getattr(ts, '__version__', '?')}, tools: {len(registry)}")
    missing = [name for name in ("tekla_create_parts", "tekla_create_components",
                                 "tekla_modify_parts", "tekla_delete_objects")
               if name not in registry]
    if missing:
        print(f"缺少工具: {missing}（当前 tekla_server.py 不是 v2.1.0+）")
        return 2
    result = call_tool({"tool": "tekla_check_connection", "args": {}})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    connected = bool((result.get("result") or {}).get("connected"))
    if not connected:
        print("连不上 Tekla：确认 Tekla 已打开模型，且用的是 64 位 .venv。")
        return 3
    return 0


def main():
    parser = argparse.ArgumentParser(description="Tekla 远程桥")
    parser.add_argument("--token", default=os.environ.get("TEKLA_BRIDGE_TOKEN", ""),
                        help="共享令牌；也可用环境变量 TEKLA_BRIDGE_TOKEN")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--relay", default="",
                        help="中继地址，如 http://10.10.10.20:8765；给了就主动轮询")
    parser.add_argument("--self-test", action="store_true",
                        help="只检查工具与连接，不监听、不写模型")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if not args.token:
        parser.error("必须提供 --token（或 TEKLA_BRIDGE_TOKEN），避免把模型写接口裸奔到局域网")
    if args.relay:
        relay_loop(args.relay, args.token)
        return 0
    serve(args.token, args.host, args.port)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("stopped")
