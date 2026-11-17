"""Acceptance-test helpers: start the real server in-process on a free port."""
import json
import os
import socket
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class AppServer:
    """Runs jeju_agent.server.make_server(config) in a thread.

    Contract: jeju_agent.server.make_server(config: dict) -> http.server.HTTPServer
    config keys used by tests: host, port, runtime_dir, llm_base_url, llm_api_key,
    llm_model, hub_base_url, hub_api_key, open_browser (False in tests).
    Missing llm_api_key/llm_base_url means LLM disabled (rule mode).
    """

    def __init__(self, tmpdir, **overrides):
        from jeju_agent.server import make_server
        self.port = free_port()
        cfg = {
            "host": "127.0.0.1",
            "port": self.port,
            "runtime_dir": tmpdir,
            "llm_base_url": "",
            "llm_api_key": "",
            "llm_model": "",
            "hub_base_url": "",
            "hub_api_key": "",
            "open_browser": False,
        }
        cfg.update(overrides)
        self.httpd = make_server(cfg)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.port

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            body = r.read()
            ctype = r.headers.get("content-type", "")
            return json.loads(body) if "json" in ctype else body

    def post(self, path, payload=None, raw=None, headers=None):
        data = raw if raw is not None else json.dumps(payload or {}).encode("utf-8")
        h = {"content-type": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    def chat(self, message, session_id="t1", timeout=180):
        """POST /api/chat -> {"job_id"}; poll GET /api/jobs/<id> until status done|error."""
        job = self.post("/api/chat", {"session_id": session_id, "message": message})
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = self.get("/api/jobs/" + job["job_id"])
            if st["status"] in ("done", "error"):
                return st
            time.sleep(0.2)
        raise AssertionError("chat job did not finish")


class FakeLLM:
    """OpenAI-compatible /v1/chat/completions stub.

    script: list of responses; each is either {"tool_calls": [(name, args_dict), ...]}
    or {"content": "text"}. After the script is exhausted it answers with content "끝".
    All request bodies are recorded in self.requests.
    """

    def __init__(self, script, fail=False):
        self.script = list(script)
        self.requests = []
        self.fail = fail
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.requests.append(body)
                if outer.fail:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b'{"error":"boom"}')
                    return
                step = outer.script.pop(0) if outer.script else {"content": "끝"}
                msg = {"role": "assistant", "content": step.get("content")}
                if step.get("tool_calls"):
                    msg["tool_calls"] = [
                        {"id": "c%d_%d" % (len(outer.requests), i), "type": "function",
                         "function": {"name": nm, "arguments": json.dumps(a, ensure_ascii=False)}}
                        for i, (nm, a) in enumerate(step["tool_calls"])
                    ]
                resp = {"id": "x", "object": "chat.completion", "model": "fake",
                        "choices": [{"index": 0, "message": msg,
                                     "finish_reason": "tool_calls" if step.get("tool_calls") else "stop"}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
                out = json.dumps(resp, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.port = free_port()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base_url = "http://127.0.0.1:%d/v1" % self.port

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


CENTER_1_08_HEADER = ["base_year_month", "dstrct_se", "city", "area_type", "bjd", "biz_cd", "biz_nm",
                      "user_type", "tour_type", "age", "sex", "user_cnt", "use_cnt", "use_amt"]


def center_csv(header, rows):
    """Center export format: header unquoted names, every value double-quoted, NA for missing, UTF-8 no BOM."""
    lines = [",".join(header)]
    for r in rows:
        lines.append(",".join('"%s"' % ("NA" if v is None else v) for v in r))
    return ("\n".join(lines) + "\n").encode("utf-8")
