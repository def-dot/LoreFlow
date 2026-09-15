"""沙箱 HTTP 服务 — 代码执行与依赖安装。"""

import inspect
import json
import subprocess
import sys
import tempfile
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# ---------------------------------------------------------------------------
# /exec — code 节点调用，签名 main(**ctx)
# ---------------------------------------------------------------------------

def _exec(code: str, ctx: dict) -> dict:
    ns: dict = {"__builtins__": __builtins__}
    exec(code, ns)
    fn = ns["main"]
    params = list(inspect.signature(fn).parameters)
    return {"result": fn(**{p: ctx[p] for p in params})}


# ---------------------------------------------------------------------------
# /run — 任意代码，返回 stdout / stderr
# ---------------------------------------------------------------------------

def _run(code: str, timeout: int) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
        script = f.name
    proc = subprocess.run(
        [sys.executable, script],
        capture_output=True, text=True, timeout=timeout,
    )
    return {"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}


# ---------------------------------------------------------------------------
# /pip — 安装依赖到 /opt/packages
# ---------------------------------------------------------------------------

def _pip(packages: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-cache-dir",
         "--target", "/opt/packages", *packages.split()],
        capture_output=True, text=True,
    )
    return {"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        try:
            if self.path == "/exec":
                result = _exec(body["code"], body.get("ctx", {}))
                self._respond(200, result=result["result"])
            elif self.path == "/run":
                result = _run(body["code"], body.get("timeout", 60))
                self._respond(200, **result)
            elif self.path == "/pip":
                result = _pip(body["packages"])
                self._respond(200, **result)
            else:
                self._respond(404, error="not found")
        except subprocess.TimeoutExpired:
            self._respond(408, error="execution timed out")
        except Exception as e:
            self._respond(400, error=f"{type(e).__name__}: {e}")

    def _respond(self, status: int, **payload):
        data = json.dumps(payload, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8194), Handler)
    print("sandbox listening on :8194", flush=True)
    server.serve_forever()
