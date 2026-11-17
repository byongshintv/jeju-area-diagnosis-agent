"""분석 도구(순수 함수 + LLM tool 스키마).

LLM function calling과 규칙 모드가 같은 함수를 쓴다. 각 도구는
`{summary_for_llm, artifacts, datasets_used}`를 돌려주고, 필요하면 `data`를 덧붙인다.
임의 코드 실행 없이 테이블 유틸(`table_ops`)과 사전(`dictionaries`)만 쓴다.

숫자는 모두 도구 결과 안에서만 만들고, 각 지표에 사용 데이터·기간·단위를 붙인다.
센터 업로드·가상 데이터에서 나온 표는 반출 점검(`export_check`)을 거친다.
"""
import datetime as dt
import re

from . import export_check as export_rules
from .dictionaries import (CITY_BY_EMD, DISTRICTS, get_industries, resolve_industry,
                           resolve_region)
from .table_ops import (aggregate, apply_filters, query_table, safe_div,
                        sort_result, to_number)

FLOATING_ID = "hub:583"
STORES_ID = "hub:793"
MASHUP_ID = "hub:748"
BUS_ID = "hub:613"
CARD_TIME_ID = "hub:502"
CARD_WEEKDAY_ID = "hub:504"
MENU_ID = "hub:495"
FOREIGN_ID = "hub:527"

MASKED_TEXT = "가림(10 미만)"

CENTER_IDS = {"card_emd": "center:1-08", "card_week": "center:1-12",
              "card_time": "center:1-14", "store_count": "center:1-17",
              "pos_emd": "center:1-19", "menu_emd": "center:1-24"}

TIME_ORDER = ["오전", "점심", "오후", "저녁", "심야", "밤"]
WEEK_ORDER = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

LIMITATION_BASE = [
    "유동인구 지수(허브 집계값)는 단위 미공개로 상대 비교만 사용합니다.",
    "허브 스냅샷은 고정 사본이라 최신 변동을 반영하지 않을 수 있습니다(각 지표의 기간 표시 참고).",
    "매출·이용금액은 카드·POS 표본 기준이라 현금 거래와 실제 매출 전체를 대표하지 않습니다.",
    "창업 성과를 보장하지 않으며, 임대료·인건비·인허가 등 비용 조건은 이 분석에 포함되지 않습니다.",
    "동종 점포 수는 상가(상권) 정보의 업종 분류에 따른 근사치입니다.",
]


# ---------------------------------------------------------------------------
# 공통 도우미
# ---------------------------------------------------------------------------

def _fmt_num(value, digits=0):
    num = to_number(value)
    if num is None:
        return "-"
    if digits:
        return "{:,.{d}f}".format(num, d=digits)
    return "{:,.0f}".format(num)


def _month_label(month):
    text = str(month or "")
    if len(text) >= 6 and text[:6].isdigit():
        return "%s-%s" % (text[:4], text[4:6])
    if len(text) >= 7 and text[4] in "-.":
        return text[:7]
    return text


def _months_of(rows, col="base_month"):
    return sorted({str(r.get(col) or "")[:6] for r in rows if r.get(col)})


def _series_by_month(rows, month_col, value_col, agg="sum"):
    buckets = {}
    for row in rows:
        month = str(row.get(month_col) or "")[:6]
        value = to_number(row.get(value_col))
        if not month or value is None:
            continue
        buckets.setdefault(month, []).append(value)
    out = []
    for month in sorted(buckets):
        values = buckets[month]
        if agg == "sum":
            out.append({"month": _month_label(month), "value": round(sum(values), 2)})
        else:
            out.append({"month": _month_label(month),
                        "value": round(sum(values) / len(values), 2)})
    return out


def _change_ratio(series):
    if len(series) < 2:
        return None
    first = series[0]["value"]
    last = series[-1]["value"]
    if not first:
        return None
    return round((last - first) / first * 100.0, 1)


def _region_scope(text):
    """지역 표현 → 범위 dict. 확정 실패면 (None, 후보)."""
    name, candidates = resolve_region(text)
    if not name:
        return None, candidates
    return _scope_of_name(name), []


def _scope_of_name(name):
    from .dictionaries import REGION_LIST

    if name in DISTRICTS:
        members = list(DISTRICTS[name])
        return {"name": name, "level": "권역", "city": "",
                "admin_dongs": members, "legal_dongs": [], "store_col": "행정동명",
                "store_values": members, "members": members}
    record = next((r for r in REGION_LIST if r["name"] == name), None)
    if not record:
        # 행정동·법정동 이름이 아닌 시군구 표기
        if name in ("제주시", "서귀포시"):
            members = [r["name"] for r in REGION_LIST
                       if r["city"] == name and r["level"] == "행정동"]
            return {"name": name, "level": "시군구", "city": name,
                    "admin_dongs": members, "legal_dongs": [], "store_col": "시군구명",
                    "store_values": [name], "members": members}
        return {"name": name, "level": "기타", "city": "",
                "admin_dongs": [name], "legal_dongs": [name], "store_col": "행정동명",
                "store_values": [name], "members": [name]}
    if record["level"] == "행정동":
        return {"name": name, "level": "행정동", "city": record["city"],
                "admin_dongs": [name], "legal_dongs": list(record["legal_dongs"]),
                "store_col": "행정동명", "store_values": [name], "members": [name]}
    return {"name": name, "level": "법정동", "city": record["city"],
            "admin_dongs": list(record["admin_dongs"]),
            "legal_dongs": [name], "store_col": "법정동명", "store_values": [name],
            "members": [name]}


def _industry_record(name):
    for ind in get_industries():
        if ind["name"] == name:
            return ind
    return None


def _store_matches(row, industry):
    mid = str(row.get("상권업종중분류명") or "").strip()
    small = str(row.get("상권업종소분류명") or "").strip()
    cats = industry.get("store_categories") or []
    return mid in cats or small in cats


def _industry_aliases_text(industry):
    return "%s(%s)" % (industry["name"], ", ".join(industry.get("aliases", [])[:5]))


