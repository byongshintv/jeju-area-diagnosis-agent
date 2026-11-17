"""허브 공개 데이터 스냅샷 수집기.

제주데이터허브(https://jejudatahub.net)의 **공개** 데이터를 받아 `sample_data/hub/`에
CSV + 메타 JSON으로 저장한다. 키가 있으면 Open API(`HUB_BASE_URL`)를 먼저 시도하고,
없거나 실패하면 공개 파일 다운로드로 대체한다.

키는 `HUB_API_KEY` 환경변수, 앱 폴더 `.env`의 `HUB_API_KEY`, `--key-file <JSON 경로>` 순서로 읽고,
화면·로그·메타·CSV에 절대 쓰지 않는다.

실행:
    python3 tools/fetch_hub_snapshots.py            # 받을 수 있는 것만 받고 요약 출력
    python3 tools/fetch_hub_snapshots.py --dry-run  # 목록만 확인
"""
import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = APP_ROOT / "sample_data" / "hub"

HUB_WEB = "https://jejudatahub.net"
HUB_OPENAPI = os.environ.get("HUB_BASE_URL") or "https://open.jejudatahub.net/api/proxy/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) jeju-agent-snapshot"
LICENSE = "제주데이터허브 공개 데이터(공공데이터법·시행령에 따라 이용, 출처 표시. 제출물 포함은 대회 게시판 #8·#20 답변으로 확인)"

# Open API 데이터별 고정 문자열(검수/사이트작업기록.md 6절).
API_KEYS = {
    583: "Daaa1t3at3tt8a8DD3t55538t35Dab1t",
    613: "1b1ta1a1ba6t6a1ttt3bD6b1tb1t1D3t",
    612: "DD11ab6a6t11D16baaa1a2tD26ata161",
}
API_QUOTA_PER_DAY = 1000
CALL_BUDGET = 295          # 이번 감리의 전체 300회 한도 안에서 인증 호출 상한
PAGE_SIZE = 100            # 서버가 limit=100까지만 받는다(150 이상은 500 오류)
FLOATING_MONTHS = 12
BUS_MONTHS = 3
POP_SAMPLE_DAYS = (1, 2, 3)   # 각 달 1~3일 표본(부족하면 1일만)
BUS_SAMPLE_DAYS = (1,)

# 요청 데이터와 실제로 받을 수 있었던 대체 데이터.
# `hub_id`는 실제 내려받는 데이터, `requested_id`는 TASK.md가 지정한 데이터 번호.
TARGETS = [
    {
        "slug": "card_industry_sex",
        "hub_id": 1048, "requested_id": 311,
        "name": "업종별 성별 카드 이용 정보",
        "requested_name": "업종별 성별 카드 이용 정보",
        "period_note": "최근 12개월만 저장",
        "month_col": "base_year_month",
    },
    {
        "slug": "stores",
        "hub_id": 793, "requested_id": 558,
        "name": "제주특별자치도 상가(상권) 정보",
        "requested_name": "상가(상권) 정보",
        "period_note": "2025-09-18 기준 스냅샷(전체 57,865개 점포)",
    },
    {
        "slug": "card_time",
        "hub_id": 502, "requested_id": 817,
        "name": "시간대별 카드 이용금액 현황",
        "requested_name": "시간대별 카드 이용금액 현황",
        "period_note": "최근 12개월만 저장",
        "month_col": "base_year_month",
    },
    {
        "slug": "card_weekday",
        "hub_id": 504, "requested_id": 815,
        "name": "요일별 카드 이용 건수 비교",
        "requested_name": "요일별 카드 이용 건수 비교",
        "period_note": "최근 12개월만 저장",
        "month_col": "base_year_month",
    },
    {
        "slug": "menu_sales",
        "hub_id": 495, "requested_id": 824,
        "name": "메뉴별 외식업 매출액",
        "requested_name": "메뉴별 외식업 매출액",
        "period_note": "2019~2024 전체",
    },
    {
        "slug": "bus_hourly",
        "hub_id": 512, "requested_id": 725,
        "name": "일별 시간대별 버스 승차 인원 수 현황",
        "requested_name": "일일 정류소별 버스 이용자 정보",
        "period_note": "최근 12개월만 저장(정류소 단위 아님)",
        "month_col": "base_date",
    },
    {
        "slug": "foreign_visitors_emd",
        "hub_id": 527, "requested_id": 753,
        "name": "제주도 단기체류 방문 외국인 유동인구 추이",
        "requested_name": "읍면동 단위 내국인 유동인구",
        "period_note": "최근 12개월만 저장(외국인 단기체류, 내국인 아님)",
        "month_col": "base_date",
    },
    {
        "slug": "emd_area_mashup",
        "hub_id": 748, "requested_id": None,
        "name": "상권분석 매쉬업(읍면동×업종 이용자·거주·근무·방문 인구)",
        "requested_name": "",
        "period_note": "2020년 전체(제공 범위 전체)",
    },
]

