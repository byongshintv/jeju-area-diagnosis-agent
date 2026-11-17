"""가상 센터 데이터 생성기(시드 고정).

센터 CSV 6개 시트(1-08·1-12·1-14·1-17·1-19·1-24) **형식**을 그대로 흉내 낸
`sample_data/center_dummy/` 파일을 만든다. 값은 가상이며, 허브 공개 데이터의
읍면동별 점포 수·유동인구 규모에 비례하도록 그럴듯하게 만들었다.

파일·메타 첫머리에 "가상 데이터(형식 시연용)"을 표기한다. 실데이터가 아니다.

실행: python3 tools/make_dummy_center.py [--seed 20261117]
"""
import argparse
import csv
import json
import random
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))

from jeju_agent.center_schema import BY_NO  # noqa: E402
from jeju_agent.dictionaries import REGION_LIST  # noqa: E402

OUT_DIR = APP_ROOT / "sample_data" / "center_dummy"
HUB_DIR = APP_ROOT / "sample_data" / "hub"
MARK = "가상 데이터(형식 시연용)"

MONTHS = ["202507", "202508", "202509", "202510", "202511", "202512",
          "202601", "202602", "202603", "202604", "202605", "202606"]
WEEKS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
TIMES = ["오전", "점심", "오후", "저녁", "심야"]

# 상가(상권) 정보의 읍면동 점포 수로 만든 규모 지수(0.3~3.0). 허브 파일이 없으면 기본값.
DEFAULT_SCALE = {
    "연동": 3.0, "노형동": 2.8, "이도2동": 2.2, "이도1동": 1.6, "삼도2동": 1.5,
    "일도2동": 1.9, "일도1동": 1.4, "아라동": 1.7, "화북동": 1.3, "애월읍": 2.0,
    "한림읍": 1.6, "대정읍": 1.4, "중문동": 1.3, "서귀동": 1.8, "동홍동": 1.4,
    "성산읍": 1.2, "표선면": 1.1, "조천읍": 1.2, "구좌읍": 1.1, "안덕면": 1.0,
}
BASE_SCALE = 0.7

BIZ = [
    ("I56111", "한식 음식점업", "한식", 1.0),
    ("I56220", "비알콜 음료점업", "카페", 0.55),
    ("I56113", "분식 및 김밥 전문점", "분식", 0.3),
    ("I56112", "중식 음식점업", "중식", 0.3),
    ("I56114", "일식 음식점업", "일식", 0.3),
    ("I56115", "서양식 음식점업", "양식", 0.28),
    ("G47122", "체인화 편의점", "편의점", 0.4),
    ("G47215", "빵 및 과자류 소매업", "제과점/베이커리", 0.25),
    ("I56211", "기타 주점업", "치킨/호프", 0.3),
    ("G47121", "슈퍼마켓", "슈퍼마켓/식료품", 0.35),
]

USER_TYPES = [("제주도민", "도민", 0.55), ("내국인", "개별", 0.3), ("내국인", "단체", 0.15)]
AGES = [20, 30, 40, 50, 60, 70]
SEXES = ["남성", "여성"]

POS = [
    ("Q01", "한식", "Q01001", "한식 일반", 1.0),
    ("Q06", "분식", "Q06004", "스낵", 0.35),
    ("Q08", "유흥주점", "Q08004", "일반유흥주점", 0.2),
    ("Q11", "제과/제빵/떡/케익", "Q11004", "케익전문점", 0.3),
]
MENUS = [
    ("A", "밥류", "A01", "국밥류", "A0102", "돼지국밥", "A0102003", "따로국밥", 10000),
    ("B", "면류", "B01", "냉면류", "B0101", "평양냉면", "B0101001", "평양냉면", 12000),
    ("C", "빵/과자류", "C01", "빵류", "C0101", "크루아상", "C0101001", "크루아상", 4500),
    ("D", "음료류", "D01", "커피류", "D0101", "아메리카노", "D0101001", "아메리카노", 4500),
    ("E", "구이류", "E01", "삼겹살", "E0101", "생삼겹살", "E0101001", "생삼겹살", 16000),
    ("V", "주류", "V10", "혼성주류", "V1001", "매실주", "V1001001", "매취순", 22000),
]


