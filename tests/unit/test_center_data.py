"""센터 명세 스키마·CSV 판별 단위 테스트."""
import unittest

from jeju_agent.center_data import (UploadStore, match_dataset, parse_header_and_rows,
                                    decode_bytes)
from jeju_agent.center_schema import BY_NO, CENTER_DATASETS, HEADER_INDEX

CARD_HEADER = ["base_year_month", "dstrct_se", "city", "area_type", "bjd", "biz_cd", "biz_nm",
               "user_type", "tour_type", "age", "sex", "user_cnt", "use_cnt", "use_amt"]


def center_csv(header, rows):
    lines = [",".join(header)]
    for row in rows:
        lines.append(",".join('"%s"' % ("NA" if v is None else v) for v in row))
    return ("\n".join(lines) + "\n").encode("utf-8")


class SchemaTest(unittest.TestCase):
    def test_all_70_sheets_present(self):
        self.assertEqual(len(CENTER_DATASETS), 70)
        nos = [d["no"] for d in CENTER_DATASETS]
        self.assertEqual(nos[0], "1-01")
        self.assertIn("1-08", nos)
        self.assertIn("5-06", nos)

    def test_every_column_has_korean_desc_and_numeric_flag(self):
        for ds in CENTER_DATASETS:
            self.assertTrue(ds["columns"], ds["no"])
            for col in ds["columns"]:
                self.assertIn(col["type"], ("number", "text"))
                self.assertTrue(col["desc"])
                self.assertNotEqual(col["desc"], "", ds["no"])
        card = BY_NO["1-08"]
        types = {c["name"]: c["type"] for c in card["columns"]}
        self.assertEqual(types["use_amt"], "number")
        self.assertEqual(types["bjd"], "text")
        self.assertEqual(types["biz_cd"], "text")

    def test_numeric_inference_on_known_sheets(self):
        store = {c["name"]: c["type"] for c in BY_NO["1-17"]["columns"]}
        self.assertEqual(store["store_cnt"], "number")
        menu = {c["name"]: c["type"] for c in BY_NO["1-24"]["columns"]}
        self.assertEqual(menu["unit_price"], "number")
        self.assertEqual(menu["menu_sub_cd"], "text")


class CsvDetectTest(unittest.TestCase):
    def test_detect_card_v02(self):
        self.assertEqual(match_dataset(CARD_HEADER)[0], "1-08")

    def test_bom_and_cp949_accepted(self):
        raw = b"\xef\xbb\xbf" + center_csv(CARD_HEADER, [])
        text, enc = decode_bytes(raw)
        self.assertEqual(enc, "utf-8-sig")
        self.assertTrue(text.startswith("base_year_month"))
        cp949 = ("base_year_month,dstrct_se\n202601,제주시 동부\n").encode("cp949")
        text, enc = decode_bytes(cp949)
        self.assertEqual(enc, "cp949")

    def test_na_becomes_none(self):
        header, rows = parse_header_and_rows(
            "base_year_month,bjd,user_cnt\n202607,노형동,NA\n202607,노형동,NULL\n")
        self.assertEqual(rows[0]["user_cnt"], None)
        self.assertEqual(rows[1]["user_cnt"], None)

    def test_disambiguate_same_header_by_value(self):
        header = ["base_year_month", "province", "market_type", "user_type",
                  "amount_per_person", "amount_per_case"]
        self.assertEqual(match_dataset(header, [{"user_type": "중국인관광객"}])[0], "1-05")
        self.assertEqual(match_dataset(header, [{"user_type": "내국인관광객"}])[0], "1-02")

    def test_unknown_header_rejected(self):
        no, score = match_dataset(["a", "b"])
        self.assertIsNone(no)
        self.assertLess(score, 0.9)

    def test_upload_store_roundtrip(self):
        store = UploadStore(None)
        result = store.add(center_csv(CARD_HEADER, [
            ["202607", "제주시 중부(서)", "제주시", "도심", "노형동", "I56220", "비알콜 음료점업",
             "제주도민", "도민", "30", "여성", "10", "20", "300000"]]), "x.csv")
        self.assertTrue(result["ok"])
        self.assertEqual(result["dataset_id"], "center:1-08")
        item = store.get(result["upload_id"])
        self.assertEqual(item["rows"][0]["use_amt"], 300000)
        self.assertEqual(store.by_dataset("center:1-08")["meta"]["rows"], 1)
        self.assertEqual(store.clear(), 1)
        self.assertEqual(len(store), 0)

    def test_reupload_replaces_same_dataset(self):
        store = UploadStore(None)
        store.add_demo("1-08", {"name": "가상"}, CARD_HEADER, [{"use_amt": 1}])
        row = ["202607", "제주시 중부(서)", "제주시", "도심", "노형동", "I56220", "비알콜 음료점업",
               "제주도민", "도민", "30", "여성", "10", "20", "300000"]
        store.add(center_csv(CARD_HEADER, [row]), "a.csv")
        second = store.add(center_csv(CARD_HEADER, [row, row]), "b.csv")
        self.assertEqual(len(store), 1)
        self.assertEqual(store.by_dataset("center:1-08")["meta"]["upload_id"], second["upload_id"])
        self.assertEqual(store.by_dataset("center:1-08")["meta"]["rows"], 2)


if __name__ == "__main__":
    unittest.main()
