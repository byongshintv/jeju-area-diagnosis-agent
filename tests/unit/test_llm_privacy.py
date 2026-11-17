"""LLM 모드 경계·파싱 단위 테스트(네트워크 없음)."""
import json
import unittest

from jeju_agent import agent_llm
from jeju_agent.analysis import _result


class CenterPayloadTest(unittest.TestCase):
    def _ctx(self, mode="off"):
        class Ctx:
            config = {"center_to_llm": mode}
        return Ctx()

    def test_off_mode_hides_center_numbers_but_keeps_hub_lines(self):
        result = _result("노형동 카페 상권 진단\n[경쟁] 점포 180개(hub:793).\n"
                         "[수요] 카드 이용금액 월평균 123,456,789원(센터 데이터).",
                         datasets=["hub:793", "center:1-08"],
                         data={"demand": ["카드 이용금액은 월평균 123,456,789원입니다(센터 데이터)."]})
        payload = json.loads(agent_llm.tool_message_payload(self._ctx("off"), result))
        self.assertNotIn("123,456,789", payload["summary"])
        self.assertIn("180개", payload["summary"])
        self.assertIn("화면에 별도 표시", payload["summary"])

    def test_aggregates_mode_requires_clean_export_check(self):
        result = _result("센터 집계 12,345원",
                         datasets=["center:1-08"],
                         data={"export_check": {"suppressed": [], "total_hidden": False}})
        payload = json.loads(agent_llm.tool_message_payload(self._ctx("aggregates"), result))
        self.assertIn("12,345", payload["summary"])

        blocked = _result("센터 집계 3원", datasets=["center:1-08"],
                          data={"export_check": {"suppressed": [{"row": 0, "col": 1}],
                                                "total_hidden": False}})
        payload = json.loads(agent_llm.tool_message_payload(self._ctx("aggregates"), blocked))
        self.assertNotIn("3원", payload["summary"])

    def test_parse_call_tolerates_bad_arguments(self):
        name, args = agent_llm._parse_call({"function": {"name": "resolve_region",
                                                         "arguments": "not json"}})
        self.assertEqual(name, "resolve_region")
        self.assertEqual(args, {})
        name, args = agent_llm._parse_call({"function": {"name": "diagnose_area",
                                                         "arguments": '{"region": "노형동"}'}})
        self.assertEqual(args, {"region": "노형동"})


class UsageTest(unittest.TestCase):
    def test_usage_is_summed_across_calls(self):
        from jeju_agent.llm import sum_usage

        total = sum_usage([{"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                           {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}])
        self.assertEqual(total["total_tokens"], 20)
        self.assertEqual(total["calls"], 2)


if __name__ == "__main__":
    unittest.main()


class StepLogArgsTest(unittest.TestCase):
    def test_nested_session_values_are_not_logged(self):
        from jeju_agent.agent_llm import _short_args
        text = _short_args({"session": {"diagnosis": {"demand": ["센터 금액 987654321원"]}},
                            "region": "노형동", "metrics": [{"col": "use_amt"}]})
        self.assertNotIn("987654321", text)
        self.assertIn("region=노형동", text)
        self.assertIn("session={1개 항목}", text)
        self.assertIn("metrics=[1개]", text)