class DataHub:
    """카탈로그·사전을 묶어 지표를 계산하는 얇은 계층."""

    def __init__(self, catalog):
        self.catalog = catalog
        self._cache = {}

    # -- 데이터 ------------------------------------------------------------
    def table(self, dataset_id):
        if dataset_id in self._cache:
            return self._cache[dataset_id]
        table = self.catalog.table(dataset_id)
        # 업로드/시연 데이터는 세션 중 추가·삭제될 수 있으므로 캐시하지 않는다.
        # 없는 데이터(None)도 캐시하면 이후 업로드를 못 보게 된다.
        if table is None or str(dataset_id).startswith("center:"):
            return table
        self._cache[dataset_id] = table
        return table

    def rows(self, dataset_id):
        table = self.table(dataset_id)
        return table["rows"] if table else []

    def available(self, dataset_id):
        return self.table(dataset_id) is not None

    def center_table(self, key):
        """가상/업로드 센터 데이터(가장 최근). 없으면 None."""
        dataset_id = CENTER_IDS.get(key)
        if not dataset_id:
            return None
        return self.table(dataset_id)

    def center_source_kind(self, table):
        return table.get("source_kind") or "center"

    # -- 유동인구 ----------------------------------------------------------
    def floating_rows(self, scope):
        rows = self.rows(FLOATING_ID)
        if not rows:
            return []
        wanted = set(scope["admin_dongs"])
        return [r for r in rows if str(r.get("emd") or "") in wanted]

    def floating_composition(self, scope):
        """최근 12개월 일평균 거주·근무·방문 구성(비중)."""
        rows = self.floating_rows(scope)
        if not rows:
            return None
        months = _months_of(rows, "base_month")[-12:]
        totals = {"resd_pop": 0.0, "work_pop": 0.0, "visit_pop": 0.0}
        month_count = set()
        for row in rows:
            month = str(row.get("base_month") or "")[:6]
            if month not in months:
                continue
            month_count.add(month)
            for field in totals:
                totals[field] += to_number(row.get(field)) or 0.0
        n = max(1, len(months))
        average = {k: v / n for k, v in totals.items()}
        total = sum(average.values())
        if not total:
            return None
        return {
            "resd": round(average["resd_pop"] / total * 100.0, 1),
            "work": round(average["work_pop"] / total * 100.0, 1),
            "visit": round(average["visit_pop"] / total * 100.0, 1),
            "total": round(total, 1),
            "months": len(months),
        }

    def floating_metric(self, scope, months=None, metric="total"):
        """월별 일평균 유동인구(거주+근무+방문 합 또는 방문만)."""
        rows = self.floating_rows(scope)
        if months:
            rows = [r for r in rows if str(r.get("base_month") or "")[:6] in months]
        fields = ["visit_pop"] if metric == "visit" else ["resd_pop", "work_pop", "visit_pop"]
        buckets = {}
        for row in rows:
            month = str(row.get("base_month") or "")[:6]
            if not month:
                continue
            total = 0.0
            for field in fields:
                total += to_number(row.get(field)) or 0.0
            # 성별×연령대별 표본일 평균 집계값을 합한다(실인원 단위는 미공개).
            buckets[month] = buckets.get(month, 0.0) + total
        return [{"month": _month_label(m), "value": round(buckets[m], 1)}
                for m in sorted(buckets)]

    def all_emd_floating(self, metric="total"):
        """43개 행정동 최근 12개월 평균(읍면동 분위 계산용)."""
        rows = self.rows(FLOATING_ID)
        if not rows:
            return {}
        months = _months_of(rows, "base_month")[-12:]
        fields = ["visit_pop"] if metric == "visit" else ["resd_pop", "work_pop", "visit_pop"]
        totals = {}
        month_totals = {}
        for row in rows:
            month = str(row.get("base_month") or "")[:6]
            if month not in months:
                continue
            emd = str(row.get("emd") or "")
            total = sum(to_number(row.get(f)) or 0.0 for f in fields)
            month_totals.setdefault(emd, {}).setdefault(month, 0.0)
            month_totals[emd][month] += total
        for emd, by_month in month_totals.items():
            values = list(by_month.values())
            totals[emd] = sum(values) / len(values) if values else 0.0
        return totals

    # -- 점포 --------------------------------------------------------------
    def store_rows(self, scope):
        rows = self.rows(STORES_ID)
        col = scope["store_col"]
        wanted = set(scope["store_values"])
        return [r for r in rows if str(r.get(col) or "").strip() in wanted]

    def store_count(self, scope, industry):
        return sum(1 for r in self.store_rows(scope) if _store_matches(r, industry))

    def all_emd_store_count(self, industry):
        rows = self.rows(STORES_ID)
        counts = {emd: 0 for emd in CITY_BY_EMD}
        for row in rows:
            if not _store_matches(row, industry):
                continue
            emd = str(row.get("행정동명") or "").strip()
            if emd:
                counts[emd] = counts.get(emd, 0) + 1
        return counts

    # -- 상권분석 매쉬업(2020, 읍면동×업종) ----------------------------------
    def mashup_rows(self, scope, industry):
        rows = self.rows(MASHUP_ID)
        if not rows:
            return []
        emds = set(scope["admin_dongs"])
        names = set(industry.get("card_names") or [])
        return [r for r in rows
                if str(r.get("읍면동명") or "").strip() in emds
                and str(r.get("업종명") or "").strip() in names]

    def all_emd_mashup_sales(self, industry):
        rows = self.rows(MASHUP_ID)
        names = set(industry.get("card_names") or [])
        totals = {}
        for row in rows:
            if str(row.get("업종명") or "").strip() not in names:
                continue
            emd = str(row.get("읍면동명") or "").strip()
            amount = to_number(row.get("이용금액")) or 0.0
            if emd:
                totals[emd] = totals.get(emd, 0.0) + amount
        return totals

    # -- 접근성 ------------------------------------------------------------
    def bus_rows(self, scope):
        rows = self.rows(BUS_ID)
        emds = set(scope["admin_dongs"])
        return [r for r in rows if str(r.get("emd") or "") in emds]

    def bus_summary(self, scope):
        rows = self.bus_rows(scope)
        if not rows:
            return None
        months = _months_of(rows, "base_month")
        latest = months[-1] if months else ""
        latest_rows = [r for r in rows if str(r.get("base_month") or "")[:6] == latest]
        stations = {str(r.get("station_id")) for r in latest_rows if r.get("station_id")}
        boards = [to_number(r.get("board_count")) or 0.0 for r in latest_rows]
        return {
            "stations": len(stations),
            "board_total": round(sum(boards), 1),
            "board_mean": round(sum(boards) / len(boards), 1) if boards else 0.0,
            "month": _month_label(latest),
        }

    def all_emd_bus(self):
        rows = self.rows(BUS_ID)
        if not rows:
            return {}
        months = _months_of(rows, "base_month")
        latest = months[-1] if months else ""
        totals = {}
        for row in rows:
            if str(row.get("base_month") or "")[:6] != latest:
                continue
            emd = str(row.get("emd") or "")
            if emd:
                totals[emd] = totals.get(emd, 0.0) + (to_number(row.get("board_count")) or 0.0)
        return totals

    # -- 제주 전체 카드 시간·요일 -------------------------------------------
    def card_time_share(self):
        rows = self.rows(CARD_TIME_ID)
        if not rows:
            return []
        months = _months_of(rows, "base_year_month")[-12:]
        buckets = {}
        for row in rows:
            if str(row.get("base_year_month") or "")[:6] not in months:
                continue
            buckets[str(row.get("time_type") or "")] = (
                buckets.get(str(row.get("time_type") or ""), 0.0)
                + (to_number(row.get("use_amout_by_million")) or 0.0))
        total = sum(buckets.values())
        out = []
        for key in sorted(buckets, key=lambda k: TIME_ORDER.index(k) if k in TIME_ORDER else 99):
            if not key:
                continue
            out.append({"name": key, "value": round(buckets[key] / total * 100.0, 1)
                        if total else 0.0})
        return out

    def card_weekday_share(self):
        rows = self.rows(CARD_WEEKDAY_ID)
        if not rows:
            return []
        months = _months_of(rows, "base_year_month")[-12:]
        buckets = {}
        for row in rows:
            if str(row.get("base_year_month") or "")[:6] not in months:
                continue
            key = str(row.get("day_of_week") or "")
            buckets[key] = buckets.get(key, 0.0) + (to_number(row.get("use_count")) or 0.0)
        total = sum(buckets.values())
        return [{"name": key, "value": round(buckets[key] / total * 100.0, 1) if total else 0.0}
                for key in WEEK_ORDER if key in buckets]

    # -- 메뉴 --------------------------------------------------------------
    def menu_share(self, industry):
        rows = self.rows(MENU_ID)
        keywords = industry.get("menu_keywords") or []
        if not rows or not keywords:
            return []
        buckets = {}
        for row in rows:
            name = str(row.get("menu_name") or "")
            if not any(k in name for k in keywords):
                continue
            buckets[name] = buckets.get(name, 0.0) + (
                to_number(row.get("sale_amount_by_million")) or 0.0)
        total = sum(buckets.values())
        out = [{"name": k, "value": round(v / total * 100.0, 1) if total else 0.0}
               for k, v in sorted(buckets.items(), key=lambda kv: -kv[1])]
        return out[:8]

    def menu_period(self):
        rows = self.rows(MENU_ID)
        months = _months_of(rows, "base_year_month")
        if not months:
            return ""
        return "%s ~ %s" % (_month_label(months[0]), _month_label(months[-1]))

    # -- 센터(업로드·가상) --------------------------------------------------
    def center_card_rows(self, scope, industry):
        table = self.center_table("card_emd")
        if not table:
            return []
        names = set(industry.get("card_names") or [])
        legal = set(scope["legal_dongs"])
        rows = [r for r in table["rows"]
                if str(r.get("bjd") or "").strip() in legal
                and str(r.get("biz_nm") or "").strip() in names]
        return rows

    def center_time_rows(self, scope, industry):
        table = self.center_table("card_time")
        if not table:
            return []
        names = set(industry.get("card_names") or [])
        legal = set(scope["legal_dongs"])
        return [r for r in table["rows"]
                if str(r.get("bjd") or "").strip() in legal
                and str(r.get("biz_nm") or "").strip() in names]

    def center_week_rows(self, scope, industry):
        table = self.center_table("card_week")
        if not table:
            return []
        names = set(industry.get("card_names") or [])
        legal = set(scope["legal_dongs"])
        return [r for r in table["rows"]
                if str(r.get("bjd") or "").strip() in legal
                and str(r.get("biz_nm") or "").strip() in names]

    def center_menu_rows(self, scope):
        table = self.center_table("menu_emd")
        if not table:
            return []
        legal = set(scope["legal_dongs"])
        return [r for r in table["rows"] if str(r.get("bjd_nm") or "").strip() in legal]

    def center_pos_rows(self, scope, industry):
        table = self.center_table("pos_emd")
        if not table:
            return []
        legal = set(scope["legal_dongs"])
        pos_codes = [c for c in (industry.get("pos_codes") or []) if c]
        mid = {c[:3] for c in pos_codes}
        rows = []
        for row in table["rows"]:
            if str(row.get("bjd_nm") or "").strip() not in legal:
                continue
            code = str(row.get("biz_mid_cd") or "")
            if pos_codes and (code in pos_codes or code in mid):
                rows.append(row)
        return rows

    def center_period(self, table):
        return (table.get("meta") or {}).get("period") or ""


