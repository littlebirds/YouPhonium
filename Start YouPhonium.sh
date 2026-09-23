#!/bin/sh
# First-run entry point for Linux file managers: right-click > Run as a Program.
launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
if ! python3 -c 'import sys, venv, ensurepip; assert sys.version_info >= (3, 11)' 2>/dev/null; then
    message='YouPhonium needs Python 3.11 or newer with venv. On Debian, install python3 and python3-venv, then try again.'
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title='YouPhonium setup required' --text="$message"
    elif command -v xmessage >/dev/null 2>&1; then
        xmessage "$message"
    else
        printf '%s\n' "$message" >&2
    fi
    exit 1
fi
exec python3 "$launcher_dir/launch.py"
