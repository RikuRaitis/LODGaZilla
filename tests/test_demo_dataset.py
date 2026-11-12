"""The synthetic demo data generator: simulation, diagnostics, files and the 360 lens model."""
import json
import math
import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np

from src.data.navigator_data import read_h5_navigator_data
from src.data.workspace_file import read_workspace_file
from tools.demo_data.course import main_course, random_course, simulate
from tools.demo_data.render import CUBE_FACES, lens_lookup, lens_radius, lens_theta
from tools.demo_data.sensors import (
    FOOT_POD_CLOCK_ERROR_S, coverage, foot_pod_series, watch_series,
)
from tools.demo_data.workspace import write_demo_workspace
from tools.demo_data.writers import write_device


class DemoCourseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.track = simulate(main_course())

    def test_main_course_climbs_turns_and_alternates_feet(self):
        track, spec = self.track, main_course()
        run = track.t >= 0
        self.assertGreater(track.ground[run].max() - track.ground[run][0], 12)
        self.assertAlmostEqual(math.degrees(float(track.heading_at(spec.duration) - track.heading_at(0.0))),
                               sum(turn.degrees for turn in spec.turns), places=0)
        self.assertTrue(np.all(track.step_feet[:-1] != track.step_feet[1:]))
        landing = spec.jumps[0].landing
        self.assertTrue(np.any(np.isclose(track.step_times, landing, atol=0.011)))
        self.assertFalse(np.any((track.step_times > spec.jumps[0].takeoff + 0.02) & (track.step_times < landing - 0.02)))

    def test_imu_series_have_sample_loss_and_clipping(self):
        series = watch_series(self.track, seed=1)
        imu = [item for item in series if item.group in ("Accelerometer", "Gyroscope", "Magnetometer")]
        both, total = coverage(imu)
        self.assertEqual(total, 9)
        self.assertGreaterEqual(both, 5)
        accelerometer = imu[0]
        self.assertEqual(accelerometer.time[0], 0.0)
        self.assertTrue(np.all(np.diff(accelerometer.time) > 0))

    def test_timing_problems_are_present(self):
        series = {item.key: item for item in watch_series(self.track, seed=1)}
        self.assertEqual(np.min(np.diff(series["Barometer/Pressure"].time)), 0.0)
        latitude = series["GNSS/Latitude"].time
        self.assertGreater(np.max(np.diff(latitude)), 5.0)
        self.assertEqual(series["GNSS/Fix"].labels["0"], "No fix")
        pod = {item.key: item for item in foot_pod_series(self.track, np.random.default_rng(1))}
        right = self.track.step_times[self.track.step_feet == 2]
        strikes = pod["Foot pod/Foot strike"].time
        self.assertAlmostEqual(float(strikes[0] - right[0]), FOOT_POD_CLOCK_ERROR_S, places=6)

    def test_long_course_is_light(self):
        track = simulate(random_course(120.0, 5, jumps_per_minute=0.5))
        keys = {item.key for item in watch_series(track, full=False, seed=5)}
        self.assertIn("Accelerometer/X", keys)
        self.assertNotIn("Gyroscope/X", keys)
        self.assertNotIn("Heart rate/PPG", keys)


class DemoFileTests(unittest.TestCase):
    def test_csv_and_h5_carry_units_metadata_and_diagnostics(self):
        track = simulate(random_course(12.0, 3, jumps_per_minute=6))
        series = watch_series(track, seed=3)
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "20261110_120000"
            folder.mkdir()
            h5_path = write_device(folder, "watch_20261110_120000", series, 1_800_000_000.0, {"device": "Demo watch"})
            header = [line for line in (folder / "watch_20261110_120000.csv").read_text(encoding="utf-8").splitlines()
                      if line.startswith("#")]
            rows = (folder / "watch_20261110_120000.csv").read_text(encoding="utf-8").splitlines()[len(header):]
            self.assertIn("# device: Demo watch", header)
            self.assertTrue(any(line.startswith("# series Accelerometer/X: unit=g;") and "measurement_range=+-8 g" in line
                                for line in header))
            self.assertEqual(rows[0], "time_unix_s,time_s,series,value,unit")
            self.assertTrue(rows[1].endswith(",g") and ",Accelerometer/X," in rows[1])
            navigator = read_h5_navigator_data(h5_path)
            self.assertIsNone(navigator.error)
            self.assertEqual({item.name for item in navigator.series}, {item.key for item in series})
            self.assertEqual(navigator.time_range[0], 1_800_000_000.0)
            hidden = {item.name for item in navigator.series if not item.visible}
            self.assertEqual(hidden, {"System/Temperature", "System/Battery"})
            with h5py.File(h5_path, "r") as h5_file:
                accelerometer = h5_file["series/Accelerometer/X"]
                self.assertEqual(accelerometer.attrs["unit"], "g")
                self.assertEqual(accelerometer.attrs["measurement_range"], "+-8 g")
                self.assertEqual(h5_file["series/Activity/Step"].attrs["event_state_kind"], "event")
                self.assertEqual(json.loads(h5_file["series/GNSS/Fix"].attrs["state_labels_json"])["2"], "3D fix")
                self.assertEqual(h5_file.attrs["session_device"], "Demo watch")
                index = {entry["name"]: entry for entry in json.loads(h5_file.attrs["series_index_json"])}
        warnings = [{item["id"]: item["value"] for item in index[f"Accelerometer/{axis}"]["navigator_metadata"]}["warning"]
                    for axis in "XYZ"]
        self.assertTrue(all("Sampleloss" in warning for warning in warnings))
        # The landing clips at least one axis, depending on the arm's orientation.
        self.assertTrue(any("Clipping" in warning for warning in warnings))

    def test_workspace_lists_every_card_type(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "20261103_071500"
            folder.mkdir()
            files = [folder / name for name in ("watch.h5", "pod.h5", "run.mp4", "run.insv", "route.png")]
            saved = write_demo_workspace(root / "demo.umw", root, folder, *files[:2], log_start=100.0,
                                         flat_video=files[2], insv_video=files[3], image=files[4], markers=(1.0, 2.0))
            state = read_workspace_file(saved)
        cards = [card for row in state["workspace"]["rows"] for card in row["cards"]]
        self.assertEqual({card["type"] for card in cards}, {
            "video", "gnss", "image", "multi", "line", "event", "frequency", "interval", "xy", "xyz", "gnss_3d"})
        self.assertEqual(Path(state["navigator"]["folder"]), root.resolve())
        self.assertEqual(cards[0]["path"], str(files[2].resolve()))
        line = next(card for card in cards if card["type"] == "line")
        self.assertEqual(line["markers"], [101.0, 102.0])
        self.assertTrue(all("groups" in card for card in cards if card["type"] in {"gnss", "gnss_3d", "xy", "xyz"}))


class LensModelTests(unittest.TestCase):
    def test_theta_inverts_the_lens_model(self):
        theta = np.linspace(0, 1.8, 50)
        np.testing.assert_allclose(lens_theta(lens_radius(theta)), theta, atol=1e-6)

    def test_lens_centres_look_forward_and_back(self):
        size, face = 64, 40
        front, back = lens_lookup(size, face, back=False), lens_lookup(size, face, back=True)
        faces = len(CUBE_FACES)
        self.assertEqual(front[size // 2, size // 2] // (face * face), 0)  # the front cube face
        self.assertEqual(back[size // 2, size // 2] // (face * face), 2)  # the back cube face
        # The corners are outside the image circle and black.
        self.assertEqual(front[0, 0], faces * face * face)


if __name__ == "__main__":
    unittest.main()
