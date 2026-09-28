#!/bin/bash
# Trigger the digests via workflow_dispatch, and notify if the morning fails.
#
# GitHub's `schedule` trigger is unusable on this repo: runs fired 3-5.5 hours
# late every day for a week, and neither moving off the top of the hour nor
# making the repo public helped. Dispatches start within ~20 seconds, so a
# launchd agent does the timekeeping and GitHub still executes -- which keeps
# the Slack webhooks in Actions secrets and the CI test gate in front of every
# post. The Mac only supplies the clock.
#
# The Mac supplying the clock brings its own failure: at 9am it is typically
# waking and joining a network, so early attempts hit dead DNS, timeouts and
# connection resets. Four of the first five mornings lost a digest that way,
# silently. Hence: wait for connectivity, retry, and record a per-day marker
# on success so the agent can run repeatedly through the morning and only ever
# send what has not already gone out.
#
# On the last run of the morning, audit what actually happened and raise a
# macOS notification if anything is missing or failed. Dispatching is not the
# same as succeeding: a digest can dispatch cleanly and then fail in CI (the
# source site answers 403 intermittently), which no marker would ever catch.

set -uo pipefail

REPO="${REPO:-SamirDurvasulaWhoop/boston-events-feed}"
GH="/opt/homebrew/bin/gh"
LOG="${HOME}/Library/Logs/boston-events-feed.log"
STATE="${HOME}/Library/Application Support/boston-events-feed"
TODAY="$(date +%F)"

NET_WAIT_SECONDS=300
NET_POLL_SECONDS=15
DISPATCH_ATTEMPTS=4
FINAL_RUN_HOUR="${FINAL_RUN_HOUR:-12}"   # last scheduled window; audit runs then

mkdir -p "$(dirname "$LOG")" "$STATE"
say() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$1" >>"$LOG"; }

notify() {
  say "NOTIFY: $1"
  /usr/bin/osascript -e "display notification \"$1\" with title \"Boston events feed\" subtitle \"Morning digest problem\" sound name \"Basso\"" 2>/dev/null
}

[ -x "$GH" ] || { say "ERROR: gh not found at $GH"; exit 1; }

find "$STATE" -name '*.done' ! -name "${TODAY}-*" -delete 2>/dev/null

# Which digests are due today. The week-ahead one is Mondays only (%u: 1=Mon).
due=(daily-digest.yml aiweek-digest.yml)
[ "$(date +%u)" = "1" ] && due+=(weekly-digest.yml)

is_final_run() { [ "$(date +%H)" -ge "$FINAL_RUN_HOUR" ]; }

# --- audit: did each digest actually run, and did it succeed? --------------
audit() {
  local problems=()
  for wf in "${due[@]}"; do
    if [ ! -f "$STATE/${TODAY}-${wf}.done" ]; then
      problems+=("${wf%%-digest.yml} never dispatched")
      continue
    fi
    # Most recent run of this workflow created today.
    local concl
    concl=$("$GH" run list --workflow "$wf" --repo "$REPO" --limit 5 \
              --json createdAt,conclusion,status \
              --jq "[.[] | select(.createdAt | startswith(\"$TODAY\"))] | .[0]
                    | if . == null then \"missing\"
                      elif .status != \"completed\" then .status
                      else (.conclusion // \"unknown\") end" 2>/dev/null)
    case "$concl" in
      success)            ;;
      ""|missing)         problems+=("${wf%%-digest.yml}: no run found") ;;
      queued|in_progress) ;;   # still going; not a failure yet
      *)                  problems+=("${wf%%-digest.yml}: $concl") ;;
    esac
  done

  if [ ${#problems[@]} -gt 0 ]; then
    local joined
    joined=$(printf '%s; ' "${problems[@]}"); joined=${joined%; }
    notify "$joined"
  else
    say "audit: all ${#due[@]} digests succeeded"
  fi
}

# --- dispatch what is still pending ---------------------------------------
pending=()
for wf in "${due[@]}"; do
  [ -f "$STATE/${TODAY}-${wf}.done" ] || pending+=("$wf")
done

if [ ${#pending[@]} -eq 0 ]; then
  is_final_run && audit
  exit 0
fi

waited=0
until curl -sS --max-time 10 -o /dev/null https://api.github.com/zen 2>/dev/null; do
  if [ "$waited" -ge "$NET_WAIT_SECONDS" ]; then
    say "no network after ${NET_WAIT_SECONDS}s; leaving ${#pending[@]} for the next run"
    is_final_run && notify "No network — ${#pending[@]} digest(s) never sent today"
    exit 0
  fi
  sleep "$NET_POLL_SECONDS"
  waited=$((waited + NET_POLL_SECONDS))
done
[ "$waited" -gt 0 ] && say "network came up after ${waited}s"

for wf in "${pending[@]}"; do
  for attempt in $(seq 1 "$DISPATCH_ATTEMPTS"); do
    if out=$("$GH" workflow run "$wf" --repo "$REPO" 2>&1); then
      touch "$STATE/${TODAY}-${wf}.done"
      say "dispatched $wf (attempt $attempt)"
      break
    fi
    if [ "$attempt" -eq "$DISPATCH_ATTEMPTS" ]; then
      say "FAILED  $wf after $attempt attempts: ${out//$'\n'/ }"
    else
      sleep $((attempt * 20))
    fi
  done
done

# Give the runs a moment to finish before judging them.
if is_final_run; then
  sleep 90
  audit
fi
