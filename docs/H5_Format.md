# LODGaZilla H5 format and importer data contract

This guide describes the HDF5 files LODGaZilla reads and the data an importer
plugin returns to produce them. It is intended for developers and AI agents
implementing plugins or generating compatible data files. Checked against the
implementation on 2026-11-10; the host writer currently uses `schema_version =
"0.1"`. This schema identifier is separate from plugin `api_version = 1`.

For plugin packaging, manifests, dependencies and supported imports, see
[Plugin API](Plugin_API.md). Importer production code uses
`src.contracts.importer`; the implementation links at the end of this document
are references, not additional public plugin APIs.

## 1. Choose how to produce the file

**For an importer plugin, return the common series dictionary from
`DataParser.parse()`.** The host writes H5, transforms values, detects sample
loss/clipping, builds LOD levels and the Navigator index, and publishes the
finished cache atomically. Do not duplicate that implementation in each plugin.

**For an external exporter or standalone data generator, write the H5 layout
described below.** A small file can work without LOD, diagnostic groups or a
Navigator index. A complete standalone example is included in section 11.
Extra arbitrary HDF5 datasets are not automatically recognized as data series.

An importer can combine multiple same-folder sources through the optional
`ImportSet` contract described in [Plugin API](Plugin_API.md#multiple-inputs-producing-one-h5).
The host writes `import_set_key`, `import_set_title`, `source_files_json` and
`source_dependencies_json` as root attributes, alongside the legacy `source_file`
attribute identifying the member used to start the import. The JSON attributes
are lists of relative filenames, including dependencies used for time alignment.
These attributes let the Navigator reconstruct an expandable Source row without
folder metadata. Series payloads and the H5 series layout are unchanged.

The Navigator discovers `.h5` and `.hdf5` files in the selected folder tree.
H5 is the data source; folder metadata and image/video attachment links are
stored separately in `<folder-name>_info.json` (section 10).

## 2. Minimum usable series and general layout

Each data series is a **group below `/series`** containing a one-dimensional
numeric `value` dataset and a matching timestamp dataset. Nesting groups is
supported. Use one series per component: accelerometer X, Y and Z are three
series, not one `N x 3` dataset.

```text
recording.h5
  @schema_version = "0.1"
  @metadata_json = "{...}"                 optional source metadata
  @time_start_seconds, @time_end_seconds    file bounds, in seconds
  @log_start_unix_seconds                  optional relative-clock anchor
  /series
    /Accelerometer
      /X
        @navigator_group = "Accelerometer"
        @navigator_name = "X"
        @unit = "m/s^2"
        @time_dataset = "time_unix"
        @time_unit = "s"
        @time_reference = "unix"
        time_unix                         float64[N]
        value                             numeric[N], plotted values
        value_raw                         optional numeric[N], original values
        /sample_loss_list                 optional diagnostic intervals
        /clipping_list                    optional diagnostic intervals
        /lod                              optional display acceleration
          /level_001
          /level_002
      /Y
      /Z
    /GNSS
      /latitude_deg
      /longitude_deg
      /altitude_m
```

`@` above denotes an HDF5 attribute, not a dataset. Set these series attributes
explicitly even where individual readers have fallbacks:

| Attribute | Type | Meaning |
| --- | --- | --- |
| `navigator_name` | UTF-8 string | Leaf name displayed in the Navigator; e.g. `X`. |
| `navigator_group` | UTF-8 string | Logical group path, e.g. `Sensors/Accelerometer`. Use `""` for an ungrouped series. |
| `time_dataset` | UTF-8 string | Name of the timestamp dataset accessible through this series group. |
| `time_unit` | UTF-8 string | Use `s`, `ms`, `us` or `ns`. Explicit units avoid inconsistent legacy inference. |
| `time_reference` | UTF-8 string | `unix` or, for a relative device clock, `device_relative`. |
| `unit` | UTF-8 string | Unit of `value`, e.g. `V`, `m/s^2`, `deg`, `m`; `bool` and `index` select event/state handling. |

Keep the full logical name (`navigator_group/navigator_name`) unique within
one file. HDF5 paths and logical names may differ: the host replaces characters
outside `A-Z`, `a-z`, `0-9`, `_`, `.`, `-` with underscores in each physical path
component, while retaining the original Navigator names in attributes. Avoid
names which collide after this conversion. Do not put a `/` in a leaf name;
use `navigator_group` for hierarchy.

### Array requirements

- `time` and `value` are both shape `(N,)`, with the same length and sample order.
- Use finite, ascending timestamps. Strictly increasing is the safest choice;
  repeated timestamps are retained by the generic writer, but are not useful
  to every analysis. Unwrap counter rollover and resolve clock resets before
  returning data. A `sample_rate_hz` attribute does not replace timestamps.
- Use real numeric values: boolean, signed/unsigned integer, or floating-point.
  Encode categorical states as numeric IDs with labels. String, object, complex,
  compound-record and multidimensional values are not supported series formats.
- Floating-point NaN values can preserve missing/invalid measurements; keep
  their timestamps. LOD calculations exclude nonfinite values from extrema.
  Do not fill missing measurements with zero merely to make a dense array.
- Prefer `float64` timestamps. Readers convert time to float64; integer Unix
  nanoseconds therefore do not retain exact nanosecond precision in plots.
  Relative timestamps with a separate epoch anchor avoid storing a large epoch
  number in every sample, although plotting still has floating-point limits.
- Empty series are skipped by the generic importer writer. Omit unavailable
  channels rather than advertising an empty series in an index.

## 3. Timestamps: absolute, relative, independent or shared

### Absolute UTC timestamps

The simplest interoperable choice is Unix seconds in `float64`:

```text
time_dataset   = "time_unix"
time_unit      = "s"
time_reference = "unix"
time_unix      = [1700000000.000, 1700000000.010, ...]
```

Other units work when declared explicitly, e.g. `time_ms`, `time_unit="ms"`,
`time_reference="unix"`. Canonical time is `stored_time * seconds_per_unit`.
Do not store timezone-local wall-clock numbers, datetime strings, GPS week/TOW,
or device ticks while labeling them Unix time. The importer must convert those
representations, including any needed epoch/time-scale correction.

The name **`time_unix` itself also makes readers treat the axis as Unix time**.
Never use that name for relative timestamps, even with
`time_reference="device_relative"`.

### Device-relative timestamps and the common file origin

For a device clock in milliseconds, use:

```text
series attributes:
  time_dataset   = "time_ms"
  time_unit      = "ms"
  time_reference = "device_relative"
file attributes:
  time_start_seconds     = 12.0
  log_start_unix_seconds = 1700000000.0
```

Here device time **12000 ms corresponds to Unix time 1700000000 seconds**.
The reader computes:

```text
canonical Unix seconds = log_start_unix_seconds
                       + stored_time * seconds_per_unit
                       - file.time_start_seconds
```

`time_start_seconds` is in **seconds**, even when samples use milliseconds.
It is a common file-level origin, not each series' first timestamp. A series
whose first sample is 12500 ms must begin 0.5 seconds after the above anchor;
do not independently subtract the first sample from every series.

If `time_start_seconds` is absent, the reader falls back to each series' first
sample as its origin. That can incorrectly align channels which started at
different times. If `log_start_unix_seconds` is absent, the canonical-time helper
leaves relative timestamps in native units; cross-file and mixed-unit alignment
is then not reliable. Provide an anchor for reliable synchronized plotting, or
normalize all series to a common seconds axis when no absolute epoch is known.

For a relative-clock file, set root bounds to the minimum and maximum native
times converted to seconds. For a Unix-clock file they are Unix seconds.
Avoid mixing Unix and device-relative series in one file: the generic writer
combines their bounds without first reconciling their epochs. Convert them to
one common clock in the importer instead.

**Current importer limitation:** the generic writer derives
`log_start_datetime` and `log_start_unix_seconds` from a source filename containing
`YYYYMMDD_HHMMSS` or `YYYYMMDD-HHMMSS`, interpreted in the importing machine's
local timezone on that date. It does not promote a timestamp inside
`import_result["metadata"]` to these root attributes. When the source provides
an authoritative UTC clock, return Unix timestamps rather than relying on a
filename or an undocumented payload field.

### Per-series timestamps

Each series may have its own timestamp array, sample count and rate. This is
the host writer's normal layout. For example, 100 Hz acceleration and 1 Hz
temperature need no padding or forced resampling. X/Y, 3D and map readers align
their sources in time; do not assume the same array index means the same instant.

### Shared timestamps

For channels sampled at exactly the same instants, an external H5 writer can
store one dataset and give each series a **local hard link** to it:

```python
# Within an open h5py.File named h5:
shared = h5.create_dataset("clocks/imu_ms", data=imu_times)
x = h5.create_group("series/IMU/X")
y = h5.create_group("series/IMU/Y")
x["time_ms"] = shared
y["time_ms"] = shared
# Set time_dataset="time_ms", time_unit="ms", time_reference, names and values
# on EACH series. These two links refer to one stored array, without copying it.
```

Use local conventional names such as `time_ms` or `time_unix` for the links.
Although HDF5 can resolve absolute paths through `time_dataset`, diagnostics and
LOD derive their dataset names from this string; a path like `/clocks/imu_ms`
does not follow the normal naming convention. Links within one file also avoid
dependencies on another file's location. Every linked series must have exactly
as many values as the shared clock has timestamps and the same time semantics.

The importer payload has no shared-clock identifier: supply `time` for each
series. Reusing the same NumPy array in those payloads is fine, but the generic
host writer currently writes a timestamp dataset for each series; it does not
deduplicate them into HDF5 links.

A hard link refers to the same mutable HDF5 object: changing that clock changes
the timestamps of every linked series. Rebuild affected bounds, diagnostics,
LOD and index entries whenever samples or timestamps change.

## 4. Values, units, transformations and series attributes

The dataset named **`value` is what is plotted**, including any conversion
already applied by the host writer. `value_raw` preserves the original values
for marker details. The two may have different numeric dtypes but must have the
same sample count and ordering. If the values are identical, a standalone writer
can hard-link `value_raw` to `value` to save space. Do not do that when a conversion
has changed the values.

| Optional attribute | Meaning |
| --- | --- |
| `display_name` | Display fallback; normally the same as `navigator_name`. |
| `description`, `source` | Human-readable description and source identifier. |
| `original_unit` | Unit of `value_raw`; `unit` always describes plotted `value`. |
| `visible` | HDF5 boolean; hidden/irrelevant series can be filtered in the Navigator. Do not store the string `"false"`. |
| `sample_count` | Integer length of `value`. |
| `sample_rate_hz` | Representative rate; host computes `(N-1)/(last-first)` in seconds. For irregular data this is an average, not a guarantee. |
| `time_start_seconds`, `time_end_seconds`, `duration_seconds` | Bounds in seconds and their difference, in the series' clock reference. |
| `state_labels_json` | JSON object mapping numeric state IDs, as string keys, to labels. |
| `max_sample_interval_ms` | Gap-detection threshold, always in milliseconds. |
| `min_value`, `max_value` | Clipping thresholds, not the observed data extrema. |
| `transform_original_axis`, `transform_formula` | Descriptive provenance written by the host; these do not trigger plot-time conversion. |

Ordinary unit strings are labels, not a general automatic conversion system.
For example, writing `unit="m"` does not convert centimeters to meters.
Convert in the parser or use the host's affine transform payload (section 9).
GNSS unit handling and `bool`/`index` routing are explicit special cases.

Extra custom attributes and groups can be stored for other consumers, but they
do not automatically appear in the Navigator or change card behavior. Avoid
reusing reserved names with different meanings.

## 5. Events, states and GNSS

### Events and states are numeric series too

Use `unit="bool"` for boolean states, or `unit="index"` for numeric event/state
codes. Card selection and Multi-series identify timeline data by those units, by
an `event_state_kind` attribute (`"event"` or `"state"`), or by a boolean dtype
(`is_event_state_group` in `src/data/h5_series.py`). Prefer the unit: it also
says what the values are.

For named states, store an attribute such as:

```json
{"0": "Idle", "1": "Recording", "2": "Paused"}
```

as the JSON **string** `state_labels_json` on the series group. In an importer
payload, pass the Python dictionary as `state_labels` instead.

`event_state_kind="state"` or `"event"` decides how the timeline renderer draws
the samples. Without it, a nonempty label map or binary values make them states.
State transitions begin at their recorded times, with no invented state before
the first sample. Boolean states extend to the file end after their last
transition; other states end at their last timestamp. Other samples render as
individual events, each named from the label map when it has one (such as
`{"1": "Left foot", "2": "Right foot"}`). `event_state_kind` is read from H5 but is **not** currently copied from
an arbitrary importer payload by the generic writer; prefer units and labels.

### GNSS naming and units

For predictable map discovery, use logical names such as
`GNSS/latitude_deg`, `GNSS/longitude_deg`, and `GNSS/altitude_m`, with units
`deg`, `deg`, and `m`. Keep each track's coordinates in the same logical group
and H5 file; unrelated series in that group are allowed.

The card-selection rules recognize these leaf names, case-insensitively:

| Role | Names |
| --- | --- |
| Latitude | `lat`, `latitude`, `lat_deg`, `latitude_deg`, `latitude_rad` |
| Longitude | `lon`, `longitude`, `lon_deg`, `longitude_deg`, `longitude_rad` |
| Altitude | `alt`, `altitude`, `altitude_m`, `height`, `height_m`, `elevation`, `elevation_m` |

The map reader also has legacy naming heuristics; use `latitude_deg` and
`longitude_deg` rather than depending on those heuristics. Explicit coordinate
units support degrees (`deg`), radians (`rad`), E7 integers (`e7` or `deg*1e7`),
and tenths of a degree (`deg*10`). New plugins should preferably emit decimal
degrees. The map excludes nonfinite coordinates and coordinates outside valid
latitude/longitude limits after conversion. Store altitude in meters; a unit
label does not perform arbitrary altitude conversion. X/Y and Cartesian 3D
series need no special names: their axes are assigned by the user.

## 6. Sample loss and clipping

Diagnostics have two parts: configuration describing what to detect, and
stored intervals describing what was detected. Setting thresholds on an
already-written H5 file does not cause the plot reader to generate intervals.

### Automatic detection when importing

- `max_sample_interval_ms`: a gap is recorded when consecutive timestamps differ
  by **more than** this positive threshold. The stored interval runs from the
  last sample before the gap to the first sample after it. There is no separate
  missing-sample count. Omit the threshold for event-driven/irregular data when
  a long interval is not an error.
- `min_value` and/or `max_value`: a finite plotted value **at or below** the
  minimum or **at or above** the maximum is clipped. Consecutive clipped sample
  indices form one interval; its bounds are the first and last clipped sample's
  timestamps. An isolated clipped sample has equal start and stop times. NaN
  and infinity are not counted as clipped samples.
- Without the corresponding threshold, the host does not infer a failure from
  sample rate, observed extrema or NaNs alone.

### Stored diagnostic layout

For `time_dataset="time_ms"`:

```text
/series/Accelerometer/X/sample_loss_list
  @sample_loss_count = 1
  @messages_json = '["Sampleloss detected"]'
  sample_loss_start_ms = [20.0]
  sample_loss_stop_ms  = [50.0]
/series/Accelerometer/X/clipping_list
  @clipping_count = 1
  @messages_json = '["Clipping detected"]'
  clipping_start_ms = [60.0]
  clipping_stop_ms  = [60.0]
```

Start and stop datasets are parallel one-dimensional numeric arrays. Each pair
describes one interval in the series' **native timestamp units and reference**,
not necessarily Unix seconds. Store sorted intervals with start <= stop. Counts
are numbers of intervals. For zero intervals, the host writes the group with
count `0` and `messages_json="[]"`, without start/stop datasets. Omitting the
group entirely is also read as no diagnostic intervals.

Names follow the literal rule
`suffix = time_dataset.removeprefix("time_")`:

| `time_dataset` | Sample-loss datasets | Clipping datasets |
| --- | --- | --- |
| `time_unix` | `sample_loss_start_unix`, `sample_loss_stop_unix` | `clipping_start_unix`, `clipping_stop_unix` |
| `time_ms` | `sample_loss_start_ms`, `sample_loss_stop_ms` | `clipping_start_ms`, `clipping_stop_ms` |
| `time_s` | `sample_loss_start_s`, `sample_loss_stop_s` | `clipping_start_s`, `clipping_stop_s` |
| `time` | `sample_loss_start_time`, `sample_loss_stop_time` | `clipping_start_time`, `clipping_stop_time` |

Notice the `_time` suffix for the plain dataset name `time`. Do not guess names
from `time_unit` alone. Legacy sample-loss groups named `failure_list` are also
read; new files should use `sample_loss_list`.

The Navigator derives warnings from these groups. Plot readers need the actual
start/stop datasets; a message or positive count alone cannot draw a highlight.
The minimum diagnostic width setting only widens narrow highlights on screen;
it does not alter these stored intervals.

Importer payloads currently specify thresholds; there is no supported arbitrary
`sample_loss_list`/`clipping_list` payload passthrough. External writers can store
precomputed intervals using the above layout.

## 7. LOD: optional precomputed display levels

LOD means level of detail. It accelerates zoomed-out time plots while retaining
extrema. It supplements `value` and the original timestamps; never replace
those datasets with downsampled data.

`/series/<series>/lod` has `levels` (integer) and, when levels exist,
`top_level` (e.g. `"level_003"`). The lowest-numbered level present is the finest
aggregated level (the host writes none finer than 16 samples per bucket, so its numbering
may start at `level_002`);
larger level numbers contain larger buckets and fewer points. `levels=0` and
no level subgroups is valid for a short recording. Omitting `lod` is also valid,
but large plots may need more reads and work.

Each `level_###` stores attributes `source_sample_count`, `bucket_size_samples`
and `bucket_count`, plus these equal-length arrays, one row per bucket:

| Dataset | Meaning |
| --- | --- |
| `time_start_<suffix>`, `time_end_<suffix>` | Timestamps of the first and last sample in each bucket. |
| `time_min_<suffix>`, `time_max_<suffix>` | Timestamps of the minimum and maximum value, respectively; not time extrema. |
| `value_min`, `value_max` | Finite value extrema. |
| `value_first`, `value_last` | First and last values, preserving boundary continuity. |
| `sample_index_min`, `sample_index_max` | Zero-based indices into the original arrays. |
| `count`, `finite_count` | Total samples and finite-value samples in the bucket. |

Use the same suffix rule as diagnostics, e.g. `time_start_unix` or
`time_start_time`. Bucket times use the original timestamp units/reference.
Buckets cover contiguous ranges of original samples; the final bucket may be
shorter. For floating-point buckets with no finite values, min/max values and
their timestamps are NaN and extrema indices are `-1`.

The host multiplies bucket size by the configured `lod_bucket_growth` at each
level and stops when the bucket count is at most `lod_target_buckets`. The plot
selects a level for its current point budget. X/Y, Cartesian 3D and maps instead
sample original series according to their card sample limit; LOD is not a
substitute for retaining every original sample. Importer plugins should let the
host build these structures.

### Chunking and compression

The host writes one-dimensional chunked datasets with the shuffle filter. Chunk
length is at most the HDF5 setting `chunk_rows` and the dataset length.
`compression` controls storage compression (`none`, `gzip` or `lzf` in the
application settings); `compression_level` applies to gzip. These settings and
the LOD bucket settings belong to the host, not to an individual source format.
Compression does not change timestamp or value semantics.

For externally written large files, use chunked datasets so readers can load a
time window without loading every sample. Prefer standard filters available in
the application's h5py installation. A tiny demonstration file may use
contiguous, uncompressed datasets, as the example below does. Store textual
attributes as UTF-8 strings; serialize dictionaries/lists into the documented
JSON attributes instead of placing Python objects in numeric datasets.

## 8. File metadata and the Navigator index

The generic host writer adds these root attributes:

| Attribute | Meaning |
| --- | --- |
| `schema_version` | Current host format identifier, string `"0.1"`. |
| `tool`, `tool_version`, `created_utc` | Writer identity, the LODGaZilla version that wrote the file (such as `1.0.0-beta.1`), and creation timestamp. |
| `source_file`, `source_size_bytes` | Original source provenance. |
| `config_file`, `config_name`, `importer`, `importer_format` | Import configuration and format provenance. |
| `metadata_json` | JSON string of the import result's `metadata` dictionary. |
| `series_count` | Number of written series. |
| `time_start_seconds`, `time_end_seconds`, `duration_seconds` | Overall bounds and duration (see time-reference rules). |
| `log_start_datetime`, `log_start_unix_seconds` | Optional calendar-time origin. |
| `series_index_json` | Optional JSON list used to populate the Navigator efficiently. |

The `tool` and `source_file` values also control whether the importer may replace
an existing cache. A standalone exporter should use its own truthful provenance,
not pretend its output is a disposable host cache. Use temporary files and an
atomic rename/replace to avoid the Navigator seeing a half-written H5 file.

### Index entries

Without `series_index_json`, the Navigator walks `/series` and reads series
attributes. For standalone files, **omitting the index is safer than writing an
incomplete or stale one**. A valid but empty index hides otherwise valid series.

The host writes entries like this (stored as a JSON string on the root):

```json
[
  {
    "name": "Accelerometer/X",
    "display_name": "X",
    "navigator_name": "X",
    "navigator_group": "Accelerometer",
    "unit": "m/s^2",
    "sample_count": 6,
    "sample_rate_hz": 71.4285714286,
    "visible": true,
    "time_start_seconds": 0.0,
    "time_end_seconds": 0.07,
    "duration_seconds": 0.07,
    "navigator_metadata": [
      {"id": "unit", "title": "Unit", "format": "text", "value": "m/s^2"}
    ],
    "diagnostic_warning": ""
  }
]
```

`diagnostic_warning` is the Navigator's warning text for the series ("Sampleloss
detected", "Clipping detected", "Sampleloss and Clipping detected", or empty), matching
its `sample_loss_list` and `clipping_list`. When every entry has it, the Navigator does not
walk the file for warnings; without it, the warnings are read from the groups.

Include every series and keep index names, counts, units, visibility and bounds
consistent with the actual groups. `name` is the series' identity in LODGaZilla (the key
the importer used); it need not equal `navigator_group/navigator_name`, because readers map
it to its group through those two fields. `navigator_metadata` entries provide logical
Navigator columns with `id`, `title`, `format` and a resolved `value`. Supported
formatters are `integer`, `number`, `rate` and ordinary text. The index is a
cache of presentation metadata, not a place to store sample arrays.

