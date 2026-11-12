"""Create plugin: the plugin repository, the agent's commands and a conversation."""
import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.ai import claude_cli, codex_cli, plugin_builder  # noqa: E402
from src.ai.providers import CLAUDE, CODEX  # noqa: E402
from src.jobs.plugin_chat import PluginChatJob  # noqa: E402
from tests.qt_wait import qt_wait_until  # noqa: E402

# A stand-in agent: answers in Claude's stream format, writes the manifest on the
# second question, and records its prompt and arguments.
FAKE_AGENT = r'''
import json, sys
from pathlib import Path
prompt = sys.stdin.read()
runs = Path(".lodgazilla_ai/runs.json")
history = json.loads(runs.read_text()) if runs.exists() else []
history.append({"prompt": prompt, "argv": sys.argv[1:]})
runs.write_text(json.dumps(history))
if len(history) == 2:
    Path("plugin.json").write_text(json.dumps({"id": Path.cwd().name, "api_version": 1}))
print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "text", "text": f"Answer {len(history)}."},
    {"type": "tool_use", "name": "WebFetch", "input": {"url": "https://example.com/spec.pdf"}}]}}))
print(json.dumps({"type": "result", "subtype": "success", "result": f"Answer {len(history)}.",
                  "session_id": "session-1", "total_cost_usd": 0.05}))
'''


class PluginBuilderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "plugin"
        self.root.mkdir()

    def test_plugin_ids_are_checked_and_suggested(self):
        self.assertEqual(plugin_builder.plugin_id_from("Logger 2000 v3.bin"), "logger_2000_v3_bin_importer")
        self.assertEqual(plugin_builder.plugin_id_from("2024-run"), "run_importer")
        self.assertEqual(plugin_builder.check_plugin_id("logger_importer", self.root), "")
        for bad in ("Logger", "1logger", "l", "logger-importer"):
            self.assertTrue(plugin_builder.check_plugin_id(bad, self.root), bad)
        (self.root / "taken").mkdir()
        self.assertIn("already exists", plugin_builder.check_plugin_id("taken", self.root))

    def test_plugin_folder_is_a_git_repository_without_the_agents_files(self):
        folder = plugin_builder.create_plugin_folder("logger_importer", "CLAUDE.md", root=self.root)
        self.assertTrue((folder / ".git").is_dir())
        text = (folder / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("plugin/logger_importer", text)
        self.assertIn("Plugin_API.md", text)
        self.assertIn("## Units", text)
        source = Path(self.root.parent) / "logger.bin"
        source.write_bytes(b"\x01\x02")
        copies = plugin_builder.copy_inputs(folder, [source, source])
        self.assertEqual([path.name for path in copies], ["logger.bin", "logger_2.bin"])
        plugin_builder.add_instructions(folder, "logger_importer", "AGENTS.md")
        # The instructions, the user's files and trial output never reach a commit.
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=folder,
                                capture_output=True, text=True, check=True).stdout
        self.assertEqual(status, "")
        (folder / "importer.py").write_text("")
        status = subprocess.run(["git", "status", "--porcelain"], cwd=folder, capture_output=True, text=True,
                                check=True).stdout
        self.assertEqual(status.strip(), "?? importer.py")
        with self.assertRaisesRegex(ValueError, "already exists"):
            plugin_builder.create_plugin_folder("logger_importer", "CLAUDE.md", root=self.root)

    def test_prompt_names_the_instructions_once_and_the_attached_files(self):
        folder = plugin_builder.create_plugin_folder("logger_importer", "AGENTS.md", root=self.root)
        source = self.root.parent / "spec.pdf"
        source.write_bytes(b"%PDF")
        copies = plugin_builder.copy_inputs(folder, [source])
        first = plugin_builder.plugin_prompt("Make an importer", copies, "AGENTS.md", first=True)
        self.assertIn("Follow AGENTS.md", first)
        self.assertIn(".lodgazilla_ai/inputs/spec.pdf (4 bytes)", first)
        self.assertIn("Make an importer", first)
        later = plugin_builder.plugin_prompt("Yes", [], "AGENTS.md", first=False)
        self.assertNotIn("Follow", later)


class AgentCommandTests(unittest.TestCase):
    def test_claude_runs_git_reads_the_web_when_allowed_and_resumes(self):
        command = claude_cli.plugin_command(Path("claude"), [Path("host")], max_budget_usd=1.0, web=True,
                                            resume="s1")
        self.assertIn("Bash(git:*)", command)
        self.assertIn("WebFetch", command)
        self.assertEqual(command[command.index("--resume") + 1], "s1")
        self.assertEqual(command[command.index("--add-dir") + 1], "host")
        plain = claude_cli.magic_drop_command(Path("claude"), [], max_budget_usd=1.0)
        self.assertNotIn("WebFetch", plain)
        self.assertNotIn("Bash(git:*)", plain)
        self.assertNotIn("--resume", plain)

    def test_codex_searches_the_web_only_when_allowed_and_resumes_its_session(self):
        command = codex_cli.plugin_command(Path("codex"), [Path("host")], web=True, resume="t1")
        self.assertIn('web_search="live"', command)
        self.assertIn("sandbox_workspace_write.network_access=false", command)
        self.assertEqual(command[-3:], ["resume", "t1", "-"])
        self.assertNotIn("--add-dir", command)
        self.assertIn('web_search="disabled"', codex_cli.magic_drop_command(Path("codex"), []))
        events = codex_cli.parse_stream_line(json.dumps(
            {"type": "item.started", "item": {"type": "web_search", "query": "logger format"}}))
        self.assertEqual([(event.kind, event.text) for event in events], [("tool", "Searched the web: logger format")])


class PluginChatJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        self.root = self.temp / "plugin"
        self.root.mkdir()
        fake = self.temp / "fake_agent.py"
        fake.write_text(FAKE_AGENT, encoding="utf-8")
        self.commands = []

        def command(executable, folders, **options):
            self.commands.append(options)
            return [sys.executable, str(fake), "--resume", options.get("resume", "")]

        self.provider = dataclasses.replace(CLAUDE, plugin_command=command)
        # Commits need an identity, whatever this machine's Git settings are.
        identity = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
                    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"}
        patch = mock.patch.dict(os.environ, identity)
        patch.start()
        self.addCleanup(patch.stop)

    def ask(self, text, files=(), session_id=""):
        job = PluginChatJob(Path("claude"), "logger_importer", text, list(files), session_id=session_id,
                            provider=self.provider, web=True, plugin_root=self.root)
        events, finished = [], []
        job.event.connect(lambda kind, event_text: events.append((kind, event_text)))
        job.finished.connect(lambda *result: finished.append(result))
        job.start()
        self.assertTrue(qt_wait_until(lambda: bool(finished), 60_000))
        return events, finished[0]

    def test_conversation_creates_the_repository_and_continues_the_session(self):
        example = self.temp / "logger.bin"
        example.write_bytes(b"\x00" * 8)
        events, (succeeded, note, session) = self.ask("Make an importer for this", [example])
        self.assertTrue(succeeded)
        folder = self.root / "logger_importer"
        self.assertTrue((folder / ".git").is_dir())
        self.assertTrue((folder / ".lodgazilla_ai" / "inputs" / "logger.bin").is_file())
        self.assertEqual(session, "session-1")
        self.assertEqual(note, "")
        self.assertIn(("status", "Created plugin/logger_importer as a new Git repository."), events)
        self.assertIn(("tool", "Fetched https://example.com/spec.pdf"), events)
        self.assertIn(("text", "Answer 1."), events)
        self.assertEqual(self.commands[0], {"max_budget_usd": 2.0, "web": True, "resume": ""})
        # The next question continues the session; the new manifest gets a note.
        events, (succeeded, note, session) = self.ask("Yes, go ahead", session_id=session)
        self.assertTrue(succeeded)
        self.assertEqual(self.commands[1]["resume"], "session-1")
        self.assertIn("Settings > Plugins", note)
        # LODGaZilla commits what the agent changed, named after the request; the first
        # answer changed only excluded files, so it made no commit.
        commits = [text for kind, text in events if kind == "status" and text.startswith("Committed ")]
        self.assertEqual(len(commits), 1)
        self.assertTrue(commits[0].endswith("in plugin/logger_importer: Yes, go ahead"))
        log = subprocess.run(["git", "log", "--format=%s"], cwd=folder, capture_output=True, text=True).stdout
        self.assertEqual(log.splitlines(), ["Yes, go ahead"])
        tracked = subprocess.run(["git", "ls-files"], cwd=folder, capture_output=True, text=True).stdout
        self.assertEqual(tracked.split(), ["plugin.json"])
        runs = json.loads((folder / ".lodgazilla_ai" / "runs.json").read_text())
        self.assertIn("Follow CLAUDE.md", runs[0]["prompt"])
        self.assertIn("logger.bin", runs[0]["prompt"])
        self.assertNotIn("Follow CLAUDE.md", runs[1]["prompt"])
        self.assertIn("Yes, go ahead", runs[1]["prompt"])

    def test_switching_to_codex_adds_its_instructions_file(self):
        self.ask("Hello")
        codex = dataclasses.replace(CODEX, plugin_command=self.provider.plugin_command,
                                    parse_stream_line=CLAUDE.parse_stream_line)
        job = PluginChatJob(Path("codex"), "logger_importer", "Continue", [], provider=codex, plugin_root=self.root)
        finished = []
        job.finished.connect(lambda *result: finished.append(result))
        job.start()
        self.assertTrue(qt_wait_until(lambda: bool(finished), 60_000))
        self.assertTrue((self.root / "logger_importer" / "AGENTS.md").is_file())
        exclude = (self.root / "logger_importer" / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertIn("/AGENTS.md", exclude)
        self.assertIn("/CLAUDE.md", exclude)


if __name__ == "__main__":
    unittest.main()