# ---------------------------------------------------------------------------
# 도구 결과 뼈대
# ---------------------------------------------------------------------------

def _result(summary, artifacts=None, datasets=None, data=None):
    out = {"summary_for_llm": summary, "artifacts": artifacts or [],
           "datasets_used": sorted(set(datasets or []))}
    if data is not None:
        out["data"] = data
    return out


def _source_note(hub, dataset_id):
    item = hub.catalog.get(dataset_id) or {}
    return {"dataset": item.get("name") or dataset_id, "provider": item.get("provider") or "",
            "period": item.get("period") or "", "source_kind": item.get("source_kind") or "hub",
            "id": dataset_id}


# ---------------------------------------------------------------------------
# 도구 1·2: 지역·업종 확정
# ---------------------------------------------------------------------------

def resolve_region_tool(hub, text):
    name, candidates = resolve_region(text)
    if name:
        scope = _scope_of_name(name)
        summary = "'%s'을(를) %s %s(으)로 확정했습니다." % (
            text, scope["city"] or "", scope["name"])
        data = {"match": name, "candidates": [], "scope": scope}
    elif candidates:
        summary = "'%s'이(가) 여러 지역과 겹칩니다. 후보: %s" % (text, ", ".join(candidates[:10]))
        data = {"match": None, "candidates": candidates}
    else:
        summary = "'%s'에 해당하는 제주 읍면동을 찾지 못했습니다." % text
        data = {"match": None, "candidates": []}
    return _result(summary, datasets=[], data=data)


def resolve_industry_tool(hub, text):
    name, candidates = resolve_industry(text)
    if name:
        industry = _industry_record(name)
        summary = "'%s'을(를) 업종 '%s'(으)로 확정했습니다%s." % (
            text, name, (" — 카드 업종명 " + ", ".join(industry.get("card_names") or []))
            if industry and industry.get("card_names") else "")
        data = {"match": name, "candidates": [], "industry": industry}
    elif candidates:
        summary = "'%s'이(가) 여러 업종과 겹칩니다. 후보: %s" % (text, ", ".join(candidates[:10]))
        data = {"match": None, "candidates": candidates}
    else:
        summary = "'%s'에 해당하는 업종을 사전에서 찾지 못했습니다." % text
        data = {"match": None, "candidates": []}
    return _result(summary, datasets=[], data=data)


# ---------------------------------------------------------------------------
# 도구 3·4: 카탈로그·프로파일
# ---------------------------------------------------------------------------

def list_datasets_tool(hub, topic=None):
    items = hub.catalog.datasets()
    topic_text = str(topic or "").strip()
    if topic_text:
        items = [d for d in items
                 if topic_text in (d.get("name") or "") or topic_text in (d.get("id") or "")]
    rows = []
    for item in items:
        rows.append({
            "id": item.get("id"), "name": item.get("name"),
            "source_kind": item.get("source_kind"), "period": item.get("period") or "",
            "rows": item.get("rows"), "available": item.get("available"),
        })
    usable = [r for r in rows if r["available"]]
    summary = "사용 가능한 데이터 %d개%s." % (
        len(usable), (" (주제 '%s')" % topic_text) if topic_text else "")
    for r in usable[:12]:
        summary += "\n- %s: %s (%s, %s행)" % (r["id"], r["name"], r["period"], r["rows"])
    return _result(summary, artifacts=[{
        "id": "catalog", "type": "table", "title": "사용 가능 데이터 목록",
        "columns": ["id", "name", "source_kind", "period", "rows"],
        "rows": [[r["id"], r["name"], r["source_kind"], r["period"], r["rows"]] for r in usable],
        "source": "카탈로그",
    }], datasets=[r["id"] for r in usable], data={"datasets": rows})


def profile_dataset_tool(hub, dataset_id):
    table = hub.table(dataset_id)
    if not table:
        return _result("'%s' 데이터를 찾을 수 없습니다." % dataset_id, datasets=[],
                       data={"found": False})
    header = table["header"]
    rows = table["rows"]
    preview = [{c: row.get(c) for c in header} for row in rows[:3]]
    numeric = []
    for col in header:
        values = [to_number(r.get(col)) for r in rows]
        known = [v for v in values if v is not None]
        if known and len(known) >= max(1, int(len(values) * 0.6)):
            numeric.append(col)
    missing = {}
    for col in header:
        missing[col] = sum(1 for r in rows if r.get(col) in (None, "", "NA", "NULL"))
    meta = table.get("meta") or {}
    summary = "%s: %d행 × %d열, 기간 %s. 숫자형 열: %s." % (
        dataset_id, len(rows), len(header), meta.get("period") or "-",
        ", ".join(numeric[:10]) or "없음")
    return _result(summary, artifacts=[{
        "id": "profile", "type": "table", "title": "%s 프로파일" % dataset_id,
        "columns": ["column", "missing", "numeric"],
        "rows": [[c, missing[c], c in numeric] for c in header],
        "source": dataset_id,
    }], datasets=[dataset_id], data={"columns": header, "rows": len(rows),
                                     "missing": missing, "numeric": numeric,
                                     "period": meta.get("period") or "",
                                     "preview": preview})


def query_table_tool(hub, dataset_id, filters=None, group_by=None, metrics=None,
                     sort=None, limit=None):
    table = hub.table(dataset_id)
    if not table:
        return _result("'%s' 데이터를 찾을 수 없습니다." % dataset_id, datasets=[],
                       data={"found": False})
    result = query_table(table["rows"], filters=filters, group_by=group_by,
                         metrics=metrics, sort=sort, limit=limit)
    columns = result["columns"]
    rows = result["rows"]
    shown_rows = [[row.get(c) for c in columns] for row in rows]
    summary = "%s 집계: %d행 중 %d행 일치, %d개 그룹." % (
        dataset_id, result["total_input"], result["matched"], result["groups"])
    for row in shown_rows[:5]:
        summary += "\n- " + ", ".join("%s=%s" % (c, row[i]) for i, c in enumerate(columns))
    return _result(summary, artifacts=[{
        "id": "query", "type": "table", "title": "%s 집계 결과" % dataset_id,
        "columns": columns, "rows": shown_rows, "source": dataset_id,
    }], datasets=[dataset_id], data=result)


# ---------------------------------------------------------------------------
# 도구: 차트 스펙
# ---------------------------------------------------------------------------

CHART_KINDS = ("bar", "line", "stacked", "map-choropleth", "donut")


def make_chart_spec(kind, data, title, unit="", source="", x_label="", series=None):
    kind = kind if kind in CHART_KINDS else "bar"
    points = []
    for item in data or []:
        if isinstance(item, dict):
            points.append({"name": str(item.get("name") or item.get("month") or ""),
                           "value": to_number(item.get("value"))})
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            points.append({"name": str(item[0]), "value": to_number(item[1])})
    return {"kind": kind, "title": title, "unit": unit, "source": source,
            "x_label": x_label or (points[0]["name"] if points else ""),
            "series": series or [{"name": title, "points": points}]}


def make_chart_tool(hub, kind, data, title, unit="", source=""):
    spec = make_chart_spec(kind, data, title, unit=unit, source=source)
    artifact = {"id": "chart", "type": "chart", "title": title}
    artifact.update(spec)
    return _result("차트 '%s'(%s, %d개 항목)을 만들었습니다." % (title, spec["kind"],
                                                              len(spec["series"][0]["points"])),
                   artifacts=[artifact], datasets=[], data=spec)


# ---------------------------------------------------------------------------
# 도구: 진단(핵심)
# ---------------------------------------------------------------------------

