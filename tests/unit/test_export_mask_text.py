"""반출 점검이 근거 표에서 가린 값은 진단 문장·핵심 지표·비교 막대·보고서 본문에도 남지 않는다."""
import tempfile
import unittest

from jeju_agent.analysis import DataHub, MASKED_TEXT, build_report_tool, diagnose_area_tool
from jeju_agent.catalog import Catalog, load_dummy_tables
from jeju_agent.center_data import UploadStore


class ExportMaskTextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        store = UploadStore(cls.tmp.name)
        catalog = Catalog(None, store)
        for no, table in load_dummy_tables(catalog.dummy_dir).items():
            store.add_demo(no, table["meta"], table["header"], table["rows"])
        cls.hub = DataHub(catalog)
        cls.result = diagnose_area_tool(cls.hub, "추자면", "편의점")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_store_count_masked_in_table(self):
        evidence = next(a for a in self.result["artifacts"] if a["id"] == "evidence")
        row = next(r for r in evidence["rows"] if r[0] == "동종 점포 수")
        self.assertIsNone(row[1])

    def test_masked_value_absent_from_sentences_and_summary(self):
        data = self.result["data"]
        competition = " ".join(data["competition"])
        self.assertIn(MASKED_TEXT, competition)
        self.assertNotRegex(competition, r"점포\(상가정보 기준\) \d")
        self.assertNotRegex(self.result["summary_for_llm"], r"동종 점포\(상가정보 기준\) \d")
        row = next(r for r in data["evidence_rows"] if r[0] == "동종 점포 수")
        self.assertIsNone(row[1])

    def test_masked_value_absent_from_kpi_and_bar(self):
        kpi = next(a for a in self.result["artifacts"] if a["id"] == "kpi")
        item = next(i for i in kpi["items"] if i["label"] == "동종 점포 수")
        self.assertEqual(item["value"], "가림")
        bar = next(a for a in self.result["artifacts"] if a["id"] == "competition_bar")
        own = next(p for p in bar["series"][0]["points"] if p["name"] == "추자면")
        self.assertIsNone(own["value"])

    def test_report_body_keeps_mask(self):
        r = self.result
        report = build_report_tool(self.hub, {"question": "추자면 편의점", "diagnosis": r["data"],
                                              "artifacts": r["artifacts"]})["data"]["report"]
        body = "\n".join(s["markdown"] for s in report["sections"])
        self.assertIn(MASKED_TEXT, body)
        self.assertNotRegex(body, r"점포\(상가정보 기준\) \d")

    def test_unmasked_region_keeps_values(self):
        r = diagnose_area_tool(self.hub, "노형동", "카페")
        self.assertFalse(r["data"]["export_check"]["suppressed"])
        self.assertNotIn(MASKED_TEXT, r["summary_for_llm"])
        self.assertRegex(" ".join(r["data"]["competition"]), r"점포\(상가정보 기준\) [\d,]+개")


if __name__ == "__main__":
    unittest.main()


class ExportCasesToolTest(unittest.TestCase):
    def test_all_documented_cases_pass(self):
        import contextlib
        import io
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
        import export_cases
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(export_cases.main([]), 0)
        self.assertNotIn("실패", out.getvalue())
