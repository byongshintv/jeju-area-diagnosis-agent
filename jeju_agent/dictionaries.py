"""지역·업종 사전.

`sample_data/hub/stores.csv`(상가(상권) 정보, hub:793)의 실제 값에서
행정동 43개·법정동 73개·행정동↔법정동 대응·행정동 코드를 뽑았고,
`emd_area_mashup.csv`·`foreign_visitors_emd.csv`의 `읍면동명`/`emd_name` 표기와
`dstrct_se`(권역) 표기로 별칭을 보강했다.

업종 대응표는 카드 `biz_nm`(hub:1048), 상가 `상권업종중분류명`/`상권업종소분류명`,
외식업 POS 분류(센터 1-18·1-19), 메뉴 분류(hub:495, 센터 1-24)를 손으로 연결한 것이다.
"""
import csv
import re
from pathlib import Path

from .config import APP_ROOT

# ---------------------------------------------------------------------------
# 지역
# ---------------------------------------------------------------------------

CITY_BY_EMD = {}
ADMIN_TO_LEGAL = {}
ADMIN_CODES = {}

_RAW_REGIONS = """
건입동|제주시|건입동|50110590
구좌읍|제주시|구좌읍|50110256
남원읍|서귀포시|남원읍|50130253
노형동|제주시|노형동,해안동|50110660
대륜동|서귀포시|법환동,서호동,호근동|50130590
대정읍|서귀포시|대정읍|50130250
대천동|서귀포시|강정동,도순동,영남동,월평동|50130600
도두동|제주시|도두이동,도두일동|50110690
동홍동|서귀포시|동홍동|50130570
봉개동|제주시|봉개동,용강동,회천동|50110620
삼도1동|제주시|삼도일동|50110550
삼도2동|제주시|삼도이동|50110560
삼양동|제주시|도련이동,도련일동,삼양삼동,삼양이동,삼양일동|50110610
서홍동|서귀포시|서홍동|50130580
성산읍|서귀포시|성산읍|50130259
송산동|서귀포시|동홍동,보목동,서귀동,토평동|50130510
아라동|제주시|아라이동,아라일동,영평동,오등동,월평동,이도이동|50110630
안덕면|서귀포시|안덕면|50130310
애월읍|제주시|애월읍|50110253
연동|제주시|연동|50110650
영천동|서귀포시|상효동,토평동|50130560
예래동|서귀포시|상예동,색달동,하예동|50130620
오라동|제주시|오라삼동,오라이동,오라일동|50110640
외도동|제주시|내도동,도평동,외도이동,외도일동|50110670
용담1동|제주시|용담일동|50110570
용담2동|제주시|용담삼동,용담이동|50110580
우도면|제주시|우도면|50110330
이도1동|제주시|이도일동|50110530
이도2동|제주시|도남동,이도이동|50110540
이호동|제주시|이호이동,이호일동|50110680
일도1동|제주시|일도일동|50110510
일도2동|제주시|일도이동|50110520
정방동|서귀포시|서귀동|50130520
조천읍|제주시|조천읍|50110259
중문동|서귀포시|대포동,중문동,하원동,회수동|50130610
중앙동|서귀포시|서귀동|50130530
천지동|서귀포시|서귀동,서홍동|50130540
추자면|제주시|추자면|50110320
표선면|서귀포시|표선면|50130320
한경면|제주시|한경면|50110310
한림읍|제주시|한림읍|50110250
화북동|제주시|화북이동,화북일동|50110600
효돈동|서귀포시|신효동,하효동|50130550
"""

# 제주데이터허브 상가정보의 `상권업종` 데이터와 2021년 이후 통합된 구 명칭.
# 카드 1-08의 법정동 표기(예: 대륜동, 중문동)와 행정동 표기가 섞여 들어오므로 함께 둔다.
EXTRA_REGION_ALIASES = {
    "중문동": ["중문"],
    "대륜동": ["대륜"],
    "대천동": ["대천"],
    "성산읍": ["성산"],
    "표선면": ["표선"],
    "안덕면": ["안덕"],
    "한림읍": ["한림"],
    "애월읍": ["애월"],
    "구좌읍": ["구좌"],
    "조천읍": ["조천"],
    "한경면": ["한경"],
    "우도면": ["우도"],
    "추자면": ["추자"],
    "남원읍": ["남원"],
}

