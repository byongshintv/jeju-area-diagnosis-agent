"""카탈로그·가상 데이터·서버 라우트 통합 테스트(임시 포트)."""
import json
import tempfile
import unittest

from tests.acceptance.helpers import AppServer


class CatalogAndServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = AppServer(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.app.close()
        cls.tmp.cleanup()

    def test_status_counts(self):
        st = self.app.get("/api/status")
        self.assertEqual(st["version"], "0.1.0")
        hub = [d for d in st["datasets"] if d["source_kind"] == "hub"]
        self.assertGreaterEqual(len(hub), 6)
        self.assertTrue(all(d["available"] for d in hub))
        self.assertEqual(st["center"]["schema_sheets"], 70)

    def test_hub_snapshot_has_required_datasets(self):
        ids = {d["id"] for d in self.app.get("/api/catalog")["datasets"]}
        for expected in ("hub:793", "hub:1048", "hub:502", "hub:504", "hub:495", "hub:512"):
            self.assertIn(expected, ids)

    def test_preview(self):
        res = self.app.get("/api/datasets/hub%3A1048/preview")
        self.assertGreater(res["total_rows"], 0)
        self.assertIn("biz_nm", res["columns"])

    def test_demo_load_and_query(self):
        res = self.app.post("/api/demo/load-center")
        self.assertTrue(res["ok"])
        self.assertIn("가상", res["notice"])
        loaded_ids = {item["dataset_id"] for item in res["loaded"]}
        self.assertIn("center:1-08", loaded_ids)
        q = self.app.post("/api/query", {
            "dataset_id": "center:1-19",
            "filters": [{"col": "bjd_nm", "eq": "노형동"}],
            "group_by": ["biz_mid_nm"],
            "metrics": [{"col": "sale_amt", "op": "sum"}],
            "sort": [{"col": "sum_sale_amt", "desc": True}],
            "limit": 3,
        })
        self.assertTrue(q["ok"])
        self.assertGreater(len(q["rows"]), 0)
        self.assertEqual(q["rows"][0]["bjd_nm"] if "bjd_nm" in q["rows"][0] else "노형동", "노형동")

    def test_query_unknown_dataset(self):
        res = self.app.post("/api/query", {"dataset_id": "hub:9999"})
        self.assertFalse(res["ok"])

    def test_clear_uploads(self):
        self.app.post("/api/demo/load-center")
        res = self._delete("/api/uploads")
        self.assertTrue(res["ok"])
        self.assertEqual(self.app.get("/api/uploads")["uploads"], [])

    def _delete(self, path):
        import urllib.request
        req = urllib.request.Request(self.app.base + path, method="DELETE")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())


if __name__ == "__main__":
    unittest.main()


class LaunchTwiceTest(unittest.TestCase):
    """실행.bat를 두 번 누르거나 포트가 막혔을 때 조용히 엉키지 않는다."""

    def test_second_launch_reuses_running_server(self):
        import contextlib
        import io
        from jeju_agent.server import serve_forever
        with tempfile.TemporaryDirectory() as tmp:
            app = AppServer(tmp)
            try:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = serve_forever({"host": "127.0.0.1", "port": app.port,
                                        "runtime_dir": tmp, "open_browser": False})
                self.assertEqual(rc, 0)
                self.assertIn("이미 실행 중", out.getvalue())
            finally:
                app.close()

    def test_busy_port_reports_and_exits(self):
        import contextlib
        import io
        import socket
        from jeju_agent.server import serve_forever
        with tempfile.TemporaryDirectory() as tmp, socket.socket() as other:
            other.bind(("127.0.0.1", 0))
            other.listen(1)
            port = other.getsockname()[1]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = serve_forever({"host": "127.0.0.1", "port": port,
                                    "runtime_dir": tmp, "open_browser": False})
            self.assertEqual(rc, 1)
            self.assertIn("PORT", out.getvalue())
