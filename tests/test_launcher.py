"""Launcher regressions; no packages or model weights are downloaded."""
import fcntl
import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import launch


class LauncherTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="youphonium launcher ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "backend").mkdir()
        (self.root / "backend/requirements.txt").write_text("homr>=0.6.0\n")
        self.events = []
        self.runner = launch.Launcher(self.root, self.record)
        self.process = Mock(pid=987654321, returncode=0)
        self.process.poll.return_value = None
        self.kill = self.enterContext(patch.object(launch.os, "killpg"))
        self.popen = self.enterContext(patch.object(launch.subprocess, "Popen", return_value=self.process))
        self.existing = self.enterContext(patch.object(self.runner, "existing_port", return_value=None))
        self.enterContext(patch.object(self.runner, "free_port", return_value=8004))
        self.enterContext(patch.object(self.runner, "health", return_value=True))
        self.fingerprint = self.enterContext(patch.object(self.runner, "fingerprint", return_value="installed"))

    def record(self, kind, text):
        self.events.append((kind, text))
        if kind == "ready":
            self.runner.cancel.set()

    def prepare(self, value="installed"):
        self.runner.state_dir.mkdir(exist_ok=True)
        (self.runner.state_dir / "setup-complete").write_text(value)

    def test_setup_requires_consent_before_starting_any_process(self):
        self.runner.run()
        self.assertEqual([kind for kind, _ in self.events], ["setup"])
        self.popen.assert_not_called()
        self.kill.assert_not_called()

    def test_prepared_app_starts_from_project_folder_and_stops_owned_process(self):
        self.prepare()
        self.runner.run()
        self.assertEqual(self.events[-2:], [("ready", "http://127.0.0.1:8004"),
                                          ("stopped", "YouPhonium stopped.")])
        args, kwargs = self.popen.call_args
        self.assertEqual(args[0], [str(self.runner.python), "-m", "uvicorn", "main:app",
                                  "--app-dir", str((self.root / "backend").resolve()), "--host",
                                  "0.0.0.0", "--port", "8004"])
        self.assertEqual(kwargs["cwd"].resolve(), self.root.resolve())
        self.assertTrue(kwargs["start_new_session"])
        self.kill.assert_called_once_with(self.process.pid, signal.SIGTERM)
        self.assertIsNone(self.runner.process)

    def test_existing_server_is_reused_without_installing_or_stopping_it(self):
        self.existing.return_value = 8002
        self.runner.run()
        self.runner.stop()
        self.assertEqual(self.events, [("existing", "http://127.0.0.1:8002")])
        self.popen.assert_not_called()
        self.kill.assert_not_called()
        self.fingerprint.assert_not_called()

    def test_first_setup_creates_venv_installs_requirements_and_initializes_models(self):
        self.fingerprint.side_effect = [None, "installed"]
        with patch.object(self.runner, "command") as command:
            self.runner.run(allow_setup=True)
        commands = [call.args[0] for call in command.call_args_list]
        self.assertEqual(commands[0], [sys.executable, "-m", "venv", str((self.root / "backend/venv").resolve())])
        self.assertIn("pip", commands[1])
        self.assertEqual(commands[1][-2:], ["-r", str((self.root / "backend/requirements.txt").resolve())])
        self.assertEqual(commands[2], [str(self.runner.python.parent / "homr"), "--init"])
        self.assertEqual((self.runner.state_dir / "setup-complete").read_text(), "installed")
        self.assertEqual(self.events[-1][0], "stopped")

    def test_changed_requirements_require_consent_and_reinstall(self):
        self.prepare("old requirements")
        self.runner.run()
        self.assertEqual(self.events[-1][0], "setup")
        self.popen.assert_not_called()
        self.runner.cancel.clear()
        self.runner.python.parent.mkdir(parents=True)
        self.runner.python.touch()
        with patch.object(self.runner, "command") as command:
            self.runner.run(allow_setup=True)
        commands = [call.args[0] for call in command.call_args_list]
        self.assertEqual(len(commands), 2, "Reuse the venv, but check updated requirements")
        self.assertIn("pip", commands[0])
        self.assertEqual(commands[1][-1], "--init")

    def test_failed_setup_is_reported_without_a_completion_marker(self):
        with patch.object(self.runner, "command", side_effect=RuntimeError("Download failed")):
            self.runner.run(allow_setup=True)
        self.assertEqual(self.events[-1], ("error", "Download failed"))
        self.assertFalse((self.runner.state_dir / "setup-complete").exists())
        self.popen.assert_not_called()

    def test_duplicate_launch_during_setup_does_not_start_a_process(self):
        self.prepare()
        with (self.runner.state_dir / "launch.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.runner.run()
        self.assertEqual(self.events[-1][0], "error")
        self.assertIn("another launcher", self.events[-1][1])
        self.popen.assert_not_called()
        self.kill.assert_not_called()

    def test_unwritable_state_directory_reports_an_error(self):
        self.runner.state_dir.write_text("not a directory")
        self.runner.run()
        self.assertEqual(self.events[-1][0], "error")
        self.popen.assert_not_called()

    def test_cancel_before_worker_starts_is_not_cleared(self):
        self.runner.cancel.set()
        self.runner.run()
        self.assertEqual(self.events, [("stopped", "YouPhonium stopped.")])
        self.popen.assert_not_called()

    def test_stop_during_dependency_check_does_not_prompt_for_setup(self):
        self.fingerprint.side_effect = lambda: self.runner.cancel.set()
        self.runner.run()
        self.assertEqual(self.events, [("stopped", "YouPhonium stopped.")])
        self.popen.assert_not_called()

    def test_startup_failure_has_no_ready_event(self):
        self.prepare()
        self.process.poll.return_value = 1
        self.runner.run()
        self.assertEqual(self.events[-1][0], "error")
        self.assertNotIn("ready", [kind for kind, _ in self.events])
        self.kill.assert_not_called()

    def test_stubborn_owned_child_is_killed_after_grace_period(self):
        self.runner.process = self.process
        self.process.wait.side_effect = [subprocess.TimeoutExpired("server", 5), 0]
        self.runner.stop()
        self.assertEqual([call.args for call in self.kill.call_args_list],
                         [(self.process.pid, signal.SIGTERM), (self.process.pid, signal.SIGKILL)])
        self.runner.stop()
        self.assertEqual(self.kill.call_count, 2, "Stopping twice must not signal again")


class DiscoveryTests(unittest.TestCase):
    def test_lan_address_falls_back_to_macos_interface(self):
        udp_socket = Mock()
        udp_socket.__enter__ = Mock(return_value=udp_socket)
        udp_socket.__exit__ = Mock(return_value=False)
        udp_socket.connect.side_effect = OSError("no default route")
        interface_results = [
            Mock(returncode=1, stdout=""),
            Mock(returncode=0, stdout="192.168.50.7\n"),
        ]
        with patch.object(launch.socket, "socket", return_value=udp_socket), \
                patch.object(launch.socket, "getaddrinfo", return_value=[]), \
                patch.object(launch.sys, "platform", "darwin"), \
                patch.object(launch.subprocess, "run", side_effect=interface_results) as run:
            self.assertEqual(launch.lan_ipv4_address(), "192.168.50.7")
        self.assertEqual(run.call_args_list[0].args[0],
                         ["/usr/sbin/ipconfig", "getifaddr", "en0"])
        self.assertEqual(run.call_args_list[1].args[0],
                         ["/usr/sbin/ipconfig", "getifaddr", "en1"])

    def test_device_url_uses_route_selected_lan_address(self):
        with patch.object(launch, "lan_ipv4_address", return_value="192.168.20.45"):
            self.assertEqual(
                launch.device_url("http://127.0.0.1:8004"),
                "http://192.168.20.45:8004",
            )
        with patch.object(launch, "lan_ipv4_address", return_value=None):
            self.assertIsNone(launch.device_url("http://127.0.0.1:8004"))

    def test_health_requires_success_and_matching_project_identity(self):
        runner = launch.Launcher()
        payload = {"status": "ok", "app": "YouPhonium", "project_id": launch.project_id(launch.ROOT)}
        with patch.object(launch.urllib.request, "build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            for data, expected in [(payload, True), ({**payload, "project_id": "elsewhere"}, False),
                                   ({**payload, "status": "loading"}, False),
                                   ({"status": "ok"}, False), ([], False), (None, False)]:
                response.read.return_value = json.dumps(data).encode()
                self.assertEqual(runner.health(8000), expected)
            response.read.return_value = b"not JSON"
            self.assertFalse(runner.health(8000))
            self.assertEqual(opener.call_args.args[0].proxies, {})

    def test_busy_ports_are_skipped_and_never_terminated(self):
        with patch.object(launch.socket, "socket") as socket, patch.object(launch.os, "killpg") as kill:
            sock = socket.return_value.__enter__.return_value
            sock.bind.side_effect = [OSError("busy"), OSError("busy"), None]
            self.assertEqual(launch.Launcher().free_port(), 8002)
            self.assertEqual(sock.bind.call_args.args[0], ("0.0.0.0", 8002))
            sock.bind.side_effect = OSError("busy")
            with self.assertRaisesRegex(RuntimeError, "All app ports"):
                launch.Launcher().free_port()
            kill.assert_not_called()

    def test_fingerprint_changes_with_requirements(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = launch.Launcher(Path(directory))
            self.assertIsNone(runner.fingerprint())
            runner.python.parent.mkdir(parents=True)
            runner.python.touch()
            requirements = runner.root / "backend/requirements.txt"
            requirements.write_text("homr>=0.6\n")
            with patch.object(launch.subprocess, "run", return_value=Mock(returncode=0, stdout="0.7.0")):
                first = runner.fingerprint()
                self.assertEqual(first, runner.fingerprint())
                requirements.write_text("homr>=0.7\n")
                self.assertNotEqual(first, runner.fingerprint())


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux file-manager integration")
class ShortcutTests(unittest.TestCase):
    def test_shortcut_is_terminal_free_and_valid_with_special_path_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'Music scores $HOME `test` "quoted" %f back\\slash'
            applications = Path(directory) / "applications"
            target = launch.install_shortcut(root, applications)
            contents = target.read_text()
            self.assertIn("Terminal=false\n", contents)
            self.assertIn(launch.desktop_quote(str(root / "launch.py")), contents)
            self.assertNotIn("sh -c", contents)
            self.assertEqual(target, launch.install_shortcut(root, applications))
            self.assertEqual(len(list(applications.iterdir())), 1)
            if shutil.which("desktop-file-validate"):
                subprocess.run(["desktop-file-validate", str(target)], check=True, capture_output=True)

    def test_shortcut_rejects_line_break_injection(self):
        with self.assertRaises(ValueError):
            launch.desktop_quote("bad\nExec=something")

    def test_desktop_launch_roundtrips_the_actual_script_path(self):
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio
        except ImportError:
            self.skipTest("Optional desktop integration test needs PyGObject")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'Music scores $HOME `test` "quoted" %f back\\slash'
            root.mkdir()
            (root / "launch.py").write_text(
                "from pathlib import Path\n"
                "Path(__file__).with_name('launched').write_text(str(Path(__file__)))\n")
            target = launch.install_shortcut(root, Path(directory) / "applications")
            app = Gio.DesktopAppInfo.new_from_filename(str(target))
            self.assertIsNotNone(app)
            self.assertTrue(app.launch([], None))
            deadline = time.monotonic() + 5
            result = root / "launched"
            while not result.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(result.read_text(), str(root / "launch.py"))

    def test_file_manager_script_works_from_an_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Project with spaces"
            root.mkdir()
            script = root / "Start YouPhonium.sh"
            shutil.copy2(launch.ROOT / script.name, script)
            (root / "launch.py").write_text("print('launched in the correct project')\n")
            result = subprocess.run([str(script)], cwd="/tmp", capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "launched in the correct project")


if __name__ == "__main__":
    unittest.main()
