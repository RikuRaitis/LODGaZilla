"""Exercise the Qt controls and generated JavaScript without online map tiles."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import h5py

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QLocale, QPoint, QPointF, Qt, Signal, QThreadPool
from PySide6.QtGui import QWheelEvent
from PySide6.QtQml import QJSEngine
from PySide6.QtTest import QTest
from tests.qt_wait import qt_wait
from src.gui.plots.markers import set_track_selection_style, track_selection_rgba
from src.gui.plots.series_style import series_line_width
from PySide6.QtWidgets import QApplication, QWidget

from src.gui.plots.gnss_3d import Gnss3DPlotWidget


# The stub elevation tiles describe flat ground at this height.
GROUND_M = 100

MAP_STUBS = """
const groundHeight = %d;
const demRequests = [];
// A canvas returns Terrarium pixels for flat ground: height = R*256 + G + B/256 - 32768.
const canvasStub = {getContext: () => ({drawImage() {}, getImageData: (x, y, w, h) => {
  const value = groundHeight + 32768, data = [];
  for (let i = 0; i < w * h; i++) data.push(Math.floor(value / 256), value %% 256, 0, 255);
  return {data};
}})};
const mapElements = [{style: {}}, {style: {}}, {style: {}}];
const document = {getElementById: () => mapElements[2], querySelectorAll: () => mapElements,
                  createElement: () => canvasStub};
