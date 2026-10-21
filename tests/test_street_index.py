import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from street_index import clean_street, group_streets, street_file_id, street_name_from_address


class StreetIndexTest(unittest.TestCase):
    def test_address_strips_one_house_number(self):
        self.assertEqual(street_name_from_address("הרצל 12"), "הרצל")
        self.assertEqual(street_name_from_address("הרצל 12א"), "הרצל")
        self.assertEqual(street_name_from_address("שדרות בן גוריון 5"), "שדרות בן גוריון")
        self.assertEqual(street_name_from_address("רח' 1002 15"), "רח' 1002")

    def test_clean_street_keeps_a_numeric_street_name(self):
        self.assertEqual(clean_street("  רח'   1002 "), "רח' 1002")
        self.assertEqual(clean_street(None), "")

    def test_median_and_file_id(self):
        grouped = group_streets([
            ("חיפה", "הרצל", 1, 10),
            ("חיפה", "הרצל", 3, 30),
            ("חיפה", "הרצל", 2, 20),
            ("חיפה", "הנמל", 4, 40),
            ("", "הרצל", 1, 1),
            ("חיפה", "", 1, 1),
        ])
        self.assertEqual(grouped["חיפה"][0][:2], ["הרצל", 2])
        self.assertEqual(grouped["חיפה"][0][2:], [20, 3])
        self.assertEqual(grouped["חיפה"][1][0], "הנמל")
        self.assertEqual(len(street_file_id("חיפה")), 16)
        self.assertEqual(street_file_id("חיפה"), street_file_id("חיפה"))


if __name__ == "__main__":
    unittest.main()
