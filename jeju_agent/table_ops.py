"""작은 테이블 유틸(표준 라이브러리만).

2단계 분석 도구가 쓸 필터·그룹·집계·정렬의 최소 구현. 행은 dict 리스트로 다룬다.
임의 코드 실행 없이 JSON DSL만 해석한다.
"""
import math
import re

MISSING = {"", "NA", "N/A", "NULL", "null", "None", "-"}

_NUM_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


def to_number(value):
    """숫자로 바꿀 수 있으면 float, 아니면 None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text in MISSING:
        return None
    if not _NUM_RE.match(text):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def is_missing(value):
    return value is None or (isinstance(value, str) and value.strip() in MISSING)


def parse_filter(spec):
    """필터 한 개를 해석한다.

    지원 형식:
      {"col": "bjd", "eq": "노형동"}
      {"col": "base_year_month", "in": ["202501", "202502"]}
      {"col": "use_amt", "gte": 1000} / "lte" / "gt" / "lt"
      {"col": "biz_nm", "contains": "카페"}
      {"col": "emd", "ne": "연동"}
    """
    col = (spec or {}).get("col")
    if not col:
        return None

    def match(row):
        value = row.get(col)
        if "eq" in spec:
            return _text(value) == _text(spec["eq"])
        if "ne" in spec:
            return _text(value) != _text(spec["ne"])
        if "in" in spec:
            wanted = {_text(v) for v in spec["in"] or []}
            return _text(value) in wanted
        if "contains" in spec:
            return _text(spec["contains"]) in _text(value)
        num = to_number(value)
        for op, cmp in (("gte", lambda a, b: a >= b), ("lte", lambda a, b: a <= b),
                        ("gt", lambda a, b: a > b), ("lt", lambda a, b: a < b)):
            if op in spec:
                limit = to_number(spec[op])
                if num is None or limit is None:
                    return False
                return cmp(num, limit)
        return True

    return match


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def apply_filters(rows, filters):
    """filters: list of dict | dict. dict면 AND 조건들의 list로 본다."""
    if not filters:
        return list(rows)
    if isinstance(filters, dict):
        filters = [filters]
    matchers = [m for m in (parse_filter(f) for f in filters) if m]
    out = []
    for row in rows:
        if all(m(row) for m in matchers):
            out.append(row)
    return out


def group_rows(rows, group_by):
    """group_by: 컬럼명 또는 컬럼명 list. 삽입 순서를 유지한 dict 반환.

    컬럼이 하나면 key가 문자열, 여러 개면 튜플이다.
    """
    single = isinstance(group_by, str)
    columns = [group_by] if single else list(group_by)
    groups = {}
    for row in rows:
        parts = tuple(_text(row.get(c)) for c in columns)
        key = parts[0] if single else parts
        groups.setdefault(key, []).append(row)
    return groups


def aggregate(rows, metrics):
    """metrics: list of {"col","op"} — op는 sum/mean/count/median/min/max/share.

    share는 같은 col 합계 대비 비율(0~1)을 뜻한다. `count`는 col 값이 결측이 아닌 행 수.
    """
    result = {}
    total_cache = {}
    for metric in metrics or []:
        col = metric.get("col")
        op = (metric.get("op") or "sum").lower()
        alias = metric.get("as") or "%s_%s" % (op, col)
        if op == "count":
            result[alias] = sum(1 for r in rows if not is_missing(r.get(col)))
            continue
        values = [v for v in (to_number(r.get(col)) for r in rows) if v is not None]
        result[alias] = _reduce(values, op, col, total_cache)
    return result


def _reduce(values, op, col, total_cache):
    if not values:
        return None
    if op == "sum":
        total = sum(values)
        total_cache[col] = total
        return round(total, 6) if isinstance(total, float) and not total.is_integer() else int(total)
    if op == "mean":
        return round(sum(values) / len(values), 6)
    if op == "median":
        return round(_median(values), 6)
    if op == "min":
        return min(values)
    if op == "max":
        return max(values)
    if op == "share":
        total = total_cache.get(col) or sum(values)
        return round(sum(values) / total, 6) if total else None
    raise ValueError("unsupported metric op: %r" % op)


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def sort_result(table, sort):
    """sort: list of {"col","desc"} 또는 컬럼명 문자열 list(오름차순)."""
    if not sort:
        return table
    if isinstance(sort, str):
        sort = [sort]
    specs = []
    for item in sort:
        if isinstance(item, str):
            specs.append({"col": item, "desc": False})
        elif isinstance(item, dict) and item.get("col"):
            specs.append({"col": item["col"], "desc": bool(item.get("desc"))})

    out = list(table)
    for spec in reversed(specs):
        # 안정 정렬을 위해 뒤쪽 키부터 적용한다. 숫자가 아닌 값(결측·문자)은 방향과
        # 무관하게 항상 뒤로 보낸다.
        col = spec["col"]
        desc = spec["desc"]

        def sortable(row, _col=col):
            num = to_number(row.get(_col))
            return (0, num) if num is not None else (1, _text(row.get(_col)))

        numeric = [r for r in out if to_number(r.get(col)) is not None]
        other = [r for r in out if to_number(r.get(col)) is None]
        numeric.sort(key=sortable, reverse=desc)
        other.sort(key=sortable)
        out = numeric + other
    return out


def query_table(rows, filters=None, group_by=None, metrics=None, sort=None, limit=None):
    """필터 → 그룹 → 집계 → 정렬 → 제한 순으로 처리한 결과.

    반환: {"columns": [...], "rows": [{...}, ...], "total_input": n, "matched": m}
    """
    matched = apply_filters(rows, filters)
    if group_by:
        columns = [group_by] if isinstance(group_by, str) else list(group_by)
        groups = group_rows(matched, columns)
        out = []
        for key, group in groups.items():
            parts = (key,) if len(columns) == 1 and not isinstance(key, tuple) else key
            record = dict(zip(columns, parts))
            record.update(aggregate(group, metrics))
            record["_count"] = len(group)
            out.append(record)
    else:
        out = []
        if metrics:
            record = aggregate(matched, metrics)
            record["_count"] = len(matched)
            out.append(record)
        else:
            out = list(matched)
        columns = list(out[0].keys()) if out else []
    out = sort_result(out, sort)
    total = len(out)
    if limit:
        out = out[: int(limit)]
    return {"columns": columns, "rows": out, "total_input": len(rows),
            "matched": len(matched), "groups": total}


def percentile_rank(values, value):
    """value의 백분위(0~100). 값이 없으면 None."""
    clean = [v for v in (to_number(x) for x in values) if v is not None]
    target = to_number(value)
    if target is None or not clean:
        return None
    below = sum(1 for v in clean if v < target)
    equal = sum(1 for v in clean if v == target)
    rank = (below + 0.5 * equal) / len(clean) * 100.0
    return round(rank, 1)


def safe_div(numerator, denominator, digits=4):
    num = to_number(numerator)
    den = to_number(denominator)
    if num is None or den in (None, 0):
        return None
    value = num / den
    if math.isfinite(value):
        return round(value, digits)
    return None