## 9. Importer payload and presentation configuration

`DataParser.parse()` returns a mapping with `format`, optional `metadata`, and
`series`. Each `series` entry contains arrays named **`time` and `values`**;
the H5 dataset is singular **`value`**. This distinction matters.

This executable example builds an import result; a real parser replaces the
synthetic arrays with decoded source data:

```python
import numpy as np


def build_import_result():
    times = 1700000000.0 + np.array([0, 0.01, 0.02, 0.05, 0.06, 0.07])
    return {
        "format": "example-sensor",
        "metadata": {"device": "Example logger", "serial_number": "DEMO-001"},
        "series": {
            "Accelerometer/X": {
                "navigator_group": "Accelerometer",
                "navigator_name": "X",
                "description": "Acceleration along X",
                "source": "channel_0",
                "time": times,
                "values": np.array([0, 1, 2, 9, 10, 2], dtype=np.int16),
                "time_unit": "s",
                "time_reference": "unix",
                "time_sorted": True,
                "unit": "m/s^2",
                "original_unit": "m/s^2",
                "visible": True,
                "max_sample_interval_ms": 15,
                "min_value": -10,
                "max_value": 10,
                "navigator_metadata": [
                    {"id": "count", "title": "Count", "source": "sample_count", "format": "integer"},
                    {"id": "warning", "title": "Warning", "source": "warning", "format": "text"},
                ],
            }
        },
    }
```

