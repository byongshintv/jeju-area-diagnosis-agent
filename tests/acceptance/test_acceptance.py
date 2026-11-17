"""Acceptance tests for the Jeju commercial-area diagnosis agent (stdlib unittest only).

Run: python -m unittest discover -s tests -t .   (from the app root)
"""
import ast
import json
import os
import sys
import tempfile
import unittest

from tests.acceptance.helpers import (APP_ROOT, AppServer, CENTER_1_08_HEADER, FakeLLM,
                                      center_csv)

SENTINEL_BJD = "가상검증동"          # appears only in uploaded center rows
SENTINEL_AMT = "987654321"


def _walk_py(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in ("python", ".git", "__pycache__", "tests", "runtime")]
        for f in files:
            yield os.path.join(d, f)


class T01_NoNativeDependencies(unittest.TestCase):
    """Center PCs block unsigned .pyd/.dll: the app must run on the official Python alone."""

    def test_no_binary_extension_files(self):
        bad = [p for p in _walk_py(APP_ROOT) if p.lower().endswith((".pyd", ".dll", ".so", ".exe", ".whl"))]
        self.assertEqual(bad, [])

    def test_only_stdlib_and_own_imports(self):
        std = set(getattr(sys, "stdlib_module_names", ()))
        if not std:
            self.skipTest("needs Python 3.10+")
        own = {"jeju_agent", "tests"}
        offenders = []
        for p in _walk_py(APP_ROOT):
            if not p.endswith(".py"):
                continue
            tree = ast.parse(open(p, encoding="utf-8").read(), p)
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                for n in names:
                    top = n.split(".")[0]
                    if top not in std and top not in own:
                        offenders.append((p, n))
        self.assertEqual(offenders, [])


class ServerCase(unittest.TestCase):
    server_kwargs = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = AppServer(self.tmp.name, **self.server_kwargs)

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()


class T02_StatusAndCatalog(ServerCase):
    def test_status_reports_modes_and_datasets(self):
        st = self.app.get("/api/status")
        self.assertIn("llm", st)
        self.assertFalse(st["llm"]["configured"])           # rule mode in this test
        self.assertIn("datasets", st)
        hub = [d for d in st["datasets"] if d["source_kind"] == "hub"]
        self.assertGreaterEqual(len(hub), 4)                # at least 4 Jeju Data Hub datasets usable offline
        for d in st["datasets"]:
            for k in ("id", "name", "provider", "source_kind", "period", "license", "available"):
                self.assertIn(k, d)

    def test_regions_and_industries_dictionaries(self):
        regions = self.app.get("/api/regions")
        names = {r["name"] for r in regions}
        for emd in ("노형동", "애월읍", "대정읍", "서귀동", "일도이동"):
            self.assertIn(emd, names)
        inds = self.app.get("/api/industries")
        self.assertTrue(any("카페" in (i["name"] + " ".join(i.get("aliases", []))) for i in inds))


class T03_CenterCsvUpload(ServerCase):
    def test_recognises_center_card_v02_and_na(self):
        rows = [["202607", "서귀포시 서부", "서귀포시", "읍면", "대정읍", "I56111", "한식 음식점업",
                 "내국인", "단체", "60", "여성", "40", "52", "1000000"],
                ["202607", "서귀포시 서부", "서귀포시", "읍면", "대정읍", "I56220", "비알콜 음료점업",
                 "제주도민", "도민", "30", "남성", "NA", "12", "250000"]]
        res = self.app.post("/api/upload", raw=center_csv(CENTER_1_08_HEADER, rows),
                            headers={"content-type": "text/csv", "x-filename": "Results_20261117_101500.csv"})
        self.assertEqual(res["dataset_id"], "center:1-08")
        self.assertEqual(res["rows"], 2)
        self.assertEqual(res["missing"]["user_cnt"], 1)

    def test_unknown_header_is_rejected_politely(self):
        res = self.app.post("/api/upload", raw=b"a,b\n1,2\n",
                            headers={"content-type": "text/csv", "x-filename": "x.csv"})
        self.assertFalse(res["ok"])
        self.assertTrue(res["message"])


