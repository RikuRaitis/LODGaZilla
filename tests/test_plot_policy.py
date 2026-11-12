"""Default-card decisions and lossless minimum-card packing."""

from pathlib import Path
import unittest

from src.data.plot_policy import CARD_TITLES, PlotInput, PlotSource, compatible_cards, gnss_coordinate_sets, plan_cards


class PlotPolicyTests(unittest.TestCase):
    def series(self, name, *, state=False, role="", path="data.h5", group="GNSS"):
        supported = tuple(kind for kind in CARD_TITLES if kind not in {"image", "video"}
                          and not (state and kind == "frequency"))
        return PlotInput(PlotSource("series", Path(path), name), supported, not state, role, group)

    def test_numeric_count_defaults(self):
        for count, kind in ((1, "multi"), (2, "xy"), (3, "xyz"), (4, "multi"), (7, "multi")):
            with self.subTest(count=count):
                items = [self.series(str(i)) for i in range(count)]
                plans = plan_cards(items)
                self.assertEqual([plan.kind for plan in plans], [kind])
                self.assertEqual(plans[0].items, tuple(items))

    def test_states_use_multi_even_when_mixed_with_numeric(self):
        for count in (1, 2, 3, 4):
            items = [self.series("state", state=True)] + [self.series(str(i)) for i in range(count - 1)]
            self.assertEqual(plan_cards(items)[0].kind, "multi")

    def test_gnss_roles_order_and_same_track_requirement(self):
        lat, lon, alt = [self.series(role, role=role) for role in ("lat", "lon", "alt")]
        pair = plan_cards([lon, lat])[0]
        self.assertEqual(pair.kind, "gnss")
        self.assertEqual(pair.items, (lat, lon))
        triple = plan_cards([alt, lon, lat])[0]
        self.assertEqual(triple.kind, "gnss_3d")
        self.assertEqual(triple.items, (lat, lon, alt))
        self.assertEqual(plan_cards([lat, self.series("lon", role="lon", path="other.h5")])[0].kind, "xy")
        self.assertEqual(plan_cards([lat, self.series("lon", role="lon", group="other")])[0].kind, "xy")
        self.assertEqual(plan_cards([lat, lon, self.series("unrelated")])[0].kind, "xyz")
        self.assertEqual(plan_cards([lat, lon, alt, self.series("extra")])[0].kind, "multi")

    def test_explicit_grouped_cards_keep_all_items_in_one_card(self):
        items = [self.series(str(i)) for i in range(7)]
        for kind in ("xy", "xyz", "gnss", "gnss_3d", "line", "event", "multi"):
            plans = plan_cards(items, kind)
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0].items, tuple(items))

    def test_frequency_uses_one_card_per_numeric_source_and_defaults_for_others(self):
        numeric = [self.series(str(i)) for i in range(3)]
        state = self.series("state", state=True)
        image = PlotInput(PlotSource("image", Path("image.png")), ("image",))
        video = PlotInput(PlotSource("video", Path("video.mp4")), ("video",))
        items = numeric + [state, image, video]
        plans = plan_cards(items, "frequency")
        self.assertEqual([plan.kind for plan in plans], ["frequency"] * 3 + ["multi", "image", "video"])
        self.assertCountEqual([item for plan in plans for item in plan.items], items)
        self.assertIn("frequency", compatible_cards(items))

    def test_media_remain_separate_and_do_not_change_numeric_default(self):
        numeric = [self.series("x"), self.series("y")]
        media = [PlotInput(PlotSource("image", Path(name)), ("image",)) for name in ("one.png", "two.png")]
        plans = plan_cards(numeric + media)
        self.assertEqual([plan.kind for plan in plans], ["xy", "image", "image"])
        self.assertCountEqual([item for plan in plans for item in plan.items], numeric + media)

    def test_empty_and_duplicate_selection(self):
        item = self.series("x")
        self.assertEqual(plan_cards([]), [])
        self.assertEqual(plan_cards([item, item])[0].items, (item,))

    def test_gnss_actions_extract_only_complete_tracks_among_other_series(self):
        lat, lon, alt = [self.series(role, role=role) for role in ("lat", "lon", "alt")]
        other_lat = self.series("other_lat", role="lat", group="other")
        other_lon = self.series("other_lon", role="lon", group="other")
        items = [alt, self.series("speed"), lon, lat, other_lon, other_lat, self.series("state", state=True)]
        self.assertEqual(gnss_coordinate_sets(items), {
            "gnss": (lat, lon, other_lat, other_lon), "gnss_3d": (lat, lon, alt),
        })
        self.assertEqual(gnss_coordinate_sets([
            lat, self.series("lon", role="lon", path="other.h5"),
            self.series("lon", role="lon", group="other"),
            self.series("lon", role="lon", state=True),
        ]), {})


if __name__ == "__main__":
    unittest.main()