# Open API(키 필요) 데이터. `collector`가 별도 수집 함수를 가리킨다.
API_TARGETS = [
    {
        "slug": "floating_pop_emd",
        "hub_id": 583, "requested_id": 753,
        "name": "유동인구 지수(허브 집계값)",
        "requested_name": "읍면동 단위 내국인 유동인구",
        "collector": "floating",
    },
    {
        "slug": "bus_station",
        "hub_id": 613, "requested_id": 725,
        "name": "정류소별 버스 승차 인원(월 일평균) + 정류소 기본 정보(612)",
        "requested_name": "일일 정류소별 버스 이용자 정보",
        "collector": "bus",
    },
]

_CALLS = {"count": 0}


def api_call(data_key, key, params, timeout=60):
    """Open API 1회 호출. 호출 수를 세어 예산을 넘으면 예외로 멈춘다."""
    if _CALLS["count"] >= CALL_BUDGET:
        raise RuntimeError("api_call_budget_exhausted")
    base = os.environ.get("HUB_BASE_URL") or HUB_OPENAPI
    if not base.endswith("/"):
        base += "/"
    url = "%s%s/%s?%s" % (base, data_key, key, urllib.parse.urlencode(params))
    last = None
    for attempt in range(4):
        if _CALLS["count"] >= CALL_BUDGET:
            raise RuntimeError("api_call_budget_exhausted")
        _CALLS["count"] += 1
        try:
            raw = http_get(url, timeout=timeout)
            payload = json.loads(raw.decode("utf-8"))
            return payload
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("api_call_failed: %s" % type(last).__name__)


def api_page_all(data_key, key, params):
    """`limit`·`number` 페이지를 돌며 data 전체를 모은다. 중복은 버린다."""
    out = {}
    number = 1
    total = None
    while True:
        page = api_call(data_key, key, dict(params, limit=PAGE_SIZE, number=number))
        rows = page.get("data") or []
        if total is None:
            total = page.get("totCnt")
        for row in rows:
            out[json.dumps(row, ensure_ascii=False, sort_keys=True)] = row
        if not rows or not page.get("hasMore") or (total is not None and len(out) >= total):
            break
        if number > 200:
            break
        number += 1
    return list(out.values()), total


def month_back(day, months):
    """`day`(YYYYMMDD)에서 `months`개월 전 같은 날 1일자를 만든다."""
    year, month = int(day[:4]), int(day[4:6])
    index = year * 12 + (month - 1) - months
    sy, sm = divmod(index, 12)
    return "%04d%02d01" % (sy, sm + 1)


def month_of(day):
    return str(day)[:6]


def month_label(month):
    return "%s-%s" % (month[:4], month[4:6])


def find_latest_data_month(data_key, key, extra=None, max_lookback=30):
    """오늘부터 월 단위로 거슬러 1일자 1회씩 조회해 값이 있는 최근 달을 찾는다."""
    today = dt.date.today()
    year, month = today.year, today.month
    tried = []
    for _ in range(max_lookback):
        probe = "%04d%02d01" % (year, month)
        params = {"startDate": probe, "endDate": probe, "limit": 1}
        params.update(extra or {})
        page = api_call(data_key, key, params)
        count = page.get("totCnt") or 0
        tried.append({"date": probe, "totCnt": count})
        if count:
            return probe, tried
        index = year * 12 + (month - 1) - 1
        year, month = divmod(index, 12)
        month += 1
    return None, tried