The writer filters nonfinite timestamps with their matching values, checks that the
timestamps ascend and, when they do not (for example after a clock reset), performs a
stable time sort, whatever `time_sorted` says. Value NaNs are not removed by this
normalization. Complex values are refused; store magnitude and phase, or real and
imaginary parts, as separate series.

Prefer the helpers exposed by `src.contracts.importer`:

- `DataParser.make_series()` / `add_series()` use definitions from `importer.json`.
  A series with no matching definition is not added.
- `SeriesDefinition` and `make_series_from_definition()` support dynamically
  discovered channels without importing host internals.
- Helpers return the same payload format; their default time reference is
  `unix`. Always override it when emitting device-relative time.

An example presentation section in `importer.json` is:

```json
{
  "navigator": {
    "series_metadata": [
      {"id": "rate", "title": "Rate", "source": "sample_rate", "format": "rate"}
    ]
  },
  "sections": [
    {
      "name": "Sensors",
      "series": [
        {
          "name": "X",
          "group": "Accelerometer",
          "unit": "m/s^2",
          "original_unit": "counts",
          "visibility": "visible",
          "max_sample_interval_ms": 15,
          "min_value": -32768,
          "max_value": 32767,
          "transform": {"source": "$self", "scale": 0.001, "offset": 0}
        }
      ]
    }
  ]
}
```

