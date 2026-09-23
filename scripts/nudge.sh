#!/bin/bash
# Trigger the digests via workflow_dispatch.
#
# GitHub's `schedule` trigger is unusable on this repo: runs fired 3-5.5 hours
# late every day for a week, and making the repo public did not help. Manual
# dispatches, by contrast, start within ~20 seconds. So a local launchd job
# does the timekeeping and GitHub just executes -- which also means the Slack
# webhooks stay in Actions secrets rather than on this machine, and the CI
# test gate still runs before every post.
#
# launchd uses local wall-clock time, so this follows DST automatically; there
# is no November clock change to remember here.

set -uo pipefail

REPO="SamirDurvasulaWhoop/boston-events-feed"
GH="/opt/homebrew/bin/gh"
LOG="${HOME}/Library/Logs/boston-events-feed.log"

mkdir -p "$(dirname "$LOG")"
say() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$1" >>"$LOG"; }

if [ ! -x "$GH" ]; then
  say "ERROR: gh not found at $GH"
  exit 1
fi

dispatch() {
  local wf="$1"
  if out=$("$GH" workflow run "$wf" --repo "$REPO" 2>&1); then
    say "dispatched $wf"
  else
    say "FAILED  $wf: ${out//$'\n'/ }"
  fi
}

dispatch daily-digest.yml
dispatch aiweek-digest.yml

# The week-ahead digest, Mondays only (%u: 1 = Monday).
if [ "$(date +%u)" = "1" ]; then
  dispatch weekly-digest.yml
fi
