# Boston cheap-events Slack feed

Posts a daily digest to Slack of whatever is happening *today* from The Boston
Calendar's monthly ["N things to do in Boston for $10 or less"][example] column.

[example]: https://www.thebostoncalendar.com/events/102-things-to-do-in-boston-for-10-or-less-september-2026

```
🎟️  16 cheap things in Boston today — Sat 9/19

• 21st Annual What the Fluff? Festival — Union Square · Free
• Roslindale PorchFest — Roslindale · Free
• Aeronaut Oktoberfest — Somerville · $10+
• 2026 South End Open Studios — South End · Free · through 9/20

From 102 things to do in Boston for $10 or less: September 2026
```

Each title links to its Boston Calendar event page. Multi-day runs are listed
every day they're open, tagged with their end date.

## How it works

1. **Finds the month's post.** The slug's leading count changes every month
   (June 108, July 100, August 106, September 102), so the URL is discovered
   from `sitemap.xml` rather than guessed. If the sitemap lookup fails, it
   brute-forces the count over 60–160.
2. **Parses the post body.** The column uses a rigid
   `N) Title / When / Where / Cost / Info` structure, so every entry is
   extracted with plain string parsing — no LLM, no API key, no dependencies
   beyond the Python standard library.
3. **Resolves the dates.** Five formats appear in the wild, all handled:

   | `When:` value                    | Resolves to                      |
   |----------------------------------|----------------------------------|
   | `Tue. 9/1`                       | that one day                     |
   | `Tue. 9/1 - Mon. 9/7`            | every day in the range           |
   | `Thursdays, starting Thu. 9/3`   | each Thursday through month end  |
   | `Tuesdays, 9/8 & 9/22`           | those two days                   |
   | `Wednesdays, 9/9, 9/16, & 9/30`  | those three days                 |

   Anything it can't read is logged loudly to stderr (which surfaces as a
   GitHub Actions warning) rather than silently dropped.
4. **Posts today's events** to a Slack webhook — either a classic incoming
   webhook (Block Kit) or a Workflow Builder trigger (a flat `text` variable),
   picked automatically from the URL shape. On days with nothing listed it
   stays quiet by default.

Verified against the June, July, August, and September 2026 posts: 416 entries
parsed, zero unrecognized dates, zero dates leaking outside their month.

## Tests

```bash
python3 -m unittest discover -s tests -v   # 31 tests, no dependencies
```

The parser test runs against `tests/fixture_september_excerpt.html`, a verbatim
excerpt of a live post. That matters more than it sounds: the real page wraps
its labels as `<p><strong>When: </strong>Tue. 9/1</p>`, so any fixture written
by hand as `<div>When: Tue. 9/1</div>` tests a shape the site never emits — and
a scraper can pass that test while extracting nothing at all from the real
page. The payload tests pin the Slack block limits (see below).

## Setup

### 1. Slack webhook

Two kinds of webhook work, and the script auto-detects which one you gave it
from the URL shape. No flag to set.

**Workflow Builder trigger** (what this setup uses) — needed on workspaces
where you can't install a custom app, which is the case on Whoop's Slack
Enterprise Grid org:

1. Slack → workspace name → **Tools & settings** → **Workflow Builder** → **New**
2. Trigger: **Starts with a webhook**. Add a data variable named `text`,
   type **Text**.
3. Step: **Send a message to a channel** → **#events** → insert the `text`
   variable as the entire message body.
4. **Publish**, then copy the `https://hooks.slack.com/triggers/...` URL.

**Classic incoming webhook** — simpler, but requires permission to install a
Slack app:

1. <https://api.slack.com/apps> → **Create New App** → **Blank app**
2. **Incoming Webhooks** → **On** → **Add New Webhook to Workspace** → **#events**
3. Copy the `https://hooks.slack.com/services/...` URL

Either URL is a credential — anyone holding it can post to #events. Keep it in
the Actions secret below, never in this repo. If one leaks, delete the trigger
or webhook in Slack and issue a new one.

