"""반출 사전 점검 규칙별 시험 사례를 실행해 입력·기대·실제 결과를 표로 출력한다.

결과 설명자료의 '반출 규칙별 시험 사례' 표를 다시 만드는 명령이다. 네트워크·키 없이 동봉
허브 스냅샷과 가상 센터 데이터(형식 시연용)만 쓰고, 파일을 쓰지 않는다.

    python tools/export_cases.py          # Markdown 표 출력
    python tools/export_cases.py --help
"""
import argparse
import sys
import tempfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))

from jeju_agent.analysis import DataHub, MASKED_TEXT, diagnose_area_tool  # noqa: E402
from jeju_agent.catalog import Catalog, load_dummy_tables  # noqa: E402
from jeju_agent.center_data import UploadStore  # noqa: E402
from jeju_agent.export_check import apply_to_table, check_export  # noqa: E402


def _cells(result):
    return ", ".join("%d행 %s(%s)" % (c["row"] + 1, c["column"], c["reason"])
                     for c in result["suppressed"]) or "없음"


def table_cases():
    base = {"columns": ["업종", "이용자수", "이용건수"],
            "rows": [["카페", 120, 300], ["제과점", 7, 25], ["편의점", 45, 40]]}
    cases = []

    r = check_export(base)
    cases.append(("10 미만 칸 가림", "제과점 이용자수 7", "그 칸만 가림",
                  _cells(r), [(c["row"], c["col"]) for c in r["suppressed"]] == [(1, 1)]))

    with_total = dict(base, total_row=["합계", 172, 365])
    r = check_export(with_total)
    ok = {(c["row"], c["col"], c["reason"]) for c in r["suppressed"]} == {
        (1, 1, "under_10"), (2, 1, "back_calculation")}
    cases.append(("합계로 역산되는 칸", "위 표 + 합계 172 공개",
                  "같은 열에서 가장 작은 다른 값(편의점 45)도 가림", _cells(r), ok))

    alone = {"columns": ["업종", "이용자수"], "rows": [["제과점", 7]], "total_row": ["합계", 7]}
    r = check_export(alone)
    cases.append(("다른 공개값이 없는 합계", "한 행(7)과 합계 7", "합계 비공개",
                  _cells(r) + ", 합계 " + ("비공개" if r["total_hidden"] else "공개"),
                  r["total_hidden"]))

    ratio = {"columns": ["읍면동", "1인당 이용건수"], "rows": [["노형동", 1.3], ["연동", 12.5]]}
    r = check_export(ratio)
    cases.append(("나눠 구한 값(1인당)", "노형동 1.3, 연동 12.5", "1.3 가림(사칙연산 결과도 최종 결과값)",
                  _cells(r), [(c["row"], c["col"]) for c in r["suppressed"]] == [(0, 1)]))

    hub_table = {"columns": ["읍면동", "점포 수"], "rows": [["추자면", 2]]}
    shown, result = apply_to_table(hub_table, "hub")
    cases.append(("허브 공개 데이터만 쓴 표", "추자면 점포 수 2(hub:793)",
                  "점검 대상 아님(공개 데이터)",
                  "점검 안 함, 값 %s" % shown["rows"][0][1], result is None))
    return cases


def diagnosis_cases():
    with tempfile.TemporaryDirectory() as tmp:
        store = UploadStore(tmp)
        catalog = Catalog(None, store)
        for no, table in load_dummy_tables(catalog.dummy_dir).items():
            store.add_demo(no, table["meta"], table["header"], table["rows"])
        hub = DataHub(catalog)
        cases = []
        r = diagnose_area_tool(hub, "추자면", "편의점")
        evidence = next(a for a in r["artifacts"] if a["id"] == "evidence")
        cell = next(row[1] for row in evidence["rows"] if row[0] == "동종 점포 수")
        kpi = next(a for a in r["artifacts"] if a["id"] == "kpi")
        kpi_value = next(i["value"] for i in kpi["items"] if i["label"] == "동종 점포 수")
        bar = next(a for a in r["artifacts"] if a["id"] == "competition_bar")
        own = next(p["value"] for p in bar["series"][0]["points"] if p["name"] == "추자면")
        sentence = " ".join(r["data"]["competition"])
        ok = cell is None and kpi_value == "가림" and own is None and MASKED_TEXT in sentence
        cases.append(("센터 데이터가 섞인 진단", "추자면 편의점(가상 센터 데이터 로드)",
                      "근거 표·문장·핵심 지표·비교 막대 모두 점포 수 가림",
                      "표 %s, 문장 '%s', 지표 %s, 막대 %s" % (
                          "가림" if cell is None else cell, MASKED_TEXT if MASKED_TEXT in sentence
                          else "값 노출", kpi_value, "가림" if own is None else own), ok))
        r = diagnose_area_tool(hub, "노형동", "카페")
        sup = r["data"]["export_check"]["suppressed"]
        cases.append(("가릴 값이 없는 진단", "노형동 카페(가상 센터 데이터 로드)", "가림 없음",
                      "가림 %d칸" % len(sup), not sup))
        return cases


def main(argv=None):
    parser = argparse.ArgumentParser(description="반출 사전 점검 규칙별 시험 사례 실행(파일·네트워크 사용 안 함)")
    parser.parse_args(argv)
    rows = table_cases() + diagnosis_cases()
    print("| 규칙 | 입력 | 기대 | 실제 | 판정 |")
    print("|---|---|---|---|---|")
    for name, given, expected, actual, ok in rows:
        print("| %s | %s | %s | %s | %s |" % (name, given, expected, actual, "통과" if ok else "실패"))
    return 0 if all(r[-1] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