class T04_ExportCheck(ServerCase):
    def test_small_cells_ratios_and_back_calculation(self):
        table = {"columns": ["업종", "이용자수", "이용건수", "이용금액"],
                 "rows": [["카페", 120, 300, 4500000],
                          ["제과점", 7, 25, 300000],          # 이용자수 7 < 10
                          ["편의점", 45, 9, 90000]],          # 이용건수 9 < 10
                 "total_row": ["합계", 172, 334, 4890000]}
        res = self.app.post("/api/export-check", {"table": table})
        cells = {(c["row"], c["col"]): c for c in res["suppressed"]}
        self.assertIn((1, 1), cells)
        self.assertIn((2, 2), cells)
        # with the total published, one more cell per affected column must be hidden
        cols_with_secondary = {c["col"] for c in res["suppressed"] if c["reason"] == "back_calculation"}
        self.assertTrue({1, 2} <= cols_with_secondary or res["total_hidden"])
        self.assertIn("safe_table", res)
        self.assertTrue(res["notice"])                       # says final decision is the centre's review

    def test_derived_values_under_ten(self):
        table = {"columns": ["읍면동", "1인당 이용건수"], "rows": [["노형동", 1.3], ["연동", 12.5]]}
        res = self.app.post("/api/export-check", {"table": table})
        self.assertIn((0, 1), {(c["row"], c["col"]) for c in res["suppressed"]})


class T05_RuleModeDiagnosis(ServerCase):
    def test_end_to_end_without_llm(self):
        st = self.app.chat("노형동에서 카페 창업하면 어때?")
        self.assertEqual(st["status"], "done")
        r = st["result"]
        self.assertEqual(r["mode"], "rule")
        self.assertTrue(r["answer_markdown"].strip())
        self.assertGreaterEqual(len(r["steps"]), 3)
        used = {d for s in r["steps"] for d in s.get("datasets", [])}
        self.assertGreaterEqual(len(used), 3)               # combines at least three datasets
        kinds = {a["type"] for a in r["artifacts"]}
        self.assertTrue({"chart", "table"} <= kinds)
        self.assertTrue(r["report_id"])
        rep = self.app.get("/api/report/" + r["report_id"])
        self.assertTrue(rep["sources"])
        for s in rep["sources"]:
            for k in ("dataset", "provider", "period"):
                self.assertIn(k, s)
        self.assertTrue(rep["limitations"])

    def test_unknown_region_asks_back(self):
        st = self.app.chat("화성시 동탄에서 카페?")
        self.assertEqual(st["status"], "done")
        self.assertTrue(st["result"].get("clarification"))


class T06_LLMModeAndPrivacy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _upload_sentinel(self, app):
        rows = [["202607", "제주시 서부", "제주시", "도심", SENTINEL_BJD, "I56220", "비알콜 음료점업",
                 "내국인", "개별", "30", "여성", "3", "4", SENTINEL_AMT]]
        app.post("/api/upload", raw=center_csv(CENTER_1_08_HEADER, rows),
                 headers={"content-type": "text/csv", "x-filename": "Results_20261117_101500.csv"})

    def test_llm_drives_tools_and_never_sees_raw_center_rows(self):
        st0 = None
        fake = FakeLLM([
            {"tool_calls": [("resolve_region", {"text": "노형동"})]},
            {"tool_calls": [("diagnose_area", {"region": "노형동", "industry": "카페"})]},
            {"content": "노형동 카페 진단 요약입니다."},
        ])
        app = AppServer(self.tmp.name, llm_base_url=fake.base_url, llm_api_key="test", llm_model="fake")
        try:
            self._upload_sentinel(app)
            st0 = app.chat("노형동 카페 어때?")
        finally:
            app.close()
            fake.close()
        self.assertEqual(st0["status"], "done")
        r = st0["result"]
        self.assertEqual(r["mode"], "llm")
        tools_run = [s["tool"] for s in r["steps"]]
        self.assertIn("resolve_region", tools_run)
        self.assertIn("diagnose_area", tools_run)
        sent = json.dumps(fake.requests, ensure_ascii=False)
        self.assertNotIn(SENTINEL_BJD, sent)
        self.assertNotIn(SENTINEL_AMT, sent)
        self.assertTrue(fake.requests[0].get("tools"))       # tool schemas offered to the model

    def test_llm_failure_falls_back_to_rule_mode(self):
        fake = FakeLLM([], fail=True)
        app = AppServer(self.tmp.name, llm_base_url=fake.base_url, llm_api_key="test", llm_model="fake")
        try:
            st = app.chat("애월읍 한식당 창업 괜찮을까?")
        finally:
            app.close()
            fake.close()
        self.assertEqual(st["status"], "done")
        self.assertEqual(st["result"]["mode"], "rule")
        self.assertTrue(st["result"].get("notice"))


if __name__ == "__main__":
    unittest.main()
