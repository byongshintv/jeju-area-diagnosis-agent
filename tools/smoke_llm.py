"""실제 LLM(로컬 OpenAI 호환 프록시)로 3문항 스모크 테스트.

서버를 프로세스 안에서 띄우고 `/api/chat` → `/api/jobs/<id>`로 답을 받아
`runtime/smoke/*.json`에 저장한다. 실행:

    LLM_BASE_URL=http://127.0.0.1:10100/v1 LLM_API_KEY=x LLM_MODEL=gpt-6-luna \
        python3 tools/smoke_llm.py

키는 저장·출력하지 않는다.
"""
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))

from jeju_agent.server import make_server  # noqa: E402

SMOKE_DIR = APP_ROOT / "runtime" / "smoke"
FREE_PORT = 8799

QUESTIONS = [
    ("q1_노형동_카페", "노형동에서 카페 창업하면 어때?", False),
    ("q2_애월읍_한림읍_한식", "애월읍과 한림읍에서 한식당 창업을 비교해 줘.", False),
    ("q3_대정읍_편의점_시간대", "업로드한 가상 센터 데이터로 대정읍 편의점 시간대를 알려 줘.", True),
]


def post(base, path, payload, raw=None, headers=None, timeout=120):
    data = raw if raw is not None else json.dumps(payload or {}).encode("utf-8")
    head = {"content-type": "application/json"}
    head.update(headers or {})
    req = urllib.request.Request(base + path, data=data, headers=head, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())


def get(base, path, timeout=30):
    with urllib.request.urlopen(base + path, timeout=timeout) as response:
        return json.loads(response.read())


def main():
    SMOKE_DIR.mkdir(parents=True, exist_ok=True)
    runtime = APP_ROOT / "runtime" / "smoke_runtime"
    config = {
        "host": "127.0.0.1", "port": FREE_PORT, "runtime_dir": str(runtime),
        "llm_base_url": os.environ.get("LLM_BASE_URL", ""),
        "llm_api_key": os.environ.get("LLM_API_KEY", ""),
        "llm_model": os.environ.get("LLM_MODEL", ""),
        "center_to_llm": os.environ.get("CENTER_TO_LLM", "off"),
        "open_browser": False,
    }
    if not (config["llm_base_url"] and config["llm_api_key"] and config["llm_model"]):
        print(json.dumps({"ok": False, "message": "NEEDS_CRED: LLM"}, ensure_ascii=False))
        return 2
    httpd = make_server(config)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % FREE_PORT
    status = get(base, "/api/status")
    print("llm:", status["llm"])
    results = []
    try:
        for name, question, need_demo in QUESTIONS:
            if need_demo:
                demo = post(base, "/api/demo/load-center", {})
                print("demo load:", {"ok": demo.get("ok"),
                                     "count": len(demo.get("loaded") or [])})
            started = time.time()
            job = post(base, "/api/chat", {"session_id": name, "message": question})
            deadline = time.time() + 300
            state = None
            while time.time() < deadline:
                state = get(base, "/api/jobs/" + job["job_id"])
                if state["status"] in ("done", "error"):
                    break
                time.sleep(0.5)
            record = {
                "name": name, "question": question,
                "status": (state or {}).get("status"),
                "elapsed_s": round(time.time() - started, 1),
                "mode": (state or {}).get("result", {}).get("mode"),
                "notice": (state or {}).get("result", {}).get("notice"),
                "usage": (state or {}).get("result", {}).get("usage"),
                "tools": [s.get("tool") for s in (state or {}).get("result", {}).get("steps", [])],
                "steps": [{k: s.get(k) for k in ("tool", "args", "summary")}
                          for s in (state or {}).get("result", {}).get("steps", [])],
                "answer_markdown": (state or {}).get("result", {}).get("answer_markdown"),
                "report_id": (state or {}).get("result", {}).get("report_id"),
                "error": (state or {}).get("error"),
            }
            (SMOKE_DIR / (name + ".json")).write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            results.append(record)
            print("%s: mode=%s tools=%s %.1fs" % (name, record["mode"], record["tools"],
                                                  record["elapsed_s"]))
            if record["answer_markdown"]:
                print("\n".join(record["answer_markdown"].splitlines()[:3]))
            print("-" * 60)
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
