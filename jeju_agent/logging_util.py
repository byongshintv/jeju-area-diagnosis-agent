"""요청·단계 로그.

`runtime/logs/requests.log`에 한 줄씩 남긴다. 키 값과 센터 원본 값은 절대 쓰지 않는다.
"""
import json
import threading
from datetime import datetime
from pathlib import Path


class RequestLog:
    def __init__(self, folder):
        self.folder = Path(folder)
        self._lock = threading.Lock()
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.path = self.folder / "requests.log"

    def write(self, method, path, status, started_at=None, note=""):
        record = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "method": method,
            "path": path,
            "status": status,
        }
        if started_at:
            record["ms"] = int((datetime.now() - started_at).total_seconds() * 1000)
        if note:
            record["note"] = note
        try:
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def step(self, tool, args_summary="", ms=0, datasets=None):
        """2단계 이후 도구 단계 기록용. 인자 요약만 남긴다."""
        record = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "tool": tool,
            "args": args_summary,
            "ms": ms,
            "datasets": datasets or [],
        }
        try:
            with self._lock:
                with open(self.folder / "steps.log", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass
