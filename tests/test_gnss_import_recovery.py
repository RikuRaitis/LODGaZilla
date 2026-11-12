import unittest
import numpy as np
from src.data.gnss_time import filter_gnss_time_outliers
DAY = 86400

class GnssTimeFilterTests(unittest.TestCase):
    def filter_times(self, times):
        series = {"latitude": {"time": np.array(times), "values": np.arange(len(times))}}
        skipped = filter_gnss_time_outliers(series)
        return series, skipped

    def test_isolated_past_and_future_groups_are_removed(self):
        series, skipped = self.filter_times([-10 * DAY, 2, 1, 0, 10 * DAY, 10 * DAY + 1])
        np.testing.assert_array_equal(series["latitude"]["time"], [0, 1, 2])
        np.testing.assert_array_equal(series["latitude"]["values"], [3, 2, 1])
        self.assertEqual(skipped["latitude"], 3)

    def test_exactly_24_hours_and_continuous_multiple_days_are_kept(self):
        series, skipped = self.filter_times([0, DAY, 2 * DAY, 3 * DAY])
        self.assertEqual(len(series["latitude"]["time"]), 4)
        self.assertEqual(skipped["latitude"], 0)

    def test_duplicate_outlier_channels_do_not_outvote_main_recording(self):
        series = {
            "latitude": {"time": np.array([0, 1, 2]), "values": np.array([60, 61, 62])},
            "altitude": {"time": np.full(10, 10 * DAY), "values": np.ones(10)},
        }
        skipped = filter_gnss_time_outliers(series)
        self.assertNotIn("altitude", series)
        self.assertEqual(skipped["altitude"], 10)
        self.assertEqual(len(series["latitude"]["time"]), 3)

    def test_empty_single_and_equal_size_clusters(self):
        self.assertEqual(filter_gnss_time_outliers({}), {})
        series, skipped = self.filter_times([42])
        self.assertEqual(skipped["latitude"], 0)
        series, skipped = self.filter_times([3 * DAY, 0])
        np.testing.assert_array_equal(series["latitude"]["time"], [0])