This is the data-presentation configuration, not a complete plugin manifest.
`visibility` is `visible` or `hidden` in JSON configuration; it becomes the
boolean payload/H5 attribute `visible`. `state_labels` is also accepted in each
series definition. Per-series `navigator_metadata` can override the global list.
Metadata `source` can be `sample_count`, `sample_rate`, `min`, `max`, `unit`,
`warning`, `original_axis` or `formula`; the writer resolves these to index
values. Arbitrary custom source keys are not automatically evaluated.

### Affine transforms

The host applies `value = source_values * scale + offset`. The optional
`transform.source` names a series in the same Navigator group. `$self` is
expanded by the configuration parser. Keep source and target time arrays aligned;
the writer checks sample count but does not resample the transform source.
`transform.formula`, if supplied, is descriptive text, not evaluated Python.
`series_transform_defaults` can provide a default transform in configuration.

When both clipping limits are provided, the writer transforms them too and
sorts the resulting pair (important for a negative scale). A one-sided limit is
currently left unchanged by that conversion helper. For nonidentity transforms,
use both raw-domain limits or preconvert the values and thresholds in the parser.
Do not apply the same conversion in the parser and again in the host transform.

Only documented fields are consumed. Extra per-series payload keys are not
automatically copied to H5 attributes. Extra top-level `metadata` is preserved
inside `metadata_json`, but is not an arbitrary root-attribute passthrough.

