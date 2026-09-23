"""macOS entry-point and bundle checks runnable without a Mac or downloads."""
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import launch


class MacLauncherTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="youphonium-macos-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'Music $HOME `test` "scores" and apostrophe\'s'
        self.root.mkdir()
        self.script = self.root / "Start YouPhonium.command"
        shutil.copy2(launch.ROOT / self.script.name, self.script)
        self.bin = Path(directory.name) / "bin"
        self.bin.mkdir()
        self.environment = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")}
        self.environment.pop("YOUPHONIUM_PYTHON", None)
        # A separate path avoids macOS's deliberately excluded /usr/bin/python3
        # stub, even when this test host uses that path for its real Python.
        self.python = self.make_script(self.bin / "selected Python", f"exec {shlex.quote(sys.executable)} \"$@\"\n")
        (self.root / "launch.py").write_text(
            "import json, sys\nprint(json.dumps({'script': __file__, 'python': sys.executable}))\n")

    def make_script(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
        return path

    def run_script(self, executable=None):
        return subprocess.run([str(executable or self.script)], cwd="/tmp", env=self.environment,
                              capture_output=True, text=True, timeout=15)

    def test_finder_entry_works_outside_project_and_with_special_path_characters(self):
        self.environment["YOUPHONIUM_PYTHON"] = str(self.python)
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["script"], str(self.root / "launch.py"))
        self.assertTrue(os.access(self.script, os.X_OK))

    def test_existing_project_python_is_preferred(self):
        marker = self.root / "existing-python-used"
        self.make_script(self.root / "backend/venv/bin/python",
                         f"touch {shlex.quote(str(marker))}\nexec {shlex.quote(sys.executable)} \"$@\"\n")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(marker.exists())

    def test_unusable_python_is_skipped_for_a_working_one(self):
        self.make_script(self.bin / "python3.13", "exit 1\n")
        self.make_script(self.bin / "python3.12", f"exec {shlex.quote(sys.executable)} \"$@\"\n")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(json.loads(result.stdout)["script"]).resolve(), (self.root / "launch.py").resolve())

    def test_missing_explicit_python_reports_how_to_install_it(self):
        self.environment["YOUPHONIUM_PYTHON"] = str(self.root / "not installed")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Python 3.11", result.stderr)
        self.assertIn("Install Certificates.command", result.stderr)

    def test_python_without_required_modules_is_rejected(self):
        python = self.make_script(self.bin / "incomplete-python", "exit 1\n")
        self.environment["YOUPHONIUM_PYTHON"] = str(python)
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("venv support", result.stderr)

    def test_app_shortcut_metadata_permissions_quoting_and_launch(self):
        applications = self.root / "Applications"
        with patch.object(launch.sys, "platform", "darwin"):
            app = launch.install_shortcut(self.root, applications)
            self.assertEqual(app, launch.install_shortcut(self.root, applications))
        self.assertEqual(len(list(applications.iterdir())), 1)
        self.assertEqual(app.suffix, ".app")
        metadata = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        self.assertEqual(metadata["CFBundlePackageType"], "APPL")
        self.assertEqual(metadata["CFBundleDisplayName"], "YouPhonium")
        entry = app / "Contents/MacOS" / metadata["CFBundleExecutable"]
        self.assertTrue(os.access(entry, os.X_OK))
        self.environment["YOUPHONIUM_PYTHON"] = str(self.python)
        result = self.run_script(entry)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(json.loads(result.stdout)["script"]).resolve(), (self.root / "launch.py").resolve())

    def test_app_shortcut_defaults_to_user_applications(self):
        with patch.object(launch.sys, "platform", "darwin"), patch.object(launch.Path, "home", return_value=self.root):
            app = launch.install_shortcut(self.root)
        self.assertEqual(app.parent, self.root / "Applications")

    def test_mac_setup_failure_does_not_show_debian_instructions(self):
        runner = launch.Launcher(self.root)
        process = Mock(returncode=1)
        process.poll.return_value = 1
        with patch.object(runner, "spawn", return_value=process), patch.object(launch.sys, "platform", "darwin"):
            with self.assertRaisesRegex(RuntimeError, "On macOS") as error:
                runner.command(["failed"], None)
        self.assertNotIn("Debian", str(error.exception))

    def test_missing_venv_is_really_created_and_then_reused_without_downloads(self):
        events = []
        runner = launch.Launcher(self.root)

        def publish(kind, text):
            events.append((kind, text))
            if kind == "ready":
                runner.cancel.set()

        runner.publish = publish
        (self.root / "backend").mkdir()
        (self.root / "backend/requirements.txt").write_text("homr>=0.6.0\n")

        def setup(command, log, timeout):
            if command[1:3] == ["-m", "venv"]:
                # Only venv creation is real. pip installs and HOMR model downloads
                # are intercepted below; ensurepip uses Python's bundled wheels.
                subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)

        process = Mock()
        process.poll.return_value = None
        with patch.object(runner, "existing_port", return_value=None), \
                patch.object(runner, "free_port", return_value=8004), \
                patch.object(runner, "health", return_value=True), \
                patch.object(runner, "spawn", return_value=process), \
                patch.object(runner, "fingerprint", side_effect=[None, "prepared", "prepared"]), \
                patch.object(runner, "command", side_effect=setup) as command:
            runner.run(allow_setup=True)
            self.assertEqual(events[-2][0], "ready", events)
            self.assertTrue((self.root / "backend/venv/pyvenv.cfg").exists())
            probe = subprocess.run([str(runner.python), "-c", "import sys; assert sys.prefix != sys.base_prefix; import pip"],
                                   check=True, capture_output=True, text=True, timeout=10)
            self.assertEqual(probe.returncode, 0)
            commands = [call.args[0] for call in command.call_args_list]
            self.assertEqual(commands[0][1:3], ["-m", "venv"])
            self.assertEqual(commands[1][:4], [str(runner.python), "-m", "pip", "install"])
            self.assertEqual(commands[2], [str(runner.python.parent / "homr"), "--init"])
            command.reset_mock()
            runner.cancel.clear()
            runner.run()
            self.assertEqual(events[-2][0], "ready", events)
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