# 상위 권역(dstrct_se). 카드 1-08·1-12·1-14의 실제 표기(예: "제주시 중부(서)").
DISTRICTS = {
    "제주시 동부": ["건입동", "화북동", "삼양동", "봉개동", "구좌읍", "조천읍", "우도면"],
    "제주시 중부(동)": ["일도1동", "일도2동", "이도1동", "이도2동", "삼도1동", "삼도2동"],
    "제주시 중부(서)": ["노형동", "연동", "아라동", "오라동"],
    "제주시 서부": ["애월읍", "한림읍", "한경면", "추자면"],
    "제주시 남부": ["외도동", "이호동", "도두동"],
    "서귀포시 동부": ["성산읍", "표선면", "남원읍"],
    "서귀포시 중부": ["송산동", "정방동", "중앙동", "천지동", "효돈동", "영천동", "동홍동", "서홍동"],
    "서귀포시 서부": ["대정읍", "안덕면", "중문동", "예래동", "대천동", "대륜동"],
}

for _line in _RAW_REGIONS.strip().splitlines():
    _admin, _city, _legal, _code = _line.split("|")
    CITY_BY_EMD[_admin] = _city
    ADMIN_TO_LEGAL[_admin] = _legal.split(",")
    ADMIN_CODES[_admin] = _code


def _district_of(emd):
    for district, members in DISTRICTS.items():
        if emd in members:
            return district
    return ""


REGION_LIST = []
_ALIAS_INDEX = {}


def _add_alias(emd, alias):
    alias = (alias or "").strip()
    if not alias or alias == emd:
        return
    _ALIAS_INDEX.setdefault(alias, set()).add(emd)


# 법정동 → 행정동 (여러 행정동에 걸친 법정동은 후보를 모두 둔다)
LEGAL_TO_ADMIN = {}
for _admin, _legals in ADMIN_TO_LEGAL.items():
    for _legal in _legals:
        LEGAL_TO_ADMIN.setdefault(_legal, []).append(_admin)

_ADMIN_NAMES = set(ADMIN_TO_LEGAL)

for _admin in sorted(ADMIN_TO_LEGAL):
    _legals = ADMIN_TO_LEGAL[_admin]
    REGION_LIST.append({
        "name": _admin,
        "level": "행정동",
        "city": CITY_BY_EMD[_admin],
        "admin_code": ADMIN_CODES[_admin],
        "district": _district_of(_admin),
        "legal_dongs": _legals,
        "admin_dongs": [_admin],
        "kind": "읍면" if _admin.endswith(("읍", "면")) else "동",
        "aliases": [],
    })
    _add_alias(_admin, _admin[:-1] if _admin.endswith(("동", "읍", "면")) else _admin)
    for _alias in EXTRA_REGION_ALIASES.get(_admin, []):
        _add_alias(_admin, _alias)

# 카드 데이터(1-08 등)의 법정동 표기를 그대로 사전에 넣는다. 행정동과 이름이 같은
# 법정동은 중복을 피하고, 다른 법정동(예: 일도이동·서귀동·아라이동)만 별도 항목.
for _legal in sorted(LEGAL_TO_ADMIN):
    if _legal in _ADMIN_NAMES:
        continue
    _parents = LEGAL_TO_ADMIN[_legal]
    _city = CITY_BY_EMD[_parents[0]]
    REGION_LIST.append({
        "name": _legal,
        "level": "법정동",
        "city": _city,
        "admin_code": "",
        "district": _district_of(_parents[0]),
        "legal_dongs": [_legal],
        "admin_dongs": sorted(_parents),
        "kind": "읍면" if _legal.endswith(("읍", "면")) else "동",
        "aliases": [],
    })

for _record in REGION_LIST:
    aliases = []
    if _record["level"] == "행정동":
        for _legal in _record["legal_dongs"]:
            if _legal != _record["name"]:
                aliases.append(_legal)
                _add_alias(_record["name"], _legal)
    for _alias, _targets in _ALIAS_INDEX.items():
        if _record["name"] in _targets and _alias not in aliases:
            aliases.append(_alias)
    _record["aliases"] = sorted(set(a for a in aliases if a and a != _record["name"]))


def legal_to_admin(legal):
    """법정동 이름 → 행정동 후보 목록."""
    return list(LEGAL_TO_ADMIN.get((legal or "").strip(), []))


def all_region_names():
    return [r["name"] for r in REGION_LIST]


def get_regions():
    return REGION_LIST


# ---------------------------------------------------------------------------
# 업종
# ---------------------------------------------------------------------------

