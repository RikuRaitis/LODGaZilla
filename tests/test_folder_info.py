import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from src.data.folder_info import (  # noqa: E402
    FolderInfoData,
    info_file_path,
    normalise_info_mapping,
    read_info_file,
    write_info_file,
)


class FolderInfoTests(unittest.TestCase):
    def test_images_roundtrip_alongside_video_and_metadata(self):
        data = FolderInfoData(
            editable={"comment": "keep"}, plugin={"PluginField": "keep"},
            videos=[{"path": "clip.mp4", "offset_seconds": "2.5"}],
            images=[{"path": "photos/first.png"}, {"path": "../second.jpg"}],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = write_info_file(directory, data)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["images"], data.images)
            reread = read_info_file(directory)
            self.assertEqual(reread.images, data.images)
            self.assertEqual(reread.videos, data.videos)
            self.assertEqual(reread.plugin, data.plugin)
            reread.editable["comment"] = "updated"
            write_info_file(directory, reread)
            self.assertEqual(read_info_file(directory).images, data.images)

    def test_older_metadata_has_no_images(self):
        self.assertEqual(normalise_info_mapping({"video": {"path": "clip.mp4"}}).images, [])

    def test_invalid_image_collection_is_rejected(self):
        for value in ({"path": "photo.png"}, ["photo.png"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalise_info_mapping({"images": value})

    def test_legacy_video_migrates_without_losing_offset_or_metadata(self):
        legacy = {
            "editable": {"Comment": "keep"},
            "plugin": {"MixedCase": "keep"},
            "video": {"path": "../clip.mp4", "offset_seconds": "-3.25"},
        }
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            info_file_path(folder).write_text(json.dumps(legacy), encoding="utf-8")
            info = read_info_file(folder)
            self.assertEqual(info.videos, [legacy["video"]])
            write_info_file(folder, info)
            raw = json.loads(info_file_path(folder).read_text(encoding="utf-8"))
            self.assertNotIn("video", raw)
            self.assertEqual(raw["videos"], [legacy["video"]])
            self.assertEqual(raw["editable"], {"comment": "keep"})
            self.assertEqual(raw["plugin"], legacy["plugin"])

    def test_multiple_videos_roundtrip_with_independent_offsets(self):
        videos = [
            {"path": "camera1/clip.mp4", "offset_seconds": "-2.5"},
            {"path": "camera2/clip.mp4", "offset_seconds": "10.75"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            write_info_file(directory, FolderInfoData(videos=videos))
            self.assertEqual(read_info_file(directory).videos, videos)

    def test_empty_video_list_supersedes_legacy_link(self):
        info = normalise_info_mapping({"videos": [], "video": {"path": "old.mp4"}})
        self.assertEqual(info.videos, [])

    def test_invalid_video_collection_is_rejected(self):
        for value in ({"path": "clip.mp4"}, ["clip.mp4"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalise_info_mapping({"videos": value})

    def test_split_info_json_canonicalises_key_casing(self):
        info = normalise_info_mapping({
            "editable": {"Comment": "OK"},
            "readonly": {"serial number": "123"},
        })

        self.assertEqual(info.editable, {"comment": "OK"})
        self.assertEqual(info.readonly, {"serial number": "123"})

    def test_non_object_section_is_rejected(self):
        with self.assertRaises(ValueError):
            normalise_info_mapping({"editable": "not an object", "readonly": {}})

    def test_write_info_file_uses_split_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir) / "FolderA"
            data = FolderInfoData(
                editable={"Comment": "OK"},
                readonly={"serial number": ""},
            )

            path = write_info_file(folder, data)
            raw = json.loads(path.read_text(encoding="utf-8"))
            reread = read_info_file(folder)

        self.assertEqual(path, info_file_path(folder))
        self.assertEqual(raw["editable"], {"comment": "OK"})
        self.assertEqual(raw["readonly"], {"serial number": ""})
        self.assertEqual(reread.editable, {"comment": "OK"})
        self.assertEqual(reread.readonly, {"serial number": ""})

    def test_casing_variants_collapse_to_one_key(self):
        info = normalise_info_mapping({
            "editable": {"Comment": "old", "comment": "new"},
            "readonly": {"COMMENT": "readonly"},
        })

        self.assertEqual(info.editable, {"comment": "new"})
        self.assertEqual(info.readonly, {"serial number": ""})


if __name__ == "__main__":
    unittest.main()
