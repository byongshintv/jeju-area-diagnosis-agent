"""허브 재수집 도구: 미리보기(--dry-run)는 동봉 스냅샷을 건드리지 않는다."""
import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP_ROOT / "tools"))
import fetch_hub_snapshots as fetch  # noqa: E402


class FetchDryRunTest(unittest.TestCase):
    def test_dry_run_leaves_snapshot_readme(self):
        readme = fetch.OUT_DIR / "README.md"
        before = readme.read_bytes()
        env = {k: v for k, v in os.environ.items() if k != "HUB_API_KEY"}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(fetch, "http_get", side_effect=AssertionError("network")), \
                patch.object(fetch.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fetch.main(["--dry-run"]), 0)
        self.assertEqual(readme.read_bytes(), before)

    def test_skipped_api_rows_keep_previous_meta(self):
        kept = {r["slug"]: r for r in fetch.readme_from_metas()}
        self.assertEqual(kept["floating_pop_emd"]["hub_id"], 583)
        self.assertTrue(kept["bus_station"]["ok"])


if __name__ == "__main__":
    unittest.main()
