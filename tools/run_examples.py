"""예시 질문을 규칙 모드로 실제 서버에 보내 결과 요약을 출력한다(재현 기록용).

임시 포트로 서버를 띄워 화면과 같은 API(/api/chat)로 묻고, 도구 순서·쓴 데이터·소요 시간·
반출 점검·보고서 생성 여부를 Markdown 표로 출력한다. 네트워크 밖으로 나가지 않으며 AI 키를 쓰지 않는다.

    python tools/run_examples.py                 # 기본 질문 5개
    python tools/run_examples.py --demo-center   # 가상 센터 데이터를 먼저 등록
    python tools/run_examples.py --help
"""
import argparse
import json
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))

from jeju_agent.server import make_server  # noqa: E402

QUESTIONS = [
    "노형동에서 카페 창업하면 어때?",
    "애월읍과 한림읍 한식당 비교해 줘",
    "대정읍 편의점은 언제 손님이 많아?",
    "추자면 편의점은 언제 손님이 많아?",
    "화성시 동탄에서 카페?",
]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _call(base, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method="POST" if data is not None else "GET",
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ask(base, question):
    started = time.perf_counter()
    job = _call(base, "/api/chat", {"session_id": "examples", "message": question})
    while True:
        st = _call(base, "/api/jobs/" + job["job_id"])
        if st["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    elapsed = time.perf_counter() - started
    r = st.get("result") or {}
    report = _call(base, "/api/report/" + r["report_id"]) if r.get("report_id") else {}
    export = report.get("export_check") or {}
    return {
        "question": question, "status": st["status"], "mode": r.get("mode"),
        "tools": [s.get("tool") for s in r.get("steps") or []],
        "datasets": sorted({d for s in r.get("steps") or [] for d in s.get("datasets") or []}),
        "clarification": bool(r.get("clarification")),
        "artifacts": len(r.get("artifacts") or []),
        "report": bool(report), "suppressed": len(export.get("suppressed") or []),
        "seconds": round(elapsed, 2),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="예시 질문을 규칙 모드로 실행해 결과 요약 출력")
    parser.add_argument("--demo-center", action="store_true", help="가상 센터 데이터를 먼저 등록")
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        port = _free_port()
        httpd = make_server({"host": "127.0.0.1", "port": port, "runtime_dir": tmp,
                             "open_browser": False, "llm_base_url": "", "llm_api_key": "",
                             "llm_model": ""})
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % port
        try:
            if args.demo_center:
                _call(base, "/api/demo/load-center", {})
            rows = [ask(base, q) for q in QUESTIONS]
        finally:
            httpd.shutdown()
            httpd.server_close()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    print("| 질문 | 모드 | 도구 순서 | 쓴 데이터 | 결과물 | 반출 가림 | 소요(초) |")
    print("|---|---|---|---|---|---|---|")
    for r in rows:
        outcome = "되묻기" if r["clarification"] else "차트·표 %d개, 보고서 %s" % (
            r["artifacts"], "있음" if r["report"] else "없음")
        print("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["question"], {"rule": "규칙", "llm": "AI"}.get(r["mode"], r["mode"]), " → ".join(r["tools"]) or "-",
            ", ".join(r["datasets"]) or "-", outcome, "%d칸" % r["suppressed"], r["seconds"]))
    return 0 if all(r["status"] == "done" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