## 10. Folder metadata, attachments and audio

The Navigator's Info rows and folder grouping/filtering use the sidecar
`<folder-name>_info.json`, not arbitrary H5 attributes or `metadata_json`.
Plugin code should use `src.contracts.metadata` for transactional updates so
user edits and concurrent jobs are preserved.

```json
{
  "editable": {"activity": "Example measurement"},
  "readonly": {"serial number": "DEMO-001"},
  "plugin": {},
  "videos": [{"path": "camera.mp4", "offset_seconds": "0"}],
  "images": [{"path": "photo.jpg"}],
  "audio": [{"path": "sound.wav.audio.h5", "source": "sound.wav", "offset_seconds": "0"}]
}
```

Attachment paths are relative to the data folder unless absolute. Images and
videos are external media files; embedding image arrays or video bytes in an
ordinary H5 dataset does not create an Image or Video card. Removing an
attachment link must not delete its original media file.

Audio H5 files use the same numeric-series layout, with root
`media_kind="audio"`, `importer="audio"`, and `media_offset_seconds`. The host
creates `Media/Audio` for mono or `Media/Audio left` and `Media/Audio right` for
stereo, storing signed 16-bit PCM counts with `unit="counts"`. Timestamps are
relative seconds in `time_s`; LOD uses the same rules as other numeric series.
When known, `folder_log_start_unix_seconds` is the folder anchor and
`log_start_unix_seconds` is that anchor plus the media offset. The sidecar's
audio link may also contain `video`, identifying the video's soundtrack.
The host media import process manages conversion and synchronization; ordinary
sensor importers do not need to implement this special media workflow.

## 11. Standalone H5 example: shared clocks, independent clocks, diagnostics

Run this with `h5py` and `numpy` installed. It creates `example.h5` in the current
directory. Put the file in a data folder and refresh the Navigator. It has no
LOD or precomputed index, making the essential format easier to inspect.

```python
import json
from pathlib import Path

import h5py
import numpy as np


def write_example(path):
    # Exclusive creation protects any existing example/data file.
    with h5py.File(path, "x") as h5:
        h5.attrs.update({
            "schema_version": "0.1",
            "tool": "Example external exporter",
            "metadata_json": json.dumps({"device": "Example logger"}),
            "log_start_unix_seconds": 1700000000.0,
            "time_start_seconds": 0.0,
            "time_end_seconds": 0.07,
            "duration_seconds": 0.07,
            "series_count": 4,
        })
        clock = h5.create_dataset("clocks/imu_ms", data=np.array([0, 10, 20, 50, 60, 70], dtype="f8"))

        def add_series(group_name, name, values, unit, times):
            group = h5.create_group(f"series/{group_name}/{name}")
            if isinstance(times, h5py.Dataset):
                group["time_ms"] = times  # Hard link: no timestamp duplication.
            else:
                group.create_dataset("time_ms", data=np.asarray(times, dtype="f8"))
            values = np.asarray(values)
            assert values.ndim == 1 and len(values) == len(group["time_ms"])
            group.create_dataset("value", data=values)
            group["value_raw"] = group["value"]  # Valid because no transform was applied.
            group.attrs.update({
                "navigator_group": group_name,
                "navigator_name": name,
                "display_name": name,
                "unit": unit,
                "original_unit": unit,
                "visible": True,
                "sample_count": len(values),
                "time_dataset": "time_ms",
                "time_unit": "ms",
                "time_reference": "device_relative",
            })
            return group

        x = add_series("IMU", "X", [0, 1, 2, 9, 10, 2], "m/s^2", clock)
        add_series("IMU", "Y", [1, 2, 1, 2, 3, 2], "m/s^2", clock)
        add_series("Environment", "Temperature", [20.0, 20.5, 21.0], "degC", [0, 35, 70])
        state = add_series("Status", "Recording", [0, 1], "bool", [5, 35])
        state.attrs["state_labels_json"] = json.dumps({"0": "Off", "1": "On"})

        x.attrs.update(max_sample_interval_ms=15.0, min_value=-10.0, max_value=10.0)
        loss = x.create_group("sample_loss_list")
        loss.attrs.update(sample_loss_count=1, messages_json=json.dumps(["Sampleloss detected"]))
        loss.create_dataset("sample_loss_start_ms", data=[20.0])
        loss.create_dataset("sample_loss_stop_ms", data=[50.0])
        clip = x.create_group("clipping_list")
        clip.attrs.update(clipping_count=1, messages_json=json.dumps(["Clipping detected"]))
        clip.create_dataset("clipping_start_ms", data=[60.0])
        clip.create_dataset("clipping_stop_ms", data=[60.0])


if __name__ == "__main__":
    write_example(Path("example.h5"))
```

`IMU/X` and `IMU/Y` share one stored time dataset. Temperature has its own
sampling instants. Recording starts 5 ms after the common origin, switches on
at 35 ms, and persists to the file end. The X diagnostic intervals are already
computed; the attributes alone would not create them in this standalone file.

## 12. Validation checklist for developers and AI agents

1. Return the documented importer payload, or create a compatible `/series`
   tree. Do not mix payload `values` with H5 `value`.
2. Confirm every series has matching 1-D time/value arrays, finite ascending
   timestamps and a supported real numeric dtype. Verify `time_sorted` honestly.
3. Check a known first, middle and last sample after any unit/axis conversion.
   Confirm `value_raw`, `original_unit` and `unit` describe the right arrays.
4. Verify a known UTC instant and two channels with different start times. Test
   cross-file alignment if your plugin supports it. Do not silently label a
   device counter as Unix time.
5. For shared clocks, confirm hard links resolve and lengths match. Keep a local
   timestamp name on each series, including its own time metadata attributes.
6. Check unique logical and physical series names and correct visibility. If an
   index is present, compare it to all actual series; otherwise omit it.
7. Exercise a gap exactly at the threshold and one above it, values exactly at
   the clipping limits, an isolated clipped point, and no-failure data. Verify
   interval suffixes, counts and native time units.
