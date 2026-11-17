"""테이블 유틸 단위 테스트."""
import unittest

from jeju_agent.table_ops import (apply_filters, group_rows, percentile_rank, query_table,
                                  safe_div, sort_result, to_number)


ROWS = [
    {"emd": "노형동", "biz": "카페", "use_amt": 100, "user_cnt": 5},
    {"emd": "노형동", "biz": "한식", "use_amt": 300, "user_cnt": 7},
    {"emd": "연동", "biz": "카페", "use_amt": 200, "user_cnt": "NA"},
    {"emd": "연동", "biz": "한식", "use_amt": 500, "user_cnt": 3},
]


class TableOpsTest(unittest.TestCase):
    def test_to_number(self):
        self.assertEqual(to_number("1,234"), 1234.0)
        self.assertEqual(to_number(" -3.5 "), -3.5)
        self.assertIsNone(to_number("NA"))
        self.assertIsNone(to_number(""))
        self.assertIsNone(to_number("카페"))

    def test_filters(self):
        self.assertEqual(len(apply_filters(ROWS, [{"col": "emd", "eq": "노형동"}])), 2)
        self.assertEqual(len(apply_filters(ROWS, {"col": "biz", "in": ["카페"]})), 2)
        self.assertEqual(len(apply_filters(ROWS, [{"col": "use_amt", "gte": 300}])), 2)
        self.assertEqual(len(apply_filters(ROWS, [{"col": "biz", "contains": "카"}])), 2)
        self.assertEqual(len(apply_filters(ROWS, [{"col": "emd", "ne": "연동"}])), 2)

    def test_group_and_aggregate(self):
        result = query_table(ROWS, group_by=["emd"], metrics=[
            {"col": "use_amt", "op": "sum"},
            {"col": "user_cnt", "op": "count"},
            {"col": "user_cnt", "op": "mean"},
        ], sort=[{"col": "sum_use_amt", "desc": True}])
        self.assertEqual([r["emd"] for r in result["rows"]], ["연동", "노형동"])
        self.assertEqual(result["rows"][0]["sum_use_amt"], 700)
        self.assertEqual(result["rows"][0]["count_user_cnt"], 1)   # NA 제외
        self.assertEqual(result["rows"][1]["mean_user_cnt"], 6.0)

    def test_group_rows_preserves_order(self):
        groups = group_rows(ROWS, "biz")
        self.assertEqual(list(groups), ["카페", "한식"])

    def test_sort_mixed_types(self):
        rows = [{"v": "NA"}, {"v": 2}, {"v": 10}, {"v": 1}]
        ordered = sort_result(rows, [{"col": "v", "desc": True}])
        self.assertEqual([r["v"] for r in ordered], [10, 2, 1, "NA"])

    def test_percentile_and_safe_div(self):
        self.assertEqual(percentile_rank([1, 2, 3, 4, 5], 3), 50.0)
        self.assertIsNone(percentile_rank([], 3))
        self.assertEqual(safe_div(10, 4), 2.5)
        self.assertIsNone(safe_div(10, 0))

    def test_limit_and_metadata(self):
        out = query_table(ROWS, filters=[{"col": "biz", "eq": "카페"}], limit=1)
        self.assertEqual(len(out["rows"]), 1)
        self.assertEqual(out["matched"], 2)
        self.assertEqual(out["total_input"], 4)


if __name__ == "__main__":
    unittest.main()
