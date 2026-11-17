"""센터 데이터 반출 사전 점검.

게시판 Q&A #43·#51 기준:
  - 최종 결과값(건수·인원·점포수·금액 원 단위·1인당 값 등 사칙연산 결과 포함)이 10 미만이면
    블라인드하거나 상위 범주로 병합한다.
  - 블라인드한 칸이 공개 합계·다른 세부값으로 역산되지 않게 같은 열에서 2차 억제하거나
    합계를 비공개한다.
  - 비율·증감률은 분자가 10 미만이면 가린다.
최종 판정은 제주빅데이터센터 심사라는 문구를 항상 붙인다(허브 공개 데이터 수치는 대상 아님).
"""
from .table_ops import to_number

THRESHOLD = 10
MASK = None
RATIO_HINTS = ("비율", "율", "비중", "증감", "1인당", "per", "share", "rate", "ratio")
NOTICE = ("이 표에는 제주빅데이터센터 반출 기준(최종 결과값 10 미만 가림·역산 방지)이 "
          "자동 적용되었습니다. 최종 판정은 제주빅데이터센터 심사에서 이뤄집니다.")


def _is_ratio_column(name):
    text = str(name or "").lower()
    return any(hint in text for hint in RATIO_HINTS)


def _numeric_columns(columns, rows):
    """값 대부분이 숫자인 열의 인덱스 집합. 비율 열은 이름으로도 인정한다."""
    numeric = set()
    for index, name in enumerate(columns):
        values = [to_number(row[index]) for row in rows if index < len(row)]
        known = [v for v in values if v is not None]
        if _is_ratio_column(name):
            numeric.add(index)
        elif known and len(known) >= max(1, int(len(values) * 0.6)):
            numeric.add(index)
    return numeric


def _cell(table, row, col):
    rows = table.get("rows") or []
    if row < len(rows) and col < len(rows[row]):
        return rows[row][col]
    return None


def _total_cell(table, col):
    total = table.get("total_row")
    if total and col < len(total):
        return total[col]
    return None


def check_export(table, sources=None, purpose=None, dataset=None):
    """table: {columns, rows, total_row?} → 반출 점검 결과 dict."""
    table = table or {}
    columns = list(table.get("columns") or [])
    rows = [list(r) for r in (table.get("rows") or [])]
    total_row = list(table.get("total_row")) if table.get("total_row") else None
    numeric = _numeric_columns(columns, rows)
    suppressed = []

    for r_index, row in enumerate(rows):
        for c_index in numeric:
            if c_index >= len(row):
                continue
            value = to_number(row[c_index])
            if value is None:
                continue
            reason = None
            if value < THRESHOLD:
                reason = "under_10"
            if reason:
                suppressed.append({"row": r_index, "col": c_index, "reason": reason,
                                   "column": columns[c_index] if c_index < len(columns) else ""})

    total_hidden = False
    if total_row:
        blocked = {(c["row"], c["col"]) for c in suppressed}
        for c_index in numeric:
            total_value = to_number(_total_cell(table, c_index))
            if total_value is None:
                continue
            hidden_rows = [r for r in range(len(rows)) if (r, c_index) in blocked]
            if len(hidden_rows) != 1:
                continue
            published = [(r, to_number(_cell(table, r, c_index)))
                         for r in range(len(rows)) if (r, c_index) not in blocked]
            published = [(r, v) for r, v in published if v is not None]
            if not published:
                total_hidden = True
                continue
            # 공개 합계 - 다른 공개값 = 가린 값이 그대로 드러난다. 가장 작은 다른 값을 함께 가린다.
            other = min(published, key=lambda item: item[1])
            suppressed.append({"row": other[0], "col": c_index,
                               "reason": "back_calculation",
                               "column": columns[c_index] if c_index < len(columns) else ""})
            blocked.add((other[0], c_index))

    blocked = {(c["row"], c["col"]) for c in suppressed}
    safe_rows = []
    for r_index, row in enumerate(rows):
        safe_rows.append([MASK if (r_index, c_index) in blocked else value
                          for c_index, value in enumerate(row)])
    safe_total = None
    if total_row:
        safe_total = list(total_row)
        if total_hidden:
            for c_index in numeric:
                if c_index < len(safe_total):
                    safe_total[c_index] = MASK
    safe_table = {"columns": columns, "rows": safe_rows}
    if total_row:
        safe_table["total_row"] = safe_total

    suppressed.sort(key=lambda c: (c["row"], c["col"]))
    notice = NOTICE
    if any(_is_ratio_column(columns[c["col"]]) for c in suppressed if c["col"] < len(columns)):
        notice += " 비율·증감률 열은 분자가 10 미만일 수 있어 함께 가렸습니다."
    return {
        "ok": True,
        "threshold": THRESHOLD,
        "suppressed": suppressed,
        "safe_table": safe_table,
        "total_hidden": total_hidden,
        "notice": notice,
        "application_text": application_text(columns, sources, purpose, dataset),
        "safe": not suppressed and not total_hidden,
    }


def application_text(columns, sources=None, purpose=None, dataset=None):
    """반출 신청 '설명' 칸 초안. 출처·가공 방법·활용 목적을 한 문단으로 적는다."""
    source_text = ", ".join(_source_label(s) for s in (sources or [])) or (dataset or "제주빅데이터센터 제공 데이터")
    columns_text = ", ".join(str(c) for c in (columns or []) if c)
    goal = purpose or "지역·업종 상권 진단 결과를 화면과 보고서로 제시"
    return (
        "출처: %s. 분석·가공: %s 항목을 기준으로 집계·비교하여 지역·업종별 상권 지표를 산출했습니다. "
        "활용 목적: %s. 개인을 식별할 수 있는 정보는 포함하지 않았고, 최종 결과값이 10 미만인 칸은 "
        "가림 또는 상위 범주 병합 처리했습니다. 최종 반출 가능 여부는 제주빅데이터센터 심사에서 결정됩니다."
        % (source_text, columns_text or "제공 항목", goal)
    )


def _source_label(source):
    if isinstance(source, dict):
        name = source.get("dataset") or source.get("name") or ""
        period = source.get("period") or ""
        return "%s(%s)" % (name, period) if period else str(name)
    return str(source)


def apply_to_table(table, source_kind, sources=None, purpose=None, dataset=None):
    """센터 유래 표면 반출 점검을 적용한다. 허브 공개 데이터는 그대로 둔다.

    반환: (표, 점검 결과 또는 None)
    """
    if source_kind not in ("center", "center_dummy"):
        return table, None
    result = check_export(table, sources=sources, purpose=purpose, dataset=dataset)
    safe = {k: v for k, v in table.items() if k not in ("columns", "rows", "total_row")}
    safe.update(result["safe_table"])
    safe["export_checked"] = True
    safe["source_kind"] = source_kind
    return safe, result
