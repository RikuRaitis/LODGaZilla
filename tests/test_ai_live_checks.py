"""The live AI tests' case file and scoring, without any AI (tools/run_ai_tests.py)."""
import re
import unittest

from tools.run_ai_tests import DATA, evaluate, load_cases, normalized, outcome, report_text, Run, time_seconds


class CaseFileTests(unittest.TestCase):
    def test_cases_are_well_formed_and_their_small_files_exist(self):
        cases = load_cases()
        ids = [case["id"] for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["data"] and case["checks"])
                if not case.get("big"):
                    for name in case["data"] + case.get("spec", []):
                        self.assertTrue((DATA / name).is_file(), name)
                for check in case["checks"]:
                    self.assertTrue(check["label"])
                    re.compile(check.get("match", ""))
                    for low, high in check.get("ranges", []) + [check.get("samples", [0, 1])]:
                        self.assertLessEqual(low, high)
                if "duration" in case:
                    self.assertLess(case["duration"][0], case["duration"][1])


class EvaluateTests(unittest.TestCase):
    CASE = {"id": "x", "start": ["2026-05-14T07:30"], "duration": [59, 61], "metadata": ["SN-1"], "checks": [
        {"label": "heart rate", "match": "heart|\\bhr\\b", "samples": [60, 60], "ranges": [[60, 220]]},
        {"label": "pressure", "match": "press", "ranges": [[990, 1035], [99000, 103500]]},
        {"label": "two series", "samples": [60, 60], "at_least": 2},
    ]}

    def summary(self, **changes):
        summary = {"time_start": "2026-05-14T07:30:00+00:00", "time_end": "2026-05-14T07:31:00+00:00", "series": [
            {"name": "Sensors/HR", "samples": 60, "min": 70.0, "max": 150.0},
            {"name": "Air_Pressure", "samples": 60, "min": 100500.0, "max": 101000.0}]}
        summary.update(changes)
        return summary

    def test_names_units_counts_times_and_metadata(self):
        self.assertEqual(normalized("Sensors/Heart_rate (bpm)"), " sensors heart rate bpm ")
        results = evaluate(self.CASE, [self.summary()], "serial=SN-1")
        self.assertTrue(all(result["passed"] for result in results), results)
        self.assertEqual(outcome(results), "pass")

    def test_wrong_counts_units_times_or_metadata_fail(self):
        series = [{"name": "HR", "samples": 59, "min": 70.0, "max": 150.0},
                  {"name": "Pressure", "samples": 60, "min": 1005.0, "max": 1010.0}]
        results = {result["label"]: result["passed"] for result in evaluate(
            self.CASE, [self.summary(series=series, time_start="2026-05-14T10:30:00+00:00")], "")}
        self.assertEqual(results, {"heart rate": False, "pressure": True, "two series": False, "start time": False,
                                   "duration": False, "metadata SN-1": False})
        self.assertEqual(outcome([{"passed": False}]), "fail")
        self.assertEqual(outcome([{"passed": False}, {"passed": True}]), "partial")

    def test_relative_times_and_the_report(self):
        self.assertEqual(time_seconds("12.5 s"), 12.5)
        self.assertIsNone(time_seconds(""))
        relative = self.summary(time_start="0 s", time_end="60 s")
        self.assertTrue(evaluate({"checks": [], "duration": [59, 61]}, [relative])[0]["passed"])
        from datetime import datetime
        runs = [Run("x", "Claude", "magic", "pass", cost_usd=0.5),
                Run("x", "Codex", "plugin", "error", message="No importer.")]
        text = report_text(runs, datetime(2026, 11, 13, 9, 0))
        self.assertIn("2 runs: 1 pass, 0 partial, 0 fail, 1 error. Claude's reported cost: $0.50.", text)
        self.assertIn("### x, Codex, plugin: error", text)


if __name__ == "__main__":
    unittest.main()