def _industry_context(hub, scope, industry):
    """수요·경쟁 지표를 허브/센터 우선순위로 계산한다."""
    out = {}
    center_card = hub.center_card_rows(scope, industry)
    floating = hub.floating_metric(scope)
    if floating:
        out["floating"] = floating
        out["floating_avg"] = round(sum(p["value"] for p in floating) / len(floating), 1)
        out["floating_change"] = _change_ratio(floating)
        out["floating_months"] = len(floating)
        out["floating_period"] = "%s ~ %s" % (floating[0]["month"], floating[-1]["month"])
    visit = hub.floating_metric(scope, metric="visit")
    if visit:
        out["visit_avg"] = round(sum(p["value"] for p in visit) / len(visit), 1)
    composition = hub.floating_composition(scope)
    if composition:
        out["composition"] = composition
    if center_card:
        months = _months_of(center_card, "base_year_month")
        by_month = {}
        for row in center_card:
            month = str(row.get("base_year_month") or "")[:6]
            by_month[month] = by_month.get(month, 0.0) + (to_number(row.get("use_amt")) or 0.0)
        out["card"] = [{"month": _month_label(m), "value": round(by_month[m], 0)}
                       for m in sorted(by_month)]
        out["card_source"] = "center"
        out["card_users"] = sum(to_number(r.get("user_cnt")) or 0 for r in center_card)
        by_type = {}
        for row in center_card:
            key = str(row.get("user_type") or "")
            if not key:
                continue
            by_type[key] = by_type.get(key, 0.0) + (to_number(row.get("use_amt")) or 0.0)
        if by_type:
            total = sum(by_type.values())
            out["card_user_mix"] = [
                {"name": k, "value": round(v / total * 100.0, 1) if total else 0.0}
                for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])]
        out["card_uses"] = sum(to_number(r.get("use_cnt")) or 0 for r in center_card)
        out["card_period"] = "%s ~ %s" % (_month_label(months[0]), _month_label(months[-1])) \
            if months else ""
    else:
        mashup = hub.mashup_rows(scope, industry)
        if mashup:
            by_month = {}
            users = {}
            for row in mashup:
                month = str(row.get("년월") or "")[:7]
                by_month[month] = by_month.get(month, 0.0) + (to_number(row.get("이용금액")) or 0.0)
                users[month] = users.get(month, 0.0) + (to_number(row.get("이용자수")) or 0.0)
            out["card"] = [{"month": m, "value": round(by_month[m], 0)}
                           for m in sorted(by_month)]
            out["card_source"] = "mashup"
            out["card_users"] = round(sum(users.values()) / max(1, len(users)), 1)
            out["card_uses"] = None
            out["card_period"] = "%s ~ %s" % (sorted(by_month)[0][:7],
                                              sorted(by_month)[-1][:7]) if by_month else ""
            out["mashup_amount"] = sum(by_month.values())
    out["stores"] = hub.store_count(scope, industry)
    if out.get("mashup_amount") and out["stores"]:
        out["sales_per_store"] = round(out["mashup_amount"] / out["stores"], 0)
    return out


def _relative_position(values, value):
    rank = 1 + sum(v > value for v in values)
    return {"rank": rank, "count": len(values), "pct": round(rank / len(values) * 100, 1)}


def rank_label(item):
    return "%d곳 중 %d위(상위 %s%%)" % (item["count"], item["rank"], _fmt_num(item["pct"]))


def _percentile_table(hub, scope, industry, context):
    """제주 전체 읍면동 분위(해당 지표가 있는 경우만)."""
    out = {}
    all_float = hub.all_emd_floating()
    if all_float:
        value = sum(all_float.get(e, 0.0) for e in scope["admin_dongs"])
        # 법정동 scope는 행정동 합으로 근사한다.
        out["floating"] = {"value": round(value, 1),
                           **_relative_position(list(all_float.values()), value)}
    all_stores = hub.all_emd_store_count(industry)
    if all_stores:
        value = hub.store_count(scope, industry)
        out["stores"] = {"value": value,
                         **_relative_position(list(all_stores.values()), value)}
    all_sales = hub.all_emd_mashup_sales(industry)
    all_stores_map = all_stores or {}
    if all_sales:
        ratios = {}
        for emd, amount in all_sales.items():
            count = all_stores_map.get(emd) or 0
            if count:
                ratios[emd] = amount / count
        if ratios:
            value = context.get("sales_per_store")
            if value:
                out["sales_per_store"] = {"value": value,
                                          **_relative_position(list(ratios.values()), value)}
    all_bus = hub.all_emd_bus()
    if all_bus:
        value = sum(all_bus.get(e, 0.0) for e in scope["admin_dongs"])
        if value:
            out["bus"] = {"value": round(value, 1),
                          **_relative_position(list(all_bus.values()), value)}
    return out