8. For state data, check routing via `bool`/`index`, label JSON, transition times
   and file-end behavior. For GNSS, check names, grouping and coordinate units.
9. Retain all original samples when adding LOD; check first/last values, extrema
   timestamps, partial final buckets and all-NaN buckets. Generate LOD through
   the host for plugins.
10. Test no absolute start time, empty/missing channels and malformed inputs.
    Report unsupported data rather than inventing units, origins or samples.
11. Open the generated file in the Navigator; inspect numeric and state cards,
    marker values, diagnostics and a zoomed-out view. Run the plugin's tests
    against the host using the [Plugin API test workflow](Plugin_API.md#test-ownership-and-execution).
12. Keep format-specific parsing and fixtures in the plugin repository. Preserve
    source files, folder metadata and user settings during conversion.

## Implementation references

- [Importer public contract](../src/contracts/importer.py) and
  [parser helpers/configuration](../src/data/parser_base.py).
- [Host H5 writer, normalization, transforms, diagnostics and LOD](../src/jobs/import_data.py).
- [Shared series lookup and time conversion](../src/data/h5_series.py).
- [Navigator discovery, index and metadata](../src/data/navigator_data.py).
- [Plot data and diagnostic readers](../src/gui/plots/series.py),
  [event/state reader](../src/gui/plots/event_state.py), and
  [GNSS reader](../src/gui/plots/gnss.py).
- [Card selection and coordinate names](../src/data/plot_policy.py).
- [Folder/attachment metadata](../src/data/folder_info.py) and
  [audio H5 writer](../src/jobs/media_import.py).
- [Host writer regression tests](../tests/test_import_helpers.py) and
  [parser helper tests](../tests/test_parser_base.py).
