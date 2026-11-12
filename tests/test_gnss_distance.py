import unittest
import numpy as np
from src.contracts.importer import DataParser, add_gnss_distance_series
class Parser(DataParser):
    def can_parse(self, path): return True
    def parse(self, path, progress_callback=None): return {}
class DistanceTests(unittest.TestCase):
    def test_shared_distance_uses_metres_and_segment_boundaries(self):
        parser = Parser("unused", {"sections": [{"name": "GNSS", "series": [
            {"name": name, "group": "GNSS", "unit": "m"} for name in ("distance_2d_m", "distance_3d_m")]}]})
        longitude = np.degrees(3 / 6371008.8)
        for segments, expected in ((None, [0, 5, 10]), ({0.: 0, 1.: 0, 2.: 1}, [0, 5, 5])):
            series = {"GNSS/"+name: {"time": np.array([0., 1., 2.]), "values": np.array(values)}
                      for name, values in (("latitude_deg", [0, 0, 0]),
                          ("longitude_deg", [0, longitude, 2*longitude]), ("altitude_m", [0, 4, 8]))}
            add_gnss_distance_series(parser, series, segment_ids=segments)
            np.testing.assert_allclose(series["GNSS/distance_3d_m"]["values"], expected)