def diagnose_area_tool(hub, region, industry, period=None):
    scope, candidates = _region_scope(region) if isinstance(region, str) else (None, [])
    if scope is None:
        return _result("지역 '%s'을(를) 확정하지 못했습니다." % region,
                       data={"clarification": {"question": "어느 읍면동을 말씀하시는 건가요?",
                                               "candidates": candidates}})
    industry_name = industry
    industry_record = _industry_record(industry_name) if industry_name else None
    if industry_record is None:
        name, cands = resolve_industry(industry_name or "")
        if name:
            industry_record = _industry_record(name)
            industry_name = name
    if industry_record is None:
        return _result("업종 '%s'을(를) 확정하지 못했습니다." % industry,
                       data={"clarification": {"question": "어떤 업종을 말씀하시는 건가요?",
                                               "candidates": []}})

    context = _industry_context(hub, scope, industry_record)
    pcts = _percentile_table(hub, scope, industry_record, context)
    datasets = []
    sections = []
    artifacts = []
    # 근거 표 지표명 → 그 값을 적은 문장(위치·다시 쓰는 함수). 반출 점검이 가린 지표는
    # 문장에서도 같이 가린다(표만 가리고 본문에 값이 남으면 반출 기준을 못 지킨다).
    line_refs = {}

    def masked_line(label, lines, render):
        line_refs[label] = (lines, len(lines), render)
        lines.append(render(set()))

    # ① 수요
    demand_lines = []
    if context.get("floating"):
        demand_lines.append(
            "유동인구 지수(허브 집계값)는 %s, 최근 %d개월 %s(%s ~ %s, hub:583; 단위 미공개, 상대 비교만 사용)." % (
                rank_label(pcts["floating"]), context["floating_months"],
                ("%+.1f%%" % context["floating_change"]) if context.get("floating_change") is not None
                else "변화 계산 불가", context["floating"][0]["month"], context["floating"][-1]["month"]))
        composition = context.get("composition") or {}
        if composition:
            demand_lines.append(
                "구성은 거주 %s%%, 근무 %s%%, 방문 %s%%입니다." % (
                    _fmt_num(composition.get("resd"), 1), _fmt_num(composition.get("work"), 1),
                    _fmt_num(composition.get("visit"), 1)))
        datasets.append(FLOATING_ID)
    if context.get("card"):
        masked_line("동종 업종 월평균 카드 이용금액", demand_lines, lambda hidden: (
            "%s 업종 카드 이용금액은 월평균 %s 수준입니다(%s, %s)." % (
                industry_record["name"],
                MASKED_TEXT if "동종 업종 월평균 카드 이용금액" in hidden else
                _fmt_num(sum(p["value"] for p in context["card"]) /
                         max(1, len(context["card"]))) + "원",
                context.get("card_period") or "-",
                "센터 데이터" if context.get("card_source") == "center"
                else "hub:748 상권분석 매쉬업 2020년")))
        if context.get("card_user_mix"):
            demand_lines.append("카드 이용금액 기준 이용자 구성은 %s입니다." % ", ".join(
                "%s %s%%" % (m["name"], _fmt_num(m["value"], 1))
                for m in context["card_user_mix"]))
        datasets.append(CENTER_IDS["card_emd"] if context.get("card_source") == "center"
                        else MASHUP_ID)

    # ② 경쟁
    competition_lines = []
    if context.get("stores") is not None:
        masked_line("동종 점포 수", competition_lines, lambda hidden: (
            "동종 점포(상가정보 기준) %s입니다(hub:793, 2025-09 기준)."
            % (MASKED_TEXT if "동종 점포 수" in hidden else _fmt_num(context["stores"]) + "개")))
        datasets.append(STORES_ID)
    if context.get("sales_per_store"):
        masked_line("점포당 카드 이용금액", competition_lines, lambda hidden: (
            "점포당 연간 카드 이용금액은 %s입니다(2020년 기준)."
            % (MASKED_TEXT if "점포당 카드 이용금액" in hidden
               else "약 %s원" % _fmt_num(context["sales_per_store"]))))
    if pcts.get("stores"):
        competition_lines.append("동종 점포 수(경쟁)는 %s입니다." % rank_label(pcts["stores"]))

    # 인접 읍면동 비교
    neighbors = _neighbor_rows(hub, scope, industry_record)
    if neighbors:
        datasets.append(STORES_ID)
        if MASHUP_ID not in datasets:
            datasets.append(MASHUP_ID)

    # ③ 시간·요일
    time_lines = []
    center_time = hub.center_time_rows(scope, industry_record)
    center_week = hub.center_week_rows(scope, industry_record)
    time_series = []
    weekday_series = []
    if center_time:
        by_time = {}
        for row in center_time:
            key = str(row.get("time") or "")
            by_time[key] = by_time.get(key, 0.0) + (to_number(row.get("use_amt")) or 0.0)
        total = sum(by_time.values())
        time_series = [{"name": k, "value": round(by_time[k] / total * 100.0, 1) if total else 0}
                       for k in sorted(by_time, key=lambda k: TIME_ORDER.index(k)
                                       if k in TIME_ORDER else 99)]
        datasets.append(CENTER_IDS["card_time"])
        time_lines.append("시간대별 이용금액 비중은 %s입니다(센터 데이터 %s)." % (
            ", ".join("%s %s%%" % (p["name"], _fmt_num(p["value"], 1)) for p in time_series),
            hub.center_period(hub.center_table("card_time"))))
    else:
        time_series = hub.card_time_share()
        if time_series:
            datasets.append(CARD_TIME_ID)
            time_lines.append("제주 전체 카드 이용금액의 시간대 비중은 %s입니다(hub:502, "
                              "우리 지역 구분 없음)." % ", ".join(
                                  "%s %s%%" % (p["name"], _fmt_num(p["value"], 1))
                                  for p in time_series))
    if center_week:
        by_day = {}
        for row in center_week:
            key = str(row.get("week") or "")
            by_day[key] = by_day.get(key, 0.0) + (to_number(row.get("use_amt")) or 0.0)
        total = sum(by_day.values())
        weekday_series = [{"name": k, "value": round(by_day[k] / total * 100.0, 1) if total else 0}
                          for k in WEEK_ORDER if k in by_day]
        datasets.append(CENTER_IDS["card_week"])
        time_lines.append("요일별 이용금액 비중은 %s입니다(센터 데이터)." % ", ".join(
            "%s %s%%" % (p["name"], _fmt_num(p["value"], 1)) for p in weekday_series))
    else:
        weekday_series = hub.card_weekday_share()
        if weekday_series:
            datasets.append(CARD_WEEKDAY_ID)
            time_lines.append("제주 전체 카드 이용건수의 요일 비중은 %s입니다(hub:504, "
                              "우리 지역 구분 없음)." % ", ".join(
                                  "%s %s%%" % (p["name"], _fmt_num(p["value"], 1))
                                  for p in weekday_series))

    # ④ 가격대
    price_lines = []
    price_series = []
    center_menu = hub.center_menu_rows(scope)
    if center_menu:
        total_amt = sum(to_number(r.get("store_sale_amt")) or 0 for r in center_menu)
        by_menu = {}
        for row in center_menu:
            key = str(row.get("menu_large_nm") or "")
            by_menu[key] = by_menu.get(key, 0.0) + (to_number(row.get("store_sale_amt")) or 0.0)
        price_series = [{"name": k, "value": round(v / total_amt * 100.0, 1) if total_amt else 0}
                        for k, v in sorted(by_menu.items(), key=lambda kv: -kv[1])][:8]
        prices = [to_number(r.get("unit_price")) for r in center_menu]
        prices = [p for p in prices if p]
        if prices:
            masked_line("메뉴 객단가 중앙값", price_lines, lambda hidden: (
                "메뉴 객단가 중앙값은 %s입니다(센터 데이터, %s)." % (
                    MASKED_TEXT if "메뉴 객단가 중앙값" in hidden
                    else "약 %s원" % _fmt_num(sorted(prices)[len(prices) // 2]),
                    hub.center_period(hub.center_table("menu_emd")))))
            datasets.append(CENTER_IDS["menu_emd"])
    else:
        price_series = hub.menu_share(industry_record)
        if price_series:
            period = hub.menu_period()
            price_lines.append("제주 전체 메뉴 매출 비중 상위는 %s입니다(hub:495, %s, "
                               "객단가 아님·매출액 기준)." % (
                                   ", ".join("%s %s%%" % (p["name"], _fmt_num(p["value"], 1))
                                             for p in price_series[:4]), period))
            datasets.append(MENU_ID)

    # ⑤ 접근성
    access_lines = []
    bus = hub.bus_summary(scope)
    if bus:
        datasets.append(BUS_ID)
        # 정류소 평균 = 승차 인원 ÷ 정류소 수라 둘 중 하나를 가리면 평균도 가린다(역산 방지).
        def bus_line(hidden):
            stations_hidden = "정류소 수" in hidden
            board_hidden = "정류소 일평균 승차 인원" in hidden
            return "정류소 %s, 하루 평균 승차 인원 %s(정류소 평균 %s, %s 기준, hub:613)." % (
                MASKED_TEXT if stations_hidden else _fmt_num(bus["stations"]) + "곳",
                MASKED_TEXT if board_hidden else _fmt_num(bus["board_total"]) + "명",
                MASKED_TEXT if stations_hidden or board_hidden
                else _fmt_num(bus["board_mean"]) + "명", bus["month"])
        line_refs["정류소 수"] = (access_lines, len(access_lines), bus_line)
        masked_line("정류소 일평균 승차 인원", access_lines, bus_line)

    # ⑥ 종합
    judgments = []
    for key, label, high_is_good in (("floating", "유동인구 수준", True),
                                     ("stores", "동종 점포 수(경쟁)", False),
                                     ("sales_per_store", "점포당 이용금액", True),
                                     ("bus", "버스 승차 인원", True)):
        item = pcts.get(key)
        if not item or item.get("pct") is None:
            continue
        pct = item["pct"]
        if pct <= 33.3:
            tone = "강점" if high_is_good else "주의: 경쟁 많음"
        elif pct >= 66.7:
            tone = "주의" if high_is_good else "강점: 경쟁 적음"
        else:
            tone = "중간"
        judgments.append({"key": key, "metric": label, "percentile": pct,
                          "position": rank_label(item), "tone": tone,
                          "rank": item.get("rank"), "count": item.get("count")})

    if not datasets:
        return _result("'%s' 지역·'%s' 업종에 쓸 수 있는 스냅샷 데이터가 없습니다." % (
            scope["name"], industry_record["name"]), data={"empty": True})

    # 근거 표(모든 수치에 출처·기간·단위를 붙인다)
    evidence_columns = ["지표", "값", "단위", "기간", "출처 데이터"]
    evidence_rows = []
    if context.get("floating_avg"):
        evidence_rows.append(["유동인구 지수(허브 집계값)", context["floating_avg"], "허브 집계값",
                              context.get("floating_period"), "hub:583 읍면동 내국인 유동인구"])
    if context.get("visit_avg"):
        evidence_rows.append(["방문 구성 지수(허브 집계값)", context["visit_avg"], "허브 집계값",
                              context.get("floating_period"), "hub:583"])
    if context.get("card"):
        evidence_rows.append(["동종 업종 월평균 카드 이용금액",
                              round(sum(p["value"] for p in context["card"]) /
                                    max(1, len(context["card"]))), "원/월",
                              context.get("card_period"),
                              "center:1-08" if context.get("card_source") == "center"
                              else "hub:748 상권분석 매쉬업"])
    if context.get("stores") is not None:
        evidence_rows.append(["동종 점포 수", context["stores"], "개", "2025-09",
                              "hub:793 상가(상권) 정보"])
    if context.get("sales_per_store"):
        evidence_rows.append(["점포당 카드 이용금액", context["sales_per_store"], "원/년", "2020",
                              "hub:748 ÷ hub:793"])
    if bus:
        evidence_rows.append(["정류소 수", bus["stations"], "곳", bus["month"], "hub:613"])
        evidence_rows.append(["정류소 일평균 승차 인원", bus["board_total"], "명/일", bus["month"],
                              "hub:613"])
    if center_menu:
        evidence_rows.append(["메뉴 객단가 중앙값", _fmt_num(_median_price(center_menu)), "원",
                              hub.center_period(hub.center_table("menu_emd")), "center:1-24"])

    source_kind = "center" if any(d.startswith("center:") for d in datasets) else "hub"
    table_artifact = {"id": "evidence", "type": "table",
                      "title": "%s %s 진단 근거" % (scope["name"], industry_record["name"]),
                      "columns": evidence_columns, "rows": evidence_rows,
                      "source": ", ".join(datasets), "source_kind": source_kind}
    datasets_with_center = [d for d in datasets if d.startswith("center:")]
    table_artifact, export_result = export_rules.apply_to_table(
        table_artifact, source_kind, sources=[_source_note(hub, d) for d in datasets],
        purpose="지역·업종 상권 진단 근거 표시")
    artifacts.append(table_artifact)
    hidden = {evidence_rows[c["row"]][0] for c in (export_result or {}).get("suppressed") or []
              if c["col"] == 1 and c["row"] < len(evidence_rows)}
    for label in hidden:
        if label in line_refs:
            lines, index, render = line_refs[label]
            lines[index] = render(hidden)
    if export_result:
        evidence_rows = table_artifact["rows"]
    if "동종 점포 수" in hidden:
        neighbors = [dict(r, stores=None) if r["name"] == scope["name"] else r
                     for r in neighbors]

    if context.get("floating"):
        artifacts.append({"id": "demand_floating", "type": "chart",
                          "title": "%s 유동인구 지수(허브 집계값) 월별 추이" % scope["name"],
                          **make_chart_spec("line", context["floating"],
                                            "%s 유동인구 지수(허브 집계값) 월별 추이" % scope["name"],
                                            unit="허브 집계값", source="hub:583")})
    if neighbors:
        artifacts.append({"id": "competition_bar", "type": "chart",
                          "title": "인접 읍면동 %s 점포 수 비교" % industry_record["name"],
                          **make_chart_spec("bar", [{"name": r["name"], "value": r["stores"]}
                                                    for r in neighbors],
                                            "인접 읍면동 %s 점포 수 비교" % industry_record["name"],
                                            unit="개", source="hub:793")})
    if time_series:
        time_source = _time_source_label(datasets)
        time_title = ("%s 시간대 이용 비중" % scope["name"] if time_source.startswith("center")
                      else "제주 전체 시간대 카드 이용 비중")
        artifacts.append({"id": "time_bar", "type": "chart",
                          "title": time_title,
                          **make_chart_spec("bar", time_series, time_title,
                                            unit="%", source=_time_source_label(datasets))})
    if price_series:
        artifacts.append({"id": "price_donut", "type": "chart",
                          "title": "%s 가격대·메뉴 구성" % industry_record["name"],
                          **make_chart_spec("donut", price_series,
                                            "%s 가격대·메뉴 구성" % industry_record["name"],
                                            unit="%", source=_price_source_label(datasets))})

    kpi_rows = []
    if context.get("floating_avg"):
        kpi_rows.append({"label": "유동인구 지수(허브 집계값)", "value": _fmt_num(context["floating_avg"]),
                         "unit": "허브 집계값", "period": context["floating_period"], "source": "hub:583"})
    if context.get("stores") is not None:
        kpi_rows.append({"label": "동종 점포 수", "value": _fmt_num(context["stores"]),
                         "unit": "개", "period": "2025-09", "source": "hub:793"})
    for row in kpi_rows:
        row["source_kind"] = "hub"
        if row["label"] in hidden:
            row["value"] = "가림"
    if kpi_rows:
        artifacts.insert(0, {"id": "kpi", "type": "kpi", "title": "핵심 지표", "items": kpi_rows})

    if judgments:
        artifacts.insert(1 if kpi_rows else 0, {
            "id": "standing", "type": "standing", "title": "제주 읍면동 안 상대 위치",
            "items": judgments, "source_kind": "hub"})
    all_float = hub.all_emd_floating()
    if all_float:
        artifacts.append({"id": "map_floating", "type": "chart",
                          "title": "읍면동별 유동인구 지수(허브 집계값)",
                          "highlight": list(scope.get("admin_dongs") or [scope["name"]]),
                          **make_chart_spec("map-choropleth",
                                            [{"name": e, "value": round(v, 1)}
                                             for e, v in sorted(all_float.items())],
                                            "읍면동별 유동인구 지수(허브 집계값)",
                                            unit="허브 집계값", source="hub:583")})

    summary_parts = ["%s %s 상권 진단" % (scope["name"], industry_record["name"])]
    if demand_lines:
        summary_parts.append("[수요] " + " ".join(demand_lines))
    if competition_lines:
        summary_parts.append("[경쟁] " + " ".join(competition_lines))
    if time_lines:
        summary_parts.append("[시간·요일] " + " ".join(time_lines))
    if price_lines:
        summary_parts.append("[가격대] " + " ".join(price_lines))
    if access_lines:
        summary_parts.append("[접근성] " + " ".join(access_lines))
    if judgments:
        summary_parts.append("[종합] " + ", ".join(
            "%s %s(%s)" % (j["metric"], j["position"], j["tone"])
            for j in judgments))
    datasets = list(dict.fromkeys(datasets))
    summary_parts.append("사용 데이터: " + ", ".join(datasets))

    data = {
        "scope": scope, "industry": industry_record["name"], "datasets": datasets,
        "demand": demand_lines, "competition": competition_lines, "time": time_lines,
        "price": price_lines, "access": access_lines,
        "judgments": judgments, "neighbors": neighbors,
        "export_check": export_result,
        "evidence_columns": evidence_columns, "evidence_rows": evidence_rows,
        "period": context.get("floating_period"),
    }
    return _result("\n".join(summary_parts), artifacts=artifacts, datasets=datasets, data=data)


def _data_period(hub, dataset_id):
    item = hub.catalog.get(dataset_id) or {}
    return item.get("period") or "기간 미상"


def _median_price(rows):
    prices = sorted(p for p in (to_number(r.get("unit_price")) for r in rows) if p)
    if not prices:
        return None
    return prices[len(prices) // 2]


def _time_source_label(datasets):
    for dataset_id in (CENTER_IDS["card_time"], CARD_TIME_ID):
        if dataset_id in datasets:
            return dataset_id
    return "hub:502"


def _price_source_label(datasets):
    for dataset_id in (CENTER_IDS["menu_emd"], MENU_ID):
        if dataset_id in datasets:
            return dataset_id
    return "hub:495"


def _neighbor_rows(hub, scope, industry):
    """같은 시군구에서 점포 수가 가까운 4개 읍면동(자기 포함) 비교."""
    all_counts = hub.all_emd_store_count(industry)
    if not all_counts:
        return []
    own_count = hub.store_count(scope, industry)
    city = scope.get("city") or CITY_BY_EMD.get(scope["name"], "")
    pool = []
    for record in _admin_records():
        if city and record["city"] != city:
            continue
        count = all_counts.get(record["name"])
        if count is None:
            continue
        pool.append({"name": record["name"], "stores": count,
                     "distance": abs(count - (own_count or 0))})
    pool.sort(key=lambda r: r["distance"])
    chosen = pool[:4]
    if not any(r["name"] == scope["name"] for r in chosen):
        chosen = [{"name": scope["name"], "stores": own_count, "distance": 0}] + chosen[:3]
    for row in chosen:
        row.pop("distance", None)
    chosen.sort(key=lambda r: -r["stores"])
    return chosen


def _admin_records():
    from .dictionaries import REGION_LIST
    return [r for r in REGION_LIST if r["level"] == "행정동"]


# ---------------------------------------------------------------------------
# 도구: 비교·추세
# ---------------------------------------------------------------------------

def compare_areas_tool(hub, regions, industry):
    names = regions if isinstance(regions, (list, tuple)) else [regions]
    if not 2 <= len(names) <= 5:
        return _result("비교는 2~5개 지역으로 해 주세요.", data={"error": "region_count"})
    industry_record = _industry_record(industry)
    if industry_record is None:
        found, cands = resolve_industry(industry or "")
        industry_record = _industry_record(found) if found else None
    if industry_record is None:
        return _result("업종 '%s'을(를) 확정하지 못했습니다." % industry,
                       data={"clarification": {"question": "어떤 업종인가요?", "candidates": []}})
    rows = []
    datasets = [FLOATING_ID, STORES_ID, MASHUP_ID, BUS_ID]
    for name in names:
        scope, cands = _region_scope(name)
        if scope is None:
            continue
        context = _industry_context(hub, scope, industry_record)
        pcts = _percentile_table(hub, scope, industry_record, context)
        bus = hub.bus_summary(scope)
        rows.append({
            "name": scope["name"],
            "floating": context.get("floating_avg"),
            "stores": context.get("stores"),
            "sales_per_store": context.get("sales_per_store"),
            "bus": (bus or {}).get("board_total"),
            "floating_pct": (pcts.get("floating") or {}).get("pct"),
            "floating_rank": (pcts.get("floating") or {}).get("rank"),
            "floating_total": (pcts.get("floating") or {}).get("count"),
        })
    if not rows:
        return _result("비교할 지역을 확정하지 못했습니다.", data={"error": "region"})
    columns = ["지역", "유동인구 지수(허브 집계값)", "동종 점포 수(개)",
               "점포당 카드이용금액(원)", "정류소 일평균 승차(명)", "유동인구 상위 비율(%)",
               "유동인구 출처·기간", "점포 출처·기간", "매출 출처·기간", "버스 출처·기간"]
    floating_label = "%s(%s)" % (FLOATING_ID, _data_period(hub, FLOATING_ID))
    store_label = "%s(%s)" % (STORES_ID, _data_period(hub, STORES_ID))
    sales_label = "%s(%s)" % (MASHUP_ID, _data_period(hub, MASHUP_ID))
    bus_label = "%s(%s)" % (BUS_ID, _data_period(hub, BUS_ID))
    table_rows = [[r["name"], _fmt_num(r["floating"]), _fmt_num(r["stores"]),
                   _fmt_num(r["sales_per_store"]), _fmt_num(r["bus"]),
                   _fmt_num(r["floating_pct"], 0),
                   floating_label, store_label, sales_label, bus_label] for r in rows]
    summary = "%s 비교(%s)" % (" vs ".join(r["name"] for r in rows), industry_record["name"])
    summary += ("\n출처·기간: 유동인구 %s, 점포 %s, 점포당 이용금액 %s, 버스 %s." % (
        floating_label, store_label, sales_label, bus_label))
    summary += ("\n유동인구는 허브 집계값(단위 미공개)이라 인원이 아니며 상대 비교만 합니다.")
    for r in rows:
        rank_text = ("%s곳 중 %s위(상위 %s%%)" % (
            _fmt_num(r["floating_total"]), _fmt_num(r["floating_rank"]),
            _fmt_num(r["floating_pct"], 0))) if r.get("floating_rank") else \
            ("상위 %s%%" % _fmt_num(r["floating_pct"], 0))
        summary += ("\n- %s: 유동인구 지수(허브 집계값) %s — %s, 점포 %s개, "
                    "점포당 연간 카드이용금액 %s원, 정류소 일평균 승차 %s명" % (
                        r["name"], _fmt_num(r["floating"]), rank_text,
                        _fmt_num(r["stores"]), _fmt_num(r["sales_per_store"]),
                        _fmt_num(r["bus"])))
    artifacts = [{"id": "compare_table", "type": "table",
                  "title": "%s 지역 비교" % industry_record["name"],
                  "columns": columns, "rows": table_rows, "source": ", ".join(datasets)}]
    chart_data = [{"name": r["name"], "value": r["stores"]} for r in rows]
    artifacts.append({"id": "compare_chart", "type": "chart",
                      "title": "지역별 동종 점포 수",
                      **make_chart_spec("bar", chart_data, "지역별 동종 점포 수",
                                        unit="개", source="hub:793")})
    artifacts.append({"id": "compare_metrics", "type": "compare",
                      "title": "%s 지역 비교" % industry_record["name"],
                      "regions": [r["name"] for r in rows],
                      "metrics": [
                          {"key": "floating", "label": "유동인구 지수", "unit": "허브 집계값",
                           "source": FLOATING_ID, "values": [r["floating"] for r in rows]},
                          {"key": "stores", "label": "동종 점포 수", "unit": "개",
                           "source": STORES_ID, "values": [r["stores"] for r in rows]},
                          {"key": "sales_per_store", "label": "점포당 연간 카드 이용금액", "unit": "원",
                           "source": MASHUP_ID, "values": [r["sales_per_store"] for r in rows]},
                          {"key": "bus", "label": "정류소 하루 승차 인원", "unit": "명",
                           "source": BUS_ID, "values": [r["bus"] for r in rows]},
                      ]})
    all_float = hub.all_emd_floating()
    if all_float:
        artifacts.append({"id": "map_floating", "type": "chart",
                          "title": "읍면동별 유동인구 지수(허브 집계값)",
                          "highlight": [r["name"] for r in rows],
                          **make_chart_spec("map-choropleth",
                                            [{"name": e, "value": round(v, 1)}
                                             for e, v in sorted(all_float.items())],
                                            "읍면동별 유동인구 지수(허브 집계값)",
                                            unit="허브 집계값", source=FLOATING_ID)})
    return _result(summary, artifacts=artifacts, datasets=datasets,
                   data={"rows": rows, "industry": industry_record["name"]})


def trend_tool(hub, dataset_id, metric, filters=None, by="month"):
    table = hub.table(dataset_id)
    if not table:
        return _result("'%s' 데이터를 찾을 수 없습니다." % dataset_id, datasets=[])
    rows = apply_filters(table["rows"], filters)
    month_col = "base_month" if "base_month" in table["header"] else "base_year_month"
    if month_col not in table["header"]:
        month_col = "base_date"
    aggregated = query_table(rows, group_by=[month_col],
                             metrics=[{"col": metric, "op": "sum"}],
                             sort=[month_col])
    alias = "sum_%s" % metric
    series = [{"name": _month_label(row.get(month_col)), "value": row.get(alias)}
              for row in aggregated["rows"]]
    change = _change_ratio([{"month": p["name"], "value": p["value"]} for p in series
                            if p["value"] is not None])
    summary = "%s의 %s 추이: %d개 구간, 변화 %s." % (
        dataset_id, metric, len(series),
        ("%+.1f%%" % change) if change is not None else "계산 불가")
    artifact = {"id": "trend_chart", "type": "chart", "title": "%s 추이" % metric,
                **make_chart_spec("line", series, "%s 추이" % metric, source=dataset_id)}
    return _result(summary, artifacts=[artifact], datasets=[dataset_id],
                   data={"series": series, "change_pct": change})


# ---------------------------------------------------------------------------
# 도구: 반출 점검·보고서
# ---------------------------------------------------------------------------

def export_check_tool(hub, table):
    result = export_rules.check_export(table, sources=[], purpose=None)
    summary = "반출 점검: %d칸 가림(%s), 합계 %s." % (
        len(result["suppressed"]),
        ", ".join(sorted({c["reason"] for c in result["suppressed"]})) or "없음",
        "비공개" if result["total_hidden"] else "공개")
    artifact = {"id": "export_safe", "type": "table", "title": "반출 점검 후 표",
                "columns": result["safe_table"]["columns"],
                "rows": result["safe_table"]["rows"],
                "source": "반출 사전 점검"}
    return _result(summary, artifacts=[artifact], datasets=[], data=result)


def build_report_tool(hub, session):
    """session: {question, region, industry, diagnosis, steps, artifacts, sources}."""
    session = session or {}
    diagnosis = session.get("diagnosis") or {}
    question = session.get("question") or ""
    scope_name = (diagnosis.get("scope") or {}).get("name") or session.get("region") or ""
    industry_name = diagnosis.get("industry") or session.get("industry") or ""
    title = "%s %s 상권 진단 보고서" % (scope_name or "제주", industry_name or "업종")
    datasets = diagnosis.get("datasets") or session.get("datasets") or []
    sources = [_source_note(hub, d) for d in datasets]
    if not sources:
        sources = [_source_note(hub, FLOATING_ID)]

    sections = []
    artifacts = session.get("artifacts") or []
    artifact_index = {a.get("id"): a for a in artifacts}
    by_kind = {}
    for artifact in artifacts:
        by_kind.setdefault(artifact.get("type"), []).append(artifact.get("id"))

    if diagnosis.get("demand"):
        sections.append({"heading": "1. 수요", "markdown": "\n\n".join(diagnosis["demand"]),
                         "artifacts": [i for i in by_kind.get("chart", [])
                                       if i in ("demand_floating",)]})
    if diagnosis.get("competition"):
        sections.append({"heading": "2. 경쟁", "markdown": "\n\n".join(diagnosis["competition"]),
                         "artifacts": ["competition_bar"] if artifact_index.get("competition_bar")
                         else []})
    if diagnosis.get("time"):
        sections.append({"heading": "3. 시간·요일 패턴", "markdown": "\n\n".join(diagnosis["time"]),
                         "artifacts": ["time_bar"] if artifact_index.get("time_bar") else []})
    if diagnosis.get("price"):
        sections.append({"heading": "4. 가격대", "markdown": "\n\n".join(diagnosis["price"]),
                         "artifacts": ["price_donut"] if artifact_index.get("price_donut") else []})
    if diagnosis.get("access"):
        sections.append({"heading": "5. 접근성", "markdown": "\n\n".join(diagnosis["access"]),
                         "artifacts": []})
    judgments = diagnosis.get("judgments") or []
    if judgments:
        lines = ["아래 분위는 제주 43개 읍면동 안에서의 상대 위치입니다. 단정하지 않고 "
                 "강점·주의로만 표시합니다.", ""]
        for item in judgments:
            lines.append("- %s: %s — %s" % (item["metric"], item["position"], item["tone"]))
        sections.append({"heading": "6. 종합 판단", "markdown": "\n".join(lines),
                         "artifacts": ["evidence"] if artifact_index.get("evidence") else []})

    export_result = diagnosis.get("export_check")
    export_section = None
    if export_result:
        export_section = {
            "threshold": export_result.get("threshold"),
            "suppressed": export_result.get("suppressed"),
            "total_hidden": export_result.get("total_hidden"),
            "notice": export_result.get("notice"),
            "application_text": export_result.get("application_text"),
        }
    limitations = list(LIMITATION_BASE)
    if session.get("notice"):
        limitations.append(session["notice"])
    if diagnosis.get("time") and any("우리 지역 구분 없음" in line for line in diagnosis["time"]):
        limitations.append("시간대·요일 지표 일부는 제주 전체 값이라 우리 지역만의 패턴이 아닙니다.")
    if diagnosis.get("price") and any("객단가 아님" in line for line in diagnosis["price"]):
        limitations.append("메뉴 지표는 매출 비중이며 메뉴별 단가가 아닙니다.")

    report = {
        "id": "r%d" % int(dt.datetime.now().timestamp() * 1000),
        "title": title,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "question": question,
        "sections": sections,
        "sources": sources,
        "limitations": limitations,
        "export_check": export_section,
        "artifacts": artifacts,
    }
    summary = "보고서 '%s'을(를) 만들었습니다(절 %d개, 출처 %d개)." % (
        title, len(sections), len(sources))
    return _result(summary, artifacts=[], datasets=datasets, data={"report": report})


# ---------------------------------------------------------------------------
# LLM tool 스키마(정의만; 호출은 3단계 LLM 루프가 한다)
# ---------------------------------------------------------------------------

def _schema(name, description, properties, required=None):
    return {"type": "function",
            "function": {"name": name, "description": description,
                         "parameters": {"type": "object", "properties": properties,
                                        "required": required or []}}}


_FILTERS = {"type": "array", "description": "필터 목록(AND). 예: [{\"col\":\"emd\",\"eq\":\"노형동\"}]",
            "items": {"type": "object", "properties": {
                "col": {"type": "string"}, "eq": {"type": "string"},
                "ne": {"type": "string"}, "in": {"type": "array", "items": {"type": "string"}},
                "contains": {"type": "string"}, "gte": {"type": "number"},
                "lte": {"type": "number"}}}}
_METRICS = {"type": "array", "description": "집계 지표 목록",
            "items": {"type": "object", "properties": {
                "col": {"type": "string"},
                "op": {"type": "string", "enum": ["sum", "mean", "count", "median", "share"]},
                "as": {"type": "string"}}}}

TOOL_SCHEMAS = [
    _schema("resolve_region", "사용자가 말한 지역 표현을 제주 읍면동으로 확정한다. "
            "모호하면 후보 목록을 돌려준다. text에는 '노형동'처럼 읍면동 이름(또는 그 별칭)만 "
            "넣고, 문장 전체나 '제주시 노형동' 같은 시군구 조합은 넣지 않는다.",
            {"text": {"type": "string", "description": "읍면동 이름 또는 별칭(예: 노형동, 노형, 애월읍)"}},
            ["text"]),
    _schema("resolve_industry", "사용자가 말한 업종 표현을 사전 업종으로 확정한다. "
            "모호하면 후보 목록을 돌려준다. text에는 '카페', '한식'처럼 업종 말만 넣는다.",
            {"text": {"type": "string", "description": "업종 표현(예: 카페, 커피, 한식, 편의점)"}},
            ["text"]),
    _schema("list_datasets", "분석에 쓸 수 있는 데이터 카탈로그(메타: ID·이름·기간·행수)를 조회한다. "
            "센터 업로드 데이터가 있는지 확인할 때 쓴다. 실제 수치 분석은 diagnose_area·"
            "query_table로 하고, 이 도구의 결과로 수치를 답하지 않는다.",
            {"topic": {"type": "string", "description": "이름/ID 일부로 거르는 말(선택, 없으면 전체)"}}),
    _schema("profile_dataset", "데이터 한 개의 컬럼·기간·결측·행수를 확인한다.",
            {"dataset_id": {"type": "string"}}, ["dataset_id"]),
    _schema("query_table", "데이터를 필터·그룹·집계해 표를 만든다. 임의 코드 실행은 없다.",
            {"dataset_id": {"type": "string"}, "filters": _FILTERS,
             "group_by": {"description": "그룹 기준 컬럼(문자열 또는 배열)",
                          "anyOf": [{"type": "string"},
                                    {"type": "array", "items": {"type": "string"}}]},
             "metrics": _METRICS, "sort": {"type": "array", "items": {"type": "object"}},
             "limit": {"type": "integer"}}, ["dataset_id"]),
    _schema("diagnose_area", "지역·업종 상권을 종합 진단한다(수요·경쟁·시간·가격·접근성·종합). "
            "센터 업로드 데이터가 있으면 우선 쓰고 없으면 허브 공개 데이터를 쓴다. "
            "시간대·요일 패턴이나 '언제 손님이 많은가' 질문도 이 도구 하나로 답한다"
            "(별도 시간대 도구는 없다). region에는 확정된 읍면동 이름만 넣는다.",
            {"region": {"type": "string", "description": "확정된 읍면동 이름(예: 대정읍)"},
             "industry": {"type": "string", "description": "업종 이름(예: 편의점)"},
             "period": {"type": "string", "description": "기간(선택, 예: 2025)"}},
            ["region", "industry"]),
    _schema("compare_areas", "2~5개 읍면동을 같은 업종으로 비교한다.",
            {"regions": {"type": "array", "items": {"type": "string"}},
             "industry": {"type": "string"}}, ["regions", "industry"]),
    _schema("trend", "데이터 한 개의 지표를 월별로 집계해 추이를 본다.",
            {"dataset_id": {"type": "string"}, "metric": {"type": "string"},
             "filters": _FILTERS, "by": {"type": "string", "enum": ["month"]}},
            ["dataset_id", "metric"]),
    _schema("make_chart", "차트 스펙(JSON)을 만든다. 그리는 것은 화면(UI) 몫이다.",
            {"kind": {"type": "string", "enum": list(CHART_KINDS)},
             "data": {"type": "array", "items": {"type": "object"}},
             "title": {"type": "string"}, "unit": {"type": "string"},
             "source": {"type": "string"}}, ["kind", "data", "title"]),
    _schema("export_check", "표를 반출 사전 점검한다(10 미만 가림·역산 방지·비율 분자).",
            {"table": {"type": "object", "description": "{columns, rows, total_row?}"}},
            ["table"]),
    _schema("build_report", "대화 세션의 진단 결과로 보고서 JSON을 만든다.",
            {"session": {"type": "object"}}, ["session"]),
]

TOOL_FUNCTIONS = {
    "resolve_region": resolve_region_tool,
    "resolve_industry": resolve_industry_tool,
    "list_datasets": list_datasets_tool,
    "profile_dataset": profile_dataset_tool,
    "query_table": query_table_tool,
    "diagnose_area": diagnose_area_tool,
    "compare_areas": compare_areas_tool,
    "trend": trend_tool,
    "make_chart": make_chart_tool,
    "export_check": export_check_tool,
    "build_report": build_report_tool,
}


def call_tool(hub, name, args):
    """이름·인자 dict로 도구를 실행한다(LLM·규칙 모드 공용 진입점)."""
    func = TOOL_FUNCTIONS.get(name)
    if not func:
        return _result("없는 도구입니다: %s" % name, data={"error": "unknown_tool"})
    try:
        return func(hub, **(args or {}))
    except TypeError as exc:
        return _result("도구 인자가 올바르지 않습니다: %s" % exc, data={"error": "bad_args"})
    except Exception as exc:  # 도구 하나의 실패가 대화 전체를 막지 않게 한다.
        return _result("도구 실행 중 오류: %s" % type(exc).__name__,
                       data={"error": type(exc).__name__})
