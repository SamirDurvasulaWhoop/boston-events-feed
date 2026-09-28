#!/bin/bash
# Trigger the digests via workflow_dispatch.
#
# GitHub's `schedule` trigger is unusable on this repo: runs fired 3-5.5 hours
# late every day for a week, and neither moving off the top of the hour nor
# making the repo public helped. Dispatches start within ~20 seconds, so a
# launchd agent does the timekeeping and GitHub still executes -- which keeps
# the Slack webhooks in Actions secrets and the CI test gate in front of every
# post. The Mac only supplies the clock.
#
# The Mac supplying the clock brings its own failure: at 9am it is typically
# waking and joining a network, so the first attempts hit dead DNS, timeouts
# and connection resets. Four of the first five mornings lost at least one
# digest that way, with no retry and no alert.
#
# Hence: wait for connectivity, retry each dispatch, and record a per-day
# marker on success so the agent can be scheduled repeatedly through the
# morning and will only ever send what has not already gone out.

set -uo pipefail

REPO="SamirDurvasulaWhoop/boston-events-feed"
GH="/opt/homebrew/bin/gh"
LOG="${HOME}/Library/Logs/boston-events-feed.log"
STATE="${HOME}/Library/Application Support/boston-events-feed"
TODAY="$(date +%F)"

NET_WAIT_SECONDS=300   # how long to wait for a usable network
NET_POLL_SECONDS=15
DISPATCH_ATTEMPTS=4

mkdir -p "$(dirname "$LOG")" "$STATE"
say() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$1" >>"$LOG"; }

[ -x "$GH" ] || { say "ERROR: gh not found at $GH"; exit 1; }

# Forget markers from previous days so the directory cannot grow forever.
find "$STATE" -name '*.done' ! -name "${TODAY}-*" -delete 2>/dev/null

# Which digests are due today. The week-ahead one is Mondays only (%u: 1=Mon).
due=(daily-digest.yml aiweek-digest.yml)
[ "$(date +%u)" = "1" ] && due+=(weekly-digest.yml)

# Skip anything already sent today; exit quietly if that is everything, so the
# agent can fire every half hour without spamming the log.
pending=()
for wf in "${due[@]}"; do
  [ -f "$STATE/${TODAY}-${wf}.done" ] || pending+=("$wf")
done
[ ${#pending[@]} -eq 0 ] && exit 0

# Wait for the network rather than failing against a half-woken Wi-Fi stack.
waited=0
until curl -sS --max-time 10 -o /dev/null https://api.github.com/zen 2>/dev/null; do
  if [ "$waited" -ge "$NET_WAIT_SECONDS" ]; then
    say "no network after ${NET_WAIT_SECONDS}s; leaving ${#pending[@]} for the next run"
    exit 0   # not an error: a later run today will pick these up
  fi
  sleep "$NET_POLL_SECONDS"
  waited=$((waited + NET_POLL_SECONDS))
done
[ "$waited" -gt 0 ] && say "network came up after ${waited}s"

for wf in "${pending[@]}"; do
  for attempt in $(seq 1 "$DISPATCH_ATTEMPTS"); do
    if out=$("$GH" workflow run "$wf" --repo "$REPO" 2>&1); then
      touch "$STATE/${TODAY}-${wf}.done"
      say "dispatched $wf${attempt:+ (attempt $attempt)}"
      break
    fi
    if [ "$attempt" -eq "$DISPATCH_ATTEMPTS" ]; then
      say "FAILED  $wf after $attempt attempts: ${out//$'\n'/ }"
    else
      sleep $((attempt * 20))
    fi
  done
done
