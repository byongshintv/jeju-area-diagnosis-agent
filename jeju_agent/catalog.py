"""데이터 카탈로그.

`sample_data/hub/`의 메타 JSON, 센터 명세 70개 시트, 가상 센터 데이터를 한 목록으로 합친다.
카탈로그는 시작 시 한 번 읽고, 파일이 없으면 그 데이터만 `available=false`로 표시한다.
"""
import csv
import json
from pathlib import Path

from .center_schema import CENTER_DATASETS
from .config import APP_ROOT

DUMMY_DIR = APP_ROOT / "sample_data" / "center_dummy"
DUMMY_MARK = "가상 데이터(형식 시연용)"

# 반복 조회 시 CSV 파싱을 다시 하지 않기 위한 파일 단위 캐시(서버 프로세스 한정).
_TABLE_CACHE = {}

# 가상 센터 데이터로 만드는 시트.
DUMMY_SHEETS = ["1-08", "1-12", "1-14", "1-17", "1-19", "1-24"]

CENTER_PROVIDER = "제주빅데이터센터(가상 시연 데이터)"
CENTER_LICENSE = "형식 시연용 가상 데이터(실데이터 아님, 반출 대상 아님)"


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _peek_header(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return next(csv.reader(fh), [])
    except OSError:
        return []


class Catalog:
    """사용 가능한 데이터 목록. 업로드/시연 데이터가 추가되면 다시 계산한다."""

    def __init__(self, sample_dir=None, upload_store=None):
        self.sample_dir = Path(sample_dir) if sample_dir else APP_ROOT / "sample_data"
        self.hub_dir = self.sample_dir / "hub"
        self.dummy_dir = self.sample_dir / "center_dummy"
        self.upload_store = upload_store
        self._hub = self._load_hub()
        self._dummy = self._load_dummy()

    # -- 허브 ---------------------------------------------------------------
    def _load_hub(self):
        items = []
        if not self.hub_dir.is_dir():
            return items
        for meta_path in sorted(self.hub_dir.glob("*.meta.json")):
            meta = _read_json(meta_path) or {}
            csv_path = self.hub_dir / (meta.get("file") or (meta_path.stem.replace(".meta", "") + ".csv"))
            available = csv_path.is_file()
            items.append({
                "id": meta.get("id") or ("hub:" + meta_path.stem),
                "hub_dataset_id": meta.get("hub_dataset_id"),
                "name": meta.get("name") or meta_path.stem,
                "hub_title": meta.get("hub_title") or "",
                "provider": meta.get("provider") or "제주데이터허브",
                "source_kind": "hub",
                "period": meta.get("period") or "",
                "license": meta.get("license") or "",
                "available": available,
                "rows": meta.get("rows"),
                "file": csv_path.name,
                "url": meta.get("url") or "",
                "columns": meta.get("columns") or (_peek_header(csv_path) if available else []),
                "note": meta.get("note") or "",
                "collected_at": meta.get("collected_at") or "",
                "requested_id": meta.get("requested_id"),
            })
        return items

    # -- 가상 센터 데이터 -----------------------------------------------------
    def _load_dummy(self):
        items = []
        if not self.dummy_dir.is_dir():
            return items
        for meta_path in sorted(self.dummy_dir.glob("*.meta.json")):
            meta = _read_json(meta_path) or {}
            csv_path = self.dummy_dir / (meta.get("file") or "")
            items.append({
                "id": meta.get("id") or ("center_dummy:" + meta_path.stem),
                "dataset_no": meta.get("dataset_no"),
                "name": meta.get("name") or meta_path.stem,
                "provider": CENTER_PROVIDER,
                "source_kind": "center_dummy",
                "period": meta.get("period") or "",
                "license": CENTER_LICENSE,
                "available": csv_path.is_file(),
                "rows": meta.get("rows"),
                "file": csv_path.name,
                "columns": meta.get("columns") or (_peek_header(csv_path) if csv_path.is_file() else []),
                "note": DUMMY_MARK,
            })
        return items

    # -- 조회 ---------------------------------------------------------------
    def datasets(self):
        """실제로 값이 있는 데이터만 목록에 넣는다.

        센터 명세 70개 시트 전체는 `GET /api/schema`에서 보고, 여기에는 업로드된
        센터 데이터만 슬롯으로 나타난다.
        """
        items = list(self._hub) + list(self._dummy)
        if self.upload_store:
            for meta in self.upload_store.list():
                items.append({
                    "id": meta["dataset_id"],
                    "dataset_no": meta["dataset_no"],
                    "name": meta["name"],
                    "provider": "제주빅데이터센터(가상 시연)" if meta.get("demo") else "제주빅데이터센터(업로드)",
                    "source_kind": "center",
                    "period": meta.get("period") or "",
                    "license": "센터 반출 정책 적용(업로드 세션 한정)",
                    "available": True,
                    "rows": meta.get("rows"),
                    "file": meta.get("filename"),
                    "columns": meta.get("columns") or [],
                    "upload_id": meta.get("upload_id"),
                    "uploaded_at": meta.get("uploaded_at"),
                    "note": DUMMY_MARK if meta.get("demo") else "센터 CSV 업로드 — 세션 종료 시 삭제 가능",
                })
        return items

    def status_datasets(self):
        """`GET /api/status`용 축약 목록."""
        keep = ("id", "name", "provider", "source_kind", "period", "license", "available")
        return [{k: item.get(k) for k in keep} for item in self.datasets()]

    def get(self, dataset_id):
        for item in self.datasets():
            if item["id"] == dataset_id:
                return item
        return None

    def hub_items(self):
        return list(self._hub)

    def dummy_items(self):
        return list(self._dummy)

    def table(self, dataset_id):
        """dataset_id → {meta, header, rows, source_kind}. 없으면 None."""
        if dataset_id.startswith("hub:"):
            for item in self._hub:
                if item["id"] == dataset_id and item["available"]:
                    return load_csv_table(self.hub_dir / item["file"], dataset_id, "hub", item)
        if dataset_id.startswith("center_dummy:"):
            for item in self._dummy:
                if item["id"] == dataset_id and item["available"]:
                    return load_csv_table(self.dummy_dir / item["file"], dataset_id,
                                          "center_dummy", item)
        if dataset_id.startswith("center:") and self.upload_store:
            item = self.upload_store.by_dataset(dataset_id)
            if item:
                return {"meta": item["meta"], "header": item["header"],
                        "rows": item["rows"], "source_kind": "center"}
        return None


def _coerce(value):
    if value is None or value == "":
        return None
    text = value.strip()
    if text in ("NA", "NULL", "null", "None"):
        return None
    try:
        if "." in text or "e" in text.lower():
            return float(text)
        return int(text)
    except ValueError:
        return text


def load_csv_table(path, dataset_id, source_kind, meta=None):
    path = Path(path)
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return None
    cached = _TABLE_CACHE.get(key)
    if cached is not None:
        result = dict(cached)
        result["meta"] = dict(meta or cached.get("meta") or {}, id=dataset_id)
        result["source_kind"] = source_kind
        return result
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader, [])
            rows = []
            for raw in reader:
                if not raw or all(not c.strip() for c in raw):
                    continue
                rows.append({header[i]: _coerce(raw[i]) for i in range(len(header)) if i < len(raw)})
    except OSError:
        return None
    table = {"meta": dict(meta or {}, id=dataset_id), "header": header, "rows": rows,
             "source_kind": source_kind}
    _TABLE_CACHE[key] = {"header": header, "rows": rows}
    return table


def load_dummy_tables(dummy_dir=None):
    """가상 센터 데이터 전체를 {dataset_no: {meta, header, rows}}로 읽는다."""
    folder = Path(dummy_dir) if dummy_dir else DUMMY_DIR
    out = {}
    for meta_path in sorted(folder.glob("*.meta.json")):
        meta = _read_json(meta_path) or {}
        no = meta.get("dataset_no")
        if not no:
            continue
        csv_path = folder / (meta.get("file") or "")
        table = load_csv_table(csv_path, "center:" + no, "center_dummy", meta)
        if table:
            out[no] = table
    return out
