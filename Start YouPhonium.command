#!/bin/bash
# Double-click in Finder. Compatible with macOS's bundled Bash 3.2.
launcher_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd) || exit 1

# Finder does not necessarily inherit Homebrew or shell-profile PATH settings.
# An explicit override is useful for nonstandard Python installations.
if [ -n "${YOUPHONIUM_PYTHON:-}" ]; then
    python_candidates=("$YOUPHONIUM_PYTHON")
else
    python_candidates=(
        "$launcher_dir/backend/venv/bin/python"
        python3.13 python3.12 python3.11 python3
        /opt/homebrew/bin/python3 /usr/local/bin/python3
        /Library/Frameworks/Python.framework/Versions/Current/bin/python3
        /Library/Frameworks/Python.framework/Versions/3.*/bin/python3
        /opt/homebrew/opt/python@3.*/bin/python3.*
        /usr/local/opt/python@3.*/bin/python3.*
    )
fi

for candidate in "${python_candidates[@]}"; do
    python_path=$(command -v "$candidate") || continue
    # Apple's developer-tools stub can prompt to install Xcode and is not the
    # Python distribution we need. Never modify or depend on it.
    [ "$python_path" = /usr/bin/python3 ] && continue
    [ -x "$python_path" ] || continue
    if "$python_path" -c 'import sys; assert sys.version_info >= (3, 11); import tkinter, venv, ensurepip' >/dev/null 2>&1; then
        # launch.py prompts before setup, creates backend/venv if missing,
        # installs requirements there, initializes HOMR, and opens the browser.
        exec "$python_path" "$launcher_dir/launch.py"
    fi
done

message='YouPhonium needs Python 3.11 or newer with Tkinter and venv support. Install Python from https://www.python.org/downloads/macos/, run its Install Certificates.command, then double-click Start YouPhonium.command again. Python 3.13 is the version tested with this project.'
printf '%s\n' "$message" >&2
if command -v osascript >/dev/null 2>&1; then
    # Static AppleScript: no paths or environment values are interpolated.
    osascript -e 'display alert "YouPhonium needs Python" message "Install Python 3.11 or newer from python.org/downloads/macos/ (3.13 is tested with this project). The standard installer includes Tkinter and venv. Run its Install Certificates.command, then open Start YouPhonium.command again." as critical' || true
fi
exit 1
