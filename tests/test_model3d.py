"""3D model attachments: reading GLB and placement files, placing the model, and the 3D plot card."""
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import h5py
import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QThreadPool
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QTreeWidgetItemIterator

from src.data.axis_scale import METRES_PER_DEGREE_LATITUDE, METRES_PER_DEGREE_LONGITUDE
from src.data.folder_info import FolderInfoData, read_info_file, write_info_file
from src.data.model3d import (
    PLACE_GNSS, PLACE_ORIGIN, ModelError, box_matrix, enu_to_data, load_model, load_placement, model_folder_files,
    model_matrix, model_to_enu,
)
from src.jobs.model3d import decode_image, read_model_folder
from tests.qt_wait import qt_wait

WAIT_SECONDS = 10.0


def png_bytes(width=4, height=2) -> bytes:
    """A PNG whose top row is red and bottom rows blue."""
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor("blue"))
    for x in range(width):
        image.setPixelColor(x, 0, QColor("red"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def write_glb(path: Path, *, extra=None, indices=(0, 1, 2)) -> None:
    """A GLB with a coloured triangle drawn by two nodes and a textured, see-through triangle."""
    binary = bytearray()
    views, accessors = [], []

    def add(array, component, kind, normalized=False, count=None):
        data = np.ascontiguousarray(array).tobytes()
        while len(binary) % 4:
            binary.append(0)
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)})
        binary.extend(data)
        accessor = {"bufferView": len(views) - 1, "componentType": component, "type": kind,
                    "count": count if count is not None else len(array)}
        if normalized:
            accessor["normalized"] = True
        accessors.append(accessor)
        return len(accessors) - 1

    triangle = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
    position = add(triangle, 5126, "VEC3")
    index = add(np.array(indices, dtype=np.uint16), 5123, "SCALAR")
    colors = add(np.array([[255, 0, 0, 255], [0, 255, 0, 255], [0, 0, 255, 255]], dtype=np.uint8), 5121, "VEC4",
                 normalized=True)
    uv = add(np.array([[0, 0], [1, 0], [0, 1]], dtype=np.float32), 5126, "VEC2")
    image = png_bytes()
    while len(binary) % 4:
        binary.append(0)
    views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(image)})
    binary.extend(image)
    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views, "accessors": accessors,
        "images": [{"bufferView": len(views) - 1, "mimeType": "image/png"}],
        "textures": [{"source": 0}],
        "materials": [
            {"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1]}},
            {"pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 0.6], "baseColorTexture": {"index": 0}},
             "alphaMode": "BLEND"},
        ],
        "meshes": [
            {"name": "rock", "primitives": [{"attributes": {"POSITION": position, "COLOR_0": colors},
                                             "indices": index, "material": 0}]},
            {"name": "water", "primitives": [{"attributes": {"POSITION": position, "TEXCOORD_0": uv},
                                              "indices": index, "material": 1}]},
        ],
        # Two copies of the rock: one moved 10 along X inside a parent turned 90 degrees about Z.
        "nodes": [
            {"mesh": 0},
            {"rotation": [0, 0, math.sin(math.pi / 4), math.cos(math.pi / 4)], "children": [2]},
            {"mesh": 0, "translation": [10, 0, 0]},
            {"mesh": 1, "scale": [2, 2, 2]},
        ],
        "scenes": [{"nodes": [0, 1, 3]}], "scene": 0,
    }
    gltf.update(extra or {})
    document = json.dumps(gltf).encode("utf-8")
    document += b" " * (-len(document) % 4)
    binary = bytes(binary) + b"\0" * (-len(binary) % 4)
    chunks = struct.pack("<II", len(document), 0x4E4F534A) + document + struct.pack("<II", len(binary), 0x004E4942) + binary
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks)


