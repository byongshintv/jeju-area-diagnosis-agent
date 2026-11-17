"""동봉 도구의 --help는 파일·네트워크를 건드리지 않고 종료 코드 0으로 끝난다."""
import subprocess
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]


class ToolsHelpTest(unittest.TestCase):
    def test_help_exits_zero(self):
        for tool in ("export_cases.py", "run_examples.py", "fetch_hub_snapshots.py"):
            with self.subTest(tool=tool):
                done = subprocess.run([sys.executable, str(APP_ROOT / "tools" / tool), "--help"],
                                      capture_output=True, timeout=60)
                self.assertEqual(done.returncode, 0)
                self.assertIn(b"usage", done.stdout.lower())


if __name__ == "__main__":
    unittest.main()
