"""Live Windows coverage of forced batch/pool/converter shutdown."""

import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from src.jobs.parallel_jobs import run_process_jobs
from src.jobs.process_tree import kill_process_tree, popen_in_tree, start_process_tree


def _initialize(_queue, _cancel=None):
    pass


def _converter_worker(folder):
    code = ("import pathlib,sys,time; root=pathlib.Path(sys.argv[1]); "
            "(root/'ready').write_text('ready'); time.sleep(30); "
            "(root/'unexpected-write').write_text('not stopped')")
    process = subprocess.Popen([sys.executable, "-c", code, folder])
    process.wait()
    return process.returncode


def _failure(job, error):
    return str(error)


def _batch(gate, folder, pooled):
    if not gate.wait(15):
        return
    if pooled:
        run_process_jobs([folder], worker_count=1, worker=_converter_worker,
                         initializer=_initialize, emit_progress=lambda event: None,
                         make_failure_result=_failure)
    else:
        _converter_worker(folder)


@unittest.skipUnless(os.name == "nt", "Windows Job Objects")
class ProcessTreeTests(unittest.TestCase):
    def test_forced_parent_exit_cleans_pool_and_converter_descendants(self):
        for pooled in (False, True):
            with self.subTest(pooled=pooled), tempfile.TemporaryDirectory() as directory:
                context = multiprocessing.get_context("spawn")
                gate = context.Event()
                parent = context.Process(target=_batch, args=(gate, directory, pooled))
                tree = start_process_tree(parent, gate)
                try:
                    deadline = time.monotonic() + 20
                    while not (Path(directory) / "ready").exists() and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertTrue((Path(directory) / "ready").exists())
                    self.assertGreaterEqual(tree.active_processes(), 3 if pooled else 2)
                    parent.terminate()
                    parent.join(5)
                    self.assertFalse(parent.is_alive())
                    self.assertGreaterEqual(tree.active_processes(), 1)
                    # Production close() queries the job until ActiveProcesses is zero.
                    tree.close()
                    self.assertEqual(tree.active_processes(), 0)
                    self.assertFalse((Path(directory) / "unexpected-write").exists())
                finally:
                    tree.close()
                    parent.join(5)

    def test_failed_assignment_does_not_release_unowned_work(self):
        process, gate = mock.Mock(pid=123), mock.Mock()
        with mock.patch("src.jobs.process_tree.WindowsProcessTree") as owner:
            owner.return_value.assign.side_effect = OSError("assignment failed")
            with self.assertRaisesRegex(OSError, "assignment failed"):
                start_process_tree(process, gate)
        gate.set.assert_not_called()
        process.terminate.assert_called_once()
        owner.return_value.close.assert_called_once()

    def test_popen_in_tree_owns_children_started_at_once(self):
        """A CLI's children started right away cannot outlive Stop (the agent's process tree)."""
        code = ("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',"
                "\"import pathlib,sys,time; time.sleep(3); pathlib.Path(sys.argv[1]).write_text('x')\","
                "sys.argv[1]]); print('started', flush=True); time.sleep(30)")
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "child-wrote"
            process, tree = popen_in_tree([sys.executable, "-c", code, str(marker)],
                                          stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), "started")
                if tree is not None:
                    self.assertGreaterEqual(tree.active_processes(), 2)
            finally:
                kill_process_tree(process, tree)
                process.stdout.close()
            time.sleep(4)
            self.assertFalse(marker.exists())
            self.assertIsNotNone(process.poll())