def write_model_folder(folder: Path, **placement) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    write_glb(folder / "model.glb")
    data = {"schema_version": 2, "model": "model.glb", "model_anchor": [1, 2, 3],
            "geographic_anchor": {"latitude": 60.0, "longitude": 24.0, "altitude_metres": None,
                                  "vertical_reference": None},
            "scale": 2.0, "rotation_xyzw": [0, 0, 0, 1], "calibration_status": "APPROXIMATE_PREVIEW_NOT_CALIBRATED"}
    data.update(placement)
    (folder / "placement.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


class ModelFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_glb_meshes_keep_every_instance_colour_and_image(self):
        path = self.root / "model.glb"
        write_glb(path)
        model = load_model(path)
        rock, water = model.parts
        self.assertEqual((rock.name, water.name), ("rock", "water"))
        # Two rock instances: the second moved 10 along X, then turned 90 degrees about Z.
        self.assertEqual(rock.positions.shape, (6, 3))
        self.assertEqual(rock.indices.tolist(), [[0, 1, 2], [3, 4, 5]])
        np.testing.assert_allclose(rock.positions[3], [0, 10, 0], atol=1e-5)
        np.testing.assert_allclose(rock.positions[4], [0, 11, 0], atol=1e-5)
        # Vertex colours to display values; normals face up.
        np.testing.assert_allclose(rock.colors[:3], [[1, 0, 0, 1], [0, 1, 0, 1], [0, 0, 1, 1]], atol=1e-6)
        np.testing.assert_allclose(rock.normals, np.tile([0, 0, 1], (6, 1)), atol=1e-6)
        self.assertIsNone(rock.image)
        self.assertEqual(rock.alpha_mode, "OPAQUE")
        # The water: scaled, textured, see-through; its base colour converted to display values.
        np.testing.assert_allclose(water.positions[1], [2, 0, 0])
        self.assertEqual(water.image, 0)
        self.assertEqual(water.alpha_mode, "BLEND")
        self.assertAlmostEqual(water.base_color[0], 0.7354, places=3)
        self.assertAlmostEqual(water.base_color[3], 0.6)
        self.assertEqual(len(model.images), 1)
        np.testing.assert_allclose(model.bounds[0], [-1, 0, 0], atol=1e-5)
        np.testing.assert_allclose(model.bounds[1], [2, 11, 0], atol=1e-5)

    def test_unreadable_models_are_refused_with_a_reason(self):
        path = self.root / "model.glb"
        path.write_bytes(b"not a model")
        with self.assertRaisesRegex(ModelError, "not a binary glTF"):
            load_model(path)
        write_glb(path, extra={"extensionsRequired": ["KHR_draco_mesh_compression"]})
        with self.assertRaisesRegex(ModelError, "KHR_draco_mesh_compression"):
            load_model(path)
        write_glb(path, indices=(0, 1, 7))
        with self.assertRaisesRegex(ModelError, "invalid triangle indices"):
            load_model(path)

    def test_placement_file_is_checked(self):
        folder = write_model_folder(self.root / "Model", rotation_xyzw=[0, 0, 0, 2])
        placement, model_path = model_folder_files(folder)
        self.assertEqual(model_path, folder / "model.glb")
        self.assertEqual(placement.rotation_xyzw, (0, 0, 0, 1))
        self.assertEqual((placement.latitude, placement.longitude, placement.altitude_metres), (60.0, 24.0, None))
        self.assertFalse(placement.calibrated)
        for change, message in (({"schema_version": 1}, "schema_version"), ({"model": "../x.glb"}, "same folder"),
                                ({"scale": 0}, "positive"), ({"model_anchor": [1, 2]}, "3 numbers"),
                                ({"model": "missing.glb"}, "missing")):
            write_model_folder(self.root / "Bad", **change)
            with self.subTest(change=change), self.assertRaisesRegex(ModelError, message):
                model_folder_files(self.root / "Bad")

    def test_model_folder_is_read_with_its_images_decoded_top_row_first(self):
        loaded = read_model_folder(write_model_folder(self.root / "Model"))
        self.assertEqual(loaded.images[0].shape, (2, 4, 4))
        self.assertEqual(loaded.images[0][0, 0].tolist(), [255, 0, 0, 255])
        self.assertEqual(loaded.images[0][1, 0].tolist(), [0, 0, 255, 255])
        # Its own pixels, not a view of the decoder's freed image (the GPU got garbage).
        self.assertTrue(loaded.images[0].flags.owndata)
        self.assertIsNone(decode_image(b"not an image"))

    def test_model_links_are_kept_in_the_folder_info(self):
        folder = self.root / "Session"
        write_info_file(folder, FolderInfoData(models=[{"path": "../Model"}, {"path": " "}]))
        self.assertEqual(read_info_file(folder).models, [{"path": "../Model"}])


class PlacementMathsTests(unittest.TestCase):
    def setUp(self):
        folder = write_model_folder(Path(tempfile.mkdtemp()) / "Model")
        self.placement = load_placement(folder / "placement.json")

    def apply(self, matrix, point):
        return (np.asarray(matrix) @ np.r_[np.asarray(point, dtype=float), 1.0])[:3]

    def test_the_anchor_is_enu_zero_and_one_model_unit_is_scale_metres(self):
        to_enu = model_to_enu(self.placement)
        np.testing.assert_allclose(self.apply(to_enu, [1, 2, 3]), [0, 0, 0])
        np.testing.assert_allclose(self.apply(to_enu, [2, 2, 3]), [2, 0, 0])
        # A quaternion turning 90 degrees about Z takes east to north.
        from dataclasses import replace
        quarter = replace(self.placement, rotation_xyzw=(0, 0, math.sin(math.pi / 4), math.cos(math.pi / 4)))
        np.testing.assert_allclose(self.apply(model_to_enu(quarter), [2, 2, 3]), [0, 2, 0], atol=1e-9)

    def test_origin_puts_the_anchor_at_zero_in_metres(self):
        matrix, reason = enu_to_data(self.placement, PLACE_ORIGIN, ("m", "km", ""), ("", "", ""), [0] * 3, [1] * 3)
        self.assertEqual(reason, "")
        np.testing.assert_allclose(self.apply(matrix, [100, 2000, 5]), [100, 2, 5])

    def test_gnss_puts_the_anchor_on_its_latitude_and_longitude(self):
        units, roles = ("deg", "deg", "m"), ("longitude", "latitude", "")
        matrix, reason = enu_to_data(self.placement, PLACE_GNSS, units, roles, [23, 59, 0], [25, 61, 100])
        self.assertEqual(reason, "")
        np.testing.assert_allclose(self.apply(matrix, [0, 0, 0]), [24, 60, 0])
        east = 1000 / (METRES_PER_DEGREE_LONGITUDE * math.cos(math.radians(60)))
        np.testing.assert_allclose(self.apply(matrix, [1000, 1000, 7]),
                                   [24 + east, 60 + 1000 / METRES_PER_DEGREE_LATITUDE, 7])
        # Fixed-point degrees (x 10^7) and latitude on X.
        matrix, _ = enu_to_data(self.placement, PLACE_GNSS, ("raw", "raw", "ft"), ("latitude", "longitude", ""),
                                [59e7, 23e7, 0], [61e7, 25e7, 10])
        np.testing.assert_allclose(self.apply(matrix, [0, METRES_PER_DEGREE_LATITUDE, 0.3048]), [61e7, 24e7, 1])

    def test_gnss_needs_latitude_longitude_and_a_gnss_anchor(self):
        no_axes = enu_to_data(self.placement, PLACE_GNSS, ("m", "m", "m"), ("", "", ""), [0] * 3, [1] * 3)
        self.assertEqual(no_axes, (None, "Needs latitude and longitude axes"))
        time_z = enu_to_data(self.placement, PLACE_GNSS, ("deg", "deg", "s"), ("latitude", "longitude", "time"),
                             [0] * 3, [1] * 3)
        self.assertEqual(time_z, (None, "Z is time, not altitude"))
        from dataclasses import replace
        unanchored = replace(self.placement, latitude=None)
        self.assertEqual(enu_to_data(unanchored, PLACE_GNSS, ("deg", "deg", "m"), ("latitude", "longitude", ""),
                                     [0] * 3, [1] * 3), (None, "The model has no GNSS anchor"))

    def test_the_model_joins_the_traces_box(self):
        low, high, stretch = np.array([-10, 0, 5]), np.array([10, 4, 5]), np.array([1, 0.5, 2])
        # The plot's traces: (data - centre) / half range x stretch; a constant axis keeps scale 1.
        np.testing.assert_allclose(self.apply(box_matrix(low, high, stretch), [10, 1, 5]), [1, -0.25, 0])
        matrix, reason = model_matrix(self.placement, PLACE_ORIGIN, ("m",) * 3, ("",) * 3, low, high, stretch)
        self.assertEqual(reason, "")
        np.testing.assert_allclose(self.apply(matrix, [1, 2, 3]), self.apply(box_matrix(low, high, stretch), [0, 0, 0]))


class FakeGLView:
    pass


class ModelCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from src.app.lodgazilla import PlotWorkspace
        from src.gui.plots.xyz import XYZPlotWidget
        from tests.test_xyz_plot import FakeView
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.folder = root / "Data" / "Dive"
        self.folder.mkdir(parents=True)
        self.path = self.folder / "data.h5"
        with h5py.File(self.path, "w") as cache:
            for name, values, unit in (("GNSS/longitude", [23.9, 24.0, 24.1], "deg"),
                                       ("GNSS/latitude", [59.9, 60.0, 60.1], "deg"),
                                       ("GNSS/altitude", [0.0, 5.0, 10.0], "m")):
                group = cache.create_group("series/" + name)
                group.attrs.update(time_dataset="time_unix", time_unit="s", navigator_name=name, unit=unit)
                group.create_dataset("time_unix", data=[0., 1., 2.])
                group.create_dataset("value", data=values)
        self.first = write_model_folder(root / "Models" / "Quarry")
        self.second = write_model_folder(root / "Models" / "Lake")
        write_info_file(self.folder, FolderInfoData(models=[{"path": "../../Models/Quarry"}]))
        self.patch = mock.patch.object(XYZPlotWidget, "view_class", FakeView)
        self.patch.start()
        self.workspace = PlotWorkspace(settings_path=root / "settings.json")
        self.workspace.resize(900, 700)
        self.workspace.show()
        self.addCleanup(self.close_widgets)

    def close_widgets(self):
        QThreadPool.globalInstance().waitForDone(int(WAIT_SECONDS * 1000))
        qt_wait(50)
        self.workspace.close_all_plots()
        self.workspace.close()
        self.workspace.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.patch.stop()

    def make_card(self):
        card = self.workspace.add_xyz_plot(*((self.path, f"GNSS/{name}") for name in ("longitude", "latitude", "altitude")))
        self.wait_until(lambda: not card._loading)
        return card

    def wait_until(self, condition):
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            if condition():
                return
            qt_wait(20)
        self.fail("Timed out")

    def test_one_model_is_a_checkbox_placed_at_its_gnss_position(self):
        from src.gui.plots.model_item import ModelPartItem
        card = self.make_card()
        self.assertEqual(card.models, [self.first.resolve()])
        card.controls_button.click()
        qt_wait(20)
        self.assertFalse(card.model_panel.isHidden())
        self.assertFalse(card.model_check.isHidden())
        self.assertTrue(card.model_combo.isHidden())
        card.model_check.setChecked(True)
        self.wait_until(lambda: card._loaded_model is not None)
        items = [item for item in card.view.items if isinstance(item, ModelPartItem)]
        self.assertEqual(len(items), 2)
        # Latitude and longitude axes: the model starts at its GNSS position, Equal scale applied once.
        self.assertEqual(card.model_placement, PLACE_GNSS)
        self.assertTrue(card.placement_buttons[PLACE_GNSS].isChecked())
        self.wait_until(lambda: not np.all(card.axis_stretch == 1))
        # The anchor (model point 1, 2, 3) is drawn where the data's 24, 60, 0 is.
        low, high = card._extent
        anchor = items[0].model_matrix @ np.array([1, 2, 3, 1.0])
        expected = box_matrix(low, high, card.axis_stretch) @ np.array([24, 60, 0, 1.0])
        np.testing.assert_allclose(anchor, expected, atol=1e-9)
        self.assertIn("Approximate placement, not calibrated", card.model_note.text())
        self.assertFalse(card.model_note.isHidden())
        # At 0, 0, 0: the anchor at the data's 0, 0, 0.
        card.placement_buttons[PLACE_ORIGIN].click()
        expected = box_matrix(*card._extent, card.axis_stretch) @ np.array([0, 0, 0, 1.0])
        np.testing.assert_allclose(items[0].model_matrix @ np.array([1, 2, 3, 1.0]), expected, atol=1e-9)
        # Saved and reopened with its placement (the fake view has no camera of its own).
        from PySide6.QtGui import QVector3D
        card.view.opts = {"center": QVector3D(), "distance": 6.0, "elevation": 25.0, "azimuth": 45.0, "fov": 60.0}
        state = card.workspace_state()
        self.assertEqual(state["model"], {"path": str(self.first.resolve()), "placement": PLACE_ORIGIN})
        json.dumps(state)
        card.model_check.setChecked(False)
        self.assertIsNone(card.model_folder)
        self.assertEqual([item for item in card.view.items if isinstance(item, ModelPartItem)], [])
        self.assertTrue(card.model_note.isHidden())
        card.restore_workspace_state(state)
        self.wait_until(lambda: card._loaded_model is not None)
        self.assertEqual(card.model_placement, PLACE_ORIGIN)

    def test_several_models_are_a_list_and_an_unlinked_model_goes(self):
        info = read_info_file(self.folder)
        info.models.append({"path": "../../Models/Lake"})
        write_info_file(self.folder, info)
        card = self.make_card()
        card.controls_button.click()
        qt_wait(20)
        self.assertTrue(card.model_check.isHidden())
        self.assertFalse(card.model_combo.isHidden())
        self.assertEqual([card.model_combo.itemText(index) for index in range(card.model_combo.count())],
                         ["No 3D model", "Quarry", "Lake"])
        card.model_combo.setCurrentIndex(2)
        card.model_combo.activated.emit(2)
        self.wait_until(lambda: card._loaded_model is not None)
        self.assertEqual(card.model_folder, self.second.resolve())
        # The link is removed: the card offers the other model and hides this one.
        info = read_info_file(self.folder)
        info.models = [{"path": "../../Models/Quarry"}]
        write_info_file(self.folder, info)
        card.refresh_models()
        self.assertIsNone(card.model_folder)
        self.assertFalse(card.model_check.isHidden())

    def test_no_models_hide_the_selector_and_a_bad_model_says_why(self):
        write_info_file(self.folder, FolderInfoData())
        card = self.make_card()
        card.controls_button.click()
        qt_wait(20)
        self.assertTrue(card.model_panel.isHidden())
        (self.first / "model.glb").write_bytes(b"broken")
        write_info_file(self.folder, FolderInfoData(models=[{"path": "../../Models/Quarry"}]))
        card.refresh_models()
        self.assertFalse(card.model_panel.isHidden())
        card.model_check.setChecked(True)
        self.wait_until(lambda: card.model_folder is None)
        self.assertIn("not a binary glTF", card.model_note.text())
        self.assertFalse(card.model_check.isChecked())


class ModelAttachmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from src.gui.navigator.widget import DataNavigatorWidget
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.folder = root / "Data" / "Dive"
        self.folder.mkdir(parents=True)
        write_info_file(self.folder, FolderInfoData(images=[{"path": "photo.png"}]))
        self.model = write_model_folder(root / "Kaatiala")
        self.navigator = DataNavigatorWidget(self.folder.parent, settings_path=root / "settings.json")
        self.window = SimpleNamespace(navigator=self.navigator, internal_logger=mock.Mock(),
                                      on_metadata_write_failed=mock.Mock(), refresh_card_models=mock.Mock(),
                                      plot_workspace=SimpleNamespace(plots=lambda: []))
        self.addCleanup(self.close_widgets)

    def close_widgets(self):
        QThreadPool.globalInstance().waitForDone(3000)
        self.navigator.close()
        self.navigator.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def model_rows(self):
        from src.gui.navigator.widget import ITEM_TYPE_ROLE, ROW_TYPE_MODEL
        rows, iterator = [], QTreeWidgetItemIterator(self.navigator.tree)
        while iterator.value():
            if iterator.value().data(0, ITEM_TYPE_ROLE) == ROW_TYPE_MODEL:
                rows.append(iterator.value())
            iterator += 1
        return rows

    def attach(self, folder):
        from src.app.lodgazilla import LODGaZillaWindow
        with mock.patch("src.app.lodgazilla.QFileDialog.getExistingDirectory", return_value=str(folder)), \
                mock.patch("src.app.lodgazilla.QMessageBox.warning") as warning:
            LODGaZillaWindow.attach_model_to_folder(self.window, self.folder)
        return warning

    def test_attach_lists_the_model_in_the_media_group_and_remove_keeps_its_files(self):
        from src.app.lodgazilla import LODGaZillaWindow
        from src.gui.navigator.widget import ITEM_MODEL_PATH_ROLE
        self.attach(self.model)
        self.attach(self.model)
        info = read_info_file(self.folder)
        self.assertEqual(info.models, [{"path": "../../Kaatiala"}])
        self.assertEqual(info.images, [{"path": "photo.png"}])
        self.window.refresh_card_models.assert_called()
        rows = self.model_rows()
        self.assertEqual([row.text(0) for row in rows], ["Kaatiala"])
        self.assertEqual(rows[0].parent().text(0), "Media")
        self.assertEqual(rows[0].data(0, ITEM_MODEL_PATH_ROLE), str(self.model.resolve()))
        requested = []
        self.navigator.model_context_menu_requested.connect(lambda *values: requested.append(values[:2]))
        self.navigator.tree.scrollToItem(rows[0])
        self.navigator.on_context_menu_requested(self.navigator.tree.visualItemRect(rows[0]).center())
        self.assertEqual(requested, [(self.folder.resolve(), self.model.resolve())])
        LODGaZillaWindow.remove_model_link(self.window, self.folder, self.model)
        self.assertEqual(read_info_file(self.folder).models, [])
        self.assertTrue((self.model / "model.glb").is_file())
        self.assertEqual(self.model_rows(), [])

    def test_a_folder_without_a_placement_is_not_attached(self):
        empty = Path(self.temp.name) / "Empty"
        empty.mkdir()
        warning = self.attach(empty)
        warning.assert_called_once()
        self.assertIn("placement.json", warning.call_args.args[2])
        self.assertEqual(read_info_file(self.folder).models, [])


if __name__ == "__main__":
    unittest.main()
