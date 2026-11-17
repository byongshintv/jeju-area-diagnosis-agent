"""실제 허브 스냅샷·가상 센터 데이터로 diagnose_area 15조합 확인."""
import csv
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from jeju_agent.analysis import DataHub, FLOATING_ID, diagnose_area_tool
from jeju_agent.catalog import Catalog
from jeju_agent.center_data import UploadStore

REGIONS = ["노형동", "애월읍", "대정읍", "연동", "서귀동"]
INDUSTRIES = ["카페", "한식", "편의점"]


class DiagnoseMatrixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.hub = DataHub(Catalog(None, UploadStore(cls.tmp.name)))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_combinations_have_grounded_results(self):
        for region in REGIONS:
            for industry in INDUSTRIES:
                with self.subTest(region=region, industry=industry):
                    r = diagnose_area_tool(self.hub, region, industry)
                    self.assertNotIn("clarification", r.get("data") or {})
                    self.assertTrue(r["summary_for_llm"])
                    self.assertTrue(r["artifacts"])
                    self.assertIn(FLOATING_ID, r["datasets_used"])
                    summary = r["summary_for_llm"]
                    for line in r["data"]["demand"]:
                        if "유동인구" in line or "방문인구" in line:
                            self.assertNotRegex(line, r"[\d,]+(?:\.\d+)?명")
                    for line_key, metric in (("demand", "유동인구 수준"), ("competition", "동종 점포 수(경쟁)")):
                        item = next(j for j in r["data"]["judgments"] if j["metric"] == metric)
                        self.assertIn(item["position"], " ".join(r["data"][line_key]))
                    for artifact in r["artifacts"]:
                        if artifact.get("id") == "demand_floating":
                            self.assertEqual(artifact["unit"], "허브 집계값")
                    for item in r["data"]["judgments"]:
                        positions = re.findall(re.escape(item["metric"]) + r"(?:는 )? ?(\d+곳 중 \d+위\(상위 \d+%\))", summary)
                        self.assertTrue(positions, item["metric"])
                        self.assertEqual(set(positions), {item["position"]})
                        pct = float(re.search(r"상위 (\d+)%", item["position"])[1])
                        if "경쟁" in item["metric"] and pct <= 33:
                            self.assertIn("주의", item["tone"])

    def test_floating_rank_matches_independent_csv_sum(self):
        totals = {}
        with open(Path(__file__).resolve().parents[2] / "sample_data/hub/floating_pop_emd.csv", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                totals[row["emd"]] = totals.get(row["emd"], 0) + sum(
                    float(row[k]) for k in ("resd_pop", "work_pop", "visit_pop"))
        result = diagnose_area_tool(self.hub, "대정읍", "편의점")
        item = next(j for j in result["data"]["judgments"] if j["metric"] == "유동인구 수준")
        expected_rank = 1 + sum(v > totals["대정읍"] for v in totals.values())
        self.assertIn("43곳 중 %d위" % expected_rank, item["position"])

    def test_bus_collector_sums_all_fare_and_payment_rows(self):
        from tools import fetch_hub_snapshots as fetch
        boarding = [
            {"stationId": "s1", "stationName": "정류장", "moveType": "승차",
             "priceType": "성인", "payType": "카드", "userCount": 30},
            {"stationId": "s1", "stationName": "정류장", "moveType": "승차",
             "priceType": "청소년", "payType": "현금", "userCount": 12},
            {"stationId": "s1", "stationName": "정류장", "moveType": "환승",
             "priceType": "성인", "payType": "카드", "userCount": 99},
        ]
        station = [{"stationId": "s1", "stationName": "정류장", "stationAddress": "제주시 노형동"}]
        def pages(data_key, key, params):
            rows = station if data_key == fetch.API_KEYS[612] else boarding
            return rows, len(rows)
        with tempfile.TemporaryDirectory() as out, patch.object(fetch, "OUT_DIR", Path(out)), \
                patch.object(fetch, "find_latest_data_month", return_value=("20261101", [])), \
                patch.object(fetch, "api_page_all", side_effect=pages):
            result = fetch.collect_bus_station("test-key")
            self.assertTrue(result["ok"])
            with open(Path(out) / "bus_station.csv", encoding="utf-8-sig") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 3)
            self.assertEqual([float(row["board_count"]) for row in rows], [42, 42, 42])

    def test_nohyeong_bus_boardings_are_not_first_row_only(self):
        from jeju_agent.analysis import _scope_of_name
        bus = self.hub.bus_summary(_scope_of_name("노형동"))
        self.assertGreaterEqual(bus["board_mean"], 20)



if __name__ == "__main__":
    unittest.main()
