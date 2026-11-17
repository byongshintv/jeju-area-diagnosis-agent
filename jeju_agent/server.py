"""HTTP 서버·라우팅.

`make_server(config)`는 `http.server.ThreadingHTTPServer`를 돌려준다(테스트 계약).
설정 키는 `jeju_agent.config`의 `build_config`와 같다.

1단계 라우트:
    GET    /api/status
    GET    /api/catalog
    GET    /api/regions
    GET    /api/industries
    GET    /api/datasets/<id>/preview
    POST   /api/upload           (본문=CSV 바이트, 헤더 x-filename)
    DELETE /api/uploads
    POST   /api/demo/load-center
    GET    /dev.html             (개발 확인용 최소 페이지)
"""
import json
import mimetypes
import os
import re
import threading
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .agent import JobStore, RuleAgent
from .agent_llm import LLMAgent
from .catalog import Catalog, load_dummy_tables
from .center_data import UploadStore
from .center_schema import CENTER_DATASETS
from .config import build_config, hub_configured, llm_configured
from .dictionaries import (get_industries, get_regions, region_summary, resolve_industry,
                           resolve_region)
from .export_check import check_export
from .logging_util import RequestLog
from .reports import ReportStore, artifact_to_csv
from .table_ops import query_table

MAX_UPLOAD_BYTES = 256 * 1024 * 1024
PREVIEW_ROWS = 5


def remove_bad_chars(name):
    return re.sub(r"[\\/:*?\"<>|\r\n]+", "_", name)