const Image = class {set src(url) {demRequests.push(url); this.width = 4; this.height = 4; this.onload();}};
const qt = {webChannelTransport: null};
const bridgeCalls = [];
const QWebChannel = class {
  constructor(transport, ready) {
    const record = name => (...args) => bridgeCalls.push([name, ...args]);
    ready({objects: {gnssBridge: {
      select_timestamp: record('select_timestamp'), clear_marker: record('clear_marker'),
      select_track: record('select_track'),
      select_second_timestamp: record('select_second_timestamp'),
      clear_second_marker: record('clear_second_marker'), notify_view_changed: () => {},
      report_auto_settings: record('report_auto_settings'),
    }}});
  }
};
const maplibregl = {
  Map: class {
    constructor(options) {
      this.options=options; this.events={}; this.sources={}; this.terrain=null;
      this.fitCount=0; this.layout={}; this.paint={}; this.layoutById={}; this.controls=[];
      this.doubleClickZoom={disable: () => {this.doubleClickZoomDisabled=true;}};
    }
    // Screen position: longitude and latitude scaled, with no altitude.
    project(p) {return {x: p[0] * 100, y: p[1] * 100};}
    on(event, callback) {this.events[event]=callback;}
    // The deck.gl overlay is the control with props; the scale bar is a ScaleControl.
    addControl(control, position) {
      if(control instanceof maplibregl.ScaleControl){this.scaleBar=control; control.position=position;}
      else if(control instanceof maplibregl.AttributionControl){this.attribution=control; control.position=position;}
      else this.overlay=control;
      this.controls.push(control);
    }
    removeControl(control) {
      this.controls=this.controls.filter(item=>item!==control);
      control.removed=true;
      if(control===this.scaleBar)this.scaleBar=null; else this.overlay=null;
    }
    fitBounds() {this.fitCount++;}
    getStyle() {
      const layers=[{id:'land', type:'fill'}];
      if(this.overlay&&this.overlay.props.interleaved)layers.push({id:'gnss-3d-route',type:'custom'});
      return {layers};
    }
    setLayoutProperty(id, key, value) {this.layout[key]=value; this.layoutById[id]=value;}
    setPaintProperty(id, key, value) {this.paint[key]=value;}
    getCanvas() {return this.canvas||(this.canvas={style:{}});}
    addSource(id, source) {
      if (this.sources[id]) throw new Error('Duplicate terrain source');
      this.sources[id]=source;
    }
    setTerrain(terrain) {this.terrain=terrain;}
  },
  LngLatBounds: class {extend() {return this;}},
  ScaleControl: class {constructor(options) {this.options=options;}},
  AttributionControl: class {constructor(options) {this.options=options;}}
};
const deck = {
  PathLayer: class {constructor(props) {this.props=props;}},
  ScatterplotLayer: class {constructor(props) {this.props=props;}},
  MapboxOverlay: class {
    constructor(props) {this.props=props;}
    setProps(props) {Object.assign(this.props, props);}
  }
};
function routeCoordinates() {
  const layer=map.overlay.props.layers[0].props;
  return layer.getPath(layer.data[0]);
}
""" % GROUND_M


class ScriptMapView(QWidget):
    loadFinished = Signal(bool)

    def wheelEvent(self, event):
        self.browser_wheel_delta = event.angleDelta().y()
        event.accept()

    def setHtml(self, html):
        self.engine = QJSEngine()
        script = html.rsplit("<script>", 1)[1].split("</script>", 1)[0]
        # QJSEngine keeps top-level const bindings local to each evaluate call.
        # Inspect them through a closure in the same evaluation as the map.
        result = self.engine.evaluate(
            MAP_STUBS + script + "\nfunction inspectMap(expression) {return eval(expression);}"
        )
        if result.isError():
            raise AssertionError(result.toString())

    def page(self):
        return self

    def setBackgroundColor(self, color):
        self.background = color

    def setWebChannel(self, channel):
        self.channel = channel

    def stop(self):
        pass

    def runJavaScript(self, script, callback=None):
        result = self.engine.evaluate(f"inspectMap({json.dumps(script)})")
        if result.isError():
            raise AssertionError(result.toString())
        if callback is not None:
            callback(result.toVariant())
        return result.toVariant()


from tests.markers_shown import with_markers_shown  # noqa: E402


@with_markers_shown
class Gnss3DControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.h5_path = Path(folder.name) / "track.h5"
        with h5py.File(self.h5_path, "w") as handle:
            for name, values, unit in (
                ("latitude_deg", [60, 61, 62], "deg"),
                ("longitude_deg", [24, 25, 26], "deg"),
                ("altitude_m", [10, 0, -5], "m"),
            ):
                group = handle.create_group(f"series/GNSS/{name}")
                group.attrs["navigator_group"] = "GNSS"
                group.attrs["navigator_name"] = name
                group.attrs["unit"] = unit
                group.create_dataset("time_unix", data=[1.0, 2.0, 3.0])
                group.create_dataset("value", data=values)
        patcher = mock.patch("src.gui.plots.gnss_3d.QWebEngineView", ScriptMapView)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.widget = self.make_widget()

    def make_widget(self, **kwargs):
        widget = Gnss3DPlotWidget(self.h5_path, "GNSS/latitude_deg", **kwargs)
        self.wait_loaded(widget)
        self.addCleanup(lambda: QThreadPool.globalInstance().waitForDone(3000))
        self.addCleanup(widget.dispose)
        self.addCleanup(widget.close)
        return widget

    def wait_loaded(self, widget):
        for _ in range(300):
            if not widget._loading:
                break
            qt_wait(10)
        self.assertFalse(widget._loading)
        self.assertTrue(all(result is None or isinstance(result, dict) for result in widget.results), widget.results)

    def js(self, script, widget=None):
        return (widget or self.widget).map_view.runJavaScript(script)

    def load_map(self, widget=None, *, auto=False):
        """Load the page; ``auto`` keeps the new card's Auto, which the other tests leave out."""
        widget = widget or self.widget
        if not auto:
            widget._auto_pending = False
        self.wait_loaded(widget)
        self.js("map.events.load()", widget)
        widget.map_view.loadFinished.emit(True)

    def test_theme_background_survives_map_visibility_changes(self):
        from types import SimpleNamespace
        from src.app.lodgazilla import PlotWorkspace
        from src.gui.theme import dark_palette, light_palette
        self.load_map()
        for palette, expected in ((dark_palette(), "#000000"), (light_palette(), "#ffffff")):
            self.widget.setPalette(palette)
            PlotWorkspace.apply_theme(SimpleNamespace(plots=lambda: [self.widget]))
            self.assertEqual(self.widget.map_view.background.name(), expected)
            for opacity in (0, 100):
                self.widget.map_opacity.setValue(opacity)
                self.assertEqual(self.js("mapElements.map(e => e.style.backgroundColor)"), [expected] * 3)

    def test_markers_selection_values_boxes_and_clicks(self):
        self.load_map()
        self.widget.set_vertical_marker(2.0, 0)
        self.widget.set_vertical_marker(3.0, 1)
        layers = self.js("map.overlay.props.layers.map(layer=>layer.props.id)")
        self.assertEqual(layers, ["gnss-3d-route", "gnss-3d-selection", "gnss-3d-markers"])
        self.assertEqual(self.js("map.overlay.props.layers[2].props.data.map(d=>d.position)"),
                         [[25, 61, 0], [26, 62, -5]])
        # The selection covers the track between the markers.
        self.assertEqual(self.js("map.overlay.props.layers[1].props.data.map(d=>d.path)"), [[[25, 61, 0], [26, 62, -5]]])
        # At the track's width, in the selection color, above everything by default.
        self.assertEqual(self.js("map.overlay.props.layers[1].props.getWidth({index:0})"), series_line_width())
        self.assertEqual(self.js("map.overlay.props.layers[1].props.getColor"), list(track_selection_rgba()))
        self.assertEqual(self.js("map.overlay.props.layers[1].props.parameters.depthCompare"), "always")
        # On its own trace it takes the track's depth test; Off draws none.
        try:
            set_track_selection_style("#ff0000", 50, "own_trace")
            self.widget.apply_marker_style()
            self.assertEqual(self.js("map.overlay.props.layers[1].props.parameters.depthCompare"), "less-equal")
            self.assertEqual(self.js("map.overlay.props.layers[1].props.getColor"), [255, 0, 0, 128])
            set_track_selection_style("#ff0000", 50, "off")
            self.widget.apply_marker_style()
            self.assertEqual(self.js("map.overlay.props.layers.map(layer=>layer.props.id)"),
                             ["gnss-3d-route", "gnss-3d-markers"])
        finally:
            set_track_selection_style("#2196f3", 100, "always_top")
            self.widget.apply_marker_style()
        self.assertTrue(self.js("map.doubleClickZoomDisabled"))
        # A values box per track at each marker, beside its point on screen.
        self.widget.show_marker_data(2.0, 0)
        boxes = self.widget.value_boxes.visible_boxes(0)
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0].item.anchor, (2500.0, 6100.0))
        self.assertIn("61.0000000°, 25.0000000°", boxes[0].toPlainText())
        # Clicks pick the nearest point on screen; a right drag does not place a marker,
        # and a second right click soon after removes the second marker.
        self.js("map.events.click({point:{x:2390,y:6010}})")
        self.js("map.events.mousedown({originalEvent:{button:2},point:{x:2600,y:6200}})")
        self.js("map.events.contextmenu({point:{x:2400,y:6000}})")
        for _ in range(2):
            self.js("map.events.mousedown({originalEvent:{button:2},point:{x:2400,y:6000}})")
            self.js("map.events.contextmenu({point:{x:2400,y:6000}})")
        # The left click also reports the track under it: none this far from the track (-1).
        self.assertEqual(self.js("bridgeCalls"), [["select_track", -1], ["select_timestamp", 1],
                                                  ["select_second_timestamp", 1], ["clear_second_marker"]])
        # The bridge's requests reach the card as marker changes.
        placed = []
        self.widget.second_marker_placed.connect(placed.append)
        self.widget.bridge.select_second_timestamp(1.0)
        self.assertEqual((placed, self.widget.marker_values()), ([1.0], (2.0, 1.0)))

    def test_defaults_terrain_occlusion_and_near_ground_pitch_limit(self):
        self.load_map()
        self.assertEqual(self.widget.altitude_exaggeration.value(), 10)
        self.assertEqual(self.widget.track_altitude_offset.value(), 0)
        self.assertEqual(self.widget.height_series, (self.h5_path, "GNSS/altitude_m"))
        self.assertEqual(self.js("routeCoordinates()"), [[24, 60, 10], [25, 61, 0], [26, 62, -5]])
        self.assertEqual(self.js("map.options.maxPitch"), 85)
        self.assertEqual(self.widget.map_opacity.value(), 100)
        self.assertEqual(self.widget.map_opacity.value_text(), "100%")
        self.assertFalse(self.widget.follow_ground.isChecked())
        # The map's terrain is always on, and the terrain hides the track behind it.
        self.assertEqual(self.js("map.terrain.exaggeration"), 1)
        self.assertTrue(self.js("map.overlay.props.interleaved"))
        self.assertEqual(self.js("map.overlay.props.layers[0].props.parameters.depthCompare"), "less-equal")

    def test_plain_wheel_scrolls_workspace_and_ctrl_wheel_reaches_browser(self):
        from src.app.lodgazilla import PlotWorkspace

        workspace = PlotWorkspace(settings_path=self.h5_path.parent / 'settings.json')
        self.addCleanup(workspace.close)
        scrollbar = workspace.verticalScrollBar()
        scrollbar.setRange(0, 1000)
        scrollbar.setValue(500)
        self.widget.wheel_scrolled.connect(workspace.scroll_workspace)
        # Test the view and late-created nested children, as used by Chromium.
        child = ScriptMapView(QWidget(self.widget.map_view))
        for target in (self.widget.map_view, child):
            for delta, modifiers, expected in (
                (120, Qt.KeyboardModifier.NoModifier, 380),
                (-120, Qt.KeyboardModifier.NoModifier, 500),
                (120, Qt.KeyboardModifier.ControlModifier, 500),
                (-120, Qt.KeyboardModifier.ControlModifier, 500),
            ):
                with self.subTest(target=target, delta=delta, modifiers=modifiers):
                    target.browser_wheel_delta = None
                    event = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, delta),
                                        Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False)
                    QApplication.sendEvent(target, event)
                    self.assertEqual(scrollbar.value(), expected)
                    self.assertTrue(event.isAccepted())
                    self.assertEqual(target.browser_wheel_delta,
                                     delta if modifiers == Qt.KeyboardModifier.ControlModifier else None)
        workspace.deleteLater()

    def test_live_exaggeration_and_offsets_do_not_accumulate_or_change_camera(self):
        self.load_map()
        self.widget.altitude_exaggeration.setValue(25)
        # The sliders show their values on top of themselves, in the user's number format.
        self.assertEqual(self.widget.altitude_exaggeration.value_text(), QLocale().toString(2.5, "f", 1) + "x")
        self.assertEqual(self.js("routeCoordinates().map(p=>p[2])"), [25, 0, -12.5])
        self.assertEqual(self.js("map.terrain.exaggeration"), 2.5)
        # A typed offset updates its slider and the rendered altitudes.
        self.widget.altitude_exaggeration.setValue(17)
        editor = self.widget.track_offset_input
        editor.lineEdit().selectAll()
        QTest.keyClicks(editor.lineEdit(), editor.locale().toString(-12.3, "f", 1))
        QTest.keyClick(editor.lineEdit(), Qt.Key.Key_Return)
        self.assertEqual(self.widget.altitude_exaggeration.value(), 17)
        self.assertEqual(self.widget.track_altitude_offset.value(), -123)
        self.assertEqual(self.js("map.terrain.exaggeration"), 1.7)
        for actual, expected in zip(self.js("routeCoordinates().map(p=>p[2])"),
                                    [-3.91, -20.91, -29.41]):
            self.assertAlmostEqual(actual, expected)
        self.widget.track_altitude_offset.setValue(125)
        self.assertEqual(self.widget.track_offset_input.value(), 12.5)
        # The exaggeration reaches 10x; the offset 1000 m either way.
        for requested, expected in ((20, 10.0), (0, 1.0)):
            self.widget.set_altitude_factor(requested)
            self.assertEqual(self.widget.altitude_factor, expected)
            self.assertEqual(self.widget.altitude_exaggeration.value(), round(expected * 10))
            self.assertEqual(self.js("map.terrain.exaggeration"), expected)
        self.widget.altitude_exaggeration.setValue(25)
        self.widget.track_offset_input.setValue(2000)
        self.assertEqual(self.widget.track_offset_input.value(), 1000.0)
        self.assertEqual(self.widget.track_altitude_offset.value(), 10000)
        self.assertEqual(self.js("routeCoordinates().map(p=>p[2])"), [2525, 2500, 2487.5])
        self.widget.track_altitude_offset.setValue(-1000)
        self.assertEqual(self.widget.track_offset_input.value(), -100.0)
        self.assertEqual(self.js("routeCoordinates().map(p=>p[2])"), [-225, -250, -262.5])
        self.assertEqual(self.js("map.terrain.exaggeration"), 2.5)
        self.widget.track_offset_input.setValue(0)
        self.widget.altitude_exaggeration.setValue(10)
        self.assertEqual(self.js("routeCoordinates()"), self.js("tracks[0].coordinates"))
        self.assertEqual(self.js("map.fitCount"), 1)

    def test_controls_changed_before_map_load_are_applied_when_ready(self):
        self.widget._auto_pending = False
        self.widget.map_view.loadFinished.emit(True)
        self.widget.altitude_exaggeration.setValue(25)
        self.widget.track_offset_input.setValue(-50)
        self.widget.map_opacity.setValue(0)
        self.assertEqual(self.js("Object.keys(map.sources).length"), 0)
        self.js("map.events.load()")
        self.assertEqual(self.js("routeCoordinates().map(p=>p[2])"), [-100, -125, -137.5])
        self.assertIsNone(self.js("map.terrain"))
        self.assertFalse(self.js("map.overlay.props.interleaved"))
        self.assertEqual(self.js("map.layout.visibility"), "none")
        self.assertEqual(self.js("map.getCanvas().style.opacity"), "0")

    def test_map_opacity_zero_hides_map_and_terrain_without_resetting_track_or_camera(self):
        self.load_map()
        self.widget.altitude_exaggeration.setValue(25)
        self.widget.track_offset_input.setValue(-50)
        for _ in range(3):
            self.js("map.previousOverlay=map.overlay; null")
            self.widget.map_opacity.setValue(0)
            self.assertEqual(self.widget.map_opacity.value_text(), "0%")
            # A hidden map neither shows terrain nor hides the track.
            self.assertTrue(self.js("map.previousOverlay.removed"))
            self.assertIsNone(self.js("map.terrain"))
            self.assertEqual(self.js("map.layout.visibility"), "none")
            self.assertFalse(self.js("map.overlay.props.interleaved"))
            self.assertEqual(self.js("map.overlay.props.layers[0].props.parameters.depthCompare"), "always")
            self.assertFalse(self.js("'gnss-3d-route' in map.layoutById"))
            self.assertEqual(self.js("routeCoordinates().map(p=>p[2])"), [-100, -125, -137.5])
            self.widget.map_opacity.setValue(40)
            self.assertEqual(self.js("map.layout.visibility"), "visible")
            # The map fades as one image; its layers keep the style's own opacities.
            self.assertEqual(self.js("map.getCanvas().style.opacity"), "0.4")
            self.assertEqual(self.js("Object.keys(map.paint).length"), 0)
            # A faded map's canvas would fade the track too, so it is drawn on top.
            self.assertFalse(self.js("map.overlay.props.interleaved"))
            self.assertEqual(self.js("map.terrain.exaggeration"), 2.5)
            self.assertEqual(self.js("map.controls.filter(control=>control.props).length"), 1)
            self.widget.map_opacity.setValue(100)
            self.assertEqual(self.js("map.getCanvas().style.opacity"), "1")
            self.assertTrue(self.js("map.overlay.props.interleaved"))
            self.assertEqual(self.js("map.controls.filter(control=>control.props).length"), 1)
        self.assertEqual(self.js("map.fitCount"), 1)

    def test_hidden_map_survives_loading_and_refresh(self):
        self.widget.map_opacity.setValue(0)
        self.load_map()
        self.assertFalse(self.js("map.overlay.props.interleaved"))
        self.widget.on_series_changed()
        self.load_map()
        self.assertFalse(self.js("map.overlay.props.interleaved"))
        self.assertEqual(self.js("map.controls.filter(control=>control.props).length"), 1)

    def test_track_refresh_retains_slider_values(self):
        self.load_map()
        self.widget.altitude_exaggeration.setValue(30)
        self.widget.track_offset_input.setValue(100)
        self.widget.on_series_changed()
        self.load_map()
        self.assertEqual(self.js("routeCoordinates().map(p=>p[2])"), [330, 300, 285])
        self.assertEqual(self.js("map.terrain.exaggeration"), 3.0)

    def test_points_draw_style_draws_track_positions_at_their_heights(self):
        self.load_map()
        self.assertEqual(self.js("map.overlay.props.layers[0].props.id"), "gnss-3d-route")
        self.widget.set_draw_style("points")
        self.assertEqual(self.js("map.overlay.props.layers[0].props.id"), "gnss-3d-route-points")
        self.assertEqual(self.js("map.overlay.props.layers[0].props.data.map(d=>map.overlay.props.layers[0]"
                                 ".props.getPosition(d)[2])"), [10, 0, -5])
        self.assertEqual(self.js("map.overlay.props.layers[0].props.parameters.depthCompare"), "less-equal")
        self.widget.set_draw_style("lines")
        self.assertEqual(self.js("map.overlay.props.layers[0].props.id"), "gnss-3d-route")

    def test_follow_ground_ignores_altitude_and_its_offset(self):
        self.load_map()
        self.widget.track_offset_input.setValue(40)
        self.widget.altitude_exaggeration.setValue(20)
        self.widget.follow_ground.setChecked(True)
        # The track lies on the stub terrain, scaled like it.
        self.assertEqual(self.js("routeCoordinates().map(p=>p[2])"), [GROUND_M * 2] * 3)
        self.assertFalse(self.widget.track_offset_input.isEnabled())
        self.assertFalse(self.widget.track_altitude_offset.isEnabled())
        # The ground comes from the terrain's own elevation tiles.
        # At the zoom the terrain uses too, which the map's own tiles also reach.
        from src.gui.plots.gnss_3d import DEM_ZOOM
        self.assertEqual(DEM_ZOOM, 14)
        self.assertTrue(self.js(f"demRequests.every(url => url.includes('/terrarium/{DEM_ZOOM}/'))"))
        self.widget.follow_ground.setChecked(False)
        self.assertEqual(self.js("routeCoordinates().map(p=>p[2])"), [100, 80, 70])
        self.assertTrue(self.widget.track_offset_input.isEnabled())

    def test_new_card_applies_auto_fit_exaggeration_and_ground_offset(self):
        self.load_map(auto=True)
        # Heights 3x, and the offset puts the lowest point above ground (-5 m vs 100 m) on the ground.
        self.assertEqual(self.js("viewSettings.altitudeExaggeration"), 3)
        self.assertEqual(self.js("viewSettings.trackAltitudeOffset"), 105)
        self.assertEqual(self.js("routeCoordinates().map(p=>p[2])"), [345, 315, 300])
        self.assertEqual(self.js("map.terrain.exaggeration"), 3)
        self.assertGreaterEqual(self.js("map.fitCount"), 2)
        self.assertFalse(self.widget.follow_ground.isChecked())
        self.assertEqual(self.widget.altitude_factor, 3.0)
        reports = [call for call in self.js("bridgeCalls") if call[0] == "report_auto_settings"]
        self.assertEqual([json.loads(call[1]) for call in reports], [{"factor": 3, "offset": 105}])
        # The page's report updates the card's controls.
        self.widget.bridge.report_auto_settings(reports[0][1])
        self.assertEqual(self.widget.track_offset_input.value(), 105.0)
        self.assertEqual(self.widget.track_altitude_offset.value(), 1050)
        # Auto runs once by itself; the button runs it again after manual changes.
        self.widget.altitude_exaggeration.setValue(70)
        self.widget.track_offset_input.setValue(0)
        self.widget.on_series_changed()
        self.wait_loaded(self.widget)
        self.assertEqual(self.js("viewSettings.altitudeExaggeration"), 7)
        fits = self.js("map.fitCount")
        self.widget.auto_button.click()
        # The button only sets the offset: the exaggeration and the view stay.
        self.assertEqual(self.js("viewSettings.altitudeExaggeration"), 7)
        self.assertEqual(self.js("viewSettings.trackAltitudeOffset"), 105)
        self.assertEqual(self.js("map.fitCount"), fits)
        self.assertEqual(self.widget.altitude_factor, 7.0)

    def test_center_view_fits_the_track_without_changing_its_settings(self):
        self.load_map()
        self.widget.altitude_exaggeration.setValue(40)
        self.widget.track_offset_input.setValue(25)
        fits = self.js("map.fitCount")
        self.assertFalse(self.widget.center_view_button.icon().isNull())
        self.widget.center_view_button.click()
        self.assertEqual(self.js("map.fitCount"), fits + 1)
        self.assertEqual((self.js("viewSettings.altitudeExaggeration"), self.js("viewSettings.trackAltitudeOffset")),
                         (4, 25))

    def test_auto_follows_the_ground_when_the_track_has_no_altitude(self):
        with h5py.File(self.h5_path, "a") as handle:
            handle.move("series/GNSS/altitude_m", "series/Other/altitude_m")
            handle["series/Other/altitude_m"].attrs["navigator_group"] = "Other"
        widget = self.make_widget()
        self.load_map(widget, auto=True)
        self.assertTrue(widget.follow_ground.isChecked())
        self.assertEqual(self.js("routeCoordinates().map(p=>p[2])", widget), [GROUND_M * 3] * 3)

    def test_track_canvas_stays_above_the_faded_map(self):
        # Below 100% the track is on deck.gl's own canvas; the translucent map canvas must not cover it
        # (checked in a real browser: without this rule a track under the terrain stays hidden until 0%).
        from src.gui.plots.gnss_3d import _three_d_map_html
        self.assertIn("#deckgl-overlay{z-index:1}", _three_d_map_html())

    def test_scale_bar_is_shown_at_the_bottom_right_until_switched_off(self):
        self.load_map()
        self.assertEqual(self.js("map.scaleBar.position"), "bottom-right")
        self.assertEqual(self.js("map.scaleBar.options.unit"), "metric")
        # The map credit is MapLibre's small "i", at the top right, under the card's Close button.
        self.assertEqual(self.js("map.attribution.position"), "top-right")
        self.assertTrue(self.js("map.attribution.options.compact"))
        self.assertFalse(self.js("map.options.attributionControl"))
        # Placed just under the card's Close button row, with the legend below it.
        from PySide6.QtCore import QPoint
        from src.gui.plots.gnss import MAP_CREDIT_TOP
        self.widget.resize(600, 400)
        self.widget.show()
        self.app.processEvents()
        map_top = self.widget.map_container.mapTo(self.widget, QPoint(0, 0)).y()
        close_bottom = self.widget.close_button.geometry().bottom() + 1
        self.assertLessEqual(abs(map_top + MAP_CREDIT_TOP - close_bottom), 2)
        self.assertGreater(self.widget.legend.top_offset, MAP_CREDIT_TOP)
        self.widget.scale_bar_check.setChecked(False)
        self.assertIsNone(self.js("map.scaleBar"))
        self.assertFalse(self.widget.workspace_state()["map_controls"]["scale_bar"])
        # Switched off before the page loads, it stays off; a workspace restores it.
        widget = self.make_widget()
        widget.restore_workspace_state({"map_controls": {"scale_bar": False}})
        self.load_map(widget)
        self.assertIsNone(self.js("map.scaleBar", widget))
        widget.scale_bar_check.setChecked(True)
        self.assertEqual(self.js("map.scaleBar.position", widget), "bottom-right")

    def test_workspace_restores_controls_instead_of_auto(self):
        self.widget.restore_workspace_state({"map_controls": {
            "map_opacity": 60, "altitude_factor": 4.5, "track_offset": -20.0, "follow_ground": True}})
        self.load_map(auto=True)
        self.assertEqual((self.widget.map_opacity.value(), self.widget.altitude_factor,
                          self.widget.track_offset_input.value(), self.widget.follow_ground.isChecked()),
                         (60, 4.5, -20.0, True))
        self.assertEqual(self.js("viewSettings.altitudeExaggeration"), 4.5)
        self.assertEqual(self.widget.workspace_state()["map_controls"], {
            "map_opacity": 60, "altitude_factor": 4.5, "track_offset": -20.0, "follow_ground": True,
            "scale_bar": True})
        # An older workspace's hidden map becomes 0% opacity.
        self.widget.restore_workspace_state({"map_controls": {"map_visible": False, "map_opacity": 80}})
        self.assertEqual(self.widget.map_opacity.value(), 0)

    def test_explicit_height_overrides_recorded_altitude(self):
        height_path = self.h5_path.with_name("height.h5")
        with h5py.File(height_path, "w") as handle:
            group = handle.create_group("series/depth")
            group.attrs["unit"] = "m"
            group.create_dataset("time_unix", data=[1.0, 3.0])
            group.create_dataset("value", data=[-10.0, -20.0])
        widget = self.make_widget(height_series=(height_path, "depth"))
        self.load_map(widget)
        widget.altitude_exaggeration.setValue(25)
        self.assertEqual(self.js("routeCoordinates().map(p=>p[2])", widget), [-25, -37.5, -50])

    def test_track_without_altitude_draws_flat_at_zero(self):
        with h5py.File(self.h5_path, "a") as handle:
            handle.move("series/GNSS/altitude_m", "series/Other/altitude_m")
            handle["series/Other/altitude_m"].attrs["navigator_group"] = "Other"
        widget = self.make_widget()
        self.assertIsNone(widget.height_series)
        self.load_map(widget)
        widget.altitude_exaggeration.setValue(25)
        self.assertEqual(widget.legend.entries[2].text, "0 m (flat)")
        self.assertEqual(self.js("routeCoordinates().map(p=>p[2])", widget), [0, 0, 0])


if __name__ == "__main__":
    unittest.main()