def collect_floating_pop(key):
    """583: 읍면동×성별×연령대×월 일평균 유동인구."""
    data_key = API_KEYS[583]
    latest, probes = find_latest_data_month(data_key, key)
    if not latest:
        return {"ok": False, "slug": "floating_pop_emd", "hub_id": 583,
                "reason": "값이 있는 날짜를 찾지 못했습니다."}
    months = [month_back(latest, i) for i in range(FLOATING_MONTHS)]
    months = [m for m in months if m <= latest][::-1]
    days_per_month = max(1, min(len(POP_SAMPLE_DAYS), CALL_BUDGET // (7 * len(months))))
    samples = {}
    actual_dates = {}
    for month_day in months:
        month = month_of(month_day)
        dates = []
        for offset in range(days_per_month):
            day = "%s%02d" % (month, POP_SAMPLE_DAYS[offset] if offset < len(POP_SAMPLE_DAYS)
                              else offset + 1)
            try:
                rows, total = api_page_all(data_key, key,
                                           {"startDate": day, "endDate": day})
            except RuntimeError:
                break
            if not rows:
                continue
            dates.append(day)
            for row in rows:
                key_tuple = (month, row.get("emd"), row.get("gender"), row.get("ageGroup"))
                bucket = samples.setdefault(key_tuple, {"days": set(), "resd": 0.0,
                                                        "work": 0.0, "visit": 0.0})
                # 583은 날짜×읍면동×성별×연령대당 1행: 중복 날짜만 제외한다.
                if day in bucket["days"]:
                    continue
                bucket["days"].add(day)
                bucket["resd"] += _num(row.get("resdPop"))
                bucket["work"] += _num(row.get("workPop"))
                bucket["visit"] += _num(row.get("visitPop"))
        if dates:
            actual_dates[month] = dates
    if not samples:
        return {"ok": False, "slug": "floating_pop_emd", "hub_id": 583,
                "reason": "유동인구 응답이 비어 있습니다.",
                "probes": probes[-3:]}
    rows = []
    for (month, emd, gender, age_group), bucket in sorted(samples.items()):
        n = max(1, len(bucket["days"]))
        rows.append({
            "base_month": month,
            "emd": emd or "",
            "gender": gender or "",
            "age_group": age_group or "",
            "resd_pop": round(bucket["resd"] / n, 2),
            "work_pop": round(bucket["work"] / n, 2),
            "visit_pop": round(bucket["visit"] / n, 2),
            "sample_days": n,
        })
    header = ["base_month", "emd", "gender", "age_group", "resd_pop", "work_pop",
              "visit_pop", "sample_days"]
    path = OUT_DIR / "floating_pop_emd.csv"
    write_csv(path, header, rows)
    period = "%s ~ %s" % (month_label(months[0]), month_label(months[-1]))
    meta = {
        "id": "hub:583",
        "hub_dataset_id": 583,
        "requested_id": 753,
        "requested_name": "읍면동 단위 내국인 유동인구",
        "name": "유동인구 지수(허브 집계값)",
        "hub_title": "읍면동 단위 내국인 유동인구",
        "provider": "제주데이터허브",
        "source_kind": "hub",
        "period": period,
        "period_note": "최근 값이 있는 달 기준 12개월, 각 달 표본일의 일평균",
        "license": LICENSE,
        "url": "%s/api/data/view?id=583" % HUB_WEB,
        "file": path.name,
        "rows": len(rows),
        "bytes": path.stat().st_size,
        "columns": header,
        "unit": "허브 집계값",
        "unit_note": '단위 미공개, 상대 비교만 사용. 허브 583 상세 메타와 첨부 API 가이드(인구읍면동단위내국인유동인구API.hwp, file id 1096)를 확인했으나 집계 단위 설명이 없음.',
        "source": "openapi:583",
        "api_params": {"startDate": "YYYYMMDD", "endDate": "YYYYMMDD",
                       "limit": PAGE_SIZE, "number": "페이지(1부터)"},
        "sample_dates": {month_label(k): v for k, v in sorted(actual_dates.items())},
        "probe_dates": probes[:5],
        "api_calls": _CALLS["count"],
        "collected_at": dt.datetime.now().isoformat(timespec="seconds"),
        "available": True,
        "note": "제주데이터허브 Open API 스냅샷. 값은 표본일 일평균이며 원본 갱신 주기를 따르지 않는다.",
    }
    write_meta(path, meta)
    return {"ok": True, "slug": "floating_pop_emd", "hub_id": 583, "rows": len(rows),
            "bytes": meta["bytes"], "period": period,
            "unit": "허브 집계값",
        "unit_note": '단위 미공개, 상대 비교만 사용. 허브 583 상세 메타와 첨부 API 가이드(인구읍면동단위내국인유동인구API.hwp, file id 1096)를 확인했으나 집계 단위 설명이 없음.',
        "source": "openapi:583", "sample_months": len(actual_dates)}


def collect_bus_station(key):
    """613 + 612: 정류소×월 일평균 승차 인원에 정류소 주소·좌표·읍면동을 붙인다."""
    data_key = API_KEYS[613]
    latest, probes = find_latest_data_month(data_key, key, extra={"moveType": "승차"})
    if not latest:
        return {"ok": False, "slug": "bus_station", "hub_id": 613,
                "reason": "값이 있는 날짜를 찾지 못했습니다."}
    months = [month_back(latest, i) for i in range(BUS_MONTHS)][::-1]
    aggregates = {}
    actual_dates = {}
    for month_day in months:
        month = month_of(month_day)
        chosen = None
        for offset in range(len(BUS_SAMPLE_DAYS)):
            day = "%s%02d" % (month, BUS_SAMPLE_DAYS[offset])
            try:
                rows, _ = api_page_all(data_key, key,
                                       {"startDate": day, "endDate": day, "moveType": "승차"})
            except RuntimeError:
                rows = []
            if rows:
                chosen = day
                break
        if not chosen:
            continue
        actual_dates[month] = chosen
        for row in rows:
            if row.get("moveType") != "승차":
                continue
            station = str(row.get("stationId") or "")
            bucket = aggregates.setdefault((month, station), {"days": set(), "count": 0.0,
                                                              "name": ""})
            bucket["name"] = row.get("stationName") or bucket["name"]
            # 613은 같은 날의 요금·결제 유형별 승차 행을 모두 합한다.
            bucket["days"].add(chosen)
            bucket["count"] += _num(row.get("userCount"))
    if not aggregates:
        return {"ok": False, "slug": "bus_station", "hub_id": 613,
                "reason": "버스 이용자 응답이 비어 있습니다."}

    stations, _ = api_page_all(API_KEYS[612], key, {})
    info_by_id = {}
    info_by_name = {}
    for row in stations:
        info_by_id[str(row.get("stationId"))] = row
        name = _norm_station(row.get("stationName"))
        if name:
            # 주소가 있는 행을 우선한다(612의 stationId 체계가 613과 달라 이름으로 잇는다).
            previous = info_by_name.get(name)
            if previous is None or (not (previous.get("stationAddress") or "").strip()
                                    and (row.get("stationAddress") or "").strip()):
                info_by_name[name] = row

    names = _region_name_list()
    rows = []
    unmatched = 0
    for (month, station), bucket in sorted(aggregates.items()):
        base = info_by_id.get(station) or info_by_name.get(_norm_station(bucket["name"])) or {}
        address = str(base.get("stationAddress") or "")
        emd, matched = _emd_in_address(address, names)
        if not matched:
            unmatched += 1
        rows.append({
            "base_month": month,
            "station_id": station,
            "station_name": base.get("stationName") or bucket["name"],
            "station_address": address,
            "emd": emd,
            "longitude": base.get("longitude") or "",
            "latitude": base.get("latitude") or "",
            "board_count": round(bucket["count"] / max(1, len(bucket["days"])), 2),
            "sample_date": actual_dates.get(month, ""),
        })
    header = ["base_month", "station_id", "station_name", "station_address", "emd",
              "longitude", "latitude", "board_count", "sample_date"]
    path = OUT_DIR / "bus_station.csv"
    write_csv(path, header, rows)
    period = "%s ~ %s" % (month_label(months[0]), month_label(months[-1]))
    meta = {
        "id": "hub:613",
        "hub_dataset_id": 613,
        "requested_id": 725,
        "requested_name": "일일 정류소별 버스 이용자 정보",
        "name": "정류소별 버스 승차 인원(월 일평균)",
        "hub_title": "일일 정류소별 버스 이용자 정보",
        "provider": "제주데이터허브",
        "source_kind": "hub",
        "period": period,
        "period_note": "최근 값이 있는 달 기준 3개월, 표본일 승차 인원(일평균)",
        "license": LICENSE,
        "url": "%s/api/data/view?id=613" % HUB_WEB,
        "file": path.name,
        "rows": len(rows),
        "bytes": path.stat().st_size,
        "columns": header,
        "source": "openapi:613+612",
        "api_params": {"startDate": "YYYYMMDD", "endDate": "YYYYMMDD",
                       "moveType": "승차", "limit": PAGE_SIZE, "number": "페이지(1부터)"},
        "aggregation_note": "표본일 정류소별 moveType=승차의 priceType×payType 모든 행을 합산, 환승 제외. 주소가 붙은 읍면동만 접근성 비교에 사용.",
        "station_info_rows": len(stations),
        "station_matched_rows": sum(1 for r in rows if r["station_address"]),
        "emd_unmatched_rows": unmatched,
        "sample_dates": {month_label(k): v for k, v in sorted(actual_dates.items())},
        "api_calls": _CALLS["count"],
        "collected_at": dt.datetime.now().isoformat(timespec="seconds"),
        "available": True,
        "note": "제주데이터허브 Open API 스냅샷. 읍면동은 612 정류소 주소 문자열로 붙였고 실패 행은 emd가 비어 있다.",
    }
    write_meta(path, meta)
    return {"ok": True, "slug": "bus_station", "hub_id": 613, "rows": len(rows),
            "bytes": meta["bytes"], "period": period, "source": "openapi:613+612",
            "emd_unmatched_rows": unmatched}


def _norm_station(name):
    return re.sub(r"\s+", "", str(name or ""))


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _region_name_list():
    """주소 문자열에서 찾을 행정동·법정동 이름(긴 이름 우선)."""
    from jeju_agent.dictionaries import ADMIN_TO_LEGAL
    names = set(ADMIN_TO_LEGAL)
    for legals in ADMIN_TO_LEGAL.values():
        names.update(legals)
    return sorted(names, key=len, reverse=True)


def _emd_in_address(address, names):
    """주소 문자열 → (행정동, 일치 여부). 법정동이 걸리면 행정동 후보로 바꾼다."""
    from jeju_agent.dictionaries import legal_to_admin
    if not address:
        return "", False
    for name in names:
        if name and name in address:
            admins = legal_to_admin(name)
            return (admins[0] if admins else name), True
    return "", False


def read_key_file(path):
    """키 JSON에서 프로젝트 키만 꺼낸다. 값은 반환만 하고 절대 출력하지 않는다."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    for key in ("api_key", "apiKey", "project_key", "projectKey", "key", "HUB_API_KEY"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def http_json(url, timeout=30):
    raw = http_get(url, timeout=timeout)
    return json.loads(raw.decode("utf-8"))


def pick_file(file_infos):
    """여러 파일이 있으면 가장 최근 연도를 고르고, 같으면 작은 파일(빠른 로딩)을 고른다."""

    def year_of(info):
        found = re.findall(r"(?:^|[_-])(20\d{2}|\d{2})(?!\d)", info.get("fileName") or "")
        years = []
        for token in found:
            years.append(int(token) + (2000 if len(token) == 2 else 0))
        return max(years) if years else 0

    return max(file_infos, key=lambda f: (year_of(f), -(f.get("fileSize") or 0)))


def decode_csv(raw):
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def dataset_info(hub_id):
    return http_json("%s/api/data/view?id=%d" % (HUB_WEB, hub_id), timeout=30)


def download_file(hub_id, file_info):
    """공개 파일 다운로드. (파일 id, 데이터 id) 순서가 실제 API 규칙이다."""
    url = "%s/api/file?id=%s&fileId=%s" % (HUB_WEB, file_info["id"], hub_id)
    return http_get(url, timeout=120)


def openapi_fetch(data_key, key, params=None):
    """Open API 호출. 실패하면 None."""
    if not (data_key and key):
        return None
    base = os.environ.get("HUB_BASE_URL") or HUB_OPENAPI
    if not base.endswith("/"):
        base += "/"
    query = urllib.parse.urlencode(params or {})
    url = "%s%s/%s%s" % (base, data_key, key, ("?" + query) if query else "")
    try:
        raw = http_get(url, timeout=10)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return raw


def recent_months(rows, col, months=12):
    """`YYYYMM`/`YYYYMMDD` 값에서 최근 N개월만 남긴다."""
    values = sorted({str(r.get(col) or "") for r in rows if r.get(col)})
    if not values:
        return rows
    latest = values[-1]
    if len(latest) >= 6 and latest[:6].isdigit():
        year, month = int(latest[:4]), int(latest[4:6])
    else:
        return rows
    start = year * 12 + (month - 1) - (months - 1)
    sy, sm = divmod(start, 12)
    floor = "%04d%02d" % (sy, sm + 1)
    return [r for r in rows if str(r.get(col) or "")[:6] >= floor]


def parse_rows(text, delimiter=","):
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    header = [h.strip().lstrip("\ufeff") for h in rows[0]]
    out = []
    for raw in rows[1:]:
        if not raw or all(not c.strip() for c in raw):
            continue
        out.append({header[i]: (raw[i] if i < len(raw) else "") for i in range(len(header))})
    return header, out


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_meta(path, meta):
    meta_path = path.with_name(path.name.replace(".csv", ".meta.json"))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")


def month_range(rows, col):
    if not col or not rows:
        return ""
    values = sorted({norm_month(r.get(col)) for r in rows if r.get(col)})
    values = [v for v in values if v]
    if not values:
        return ""
    first, last = values[0], values[-1]
    return "%s-%s ~ %s-%s" % (first[:4], first[4:], last[:4], last[4:])


DATE_COLUMNS = ("base_date", "년월", "base_year_month", "데이터기준일자", "일자", "기준연도")


def norm_month(value):
    """`2018-01-01`, `201801`, `20180101`, `2018` → `YYYYMM`(가능한 경우)."""
    text = str(value or "").strip()
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 6:
        return digits[:6]
    if len(digits) == 4:
        return digits + "01"
    return ""


def inferred_period(rows):
    for col in DATE_COLUMNS:
        if rows and col in rows[0]:
            period = month_range(rows, col)
            if period:
                return period
    return ""


def collect_one(target, key, dry_run):
    hub_id = target["hub_id"]
    info = dataset_info(hub_id)
    title = info.get("title") or target["name"]
    data_key = info.get("dataKey")
    data_file = info.get("dataFile") or {}
    file_infos = data_file.get("fileInfos") or []
    source = None
    raw = None
    api_params = None

    if key and info.get("dataApi"):
        api_params = api_params_for(hub_id)
        raw = openapi_fetch(data_key, key, api_params)
        if raw:
            source = "openapi"

    if raw is None and file_infos:
        chosen = pick_file(file_infos)
        raw = download_file(hub_id, chosen)
        source = "file:%s" % chosen.get("fileName")

    if raw is None:
        csv_data = info.get("csvData")
        if csv_data:
            text = "\n".join(",".join('"%s"' % str(c).replace('"', '""') for c in row)
                            for row in csv_data)
            source = "preview-csv"
        else:
            return {"ok": False, "slug": target["slug"], "hub_id": hub_id,
                    "reason": "키 없이 받을 수 있는 파일/미리보기가 없음"}
    else:
        text, enc = decode_csv(raw)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if enc not in ("utf-8-sig", "utf-8"):
            pass

    header, rows = parse_rows(text)
    header = [h for h in header if h]
    if not header:
        return {"ok": False, "slug": target["slug"], "hub_id": hub_id, "reason": "빈 파일"}

    if target.get("month_col"):
        before = len(rows)
        rows = recent_months(rows, target["month_col"], 12)
        trimmed = before - len(rows)
    else:
        trimmed = 0

    period = month_range(rows, target.get("month_col")) or inferred_period(rows) or " / ".join(
        x for x in (info.get("startDate"), info.get("endDate")) if x)

    out_csv = OUT_DIR / ("%s.csv" % target["slug"])
    meta_path = OUT_DIR / ("%s.meta.json" % target["slug"])
    if dry_run:
        return {"ok": True, "slug": target["slug"], "hub_id": hub_id, "title": title,
                "source": source, "rows": len(rows), "dry_run": True}

    write_csv(out_csv, header, rows)
    meta = {
        "id": "hub:%d" % hub_id,
        "hub_dataset_id": hub_id,
        "requested_id": target.get("requested_id"),
        "requested_name": target.get("requested_name") or "",
        "name": target["name"],
        "hub_title": title,
        "provider": "제주데이터허브",
        "source_kind": "hub",
        "period": period,
        "period_note": target.get("period_note") or "",
        "license": LICENSE,
        "url": "%s/api/data/view?id=%d" % (HUB_WEB, hub_id),
        "file": out_csv.name,
        "rows": len(rows),
        "bytes": out_csv.stat().st_size,
        "columns": header,
        "source": source,
        "api_params": api_params or {},
        "trimmed_rows": trimmed,
        "collected_at": dt.datetime.now().isoformat(timespec="seconds"),
        "available": True,
        "note": "제주데이터허브 공개 데이터 스냅샷. 원본 갱신 주기를 따르지 않는 고정 사본.",
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"ok": True, "slug": target["slug"], "hub_id": hub_id, "title": title,
            "rows": len(rows), "bytes": meta["bytes"], "source": source, "period": period}


def api_params_for(hub_id):
    """Open API 필수 요청변수 기본값(날짜 범위)."""
    if hub_id in (583, 589, 590, 613, 614, 615, 616, 617, 576, 577):
        today = dt.date.today()
        end = today.strftime("%Y%m%d")
        start = (today - dt.timedelta(days=7)).strftime("%Y%m%d")
        return {"startDate": start, "endDate": end}
    return {}


def main(argv=None):
    parser = argparse.ArgumentParser(description="제주데이터허브 공개 데이터 스냅샷 수집")
    parser.add_argument("--key-file", default=None,
                        help="허브 프로젝트 키 JSON 경로(선택, 값은 저장/출력하지 않음)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(APP_ROOT))
    from jeju_agent.config import read_env_file
    env_file = read_env_file(APP_ROOT / ".env")
    key = (os.environ.get("HUB_API_KEY") or env_file.get("HUB_API_KEY")
           or (read_key_file(args.key_file) if args.key_file else ""))
    if env_file.get("HUB_BASE_URL") and not os.environ.get("HUB_BASE_URL"):
        os.environ["HUB_BASE_URL"] = env_file["HUB_BASE_URL"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for target in TARGETS:
        try:
            res = collect_one(target, key, args.dry_run)
        except Exception as exc:  # 수집 실패는 앱 동작을 막지 않는다.
            res = {"ok": False, "slug": target["slug"], "hub_id": target["hub_id"],
                   "reason": "%s: %s" % (type(exc).__name__, exc)}
        results.append(res)
        if res.get("ok"):
            print("OK  %-18s hub:%-5s %7s행 %10s bytes  %s"
                  % (res["slug"], res["hub_id"], res.get("rows", 0),
                     res.get("bytes", 0), res.get("source", "")))
        else:
            print("SKIP %-18s hub:%-5s %s" % (res["slug"], res["hub_id"], res.get("reason")))
        time.sleep(0.4)

    for target in API_TARGETS:
        if args.dry_run:
            print("DRY %-18s hub:%-5s (Open API, 키 %s)"
                  % (target["slug"], target["hub_id"], "있음" if key else "없음"))
            continue
        if not key:
            results.append({"ok": False, "slug": target["slug"], "hub_id": target["hub_id"],
                            "reason": "허브 키 없음"})
            print("SKIP %-18s hub:%-5s 허브 키 없음" % (target["slug"], target["hub_id"]))
            continue
        try:
            if target["collector"] == "floating":
                res = collect_floating_pop(key)
            else:
                res = collect_bus_station(key)
            res["title"] = target["name"]
        except Exception as exc:  # 수집 실패는 앱 동작을 막지 않는다.
            res = {"ok": False, "slug": target["slug"], "hub_id": target["hub_id"],
                   "reason": "%s: %s" % (type(exc).__name__, exc)}
        results.append(res)
        if res.get("ok"):
            print("OK  %-18s hub:%-5s %7s행 %10s bytes  %s"
                  % (res["slug"], res["hub_id"], res.get("rows", 0),
                     res.get("bytes", 0), res.get("source", "")))
        else:
            print("SKIP %-18s hub:%-5s %s" % (res["slug"], res["hub_id"], res.get("reason")))
        time.sleep(0.4)

    ok = [r for r in results if r.get("ok")]
    print("\n수집 완료 %d/%d (키 %s, Open API 호출 %d회)"
          % (len(ok), len(results), "있음" if key else "없음", _CALLS["count"]))
    if args.dry_run:
        return 0
    # 이번에 못 받은 데이터는 이전 스냅샷 파일이 그대로 남으므로 README도 그 메타로 적는다.
    kept = {r["slug"]: r for r in readme_from_metas()}
    write_readme([r if r.get("ok") else kept.get(r["slug"], r) for r in results])
    return 0 if ok else 1


def readme_from_metas():
    """API 호출 없이 기존 meta JSON만으로 README용 결과 목록을 만든다."""
    results = []
    for path in sorted(OUT_DIR.glob("*.meta.json")):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        results.append({"ok": True, "slug": meta.get("file", path.stem).replace(".csv", ""),
                        "hub_id": meta.get("hub_dataset_id"),
                        "title": meta.get("name"), "period": meta.get("period"),
                        "rows": meta.get("rows")})
    return results


def write_readme(results):
    lines = [
        "# 허브 스냅샷 (sample_data/hub)",
        "",
        "`tools/fetch_hub_snapshots.py`가 제주데이터허브 **공개** 데이터를 받아 저장한 고정 사본입니다.",
        "각 CSV 옆의 `*.meta.json`에 출처 데이터 ID·기간·행수·이용조건·컬럼이 들어 있습니다.",
        "키 값은 어디에도 저장하지 않습니다.",
        "",
        "유동인구 지수(허브 집계값): 단위 미공개, 상대 비교만 사용합니다. 583 상세 메타·첨부 API 가이드에 집계 단위 설명이 없습니다.",
        "",
        "## 요청 데이터와 실제 대체",
        "",
        "| TASK 요청 | 실제 받은 데이터 | 기간 | 행수 | 대체 이유 |",
        "|---|---|---|---|---|",
    ]
    reasons = {
        "card_industry_sex": "요청 311(업종별 성별 카드)과 동일 계열 데이터를 그대로 받음",
        "stores": "요청 558(상가·상권 정보)과 같은 데이터(최신본 ID 793)",
        "card_time": "요청 817(시간대별 카드 이용금액)과 동일 데이터",
        "card_weekday": "요청 815(요일별 카드 이용 건수)와 동일 데이터",
        "menu_sales": "요청 824(메뉴별 외식업 매출액)와 동일 데이터",
        "bus_hourly": "추가 확보: 시간대별 버스 승차 인원(정류소 단위 613은 bus_station으로 별도 수집)",
        "floating_pop_emd": "요청 753을 Open API 583으로 받아 표본일 평균 지수로 집계(단위 미공개, 상대 비교만 사용)",
        "bus_station": "요청 725(정류소별 버스 이용자)를 Open API 613으로, 정류소 정보는 612로 읍면동·좌표 보강",
        "foreign_visitors_emd": "보조 자료: 단기체류 외국인 유동인구(내국인 유동인구 대체 아님)",
        "emd_area_mashup": "추가 확보: 읍면동×업종별 이용자·거주·근무·방문 인구(상권분석 매쉬업)",
    }
    order = ["floating_pop_emd", "bus_station", "card_industry_sex", "stores", "card_time",
             "card_weekday", "menu_sales", "bus_hourly", "foreign_visitors_emd",
             "emd_area_mashup"]
    by_slug = {r["slug"]: r for r in results}
    ordered = [by_slug[s] for s in order if s in by_slug]
    ordered += [r for r in results if r["slug"] not in order]
    results = ordered
    for res in results:
        if not res.get("ok"):
            lines.append("| %s | (못 받음) | - | - | %s |" % (res["slug"], res.get("reason", "")))
            continue
        lines.append("| %s(hub:%s) | %s | %s | %s | %s |" % (
            res["slug"], res["hub_id"], res.get("title", ""), res.get("period", "-"),
            res.get("rows", 0), reasons.get(res["slug"], "")))
    lines += ["", "## 다시 받기", "", "```", "python3 tools/fetch_hub_snapshots.py", "```", ""]
    (OUT_DIR / "README.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
