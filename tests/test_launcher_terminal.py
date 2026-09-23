"""Terminal entry-point tests; no Tk installation or display is required."""
import io
from contextlib import redirect_stdout
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import launch


class TerminalLauncherTests(unittest.TestCase):
    def test_setup_runs_automatically_opens_browser_and_stops(self):
        instances = []

        class FakeLauncher:
            def __init__(self):
                self.publish = lambda *_: None
                self.cancel = threading.Event()
                self.url = None
                self.log_path = Path("/tmp/unused-youphonium-test.log")
                self.stopped = False
                instances.append(self)

            def run(self, allow_setup=False):
                self.allow_setup = allow_setup
                self.url = "http://127.0.0.1:8004"
                self.publish("status", "Preparing test app…")
                self.publish("ready", self.url)

            def stop(self):
                self.stopped = True

        output = io.StringIO()
        with patch.object(launch, "Launcher", FakeLauncher), \
                patch.object(launch.webbrowser, "open", return_value=True) as browser, \
                redirect_stdout(output):
            launch.main()

        runner = instances[0]
        self.assertTrue(runner.allow_setup)
        self.assertTrue(runner.stopped)
        opened = browser.call_args.args[0]
        self.assertTrue(opened.startswith("http://127.0.0.1:8004/?app="), opened)
        self.assertIn("Press Ctrl+C", output.getvalue())


if __name__ == "__main__":
    unittest.main()
