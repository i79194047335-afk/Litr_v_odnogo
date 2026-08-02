#!/bin/sh
# Cron wrapper for the capture healthcheck.
#
# Exists because cron starts in $HOME, and the check resolves data/ relative to
# the project root. Putting the directory here rather than in the crontab line
# means the schedule cannot be installed without it — a crontab entry missing
# its `cd` fails silently at 06:00 with nobody watching.
#
# Resolves its own location, so the repository can move without editing cron.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
exec .venv/bin/python -m scripts.collector_health "$@"
