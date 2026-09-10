#!/bin/sh
# Resolve the repository before the installer changes any working directory.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Python 3.9 or newer is required. Install python3, then run this installer again.' >&2
    exit 1
fi
exec python3 "$SCRIPT_DIR/scripts/install.py" "$@"
