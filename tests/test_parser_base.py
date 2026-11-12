import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from src.data.parser_base import DataParser, ProgressReporter, SeriesDefinition  # noqa: E402


class _Parser(DataParser):
    def can_parse(self, input_path):  # type: ignore[no-untyped-def]
        return False

    def parse(self, input_path, progress_callback=None):  # type: ignore[no-untyped-def]
        return {}


class ParserBaseTests(unittest.TestCase):
    def test_resolved_definition_uses_the_same_canonical_payload(self):
        parser = _Parser("unused.json", {"sections": []})
        parser.navigator_metadata = [{"id": "default"}]
        definition = SeriesDefinition(
            name="speed",
            navigator_group="GNSS",
            unit="m/s",
            max_value=20,
        )

        key, payload = parser.make_series_from_definition(
            definition, [1, 2], [3.0, 4.0], source="stream"
        )

        self.assertEqual(key, "GNSS/speed")
        self.assertEqual(payload["source"], "stream")
        self.assertEqual(payload["navigator_metadata"], [{"id": "default"}])
        self.assertEqual(payload["max_value"], 20)
        self.assertEqual(payload["time"].tolist(), [1.0, 2.0])

    def test_progress_reporter_throttles_and_forces_completion(self):
        received: list[float] = []
        reporter = ProgressReporter(received.append, interval_seconds=1.0)
        with mock.patch("src.data.parser_base.time.monotonic", side_effect=[0.0, 0.2, 1.1, 1.2]):
            reporter.report(0.2)
            reporter.report(0.6)
            reporter.report(0.5)
            reporter.finish()

        self.assertEqual(received, [0.2, 0.6, 1.0])


if __name__ == "__main__":
    unittest.main()
