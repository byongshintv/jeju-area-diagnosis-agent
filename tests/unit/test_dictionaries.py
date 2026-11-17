"""지역·업종 사전 단위 테스트."""
import unittest

from jeju_agent.dictionaries import (REGION_LIST, get_industries, legal_to_admin,
                                     resolve_industry, resolve_region)


class RegionTest(unittest.TestCase):
    def test_43_admin_dongs_present(self):
        admin = [r for r in REGION_LIST if r["level"] == "행정동"]
        self.assertEqual(len(admin), 43)
        names = {r["name"] for r in REGION_LIST}
        for expected in ("노형동", "애월읍", "대정읍", "서귀동", "일도이동", "우도면", "추자면"):
            self.assertIn(expected, names)
        # 서귀동은 법정동이고 행정동은 송산·정방·중앙·천지동이다.
        self.assertEqual([r["name"] for r in REGION_LIST if r["name"] == "서귀동"],
                         ["서귀동"])
        self.assertNotIn("서귀동", {r["name"] for r in admin})

    def test_legal_dongs_73(self):
        from jeju_agent.dictionaries import LEGAL_TO_ADMIN

        self.assertEqual(len(LEGAL_TO_ADMIN), 73)

    def test_alias_and_legal_lookup(self):
        self.assertEqual(resolve_region("노형"), ("노형동", []))
        self.assertEqual(resolve_region("일도이동"), ("일도이동", []))
        self.assertEqual(legal_to_admin("서귀동"), ["송산동", "정방동", "중앙동", "천지동"])

    def test_unknown_region(self):
        self.assertEqual(resolve_region("화성시 동탄"), (None, []))

    def test_district_expands(self):
        name, members = resolve_region("제주시 서부")
        self.assertEqual(name, "제주시 서부")
        self.assertIn("애월읍", members)


class IndustryTest(unittest.TestCase):
    def test_cafe_aliases(self):
        name, _ = resolve_industry("커피")
        self.assertEqual(name, "카페")
        cafe = [i for i in get_industries() if i["name"] == "카페"][0]
        self.assertIn("I56220", cafe["card_codes"])
        self.assertTrue(cafe["aliases"])

    def test_other_industries(self):
        for text, expected in (("빵집", "제과점/베이커리"), ("편의점", "편의점"),
                               ("미용실", "미용실"), ("숙박", "숙박"), ("한식", "한식")):
            self.assertEqual(resolve_industry(text)[0], expected)

    def test_industry_shape(self):
        for ind in get_industries():
            for key in ("name", "aliases", "card_codes", "store_categories", "pos_codes"):
                self.assertIn(key, ind)


if __name__ == "__main__":
    unittest.main()
