"""센터 CSV 업로드·판별.

센터 조회 결과 CSV 형식(게시판 #15):
  - 헤더 1행은 명세서 영문 컬럼명(따옴표 없음)
  - 값은 모두 큰따옴표, 첫 열은 따옴표가 없을 수 있음
  - 결측은 `NA`(또는 `NULL`)
  - 쉼표 구분, UTF-8(BOM 유무 모두)

헤더 컬럼 집합으로 70개 시트 중 하나를 판별해 `dataset_id="center:<시트번호>"`를 준다.
업로드 데이터는 메모리와 `runtime_dir/uploads/`에만 두고 `DELETE /api/uploads`로 지운다.
"""
import csv
import datetime as dt
import io
import json
from pathlib import Path

from .center_schema import BY_NO, CENTER_DATASETS, HEADER_INDEX
from .table_ops import is_missing, to_number

MISSING_TOKENS = {"", "NA", "NULL", "null", "None"}

def decode_bytes(raw):
    """업로드 바이트 → (text, encoding). BOM 유무·CP949 모두 받는다."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def split_lines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def parse_header_and_rows(text):
    """센터 CSV를 헤더·행 dict로 만든다. 값은 문자열 그대로 두고 결측만 None으로."""
    lines = [ln for ln in split_lines(text) if ln.strip()]
    if not lines:
        return [], []
    reader = csv.reader(io.StringIO("\n".join(lines)))
    raw_header = next(reader, [])
    header = [h.strip().lstrip("\ufeff") for h in raw_header]
    rows = []
    for raw in reader:
        if not raw or all(not c.strip() for c in raw):
            continue
        record = {}
        for idx, name in enumerate(header):
            if not name:
                continue
            value = raw[idx] if idx < len(raw) else ""
            record[name] = None if value.strip() in MISSING_TOKENS else value.strip()
        rows.append(record)
    return header, rows


def normalise_column(name):
    return str(name or "").strip().lstrip("\ufeff")


def match_dataset(header, rows=None):
    """헤더 리스트 → (시트 번호, 신뢰도). 못 찾으면 (None, 0.0)."""
    cleaned = tuple(normalise_column(h) for h in header if normalise_column(h))
    if not cleaned:
        return None, 0.0
    if cleaned in HEADER_INDEX:
        return refine_by_content(HEADER_INDEX[cleaned], rows), 1.0
    # 열 순서가 달라도 집합이 같으면 인정한다.
    wanted = set(cleaned)
    best, best_score = None, 0.0
    for ds in CENTER_DATASETS:
        names = {c["name"] for c in ds["columns"]}
        if not names:
            continue
        overlap = len(wanted & names)
        score = overlap / float(len(names | wanted))
        if wanted == names and score > best_score:
            best, best_score = ds["no"], 1.0
        elif score > best_score:
            best, best_score = ds["no"], score
    if best_score >= 0.9:
        return refine_by_content(best, rows), round(best_score, 3)
    return None, round(best_score, 3)


# 같은 헤더를 쓰는 시트를 값으로 구분한다(1-02/1-05, 1-03/1-06, 2-01/2-02, 5-05/5-06).
AMBIGUOUS = {
    "1-02": ("1-05", "user_type", lambda v: "중국" in v),
    "1-03": ("1-06", "user_type", lambda v: "중국" in v),
    "2-01": ("2-02", "sale_buy_code", lambda v: v.strip() == "판매"),
    "5-05": ("5-06", "tag_gno", lambda v: v.upper().startswith("KKRW6Y")),
}


def refine_by_content(dataset_no, rows):
    """값 단서로 동일 헤더의 다른 시트를 고른다."""
    rule = AMBIGUOUS.get(dataset_no)
    if not rule or not rows:
        return dataset_no
    other, col, is_other = rule
    values = [str(r.get(col) or "") for r in rows[:50]]
    if values and all(is_other(v) for v in values if v):
        return other
    return dataset_no


def summarise(rows, header):
    """행수·결측·숫자형 컬럼 요약."""
    missing = {}
    numeric = {}
    for name in header:
        if not name:
            continue
        blanks = 0
        numbers = 0
        total = 0
        for row in rows:
            total += 1
            value = row.get(name)
            if is_missing(value):
                blanks += 1
            elif to_number(value) is not None:
                numbers += 1
        missing[name] = blanks
        numeric[name] = bool(total) and numbers == (total - blanks) and numbers > 0
    return {"missing": missing, "numeric": numeric, "rows": len(rows), "columns": len(header)}


def period_of(rows):
    """base_year_month/base_date 컬럼에서 기간 문자열을 만든다."""
    for col in ("base_year_month", "base_date", "base_year", "년월"):
        values = sorted(str(r.get(col)) for r in rows if r.get(col))
        if not values:
            continue
        first, last = values[0][:8], values[-1][:8]
        if len(first) == 6:
            return "%s-%s ~ %s-%s" % (first[:4], first[4:6], last[:4], last[4:6])
        if len(first) >= 8:
            return "%s-%s-%s ~ %s-%s-%s" % (first[:4], first[4:6], first[6:8],
                                            last[:4], last[4:6], last[6:8])
        return "%s ~ %s" % (values[0], values[-1])
    return ""


def coerce_rows(rows, dataset_no):
    """명세 스키마의 숫자형 컬럼만 float으로 바꾼다(결측은 None 유지)."""
    ds = BY_NO.get(dataset_no)
    if not ds:
        return rows
    numeric_cols = [c["name"] for c in ds["columns"] if c["type"] == "number"]
    out = []
    for row in rows:
        record = dict(row)
        for col in numeric_cols:
            if col in record and record[col] is not None:
                num = to_number(record[col])
                if num is not None:
                    record[col] = int(num) if num.is_integer() else num
        out.append(record)
    return out


def friendly_name(dataset_no):
    ds = BY_NO.get(dataset_no)
    if not ds:
        return ""
    return ds["sheet"]


class UploadStore:
    """업로드된 센터 데이터를 메모리 + runtime_dir/uploads에 보관한다."""

    def __init__(self, runtime_dir):
        self.runtime_dir = Path(runtime_dir) if runtime_dir else None
        self._items = {}
        self._counter = 0

    def add(self, raw, filename, content_type=""):
        text, encoding = decode_bytes(raw)
        header, rows = parse_header_and_rows(text)
        if not header or not any(header):
            return {"ok": False, "message": "CSV 헤더를 읽을 수 없습니다. 1행에 센터 명세의 영문 컬럼명이 필요합니다."}
        dataset_no, score = match_dataset(header, rows)
        if not dataset_no:
            return {
                "ok": False,
                "message": "센터 데이터 명세(70개 시트)와 일치하는 헤더를 찾지 못했습니다. "
                           "센터 조회 결과 CSV 원본을 그대로 올려 주세요.",
                "header": header[:12],
                "best_score": score,
            }
        if not rows:
            return {"ok": False, "message": "데이터 행이 없습니다.", "dataset_id": "center:" + dataset_no}

        coerced = coerce_rows(rows, dataset_no)
        summary = summarise(rows, header)
        self._counter += 1
        upload_id = "u%d" % int(dt.datetime.now().timestamp() * 1000) + "_%d" % self._counter
        meta = {
            "upload_id": upload_id,
            "dataset_id": "center:" + dataset_no,
            "dataset_no": dataset_no,
            "name": friendly_name(dataset_no),
            "filename": str(filename or "center.csv"),
            "encoding": encoding,
            "content_type": content_type,
            "rows": len(rows),
            "columns": [c for c in header if c],
            "missing": summary["missing"],
            "numeric": summary["numeric"],
            "period": period_of(rows),
            "uploaded_at": dt.datetime.now().isoformat(timespec="seconds"),
            "match_score": score,
        }
        item = {"meta": meta, "rows": coerced, "header": [c for c in header if c]}
        self._drop_dataset(meta["dataset_id"])
        self._items[upload_id] = item
        if self.runtime_dir:
            self._persist(upload_id, item)
        return {"ok": True, "message": "센터 데이터를 인식했습니다.", **meta,
                "source_kind": "center"}

    def _persist(self, upload_id, item):
        if not self.runtime_dir:
            return
        folder = self.runtime_dir / "uploads"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / ("%s.json" % upload_id)).write_text(
                json.dumps(item["meta"], ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def add_demo(self, dataset_no, meta, header, rows):
        """가상 센터 데이터를 업로드한 것처럼 등록한다(시연 모드)."""
        self._counter += 1
        upload_id = "demo_%s" % dataset_no.replace("-", "_")
        record = {
            "upload_id": upload_id,
            "dataset_id": "center:" + dataset_no,
            "dataset_no": dataset_no,
            "name": meta.get("name") or friendly_name(dataset_no),
            "filename": meta.get("file") or "",
            "encoding": "utf-8-sig",
            "content_type": "text/csv",
            "rows": len(rows),
            "columns": [c for c in header if c],
            "missing": {},
            "numeric": {},
            "period": meta.get("period") or "",
            "uploaded_at": dt.datetime.now().isoformat(timespec="seconds"),
            "match_score": 1.0,
            "demo": True,
        }
        self._drop_dataset(record["dataset_id"])
        self._items[upload_id] = {"meta": record, "rows": rows,
                                  "header": [c for c in header if c]}
        return record

    def _drop_dataset(self, dataset_id):
        """같은 센터 데이터를 다시 올리면 새 파일이 이전 파일(가상 예시 포함)을 대체한다."""
        for upload_id in [k for k, v in self._items.items() if v["meta"]["dataset_id"] == dataset_id]:
            del self._items[upload_id]
            if self.runtime_dir:
                try:
                    (self.runtime_dir / "uploads" / ("%s.json" % upload_id)).unlink()
                except OSError:
                    pass

    def clear(self):
        removed = len(self._items)
        self._items.clear()
        if self.runtime_dir:
            folder = self.runtime_dir / "uploads"
            if folder.is_dir():
                for path in folder.glob("*.json"):
                    try:
                        path.unlink()
                    except OSError:
                        pass
        return removed

    def list(self):
        return [item["meta"] for item in self._items.values()]

    def get(self, upload_id):
        return self._items.get(upload_id)

    def by_dataset(self, dataset_id):
        for item in self._items.values():
            if item["meta"]["dataset_id"] == dataset_id:
                return item
        return None

    def latest_of(self, dataset_no):
        items = [i for i in self._items.values() if i["meta"]["dataset_no"] == dataset_no]
        return items[-1] if items else None

    def __len__(self):
        return len(self._items)