### 2. GitHub repo

```bash
cd boston-events-feed
gh repo create boston-events-feed --private --source=. --push
gh secret set SLACK_WEBHOOK_URL
```

`gh repo create --source=. --push` creates the remote and pushes in one step —
don't create the repo through the web UI first, or its auto-generated README
will conflict with this history.

Run `gh secret set` yourself rather than pasting the webhook anywhere: it reads
the value from your terminal straight into GitHub's encrypted secret store.

A private repo is fine — Actions cron works the same, and a daily one-minute
job sits far inside the free tier. Scheduled workflows only run on the
**default branch**, so keep `daily-digest.yml` on `main`.

**This deliberately lives on a personal account, not the WhoopInc org.** That
org disables Actions at the org level (a repo admin cannot override it), and
its rulesets require a Jira ticket in every commit message plus a peer
approval for any change to `main` — which is more process than a personal
events digest warrants. If it ever needs to move into the org for ownership
or continuity reasons, an org admin has to enable Actions for the repo first.

### 3. Verify

```bash
# Locally, no Slack involved:
python3 boston_feed.py --dry-run

# Preview the Workflow Builder payload specifically:
python3 boston_feed.py --workflow-payload --dry-run

# End-to-end into #events:
SLACK_WEBHOOK_URL="https://hooks.slack.com/triggers/..." python3 boston_feed.py
```

Then trigger the real thing once from GitHub: **Actions** → *Daily Boston
events digest* → **Run workflow** (tick *dry_run* first if you want a no-post
smoke test). After that it runs itself every morning.

## Options

| Flag | Effect |
|---|---|
| `--date 2026-09-19` | Run as if it were that day — handy for testing a busy Saturday |
| `--dry-run` | Print the payload and a rendered preview, post nothing |
| `--post-when-empty` | Post a "nothing today" note instead of staying silent |
| `--expect-hour 8` | Exit unless the current Boston hour matches; for DST-safe local cron |
| `--workflow-payload` | Force the flat Workflow Builder payload; useful with `--dry-run` |

Set the `SOURCE_URL` env var (or a GitHub Actions repo variable of that name)
to pin the job to a specific post URL if monthly discovery ever breaks.

## Notes and known edges

- **Post time drifts with DST.** GitHub cron is UTC-only, so `0 12 * * *` is
  8am Boston in summer and 7am in winter. Change it to `0 13 * * *` around
  November if the winter hour bothers you. GitHub also delays scheduled runs
  under load, sometimes by up to an hour — the digest is not minute-accurate by
  design.
- **The source is a hand-written column.** Its typos pass straight through
  (September's post really does list the Public Garden Swan Boats as being in
  Allston). The script reports what the column says.
- **New month, missing post.** If the column for a new month isn't up yet on
  the 1st, the run fails loudly so GitHub emails you, rather than posting an
  empty digest. It recovers on its own once the post is published.
- **If the site's HTML changes**, parsing fails fast with
  `page layout changed: ...` instead of posting a half-empty message.
- **Slack block limits.** A section block caps at 3000 characters and a message
  at 50 blocks. September 12th had 26 events (3496 characters), so the list is
  packed across multiple section blocks instead of being truncated. Worst
  observed month needs 4 blocks, well inside the limit.
- **Workflow Builder mode has no block structure.** The whole digest goes over
  as one text variable, so the per-section packing does not apply and the
  headline is bold text rather than a separate header block. Slack caps a
  single message at 4000 characters and the 26-event Sep 12 runs 3711, so the
  headroom is thin: past 3900 the tail events are dropped with a "+N more"
  link rather than risking rejection of the whole post. Multi-day runs sort
  last and so get dropped first, since they recur the next day anyway. No day
  across June–September 2026 needed trimming.
- **Scope.** Only events the column lists get posted; this is a reader of that
  column, not a general Boston Calendar crawler.
