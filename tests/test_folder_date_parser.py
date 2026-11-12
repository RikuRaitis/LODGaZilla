import sys
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from src.data.folder_date_parser import find_folder_date, parse_folder_date  # noqa: E402


class FolderDateParserTests(unittest.TestCase):
    def test_parses_current_folder_name_format(self):
        parsed = parse_folder_date("20260612_065153")

        self.assertEqual(parsed.timestamp, datetime(2026, 6, 12, 6, 51, 53))
        self.assertTrue(parsed.is_known)

    def test_invalid_folder_name_is_unknown(self):
        parsed = parse_folder_date("TestData2")

        self.assertIsNone(parsed.timestamp)
        self.assertFalse(parsed.is_known)

    def test_finds_closest_dated_parent(self):
        root = Path("C:/data")
        file_path = root / "20260612_065153" / "nested" / "cache.h5"

        parsed = find_folder_date(file_path, root)

        self.assertEqual(parsed.timestamp, datetime(2026, 6, 12, 6, 51, 53))


if __name__ == "__main__":
    unittest.main()