# {name, aliases, card_codes, store_categories, pos_codes, menu_keywords}
# card_codes: 카드 데이터 biz_cd/biz_nm (KSIC 10차 개정 기반, 센터 1-08 등)
# store_categories: 상가(상권) 정보의 상권업종(중분류/소분류) 표기
# pos_codes: 외식업 POS 중분류/소분류(센터 1-18·1-19·1-21) 중 예시에서 확인된 코드만
# menu_keywords: 메뉴 분류(센터 1-24, hub:495)에서 관련 메뉴 이름 조각
INDUSTRIES = [
    {
        "name": "카페",
        "aliases": ["카페", "커피", "커피숍", "까페", "디저트카페", "베이커리카페", "브런치"],
        "card_codes": ["I56220"],
        "card_names": ["비알콜 음료점업"],
        "store_categories": ["비알코올 ", "카페", "커피"],
        "pos_codes": [],
        "menu_keywords": ["음료", "빵", "과자", "떡", "빙과"],
    },
    {
        "name": "제과점/베이커리",
        "aliases": ["빵집", "베이커리", "제과점", "빵", "과자점", "디저트"],
        "card_codes": ["G47215"],
        "card_names": ["빵 및 과자류 소매업"],
        "store_categories": ["빵/도넛", "과자점"],
        "pos_codes": ["Q11", "Q11004"],
        "menu_keywords": ["빵", "과자", "떡", "케익"],
    },
    {
        "name": "한식",
        "aliases": ["한식", "한식당", "국밥", "백반", "식당", "음식점"],
        "card_codes": ["I56111"],
        "card_names": ["한식 음식점업"],
        "store_categories": ["한식", "기타 한식 음식점"],
        "pos_codes": [],
        "menu_keywords": ["밥", "국밥", "찌개", "전골", "탕", "김치", "나물", "조림", "구이"],
    },
    {
        "name": "분식",
        "aliases": ["분식", "김밥", "떡볶이", "만두", "스낵"],
        "card_codes": ["I56113"],
        "card_names": ["분식 및 김밥 전문점"],
        "store_categories": ["김밥/만두/분식", "분식"],
        "pos_codes": ["Q06", "Q06004"],
        "menu_keywords": ["김밥", "떡볶이", "만두", "튀김"],
    },
    {
        "name": "중식",
        "aliases": ["중식", "중국요리", "중국집"],
        "card_codes": ["I56112"],
        "card_names": ["중식 음식점업"],
        "store_categories": ["중식"],
        "pos_codes": [],
        "menu_keywords": ["중식", "짜장", "짬뽕"],
    },
    {
        "name": "일식",
        "aliases": ["일식", "초밥", "스시", "회", "횟집", "라멘"],
        "card_codes": ["I56114"],
        "card_names": ["일식 음식점업"],
        "store_categories": ["일식", "횟집"],
        "pos_codes": [],
        "menu_keywords": ["회", "초밥", "일식"],
    },
    {
        "name": "양식",
        "aliases": ["양식", "파스타", "스테이크", "피자", "패밀리레스토랑", "브런치"],
        "card_codes": ["I56115", "I56116"],
        "card_names": ["서양식 음식점업", "피자 햄버거 샌드위치 및 유사 음식점업"],
        "store_categories": ["서양식", "패스트푸드"],
        "pos_codes": [],
        "menu_keywords": ["양식", "파스타", "피자"],
    },
    {
        "name": "치킨/호프",
        "aliases": ["치킨", "통닭", "호프", "호프집", "맥주", "닭강정"],
        "card_codes": ["I56211"],
        "card_names": ["기타 주점업"],
        "store_categories": ["호프/통닭", "통닭(치킨)", "주점"],
        "pos_codes": [],
        "menu_keywords": ["치킨", "주류", "통닭"],
    },
    {
        "name": "편의점",
        "aliases": ["편의점", "CU", "GS25", "세븐일레븐", "이마트24"],
        "card_codes": ["G47122"],
        "card_names": ["체인화 편의점"],
        "store_categories": ["편의점"],
        "pos_codes": [],
        "menu_keywords": [],
    },
    {
        "name": "슈퍼마켓/식료품",
        "aliases": ["슈퍼", "마트", "식료품", "반찬", "정육", "청과"],
        "card_codes": ["G47121"],
        "card_names": ["슈퍼마켓"],
        "store_categories": ["종합 소매", "식료품 소매"],
        "pos_codes": [],
        "menu_keywords": [],
    },
    {
        "name": "미용실",
        "aliases": ["미용실", "헤어", "헤어샵", "미용", "이발소", "네일"],
        "card_codes": ["S96012", "S96011"],
        "card_names": ["이용 및 미용업", "두발 미용업"],
        "store_categories": ["이용·미용", "미용실", "이발소"],
        "pos_codes": [],
        "menu_keywords": [],
    },
    {
        "name": "숙박",
        "aliases": ["숙박", "펜션", "호텔", "게스트하우스", "민박", "모텔", "리조트", "여관"],
        "card_codes": ["I55101", "I55102", "I55103"],
        "card_names": ["호텔업", "휴양콘도 운영업", "여관업"],
        "store_categories": ["일반 숙박", "호텔/리조트", "여관/모텔", "민박", "게스트하우스"],
        "pos_codes": [],
        "menu_keywords": [],
    },
    {
        "name": "약국",
        "aliases": ["약국", "약방"],
        "card_codes": ["G47811"],
        "card_names": ["약국"],
        "store_categories": ["약국"],
        "pos_codes": [],
        "menu_keywords": [],
    },
    {
        "name": "부동산",
        "aliases": ["부동산", "공인중개사", "중개"],
        "card_codes": ["L68221"],
        "card_names": ["부동산 중개 및 대리업"],
        "store_categories": ["부동산 서비스", "부동산 중개/대리업"],
        "pos_codes": [],
        "menu_keywords": [],
    },
    {
        "name": "관광/여행",
        "aliases": ["관광", "여행사", "투어", "렌터카", "액티비티"],
        "card_codes": ["N75220", "N75110"],
        "card_names": ["여행사업", "자동차 임대업"],
        "store_categories": ["여행사·보조", "운송장비 대여"],
        "pos_codes": [],
        "menu_keywords": [],
    },
]