def load_scales():
    """허브 상가(상권) 정보에서 읍면동별 점포 수를 세어 규모 지수를 만든다."""
    path = HUB_DIR / "stores.csv"
    if not path.is_file():
        return dict(DEFAULT_SCALE)
    counts = {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                emd = (row.get("행정동명") or "").strip()
                if emd:
                    counts[emd] = counts.get(emd, 0) + 1
    except OSError:
        return dict(DEFAULT_SCALE)
    if not counts:
        return dict(DEFAULT_SCALE)
    top = max(counts.values())
    scale = {}
    for record in REGION_LIST:
        name = record["name"]
        value = counts.get(name)
        scale[name] = round(max(0.3, (value or 30) / float(top) * 3.0), 3)
    return scale


def city_of(region_name):
    for record in REGION_LIST:
        if record["name"] == region_name:
            return record["city"]
    return "제주시"


def district_of(region_name):
    for record in REGION_LIST:
        if record["name"] == region_name:
            return record["district"] or ""
    return ""


def write_csv(name, header, rows):
    path = OUT_DIR / (name + ".csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_ALL)
        writer.writerow(header)
        for row in rows:
            writer.writerow(["" if v is None else v for v in row])
    return path


def quarter_growth(rng, months):
    """월별 성장/계절 요인(0.9~1.15)."""
    seasonal = {1: 0.92, 2: 0.9, 3: 1.0, 4: 1.05, 5: 1.12, 6: 1.08,
                7: 1.15, 8: 1.14, 9: 1.02, 10: 1.04, 11: 1.0, 12: 1.05}
    out = {}
    drift = 1.0
    for month in months:
        drift *= 1.0 + rng.uniform(-0.015, 0.02)
        out[month] = seasonal.get(int(month[4:6]), 1.0) * drift
    return out


def make_card_emd(rng, scales, with_district=True):
    """1-08 카드 이용 현황(읍면동)_v02."""
    rows = []
    for region in REGION_LIST:
        if region["level"] != "행정동":
            continue
        name = region["name"]
        scale = scales.get(name, BASE_SCALE)
        district = district_of(name)
        for month in MONTHS:
            factor = 1.0 + (int(month[:4]) - 2025) * 0.03
            for biz_cd, biz_nm, _label, weight in BIZ:
                # 전체 조합을 다 쓰면 파일이 20MB를 넘어 조합 일부만 표본으로 쓴다.
                for user_type, tour_type, u_weight in rng.sample(USER_TYPES, 2):
                    age = rng.choice(AGES)
                    sex = rng.choice(SEXES)
                    base = 900 * scale * weight * u_weight * factor
                    users = max(0, int(rng.gauss(base * 0.08, base * 0.02)))
                    uses = int(users * rng.uniform(1.2, 2.4))
                    amount = int(uses * rng.uniform(9000, 32000))
                    rows.append([
                        month, district if with_district else None,
                        region["city"],
                        "읍면" if name.endswith(("읍", "면")) else "도심",
                        name, biz_cd, biz_nm, user_type, tour_type,
                        age, sex,
                        users if users > 0 else None,
                        uses, amount,
                    ])
    return rows


def make_card_week(rng, scales):
    """1-12 카드 이용 현황(요일별)_v02."""
    rows = []
    for region in REGION_LIST:
        if region["level"] != "행정동":
            continue
        scale = scales.get(region["name"], BASE_SCALE)
        for month in ("202601", "202602", "202603"):
            for biz_cd, biz_nm, _label, weight in BIZ:
                for week_index, week in enumerate(WEEKS):
                    weekend = 1.35 if week_index >= 5 else (1.02 if week_index == 4 else 0.95)
                    user_type, tour_type, u_weight = USER_TYPES[rng.randrange(len(USER_TYPES))]
                    users = max(0, int(rng.gauss(260 * scale * weight * weekend * u_weight, 12)))
                    uses = int(users * rng.uniform(1.1, 2.2))
                    rows.append([
                        month, district_of(region["name"]), region["name"],
                        biz_cd, biz_nm, user_type, tour_type, week,
                        rng.choice(AGES), rng.choice(SEXES),
                        users if users > 0 else None, uses,
                        int(uses * rng.uniform(9000, 30000)),
                    ])
    return rows


def make_card_time(rng, scales):
    """1-14 카드 이용 현황(시간대)_v02."""
    weights = {"오전": 0.55, "점심": 1.35, "오후": 1.15, "저녁": 1.25, "심야": 0.35}
    rows = []
    for region in REGION_LIST:
        if region["level"] != "행정동":
            continue
        scale = scales.get(region["name"], BASE_SCALE)
        for month in ("202601", "202602", "202603"):
            for biz_cd, biz_nm, _label, weight in BIZ:
                for time_label in TIMES:
                    user_type, tour_type, u_weight = USER_TYPES[rng.randrange(len(USER_TYPES))]
                    users = max(0, int(rng.gauss(300 * scale * weight * weights[time_label] * u_weight, 14)))
                    uses = int(users * rng.uniform(1.1, 2.1))
                    rows.append([
                        month, district_of(region["name"]), region["name"],
                        biz_cd, biz_nm, user_type, tour_type, time_label,
                        rng.choice(AGES), rng.choice(SEXES),
                        users if users > 0 else None, uses,
                        int(uses * rng.uniform(9000, 30000)),
                    ])
    return rows


def make_store_counts(rng, scales):
    """1-17 제주도 내 업종별 매장 수."""
    rows = []
    for city in ("제주시", "서귀포시"):
        city_scale = sum(s for n, s in scales.items() if city_of(n) == city)
        for month in ("202601", "202602"):
            for owner_gb, owner_weight in (("법인", 0.35), ("개인", 0.65)):
                for biz_cd, biz_nm, _label, weight in BIZ:
                    count = int(city_scale * weight * owner_weight * 26)
                    rows.append([month, city, owner_gb, biz_cd, biz_nm, max(count, 0)])
    return rows


def make_pos_emd(rng, scales):
    """1-19 외식업 포스 이용 현황(읍면동)."""
    rows = []
    for region in REGION_LIST:
        if region["level"] != "행정동":
            continue
        scale = scales.get(region["name"], BASE_SCALE)
        code = region["admin_code"] or ""
        for month in ("202601", "202602", "202603"):
            for mid_cd, mid_nm, small_cd, small_nm, weight in POS:
                stores = max(1, int(scale * weight * 14))
                uses = int(stores * rng.uniform(60, 140))
                sale = int(uses * rng.uniform(9000, 26000))
                rows.append([
                    month, code[:4], region["city"], code, region["name"],
                    mid_cd, mid_nm, small_cd, small_nm, sale, stores, uses,
                ])
    return rows


def make_menu_emd(rng, scales):
    """1-24 외식업 포스 메뉴 이용 현황(읍면동)."""
    rows = []
    for region in REGION_LIST:
        if region["level"] != "행정동":
            continue
        scale = scales.get(region["name"], BASE_SCALE)
        code = region["admin_code"] or ""
        for month in ("202601", "202602"):
            for large_cd, large_nm, mid_cd, mid_nm, small_cd, small_nm, sub_cd, sub_nm, price in MENUS:
                sales = max(0, int(rng.gauss(scale * 1_600_000, 90_000)))
                count = max(0, int(sales / max(price, 1)))
                unit = int(round(price * rng.uniform(0.95, 1.08)))
                rows.append([
                    month, code[:4], region["city"], code, region["name"],
                    large_cd, large_nm, mid_cd, mid_nm, small_cd, small_nm, sub_cd, sub_nm,
                    sales, count, unit,
                ])
    return rows


BUILDERS = [
    ("1-08", "card_emd_v02", make_card_emd),
    ("1-12", "card_week_v02", make_card_week),
    ("1-14", "card_time_v02", make_card_time),
    ("1-17", "store_count", make_store_counts),
    ("1-19", "pos_emd", make_pos_emd),
    ("1-24", "menu_emd", make_menu_emd),
]

PERIODS = {
    "1-08": "2025-07 ~ 2026-06",
    "1-12": "2026-01 ~ 2026-03",
    "1-14": "2026-01 ~ 2026-03",
    "1-17": "2026-01 ~ 2026-02",
    "1-19": "2026-01 ~ 2026-03",
    "1-24": "2026-01 ~ 2026-02",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="가상 센터 데이터 생성")
    parser.add_argument("--seed", type=int, default=20261117)
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)
    scales = load_scales()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    made = []
    for dataset_no, slug, builder in BUILDERS:
        ds = BY_NO[dataset_no]
        header = [c["name"] for c in ds["columns"]]
        rows = builder(rng, scales)
        if dataset_no == "1-08":
            # dstrct_se가 없는 v02 표기를 그대로 둔다(스키마와 일치 확인용).
            pass
        path = write_csv(slug, header, rows)
        meta = {
            "id": "center_dummy:" + dataset_no,
            "dataset_no": dataset_no,
            "name": ds["sheet"],
            "provider": "제주빅데이터센터(가상 시연용)",
            "source_kind": "center_dummy",
            "file": path.name,
            "rows": len(rows),
            "bytes": path.stat().st_size,
            "columns": header,
            "period": PERIODS.get(dataset_no, ""),
            "license": "형식 시연용 가상 데이터(실데이터 아님, 반출 대상 아님)",
            "note": MARK,
            "generator": "tools/make_dummy_center.py",
            "seed": args.seed,
            "scale_basis": "허브 상가(상권) 정보 읍면동별 점포 수 비례",
        }
        (OUT_DIR / (slug + ".meta.json")).write_text(
            json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        made.append(meta)
        print("OK %-5s %-14s %7d행 %9d bytes" % (dataset_no, slug, len(rows), meta["bytes"]))

    readme = [
        "# 가상 센터 데이터 (center_dummy)",
        "",
        "**모든 값은 가상 데이터(형식 시연용)입니다. 실제 제주빅데이터센터 데이터가 아닙니다.**",
        "",
        "`tools/make_dummy_center.py`가 시드 고정으로 만들었고, 값은 허브 공개 데이터의",
        "읍면동별 점포 수 규모에 비례하도록 그럴듯하게 생성했습니다.",
        "센터 CSV 형식(헤더 영문, 값 큰따옴표, `NA` 결측, UTF-8 BOM)을 그대로 따릅니다.",
        "",
        "| dataset_id | 시트 | 기간 | 행수 |",
        "|---|---|---|---|",
    ]
    for meta in made:
        readme.append("| %s | %s | %s | %d |" % (meta["id"], meta["name"], meta["period"], meta["rows"]))
    readme += ["", "다시 만들기: `python3 tools/make_dummy_center.py`", ""]
    (OUT_DIR / "README.md").write_text("\n".join(readme), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
