"""Equal scale for the 3D plot: one metre the same length on every axis."""
import math
import unittest

from src.data.axis_scale import (
    METRES_PER_DEGREE_LATITUDE, METRES_PER_DEGREE_LONGITUDE, clamp_stretch, equal_scale_factors, geographic_roles, is_degree_unit,
    is_latitude_name, is_longitude_name,
)


class EqualScaleTests(unittest.TestCase):
    def test_lengths_in_any_unit_are_compared_in_metres(self):
        factors, reason = equal_scale_factors([0, 0, 0], [2, 500, 10_000], ["km", "m", "cm"], ["", "", ""])
        self.assertEqual(reason, "")
        self.assertEqual(factors, (1.0, 0.25, 0.05))
        # A tiny axis keeps the smallest stretch, so it stays visible.
        self.assertEqual(equal_scale_factors([0, 0, 0], [2, 1, 1], ["km", "m", "mm"], ["", "", ""])[0][2], 0.001)

    def test_latitude_and_longitude_are_measured_in_metres(self):
        factors, _ = equal_scale_factors([60.0, 24.0, 0], [60.01, 24.02, 50], ["deg", "°", "m"],
                                         ["latitude", "longitude", ""])
        lat_m = 0.01 * METRES_PER_DEGREE_LATITUDE
        lon_m = 0.02 * METRES_PER_DEGREE_LONGITUDE * math.cos(math.radians(60.005))
        self.assertAlmostEqual(factors[0], lat_m / lon_m)
        self.assertEqual(factors[1], 1.0)
        self.assertAlmostEqual(factors[2], 50 / lon_m)

    def test_two_degree_axes_with_a_length_are_estimated_as_latitude_and_longitude(self):
        # No recognisable names: X is taken as the longitude and Y as the latitude, as on a map.
        factors, reason = equal_scale_factors([24.0, 60.0, 0], [24.02, 60.01, 50], ["°E", "deg N", "m"], ["", "", ""])
        self.assertEqual(reason, "")
        lat_m = 0.01 * METRES_PER_DEGREE_LATITUDE
        lon_m = 0.02 * METRES_PER_DEGREE_LONGITUDE * math.cos(math.radians(60.005))
        self.assertAlmostEqual(factors[1], lat_m / lon_m)
        self.assertAlmostEqual(factors[2], 50 / lon_m)
        # One recognised name decides which is which.
        self.assertEqual(geographic_roles(["deg", "deg", "m"], ["latitude", "", ""]), ["latitude", "longitude", ""])
        self.assertEqual(geographic_roles(["deg", "deg", "m"], ["", "longitude", ""]), ["latitude", "longitude", ""])
        # A single degree axis, such as a heading, is not a distance.
        self.assertEqual(geographic_roles(["deg", "m", "m"], ["", "", ""]), ["", "", ""])

    def test_axes_that_are_not_distances_are_named(self):
        cases = (
            (["s", "m", "m"], ["time", "", ""], "X is time, not a distance"),
            (["m", "m/s²", "m"], ["", "", ""], "Y is in m/s², not a distance"),
            (["deg", "m", "m"], ["", "", ""], "X is in deg, not a distance"),
            (["m", "deg", "m"], ["", "longitude", ""], "Y is a longitude, which needs a latitude axis"),
        )
        for units, roles, reason in cases:
            self.assertEqual(equal_scale_factors([0, 0, 0], [1, 1, 1], units, roles), (None, reason))
        # Temperatures in degC are not degrees of latitude or longitude.
        self.assertEqual(equal_scale_factors([0, 0, 0], [1, 1, 1], ["degC", "degC", "m"], ["", "", ""])[1],
                         "X is in degC, not a distance")

    def test_fixed_point_latitude_and_longitude_are_read_as_the_map_reads_them(self):
        # As some loggers store them: degrees x 10^7 with the unit "raw", and altitude in metres.
        raw, _ = equal_scale_factors([240_000_000, 607_300_000, 0], [240_200_000, 607_400_000, 50],
                                     ["raw", "raw", "m"], ["longitude", "latitude", ""])
        degrees, _ = equal_scale_factors([24.0, 60.73, 0], [24.02, 60.74, 50], ["deg", "deg", "m"],
                                         ["longitude", "latitude", ""])
        for raw_factor, degree_factor in zip(raw, degrees):
            self.assertAlmostEqual(raw_factor, degree_factor)
        # Without a unit, the size of the values tells.
        unlabelled, _ = equal_scale_factors([240_000_000, 607_300_000, 0], [240_200_000, 607_400_000, 50],
                                            ["", "", "m"], ["longitude", "latitude", ""])
        self.assertAlmostEqual(unlabelled[2], degrees[2])
        self.assertTrue(is_degree_unit("deg N") and is_degree_unit("°E") and not is_degree_unit("degC"))

    def test_names_and_limits(self):
        self.assertTrue(is_latitude_name("GNSS/latitude_deg") and is_latitude_name("Position/lat"))
        self.assertTrue(is_longitude_name("GNSS/Longitude") and not is_longitude_name("GNSS/latitude"))
        self.assertEqual((clamp_stretch(0), clamp_stretch(100), clamp_stretch(2)), (0.001, 20.0, 2.0))


if __name__ == "__main__":
    unittest.main()
