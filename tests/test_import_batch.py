import os
import sys
import tempfile
import unittest
import logging
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from src.jobs.import_batch import (  # noqa: E402
    ImportJob,
    MAX_IMPORT_PROCESSES,
    configured_import_worker_count,
    duplicate_output_paths,
    is_folder_info_file,
    discover_import_jobs,
)
from src.jobs.cache_files import is_cache_output


class ImportBatchTests(unittest.TestCase):
    def test_cache_write_files_never_reach_importer_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            # Caches, and LODGaZilla's own working files: an audio H5 being written, a folder
            # info lock and a folder info write.
            names = ('data.h5', 'data.hdf5', '.data.h5.abcd.tmp', '.data.HDF5.123.tmp',
                     '.talk.wav.audio.h5.x1y2.partial', 'run_info.json.lock', 'run_info.json.ab12.tmp',
                     'data.bin', 'ordinary.tmp')
            for name in names:
                (folder / name).touch()
            with mock.patch('src.jobs.import_batch.load_import_configurations', return_value=[]), \
                 mock.patch('src.jobs.import_batch.select_importer', side_effect=ValueError('unsupported')) as select:
                discover_import_jobs(folder, False, logging.getLogger('test'))
            self.assertEqual({call.args[0].name for call in select.call_args_list}, {'data.bin', 'ordinary.tmp'})
            self.assertFalse(is_cache_output(folder / '.data.bin.abcd.tmp'))

    def test_worker_count_is_capped(self):
        worker_count = configured_import_worker_count(MAX_IMPORT_PROCESSES * 3)

        self.assertGreaterEqual(worker_count, 1)
        self.assertLessEqual(worker_count, MAX_IMPORT_PROCESSES)
        self.assertLessEqual(worker_count, os.cpu_count() or 1)

    def test_worker_count_does_not_exceed_job_count(self):
        self.assertEqual(configured_import_worker_count(0), 0)
        self.assertEqual(configured_import_worker_count(1), 1)

    def test_worker_count_respects_per_import_limit(self):
        self.assertEqual(configured_import_worker_count(10, process_limit=2), 2)

    def test_duplicate_default_hdf5_outputs_are_detected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            jobs_by_folder = {
                folder: [
                    ImportJob(str(folder / "sample.bin"), str(folder), "config.ini", "Binary"),
                    ImportJob(str(folder / "sample.log"), str(folder), "config.ini", "Log"),
                ]
            }

            self.assertTrue(duplicate_output_paths(jobs_by_folder))

    def test_distinct_default_hdf5_outputs_are_not_duplicates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            jobs_by_folder = {
                folder: [
                    ImportJob(str(folder / "first.bin"), str(folder), "config.ini", "Binary"),
                    ImportJob(str(folder / "second.bin"), str(folder), "config.ini", "Binary"),
                ]
            }

            self.assertFalse(duplicate_output_paths(jobs_by_folder))

    def test_folder_info_json_is_not_an_import_source(self):
        folder = Path("Data") / "TestData1"

        self.assertTrue(is_folder_info_file(folder / "TestData1_info.json"))
        self.assertFalse(is_folder_info_file(folder / "info.json"))


if __name__ == "__main__":
    unittest.main()
