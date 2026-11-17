"""보고서 보관소.

`build_report`가 만든 JSON을 메모리에 두고 `GET /api/report/<id>`·표 CSV 다운로드에 쓴다.
세션(job)과 별개로 남기며, 서버 프로세스가 끝나면 사라진다(저장 파일 없음).
"""
import csv
import io
import threading
from datetime import datetime

MAX_REPORTS = 50


class ReportStore:
    def __init__(self):
        self._items = {}
        self._order = []
        self._lock = threading.Lock()
        self._counter = 0

    def add(self, report):
        with self._lock:
            self._counter += 1
            report = dict(report)
            report["id"] = report.get("id") or "r%d_%d" % (
                int(datetime.now().timestamp()), self._counter)
            self._items[report["id"]] = report
            self._order.append(report["id"])
            while len(self._order) > MAX_REPORTS:
                old = self._order.pop(0)
                self._items.pop(old, None)
            return report["id"]

    def get(self, report_id):
        return self._items.get(report_id)

    def list(self):
        return [self._items[i] for i in self._order if i in self._items]

    def clear(self):
        with self._lock:
            count = len(self._items)
            self._items.clear()
            self._order = []
            return count


def artifact_to_csv(report, artifact_id):
    """보고서 안의 표 artifact → utf-8-sig CSV 바이트. 없으면 None."""
    artifact = None
    for item in report.get("artifacts") or []:
        if item.get("id") == artifact_id:
            artifact = item
            break
    if artifact is None:
        for section in report.get("sections") or []:
            for item in section.get("artifacts") or []:
                if isinstance(item, dict) and item.get("id") == artifact_id:
                    artifact = item
    if artifact is None or artifact.get("type") not in ("table", "kpi"):
        return None
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if artifact.get("type") == "kpi":
        writer.writerow(["지표", "값", "단위", "기간", "출처"])
        for item in artifact.get("items") or []:
            writer.writerow([item.get("label"), item.get("value"), item.get("unit"),
                             item.get("period"), item.get("source")])
    else:
        writer.writerow(artifact.get("columns") or [])
        for row in artifact.get("rows") or []:
            writer.writerow(["" if v is None else v for v in row])
    # 엑셀 호환을 위해 utf-8-sig로 내보낸다.
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")