_ALIAS_TO_INDUSTRY = {}
for _ind in INDUSTRIES:
    for _alias in [_ind["name"]] + _ind["aliases"]:
        _ALIAS_TO_INDUSTRY.setdefault(_alias, set()).add(_ind["name"])


def get_industries():
    return INDUSTRIES


def industries_for_card_name(name):
    """카드 데이터 biz_nm → 업종 사전 항목(이름 목록)."""
    hits = []
    for ind in INDUSTRIES:
        if name in ind.get("card_names", []):
            hits.append(ind["name"])
    return hits


def industries_for_store_category(mid=None, small=None):
    """상가(상권) 정보의 중분류/소분류명 → 업종 사전 항목."""
    hits = []
    for ind in INDUSTRIES:
        cats = ind.get("store_categories", [])
        if (mid and mid in cats) or (small and small in cats):
            hits.append(ind["name"])
    return hits


def _norm(text):
    return re.sub(r"[\s_()\-]+", "", str(text or "")).lower()


def resolve_region(text):
    """사용자 표현 → (확정 이름, 후보 목록).

    확정되면 (name, []), 별칭이 여러 곳을 가리키면 (None, 후보 이름 리스트).
    """
    query = _norm(text)
    if not query:
        return None, []
    for district, members in DISTRICTS.items():
        if _norm(district) == query:
            return district, members
    exact = [r["name"] for r in REGION_LIST if _norm(r["name"]) == query]
    if len(exact) == 1:
        return exact[0], []
    if len(exact) > 1:
        return None, exact
    partial = [r["name"] for r in REGION_LIST if query in _norm(r["name"])]
    alias_hits = set()
    for alias, targets in _ALIAS_INDEX.items():
        if _norm(alias) == query:
            alias_hits |= targets
    if len(alias_hits) == 1:
        return sorted(alias_hits)[0], []
    candidates = sorted(set(partial) | alias_hits)
    if len(candidates) == 1:
        return candidates[0], []
    return None, candidates


def resolve_industry(text):
    """사용자 표현 → (확정 이름, 후보 목록)."""
    query = _norm(text)
    if not query:
        return None, []
    exact = set(_ALIAS_TO_INDUSTRY.get(query, set()))
    if len(exact) == 1:
        return sorted(exact)[0], []
    if len(exact) > 1:
        return None, sorted(exact)
    hits = set()
    for alias, targets in _ALIAS_TO_INDUSTRY.items():
        if query in alias or alias in query:
            hits |= targets
    if len(hits) == 1:
        return sorted(hits)[0], []
    return None, sorted(hits)


# ---------------------------------------------------------------------------
# 권역 분할 표 (지역 요약에 쓴다)
# ---------------------------------------------------------------------------

def region_summary():
    by_city = {}
    for record in REGION_LIST:
        by_city.setdefault(record["city"], []).append(record["name"])
    return {
        "count": len(REGION_LIST),
        "legal_count": len(LEGAL_TO_ADMIN),
        "by_city": {k: sorted(v) for k, v in by_city.items()},
        "districts": DISTRICTS,
        "source": "제주데이터허브 상가(상권) 정보(hub:793) 행정동명·법정동명 실값",
    }


def load_hub_emd_names(sample_dir=None):
    """허브 스냅샷에 등장하는 읍면동 표기(접두 시군구 포함)를 모은다."""
    sample_dir = Path(sample_dir) if sample_dir else APP_ROOT / "sample_data" / "hub"
    seen = set()
    for path in sorted(sample_dir.glob("*.csv")):
        try:
            with open(path, encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                columns = [c for c in (reader.fieldnames or ()) if c in ("emd_name", "읍면동명")]
                if not columns:
                    continue
                col = columns[0]
                for row in reader:
                    value = (row.get(col) or "").strip()
                    if value:
                        seen.add(value)
        except OSError:
            continue
    return sorted(seen)
