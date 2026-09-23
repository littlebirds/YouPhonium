#!/usr/bin/env python3
"""Desktop launcher. Uses only Python's standard library until the app starts."""
from __future__ import annotations

import fcntl
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser


ROOT = Path(__file__).resolve().parent
LOCAL_HOST = "127.0.0.1"
BIND_HOST = "0.0.0.0"
PORTS = range(8000, 8010)
PROBE = """import importlib.util, importlib.metadata, sys
names = ('fastapi', 'uvicorn', 'music21', 'multipart', 'fitz', 'PIL', 'numpy', 'homr')
if not all(importlib.util.find_spec(name) for name in names): sys.exit(1)
print(importlib.metadata.version('homr'))
"""


class SetupRequired(Exception):
    pass


class Cancelled(Exception):
    pass


def project_id(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]


def versioned_app_url(url: str, root: Path = ROOT) -> str:
    """Force a fresh app document when a launcher reuses an existing tab."""
    script = root / "frontend/app.js"
    version = script.stat().st_mtime_ns if script.exists() else int(time.time_ns())
    return f"{url.rstrip('/')}/?app={version}"


def desktop_quote(value: str) -> str:
    """Quote one Desktop Entry Exec argument (not a shell command)."""
    if any(char in value for char in "\n\r\x00"):
        raise ValueError("Shortcut paths cannot contain line breaks or null characters.")
    value = value.replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        value = value.replace(char, "\\" + char)
    # Desktop Entry string escaping is applied before Exec argument escaping.
    return '"' + value.replace("\\", "\\\\") + '"'


def install_shortcut(root: Path = ROOT, applications: Path | None = None) -> Path:
    root = root.resolve()
    if sys.platform == "darwin":
        return install_macos_shortcut(root, applications)
    if applications is None:
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        applications = data_home / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    target = applications / ("youphonium-" + project_id(root) + ".desktop")
    target.write_text(
        "[Desktop Entry]\nType=Application\nName=YouPhonium\n"
        "Comment=Recognize sheet music and play it in your browser\n"
        f"Exec={desktop_quote(sys.executable)} {desktop_quote(str(root / 'launch.py'))}\n"
        "Icon=audio-x-generic\nTerminal=false\nCategories=AudioVideo;Music;\n",
        encoding="utf-8",
    )
    return target


