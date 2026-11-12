"""Magic drop without a real Claude: the CLI adapter, the draft, the trial import and the job."""
import dataclasses
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import h5py  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.ai import claude_cli, codex_cli, magic_drop  # noqa: E402
from src.ai.providers import CLAUDE, CODEX, provider_named  # noqa: E402
from src.app.settings import DEFAULT_AI_SETTINGS, sanitize_ai_settings  # noqa: E402
from src.data.folder_info import read_info_file  # noqa: E402
from src.jobs.magic_drop import MagicDropJob, SignInCheck  # noqa: E402
from tests.qt_wait import qt_wait_until  # noqa: E402

# What Claude would write: a parser for "time_s,temperature_C,pressure_hPa" CSV files.
CSV_PARSER = '''
from pathlib import Path
import numpy as np
from src.contracts.importer import DataParser


class Parser(DataParser):
    name = "Test CSV"
    priority = 50
    extensions = frozenset({".csv"})
    format_name = "test.csv"

    def can_parse(self, input_path):
        return Path(input_path).read_text(encoding="utf-8").startswith("time_s,")

    def parse(self, input_path, progress_callback=None):
        rows = np.loadtxt(input_path, delimiter=",", skiprows=1, ndmin=2)
        series = {}
        for column, (name, unit) in enumerate((("Temperature", "C"), ("Pressure", "hPa")), start=1):
            series[f"Weather/{name}"] = {
                "navigator_group": "Weather", "navigator_name": name, "time": 1700000000.0 + rows[:, 0],
                "values": rows[:, column], "time_unit": "s", "time_reference": "unix", "time_sorted": True,
                "unit": unit, "visible": True,
            }
        return {"format": "test.csv", "metadata": {}, "series": series}
'''

# A stand-in for the Claude CLI: writes the parser and metadata.json, prints stream-json.
FAKE_CLAUDE = f'''
import json, sys
from pathlib import Path
prompt = sys.stdin.read()
Path("importer.py").write_text({CSV_PARSER!r}, encoding="utf-8")
Path("metadata.json").write_text(json.dumps({{"device": "Test logger", "rows": 3}}), encoding="utf-8")
print(json.dumps({{"type": "system", "subtype": "init", "session_id": "s1"}}))
print(json.dumps({{"type": "assistant", "message": {{"content": [
    {{"type": "text", "text": "A CSV with two channels."}},
    {{"type": "tool_use", "name": "Write", "input": {{"file_path": "C:/draft/importer.py"}}}}]}}}}))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": "Done: 2 series.",
                   "session_id": "s1", "total_cost_usd": 0.12}}))
'''

CSV_TEXT = "time_s,temperature_C,pressure_hPa\n0,20.5,1013.2\n1,20.6,1013.1\n2,20.8,1013.0\n"


# A stand-in for the Codex CLI: checks its instructions file, writes the parser, prints codex exec JSONL.
FAKE_CODEX = f"""
import json, sys
from pathlib import Path
prompt = sys.stdin.read()
assert "AGENTS.md" in prompt and Path("AGENTS.md").is_file() and not Path("CLAUDE.md").exists()
Path("importer.py").write_text({CSV_PARSER!r}, encoding="utf-8")
Path("metadata.json").write_text(json.dumps({{"device": "Codex logger"}}), encoding="utf-8")
command = '"C:/WINDOWS/powershell.exe" -Command "python -m src.ai.trial_import ."'
for record in (
    {{"type": "thread.started", "thread_id": "t1"}},
    {{"type": "turn.started"}},
    {{"type": "item.completed", "item": {{"id": "i0", "type": "agent_message", "text": "A CSV with two channels."}}}},
    {{"type": "item.started", "item": {{"id": "i1", "type": "command_execution", "command": command}}}},
    {{"type": "item.completed", "item": {{"id": "i2", "type": "file_change",
        "changes": [{{"path": "C:/draft/importer.py", "kind": "add"}}]}}}},
    {{"type": "item.completed", "item": {{"id": "i3", "type": "agent_message", "text": "Done: 2 series."}}}},
    {{"type": "turn.completed", "usage": {{"input_tokens": 1, "output_tokens": 1}}}},
):
    print(json.dumps(record))
"""