class AppContext:
    """서버 하나가 공유하는 상태 묶음."""

    def __init__(self, config):
        self.config = config
        self.runtime_dir = Path(config["runtime_dir"])
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.uploads = UploadStore(self.runtime_dir)
        self.catalog = Catalog(config.get("sample_dir"), self.uploads)
        self.log = RequestLog(self.runtime_dir / "logs")
        self.demo_loaded = []
        self.jobs = JobStore()
        self.reports = ReportStore()
        self.llm_fallback_reason = ""

    def status(self):
        datasets = self.catalog.status_datasets()
        hub_ok = [d for d in datasets if d["source_kind"] == "hub" and d["available"]]
        dummy_ok = [d for d in datasets if d["source_kind"] == "center_dummy" and d["available"]]
        return {
            "app": "jeju-agent",
            "version": __version__,
            "llm": {
                "configured": llm_configured(self.config),
                "model": self.config.get("llm_model") or "",
                "base_url_set": bool(self.config.get("llm_base_url")),
                "mode": "llm" if llm_configured(self.config) else "rule",
            },
            "hub": {
                "configured": hub_configured(self.config),
                "base_url": self.config.get("hub_base_url") or "",
                "snapshots": len(hub_ok),
            },
            "center": {
                "schema_sheets": len(CENTER_DATASETS),
                "uploads": len(self.uploads),
                "dummy_datasets": len(dummy_ok),
                "center_to_llm": self.config.get("center_to_llm") or "off",
            },
            "datasets": datasets,
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "JejuAgent/" + __version__
    protocol_version = "HTTP/1.1"

    # -- 응답 헬퍼 ---------------------------------------------------------
    @property
    def ctx(self):
        return self.server.ctx

    def log_message(self, fmt, *args):
        # 기본 stderr 접근 로그는 남기지 않고 runtime/logs에 직접 기록한다.
        pass

    def _send(self, status, body, content_type="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(status, payload)

    def _error(self, status, message, **extra):
        payload = {"ok": False, "message": message}
        payload.update(extra)
        self._json(payload, status)

    def _read_body(self):
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        if length > MAX_UPLOAD_BYTES:
            return None
        return self.rfile.read(length)

    # -- 라우팅 -------------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        started = datetime.now()
        status = 200
        try:
            if path == "/":
                path = "/index.html"
            if path.startswith("/api/"):
                status = self._api_get(path, query)
            else:
                status = self._static(path)
        except BrokenPipeError:
            return
        except Exception as exc:  # 어떤 요청도 서버를 죽이지 않는다.
            status = 500
            self._error(500, "서버 오류: %s" % type(exc).__name__)
        finally:
            self.ctx.log.write(self.command, path, status, started)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        started = datetime.now()
        status = 200
        try:
            status = self._api_post(path)
        except BrokenPipeError:
            return
        except Exception as exc:
            status = 500
            self._error(500, "서버 오류: %s" % type(exc).__name__)
        finally:
            self.ctx.log.write(self.command, path, status, started)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        started = datetime.now()
        status = 200
        try:
            if path == "/api/uploads":
                removed = self.ctx.uploads.clear()
                self.ctx.demo_loaded = []
                self._json({"ok": True, "removed": removed,
                            "message": "업로드·데모 데이터를 지웠습니다."})
            else:
                status = 404
                self._error(404, "없는 경로입니다.")
        except Exception as exc:
            status = 500
            self._error(500, "서버 오류: %s" % type(exc).__name__)
        finally:
            self.ctx.log.write(self.command, path, status, started)

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain", {"allow": "GET, POST, DELETE, OPTIONS"})

    # -- GET 라우트 ---------------------------------------------------------
    def _api_get(self, path, query):
        if path == "/api/status":
            self._json(self.ctx.status())
            return 200
        if path == "/api/catalog":
            items = self.ctx.catalog.datasets()
            self._json({"ok": True, "count": len(items), "datasets": items})
            return 200
        if path == "/api/regions":
            self._json(get_regions())
            return 200
        if path == "/api/regions/summary":
            self._json(region_summary())
            return 200
        if path == "/api/industries":
            self._json(get_industries())
            return 200
        if path == "/api/schema":
            self._json({"count": len(CENTER_DATASETS), "datasets": CENTER_DATASETS})
            return 200
        if path == "/api/uploads":
            self._json({"ok": True, "uploads": self.ctx.uploads.list(),
                        "demo_loaded": self.ctx.demo_loaded})
            return 200
        if path.startswith("/api/jobs/"):
            job_id = urllib.parse.unquote(path[len("/api/jobs/"):])
            job = self.ctx.jobs.get(job_id)
            if not job:
                self._error(404, "작업을 찾을 수 없습니다.", job_id=job_id)
                return 404
            self._json(job.snapshot())
            return 200
        if path.startswith("/api/report/"):
            return self._report_get(path)
        if path.startswith("/api/resolve/"):
            text = query.get("q", [""])[0]
            if path == "/api/resolve/region":
                name, candidates = resolve_region(text)
            elif path == "/api/resolve/industry":
                name, candidates = resolve_industry(text)
            else:
                self._error(404, "없는 경로입니다.")
                return 404
            self._json({"query": text, "match": name, "candidates": candidates})
            return 200
        if path.startswith("/api/datasets/") and path.endswith("/preview"):
            dataset_id = urllib.parse.unquote(path[len("/api/datasets/"):-len("/preview")])
            table = self.ctx.catalog.table(dataset_id)
            if not table:
                self._error(404, "데이터를 찾을 수 없습니다.", dataset_id=dataset_id)
                return 404
            rows = table["rows"]
            self._json({
                "ok": True,
                "dataset_id": dataset_id,
                "source_kind": table["source_kind"],
                "meta": table["meta"],
                "columns": table["header"],
                "rows": rows[:PREVIEW_ROWS],
                "total_rows": len(rows),
            })
            return 200
        self._error(404, "없는 API 경로입니다.")
        return 404

    # -- POST 라우트 --------------------------------------------------------
    def _api_post(self, path):
        payload = self._read_body()
        if payload is None:
            self._error(413, "요청 본문이 너무 큽니다(최대 256MB).")
            return 413

        if path == "/api/upload":
            filename = urllib.parse.unquote(self.headers.get("x-filename") or "center.csv")
            content_type = self.headers.get("content-type") or ""
            result = self.ctx.uploads.add(payload, filename, content_type)
            # 거절도 200 + {"ok": false}로 돌려준다(브라우저 fetch가 본문을 읽을 수 있게).
            self._json(result)
            return 200

        if path == "/api/demo/load-center":
            return self._load_demo()

        if path == "/api/query":
            return self._query(payload)

        if path == "/api/chat":
            return self._chat(payload)

        if path == "/api/export-check":
            return self._export_check(payload)

        self._error(404, "없는 API 경로입니다.")
        return 404

    def _load_demo(self):
        tables = load_dummy_tables(self.ctx.catalog.dummy_dir)
        if not tables:
            self._error(409, "가상 센터 데이터가 없습니다. tools/make_dummy_center.py를 먼저 실행하세요.")
            return 409
        loaded = []
        for dataset_no, table in sorted(tables.items()):
            meta = table["meta"]
            self.ctx.uploads.add_demo(dataset_no, meta, table["header"], table["rows"])
            loaded.append({"dataset_id": "center:" + dataset_no, "name": meta.get("name"),
                           "rows": len(table["rows"]), "period": meta.get("period")})
        self.ctx.demo_loaded = loaded
        self._json({"ok": True, "loaded": loaded,
                    "notice": "가상 데이터(형식 시연용)입니다. 실제 센터 데이터가 아닙니다."})
        return 200

    def _chat(self, payload):
        try:
            spec = json.loads(payload.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._error(400, "JSON 본문을 읽을 수 없습니다.")
            return 400
        message = str(spec.get("message") or "").strip()
        if not message:
            self._error(400, "message가 비어 있습니다.")
            return 400
        session_id = str(spec.get("session_id") or "default")
        job = self.ctx.jobs.create(session_id, message)
        if llm_configured(self.ctx.config):
            agent = LLMAgent(self.ctx, lambda ctx, reason: RuleAgent(ctx, reason))
        else:
            agent = RuleAgent(self.ctx)

        def worker():
            agent.run(job)

        threading.Thread(target=worker, daemon=True).start()
        self._json({"ok": True, "job_id": job.id, "status": job.status})
        return 200

    def _export_check(self, payload):
        try:
            spec = json.loads(payload.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._error(400, "JSON 본문을 읽을 수 없습니다.")
            return 400
        table = spec.get("table") if isinstance(spec, dict) else None
        if not isinstance(table, dict) or not table.get("rows"):
            self._error(400, "table(columns·rows)이 필요합니다.")
            return 400
        result = check_export(table, sources=spec.get("sources"),
                              purpose=spec.get("purpose"), dataset=spec.get("dataset"))
        self._json(result)
        return 200

    def _report_get(self, path):
        rest = path[len("/api/report/"):]
        if rest.endswith("/csv") or "/csv/" in rest:
            report_id, _, artifact_id = rest.partition("/csv/")
            report_id = urllib.parse.unquote(report_id)
            artifact_id = urllib.parse.unquote(artifact_id) if artifact_id else "evidence"
            report = self.ctx.reports.get(report_id)
            if not report:
                self._error(404, "보고서를 찾을 수 없습니다.", report_id=report_id)
                return 404
            data = artifact_to_csv(report, artifact_id)
            if data is None:
                self._error(404, "표 artifact를 찾을 수 없습니다.", artifact_id=artifact_id)
                return 404
            filename = remove_bad_chars("%s_%s.csv" % (report.get("title") or report_id,
                                                       artifact_id))
            self._send(200, data, "text/csv; charset=utf-8",
                       {"content-disposition": "attachment; filename*=UTF-8''%s"
                        % urllib.parse.quote(filename)})
            return 200
        report_id = urllib.parse.unquote(rest)
        report = self.ctx.reports.get(report_id)
        if not report:
            self._error(404, "보고서를 찾을 수 없습니다.", report_id=report_id)
            return 404
        self._json(report)
        return 200

    def _query(self, payload):
        try:
            spec = json.loads(payload.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._error(400, "JSON 본문을 읽을 수 없습니다.")
            return 400
        dataset_id = spec.get("dataset_id") or ""
        table = self.ctx.catalog.table(dataset_id)
        if not table:
            # 없는 데이터도 200 + ok:false로 돌려 UI가 사유를 표시할 수 있게 한다.
            self._error(200, "데이터를 찾을 수 없습니다.", dataset_id=dataset_id)
            return 200
        result = query_table(table["rows"], filters=spec.get("filters"),
                             group_by=spec.get("group_by"), metrics=spec.get("metrics"),
                             sort=spec.get("sort"), limit=spec.get("limit"))
        result.update({"ok": True, "dataset_id": dataset_id,
                       "source_kind": table["source_kind"]})
        self._json(result)
        return 200

    # -- 정적 파일 ----------------------------------------------------------
    def _static(self, path):
        web_dir = Path(self.ctx.config["web_dir"]).resolve()
        target = (web_dir / path.lstrip("/")).resolve()
        if web_dir not in target.parents and target != web_dir:
            self._error(403, "허용되지 않은 경로입니다.")
            return 403
        if not target.is_file():
            self._error(404, "파일을 찾을 수 없습니다.")
            return 404
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)
        return 200

    def _redirect(self, location):
        self._send(302, b"", "text/plain", {"location": location})
        return 302


class AgentServer(ThreadingHTTPServer):
    daemon_threads = True
    # Windows의 SO_REUSEADDR는 이미 쓰는 포트에도 겹쳐 묶여 두 번째 실행이 조용히 엉킨다.
    allow_reuse_address = os.name != "nt"

    def __init__(self, address, ctx):
        self.ctx = ctx
        super().__init__(address, Handler)


def make_server(config):
    """config(dict) → 실행 준비된 HTTPServer. host/port/runtime_dir 등을 읽는다."""
    cfg = build_config(config)
    ctx = AppContext(cfg)
    httpd = AgentServer((cfg["host"], cfg["port"]), ctx)
    ctx.log.write("server", "listen", 0, datetime.now(),
                  note="%s:%s" % (cfg["host"], cfg["port"]))
    return httpd


def serve_forever(config):
    """`python -m jeju_agent` 진입점이 쓰는 편의 함수. 종료 코드를 돌려준다."""
    cfg = build_config(config)
    url = "http://%s:%d/" % (cfg["host"], cfg["port"])
    if _already_running(url):
        # 실행.bat를 두 번 눌렀을 때: 새로 띄우지 않고 열린 화면으로 보낸다.
        print("이미 실행 중입니다: %s" % url)
        if cfg.get("open_browser"):
            _open_browser(url)
        return 0
    try:
        httpd = make_server(config)
    except OSError as exc:
        print("포트 %d을(를) 열 수 없습니다(%s). 다른 프로그램이 쓰고 있으면 .env의 PORT를 "
              "다른 번호(예: 8766)로 바꾼 뒤 다시 실행하세요." % (cfg["port"], exc))
        return 1
    url = "http://%s:%d/" % (httpd.server_address[0], httpd.server_address[1])
    if httpd.ctx.config.get("open_browser"):
        _open_browser(url)
    print("제주 상권 진단 Agent: %s" % url)
    print("중지: Ctrl+C")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


def _already_running(url):
    import urllib.request

    try:
        with urllib.request.urlopen(url + "api/status", timeout=1) as resp:
            return "hub" in json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError):
        return False


def _open_browser(url):
    import threading
    import webbrowser

    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
