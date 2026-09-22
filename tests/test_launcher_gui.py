"""Optional Tk smoke test: YOUPHONIUM_GUI_TEST=1 xvfb-run -a python3 -m unittest discover -s tests."""
import os
from pathlib import Path
import signal
import threading
import time
import unittest
from unittest.mock import patch

import launch


@unittest.skipUnless(os.environ.get("YOUPHONIUM_GUI_TEST") == "1", "Run under Xvfb to test the desktop window")
class LauncherWindowTests(unittest.TestCase):
    def test_setup_start_stop_restart_shortcut_and_close(self):
        self.exercise_window("close")

    def test_terminal_hangup_stops_the_launcher_and_restores_signal_handlers(self):
        self.exercise_window("hangup")

    def exercise_window(self, shutdown):
        import tkinter as tk
        from tkinter import messagebox, ttk

        instances = []

        class FakeLauncher:
            def __init__(self, publish):
                self.publish = publish
                self.cancel = threading.Event()
                self.url = None
                self.log_path = Path("/tmp/unused-youphonium-test.log")
                self.prepared = False
                instances.append(self)

            def run(self, allow_setup=False):
                if not self.prepared and not allow_setup:
                    self.publish("setup", "Prepare the app?")
                    return
                self.prepared = True
                self.url = "http://127.0.0.1:8004"
                self.publish("ready", self.url)
                self.cancel.wait(10)
                self.publish("stopped", "YouPhonium stopped.")

            def stop(self):
                self.cancel.set()

        errors = []
        phases = []
        real_tk = tk.Tk

        def create_window():
            window = real_tk()
            deadline = time.monotonic() + 8

            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)

            def drive():
                try:
                    self.assertLess(time.monotonic(), deadline, "Launcher UI timed out")
                    buttons = {widget.cget("text"): widget for widget in descendants(window)
                               if isinstance(widget, ttk.Button)}
                    if not phases and str(buttons["Open browser"].cget("state")) == "normal":
                        self.assertEqual(browser.call_count, 1)
                        consent.assert_called_once()
                        self.assertEqual(str(buttons["Stop"].cget("state")), "normal")
                        buttons["Add to Applications"].invoke()
                        shortcut.assert_called_once()
                        buttons["Stop"].invoke()
                        phases.append("ready")
                    elif phases == ["ready"] and str(buttons["Start"].cget("state")) == "normal":
                        self.assertEqual(str(buttons["Open browser"].cget("state")), "disabled")
                        buttons["Start"].invoke()
                        phases.append("stopped")
                    elif phases == ["ready", "stopped"] and browser.call_count == 2:
                        consent.assert_called_once()  # Prepared setup is reused.
                        phases.append("restarted")
                        if shutdown == "hangup":
                            self.assertTrue(callable(signal.getsignal(signal.SIGHUP)))
                            os.kill(os.getpid(), signal.SIGHUP)
                        else:
                            window.tk.call(window.protocol("WM_DELETE_WINDOW"))
                        return
                    window.after(30, drive)
                except Exception as exc:
                    errors.append(exc)
                    for instance in instances:
                        instance.stop()
                    window.destroy()

            window.after(200, drive)
            return window

        previous_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        with patch.object(launch, "Launcher", FakeLauncher), \
                patch.object(tk, "Tk", create_window), \
                patch.object(messagebox, "askyesno", return_value=True) as consent, \
                patch.object(messagebox, "showinfo"), \
                patch.object(messagebox, "showerror") as error_dialog, \
                patch.object(launch, "install_shortcut") as shortcut, \
                patch.object(launch.webbrowser, "open", return_value=True) as browser:
            launch.main()
        if errors:
            raise errors[0]
        self.assertEqual(phases, ["ready", "stopped", "restarted"])
        self.assertTrue(instances[0].cancel.is_set())
        error_dialog.assert_not_called()
        for sig, handler in previous_handlers.items():
            self.assertEqual(signal.getsignal(sig), handler)


if __name__ == "__main__":
    unittest.main()