class ClaudeCliTests(unittest.TestCase):
    def test_stream_lines_become_events(self):
        events = claude_cli.parse_stream_line(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": " Found a header. "},
            {"type": "tool_use", "name": "Bash", "input": {"command": "python -c 1", "description": "Dump bytes"}},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "C:/data/log.bin"}}]}}))
        self.assertEqual([(event.kind, event.text) for event in events],
                         [("text", "Found a header."), ("tool", "Ran: Dump bytes"), ("tool", "Read log.bin")])
        result = claude_cli.parse_stream_line(json.dumps(
            {"type": "result", "subtype": "success", "result": "ok", "session_id": "s", "total_cost_usd": 0.5}))[0]
        self.assertEqual((result.kind, result.text, result.session_id, result.cost_usd), ("result", "ok", "s", 0.5))
        failure = claude_cli.parse_stream_line(json.dumps({"type": "result", "subtype": "error_max_budget_usd"}))[0]
        self.assertEqual(failure.kind, "error")
        for line in ("not json", "[]", json.dumps({"type": "system"})):
            self.assertEqual(claude_cli.parse_stream_line(line), [])

    def test_command_denies_unlisted_tools_and_limits_writes_to_the_draft(self):
        command = claude_cli.magic_drop_command(Path("claude"), [Path("data/new")], max_budget_usd=1.5)
        self.assertEqual(command[command.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(command[command.index("--max-budget-usd") + 1], "1.5")
        self.assertEqual(command[command.index("--add-dir") + 1], str(Path("data/new")))
        self.assertIn("Write(./**)", command)
        self.assertNotIn("Write", command)
        # Python only, in either shell tool.
        self.assertIn("PowerShell(python:*)", command)
        self.assertNotIn("PowerShell", command)
        self.assertNotIn("bypassPermissions", command)

    def test_configured_path_wins_and_missing_cli_is_not_signed_in(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "claude.exe"
            executable.write_bytes(b"")
            self.assertEqual(claude_cli.find_claude(executable), executable)
        self.assertFalse(claude_cli.is_signed_in(Path(directory) / "missing.exe"))

    def test_ai_settings_default_to_no_consent(self):
        self.assertFalse(DEFAULT_AI_SETTINGS["magic_drop_consent"])
        self.assertEqual(sanitize_ai_settings({"magic_drop_consent": "yes", "time_limit_minutes": 999}),
                         {**DEFAULT_AI_SETTINGS, "time_limit_minutes": 120})


class CodexCliTests(unittest.TestCase):
    def test_jsonl_lines_become_events(self):
        def events(record):
            return [(event.kind, event.text) for event in codex_cli.parse_stream_line(json.dumps(record))]

        self.assertEqual(events({"type": "item.completed", "item": {"type": "agent_message", "text": " Hi. "}}),
                         [("text", "Hi.")])
        command = '"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command \'python -c "print(1)"\''
        self.assertEqual(events({"type": "item.started", "item": {"type": "command_execution", "command": command}}),
                         [("tool", 'Ran: python -c "print(1)"')])
        self.assertEqual(events({"type": "item.completed", "item": {"type": "command_execution", "command": command}}),
                         [])
        self.assertEqual(events({"type": "item.completed", "item": {
            "type": "file_change", "changes": [{"path": "C:/draft/importer.py", "kind": "update"}]}}),
            [("tool", "Changed importer.py")])
        self.assertEqual(events({"type": "turn.completed", "usage": {}}), [("result", "")])
        self.assertEqual(events({"type": "turn.failed", "error": {"message": "quota"}}), [("error", "quota")])
        self.assertEqual(events({"type": "error", "message": "boom"}), [("error", "boom")])
        started = codex_cli.parse_stream_line(json.dumps({"type": "thread.started", "thread_id": "t"}))[0]
        self.assertEqual((started.text, started.session_id), ("", "t"))
        for line in ("not json", "[]", json.dumps({"type": "turn.started"})):
            self.assertEqual(codex_cli.parse_stream_line(line), [])

    def test_command_runs_sandboxed_without_network_or_mcp_servers(self):
        command = codex_cli.magic_drop_command(Path("codex"), [Path("data/new")], max_budget_usd=1.5)
        self.assertEqual(command[1], "exec")
        self.assertEqual(command[command.index("--sandbox") + 1], "workspace-write")
        self.assertIn("sandbox_workspace_write.network_access=false", command)
        self.assertIn("mcp_servers={}", command)
        # The user config stays: on Windows it holds the sandbox setup.
        self.assertNotIn("--ignore-user-config", command)
        self.assertEqual(command[command.index("--add-dir") + 1], str(Path("data/new")))
        self.assertEqual(command[-1], "-")
        for unsafe in ("--dangerously-bypass-approvals-and-sandbox", "danger-full-access"):
            self.assertNotIn(unsafe, command)

    def test_configured_path_wins_and_missing_cli_is_not_signed_in(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "codex.exe"
            executable.write_bytes(b"")
            self.assertEqual(codex_cli.find_codex(executable), executable)
        self.assertFalse(codex_cli.is_signed_in(Path(directory) / "missing.exe"))

    def test_providers_have_their_own_settings_and_instructions(self):
        self.assertEqual((CLAUDE.instructions_file, CODEX.instructions_file), ("CLAUDE.md", "AGENTS.md"))
        self.assertEqual((CODEX.path_setting, CODEX.consent_setting), ("codex_path", "codex_magic_drop_consent"))
        self.assertFalse(DEFAULT_AI_SETTINGS["codex_magic_drop_consent"])
        self.assertIs(provider_named("Codex"), CODEX)
        self.assertIs(provider_named("unknown"), CLAUDE)


class MagicDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.source = self.root / "incoming" / "weather log.csv"
        self.source.parent.mkdir()
        self.source.write_text(CSV_TEXT, encoding="utf-8")

    def test_linear_unit_conversion_keeps_raw_values_and_shows_in_summary(self):
        draft = magic_drop.create_draft([self.source], root=self.root / "drafts")
        instructions = (draft / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("## Units", instructions)
        self.assertIn('"transform"', instructions)
        # The summary ends by asking whether the series are right and whether to make a plugin.
        self.assertIn("are all the series there", instructions)
        self.assertIn("importer plugin for this format", instructions)
        # Pressure recorded in Pa*100 (hPa as counts of 1/100), converted by the host transform.
        parser = CSV_PARSER.replace('"unit": unit, "visible": True,',
                                    '"unit": unit, "visible": True, **({"values": rows[:, column] * 100, '
                                    '"original_unit": "Pa", "transform": {"scale": 0.01, "offset": 0}} '
                                    'if name == "Pressure" else {}),')
        self.assertNotEqual(parser, CSV_PARSER)
        (draft / "importer.py").write_text(parser, encoding="utf-8")
        output = self.root / "converted.h5"
        summary = magic_drop.run_trial_import(draft, self.source, output, timeout=120)
        pressure = summary["series"][0]
        self.assertEqual((pressure["unit"], pressure["original_unit"]), ("hPa", "Pa"))
        self.assertAlmostEqual(pressure["min"], 1013.0)
        self.assertAlmostEqual(pressure["max"], 1013.2)
        self.assertIn("Pressure [hPa]: 3 samples, converted from Pa", magic_drop.summary_text(summary))
        self.assertNotIn("converted", magic_drop.summary_text(summary).splitlines()[1])
        with h5py.File(output, "r") as handle:
            self.assertAlmostEqual(float(handle["series/Weather/Pressure/value_raw"][0]), 101320.0)

    def test_draft_parser_imports_in_its_own_process_and_folder_info_gets_metadata(self):
        draft = magic_drop.create_draft([self.source], root=self.root / "drafts")
        for name in ("plugin.json", "importer.json", "importer.py", "CLAUDE.md", "H5_Format.md"):
            self.assertTrue((draft / name).is_file(), name)
        (draft / "importer.py").write_text(CSV_PARSER, encoding="utf-8")
        folder = magic_drop.magic_drop_folder(self.data, [self.source])
        copies = magic_drop.copy_sources([self.source], folder)
        summary = magic_drop.run_trial_import(draft, copies[0], copies[0].with_suffix(".h5"), timeout=120)
        self.assertEqual([(item["name"], item["unit"], item["samples"]) for item in summary["series"]],
                         [("Weather/Pressure", "hPa", 3), ("Weather/Temperature", "C", 3)])
        self.assertIn("Temperature [C]: 3 samples", magic_drop.summary_text(summary))
        # The summary gives each series' value range, so Claude can check its unit.
        temperature = summary["series"][1]
        self.assertEqual((temperature["min"], temperature["max"]), (20.5, 20.8))
        (draft / "metadata.json").write_text(json.dumps({"device": "X", "nested": {"a": 1}, "": "skip"}))
        magic_drop.write_folder_info(folder, magic_drop.read_metadata(draft), draft)
        info = read_info_file(folder)
        self.assertEqual(info.readonly["device"], "X")
        self.assertNotIn("nested", info.readonly)
        # A second drop of the same file gets its own folder; the original stays in place.
        self.assertEqual(magic_drop.magic_drop_folder(self.data, [self.source]).name, "weather log_2")
        self.assertTrue(self.source.is_file())

    def test_broken_parser_fails_with_its_error(self):
        draft = magic_drop.create_draft([self.source], root=self.root / "drafts")
        with self.assertRaisesRegex(RuntimeError, "NotImplementedError"):
            magic_drop.run_trial_import(draft, self.source, self.root / "out.h5", timeout=120)

    def fake_provider(self, script: str, provider=CLAUDE):
        """The provider with its CLI replaced by a Python script."""
        fake = self.root / "fake_agent.py"
        fake.write_text(script, encoding="utf-8")
        return dataclasses.replace(provider, magic_drop_command=lambda *_args, **_options: [sys.executable, str(fake)])

    def _job(self, script: str, provider=CLAUDE, **options) -> tuple[MagicDropJob, list, list]:
        job = MagicDropJob(Path("agent"), [self.source], self.data, drafts_root=self.root / "drafts",
                           provider=self.fake_provider(script, provider), **options)
        events, finished = [], []
        job.event.connect(lambda kind, text: events.append((kind, text)))
        job.finished.connect(lambda *result: finished.append(result))
        job.start()
        self.assertTrue(qt_wait_until(lambda: bool(finished), 120_000))
        return job, events, finished

    def test_job_creates_the_folder_with_copy_h5_and_info(self):
        job, events, finished = self._job(FAKE_CLAUDE)
        succeeded, message, folder = finished[0]
        self.assertTrue(succeeded, message)
        # The info writer's own lock file is left out.
        self.assertEqual(sorted(path.name for path in folder.iterdir() if path.suffix != ".lock"),
                         ["weather log.csv", "weather log.h5", "weather log_info.json"])
        with h5py.File(folder / "weather log.h5", "r") as handle:
            self.assertIn("Weather", handle["series"])
        self.assertEqual(read_info_file(folder).readonly["device"], "Test logger")
        self.assertIn(("text", "A CSV with two channels."), events)
        self.assertIn(("tool", "Write importer.py"), events)
        self.assertIn(("text", "Done: 2 series. (cost $0.12)"), events)
        self.assertEqual(sum(text.startswith("A CSV") for _kind, text in events), 1)

    def test_codex_job_reads_agents_md_and_creates_the_folder(self):
        _job, events, finished = self._job(FAKE_CODEX, CODEX)
        succeeded, message, folder = finished[0]
        self.assertTrue(succeeded, message)
        self.assertTrue((folder / "weather log.h5").is_file())
        self.assertEqual(read_info_file(folder).readonly["device"], "Codex logger")
        self.assertIn(("status", "Codex is reading the file(s) and writing a parser…"), events)
        self.assertIn(("tool", "Ran: python -m src.ai.trial_import ."), events)
        self.assertIn(("tool", "Changed importer.py"), events)
        self.assertEqual([text for kind, text in events if kind == "text"],
                         ["A CSV with two channels.", "Done: 2 series."])

    def test_failed_agent_removes_the_folder_and_reports_why(self):
        script = 'import json\nprint(json.dumps({"type": "result", "subtype": "error_max_budget_usd", "is_error": True}))\n'
        _job, _events, finished = self._job(script)
        succeeded, message, folder = finished[0]
        self.assertFalse(succeeded)
        self.assertIn("error_max_budget_usd", message)
        self.assertIsNone(folder)
        self.assertEqual(list(self.data.iterdir()), [])

    def test_stop_ends_the_agent_and_removes_the_folder(self):
        script = "import sys, time\nsys.stdin.read()\nprint('starting', flush=True)\ntime.sleep(60)\n"
        job = MagicDropJob(Path("claude"), [self.source], self.data, drafts_root=self.root / "drafts",
                           provider=self.fake_provider(script))
        finished = []
        job.finished.connect(lambda *result: finished.append(result))
        job.start()
        self.assertTrue(qt_wait_until(lambda: job._process is not None, 30_000))
        job.cancel()
        self.assertTrue(qt_wait_until(lambda: bool(finished), 30_000))
        self.assertEqual(finished[0][:2], (False, "Magic drop stopped."))
        self.assertEqual(list(self.data.iterdir()), [])

    def test_stop_before_the_prompt_is_sent_is_reported_as_stopped(self):
        # Stop lands between starting Claude and writing its prompt, so the write can fail.
        job = MagicDropJob(Path("claude"), [self.source], self.data, drafts_root=self.root / "drafts",
                           provider=self.fake_provider("import time\ntime.sleep(60)\n"))
        finished = []
        job.finished.connect(lambda *result: finished.append(result))
        prompt = magic_drop.agent_prompt

        def stop_then_prompt(*arguments, **options):  # type: ignore[no-untyped-def]
            job.cancel()
            return prompt(*arguments, **options) * 10_000

        with mock.patch.object(magic_drop, "agent_prompt", side_effect=stop_then_prompt):
            job.start()
            self.assertTrue(qt_wait_until(lambda: bool(finished), 30_000))
        self.assertEqual(finished[0][:2], (False, "Magic drop stopped."))
        self.assertEqual(list(self.data.iterdir()), [])


if __name__ == "__main__":
    unittest.main()


class MagicDropQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_queued_drops_keep_the_data_folder_they_were_dropped_into(self):
        from types import SimpleNamespace
        from src.app.lodgazilla import LODGaZillaWindow

        started = []
        window = SimpleNamespace(
            _magic_drop_queue=[([Path("a.csv")], "", CLAUDE, Path("first")),
                               ([Path("b.csv")], "", CLAUDE, Path("second"))],
            agent_executable=lambda provider: Path("claude"), settings_path=None,
            ai_panel=mock.Mock(), internal_logger=mock.Mock(), magic_drop_job=None)
        with mock.patch("src.app.lodgazilla.MagicDropJob") as job_class,                 mock.patch("src.app.lodgazilla.load_ai_settings", return_value=dict(DEFAULT_AI_SETTINGS)):
            job_class.side_effect = lambda executable, files, folder, **options: started.append(folder) or mock.Mock()
            LODGaZillaWindow._start_next_magic_drop(window)
            LODGaZillaWindow._start_next_magic_drop(window)
        self.assertEqual(started, [Path("first"), Path("second")])

    def test_sign_in_check_finds_the_cli_in_the_background(self):
        import threading
        threads, answers = [], []
        provider = dataclasses.replace(
            CLAUDE, find=lambda configured: threads.append(threading.current_thread()) or Path("claude"),
            is_signed_in=lambda executable: True)
        check = SignInCheck(provider)
        check.checked.connect(answers.append)
        check.start("")
        qt_wait_until(lambda: answers, timeout_ms=5000)
        self.assertEqual(answers, [True])
        self.assertIsNot(threads[0], threading.main_thread())