def install_macos_shortcut(root: Path, applications: Path | None = None) -> Path:
    """Create a local app shortcut, not a bundled or signed app distribution."""
    if applications is None:
        applications = Path.home() / "Applications"
    identity = project_id(root)
    target = applications / f"YouPhonium-{identity}.app"
    contents = target / "Contents"
    executable = contents / "MacOS/YouPhonium"
    metadata = {
        "CFBundleName": "YouPhonium",
        "CFBundleDisplayName": "YouPhonium",
        "CFBundleIdentifier": f"org.youphonium.launcher.{identity}",
        "CFBundleExecutable": "YouPhonium",
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleVersion": "1",
        "NSHighResolutionCapable": True,
    }
    executable.parent.mkdir(parents=True, exist_ok=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps(metadata))
    executable.write_text(
        "#!/bin/sh\nexec /bin/bash " + shlex.quote(str(root / "Start YouPhonium.command")) + "\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return target


class Launcher:
    def __init__(self, root: Path = ROOT, publish=None):
        self.root = root.resolve()
        self.publish = publish or (lambda kind, text: None)
        self.state_dir = self.root / ".launcher"
        self.log_path = self.state_dir / "youphonium.log"
        self.python = self.root / "backend/venv/bin/python"
        self.cancel = threading.Event()
        self.process = None
        self.process_lock = threading.Lock()
        self.url = None

    def health(self, port: int) -> bool:
        # Never send localhost readiness requests through a configured HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(f"http://{LOCAL_HOST}:{port}/health", timeout=0.5) as response:
                data = json.loads(response.read(8192))
            return (isinstance(data, dict) and data.get("status") == "ok" and data.get("app") == "YouPhonium"
                    and data.get("project_id") == project_id(self.root))
        except (OSError, ValueError, urllib.error.URLError):
            return False

    def existing_port(self):
        return next((port for port in PORTS if self.health(port)), None)

    def free_port(self):
        for port in PORTS:
            with socket.socket() as sock:
                try:
                    sock.bind((BIND_HOST, port))
                    return port
                except OSError:
                    continue
        raise RuntimeError("All app ports (8000–8009) are busy. Close an older app instance and try again.")

    def fingerprint(self):
        if not self.python.exists():
            return None
        try:
            result = subprocess.run([str(self.python), "-c", PROBE], cwd=self.root,
                                    capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode:
            return None
        requirements = (self.root / "backend/requirements.txt").read_bytes()
        identity = f"{self.python.stat().st_mtime_ns}:{result.stdout.strip()}".encode()
        return hashlib.sha256(requirements + identity).hexdigest()

    def spawn(self, command, log):
        with self.process_lock:
            if self.cancel.is_set():
                raise Cancelled()
            self.process = subprocess.Popen(
                command, cwd=self.root, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            return self.process

    def command(self, command, log, timeout=None):
        process = self.spawn(command, log)
        started = time.monotonic()
        while process.poll() is None:
            if self.cancel.wait(0.1):
                raise Cancelled()
            if timeout and time.monotonic() - started > timeout:
                raise RuntimeError("Setup took too long. Check your internet connection and try again.")
        if self.cancel.is_set():
            raise Cancelled()
        if process.returncode:
            hint = ("On macOS, ensure Python includes venv and run its Install Certificates.command if downloads fail with certificate errors."
                    if sys.platform == "darwin" else "On Debian, first ensure python3-venv is installed.")
            raise RuntimeError("Setup could not finish. Check the log for details. " + hint)

    def stop(self):
        self.cancel.set()
        with self.process_lock:
            process = self.process
            if process is None or process.poll() is not None:
                self.process = None
                return
            # Only signal the process group created by this launcher, never a
            # reused server or an arbitrary process listening on a port.
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            except ProcessLookupError:
                pass
            self.process = None

    def run(self, allow_setup=False):
        """Run on a worker thread; clear cancel before explicitly starting again."""
        with ExitStack() as resources:
            log = None
            try:
                if self.cancel.is_set():
                    raise Cancelled()
                self.state_dir.mkdir(parents=True, exist_ok=True)
                lock = resources.enter_context((self.state_dir / "launch.lock").open("a"))
                log = resources.enter_context(self.log_path.open("a", buffering=1))
                port = self.existing_port()
                if self.cancel.is_set():
                    raise Cancelled()
                if port is not None:
                    self.url = f"http://{LOCAL_HOST}:{port}"
                    self.publish("existing", self.url)
                    return
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError("YouPhonium is already starting in another launcher window. Please use that window.") from None
                fingerprint = self.fingerprint()
                if self.cancel.is_set():
                    raise Cancelled()
                marker = self.state_dir / "setup-complete"
                prepared = fingerprint and marker.exists() and marker.read_text() == fingerprint
                if not prepared and not allow_setup:
                    raise SetupRequired()
                if not prepared:
                    log.write(f"\n--- Preparing YouPhonium {time.ctime()} ---\n")
                    self.publish("status", "Installing app dependencies. This may take several minutes…")
                    if not self.python.exists():
                        self.command([sys.executable, "-m", "venv", str(self.python.parent.parent)], log, 120)
                    self.command([str(self.python), "-m", "pip", "install", "--disable-pip-version-check",
                                  "-r", str(self.root / "backend/requirements.txt")], log, 1800)
                    self.publish("status", "Preparing HOMR models. Missing models will be downloaded…")
                    self.command([str(self.python.parent / "homr"), "--init"], log, 1800)
                    fingerprint = self.fingerprint()
                    if not fingerprint:
                        raise RuntimeError("Dependencies are still unavailable after setup. See the log for details.")
                    marker.write_text(fingerprint)
                if self.cancel.is_set():
                    raise Cancelled()
                port = self.free_port()
                self.url = f"http://{LOCAL_HOST}:{port}"
                self.publish("status", "Starting YouPhonium…")
                log.write(f"\n--- Starting YouPhonium on {self.url} {time.ctime()} ---\n")
                process = self.spawn([str(self.python), "-m", "uvicorn", "main:app", "--app-dir",
                                      str(self.root / "backend"), "--host", BIND_HOST, "--port", str(port)], log)
                deadline = time.monotonic() + 120
                while not self.cancel.is_set():
                    if process.poll() is not None:
                        raise RuntimeError("The app could not start. See the log for details.")
                    if self.health(port):
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("The app did not become ready within two minutes. See the log for details.")
                    self.cancel.wait(0.25)
                if self.cancel.is_set():
                    raise Cancelled()
                self.publish("ready", self.url)
                while not self.cancel.wait(0.25):
                    if process.poll() is not None:
                        raise RuntimeError("YouPhonium stopped unexpectedly. See the log, then click Start to retry.")
                raise Cancelled()
            except SetupRequired:
                self.publish("setup", "YouPhonium needs first-time setup or a dependency update. This may install Python packages and download HOMR models. Internet access is required. Continue?")
            except Cancelled:
                self.publish("stopped", "YouPhonium stopped.")
            except Exception as exc:
                if log is not None:
                    log.write(f"Launcher error: {exc}\n")
                self.publish("error", str(exc))
            finally:
                self.stop()


def main():
    """Run the local web app from a terminal; the terminal owns its server."""
    if sys.version_info < (3, 11):
        raise SystemExit("YouPhonium requires Python 3.11 or newer.")

    runner = Launcher()

    def publish(kind, text):
        if kind == "error":
            print(f"ERROR: {text}", file=sys.stderr, flush=True)
            print(f"Log: {runner.log_path}", file=sys.stderr, flush=True)
            return
        if kind in ("ready", "existing"):
            print(f"YouPhonium is ready at {text}", flush=True)
            try:
                opened = webbrowser.open(versioned_app_url(text))
            except (OSError, webbrowser.Error):
                opened = False
            if not opened:
                print(f"Open this address in your browser: {text}", flush=True)
            if kind == "ready":
                print("Keep this Terminal window open. Press Ctrl+C to stop YouPhonium.", flush=True)
            return
        print(text, flush=True)

    runner.publish = publish

    def request_stop(*_):
        runner.cancel.set()

    previous_handlers = {}
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous_handlers[sig] = signal.signal(sig, request_stop)

    print("Starting YouPhonium…", flush=True)
    try:
        # This is a personal launcher: first-time setup proceeds without a GUI prompt.
        runner.run(allow_setup=True)
    except KeyboardInterrupt:
        runner.cancel.set()
    finally:
        runner.stop()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    main()
