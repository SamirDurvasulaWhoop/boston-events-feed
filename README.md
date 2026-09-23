# Boston events Slack feeds

Two daily Slack digests of cheap and free things happening in Boston *today*:

| Feed | Source | Filter | Posts |
|---|---|---|---|
| `boston_feed.py` | The Boston Calendar's monthly ["$10 or less"][example] column | everything the column lists | 9:07am |
| `aiweek_feed.py` | [Boston AI Week schedule](https://aiweek.boston/schedule?free=true) | free **and** outside work hours | 9:37am |
| `boston_feed.py --weekly` | same as above | the coming seven days | Mondays 9:12am |

Both share `slack_digest.py` for delivery, so they render identically and
support the same two webhook flavours.

[example]: https://www.thebostoncalendar.com/events/102-things-to-do-in-boston-for-10-or-less-september-2026

```
🎟️  3 cheap things in Boston today — Thu 9/17

•  Thursdays on the Lawn at the Loring Greenough House — Jamaica Plain · Free
     https://www.thebostoncalendar.com/events/thursdays-on-the-lawn-at-the-loring-greenough-house--87
•  'The Breakfast Club' Screening — Back Bay · Free
     https://www.thebostoncalendar.com/events/breakfast-club-screening
•  Third Thursdays at the MFA — Fenway · $5
     https://www.thebostoncalendar.com/events/5-third-thursdays-at-the-museum-of-fine-arts--13

Full list: https://www.thebostoncalendar.com/events/102-things-to-do-in-boston-for-10-or-less-september-2026
```

The headline is bold in Slack and each URL is auto-linked. Multi-day runs are
listed every day they're open, tagged with their end date. On a classic
incoming webhook the same digest renders as Block Kit with the titles
themselves as the links; see the setup section for why this deployment uses
the plainer path.

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
python3 -m unittest discover -s tests -v   # 97 tests, no dependencies
```

The parser test runs against `tests/fixture_september_excerpt.html`, a verbatim
excerpt of a live post. That matters more than it sounds: the real page wraps
its labels as `<p><strong>When: </strong>Tue. 9/1</p>`, so any fixture written
by hand as `<div>When: Tue. 9/1</div>` tests a shape the site never emits — and
a scraper can pass that test while extracting nothing at all from the real
page. The payload tests pin the Slack block limits (see below).

## The AI Week feed

Boston AI Week publishes 190 events; 163 are free. The digest lists the free
ones happening today that are **outside work hours** — a weekday start at or
after 5pm, or any time at the weekend. Virtual events are included and
labelled `virtual`; events the page marks full are labelled `at capacity`.

```
🤖  10 free after-hours AI Week events — Tue 9/29

•  Founders & Funders: BOS VC Reverse Pitch — 5pm–7pm
     https://aiweek.boston/schedule/founders-and-funders-bos-vc-reverse-pitch
•  Whiskey, Wine & Whiteboards — 5:30pm–7:30pm · Venture Lane
     https://aiweek.boston/schedule/whiskey-wine-whiteboards-september-29
•  AI in Action: Real Use Cases Driving Business Impact — 6pm–8pm · Questrom School of Business
     https://aiweek.boston/schedule/ai-in-action-questrom-2026
```

**Why it scrapes the page rather than reading the .ics.** An iCalendar file has
no price field, and Boston AI Week's export carries none — no `$` appears
anywhere in it. The schedule page, by contrast, renders a price badge per event
and applies `?free=true` **server-side** (163 cards versus 192, with zero paid
events in the filtered response). So the page is the only source that can
answer "is this free", and it stays current as hosts add events.

The `--ics` flag parses a calendar file anyway, for pointing this at other
calendars. On that path every event reads as free, because the format cannot
say otherwise — sound only if the export was already filtered, and the run
logs a warning saying so.

It posts through its **own** Slack workflow, named `Boston AI Week Events`, so
the posts are attributed separately and the whole thing can be retired after
the festival without touching the other feed. Set it up exactly like the first
(same `headline` + `text` variables, same bold on the headline chip) and store
it as the `SLACK_WEBHOOK_URL_AI_WEEK` secret. If that secret is absent the feed
falls back to the shared `SLACK_WEBHOOK_URL`, so it works either way.

Known rough edges in the source data, all handled:

- 4 events say `Time: TBA`. They are surfaced with a `time TBA` label rather
  than dropped, since a weekday TBA cannot be classified as after-hours.
- The location field has RSVP counts appended (`Fan Pier8 going · 4
  waitlisted · At capacity`), sometimes holds a bare URL, and often holds a
  placeholder (`(venue revealed upon approval)`, `Greater Boston — venue TBA`).
  These are cleaned or blanked; 139 of 160 yield a usable venue name.
- Some titles run past 150 characters and are truncated on a word boundary.
- Some titles contain `|`, which would silently truncate a Block Kit link
  label, so it is replaced there.

## Reacting

Daily digests number their events `1️⃣`–`🔟` so readers can react with the
matching keycap to say they are going, with a footer line stating the
convention. Events past the tenth get a plain bullet — there is no eleventh
keycap, and regional-indicator letters are unfindable in the emoji picker.
14% of days run longer than ten.

The reactions are **not** pre-seeded, so readers pick the keycap from the
picker themselves. Seeding them needs either `reactions.add` with a bot token
(no app installs available here) or a fixed set of Workflow Builder "Add
reaction" steps — but that step takes a literal emoji, not a variable, so a
three-event day would get six numbers seeded with three of them meaningless.
Gating each step behind a branch on an event-count variable would work and is
the upgrade path if the convention catches on.

The weekly digest is deliberately not numbered: it is grouped by day, so a
single run of numbers across the groups reads as if it restarts.

## The weekly digest

`boston_feed.py --weekly` covers the seven days starting today, grouped under
a heading per day, and goes out Monday mornings.

Two things differ from the daily digest, both forced by volume — a week runs
27–48 events where a day runs 0–26:

- **Each event is listed once**, on the first day it runs inside the window,
  tagged `through 9/27`. Listing a Friday–Sunday festival on all three days
  and a weekly series on every occurrence turned a 29-event week into 39
  lines of mostly repetition.
- **No per-event links on the Workflow Builder path.** A week of Boston
  Calendar URLs runs 4100–6200 characters against a 3900 cap, so every single
  week would be truncated. Block Kit keeps its links, because it can spread
  across sections. Measured worst case: 2590/3900 plain, 2855/3000 per
  section.

## Setup

### 1. Slack webhook

Two kinds of webhook work, and the script auto-detects which one you gave it
from the URL shape. No flag to set.

**Workflow Builder trigger** (what this setup uses) — needed on workspaces
where you can't install a custom app, which is the case on Whoop's Slack
Enterprise Grid org:

1. Slack → workspace name → **Tools & settings** → **Workflow Builder** → **New**
2. Trigger: **Starts with a webhook**. Add two data variables, both type
   **Text**: `headline` and `text`.
3. Step: **Send a message to a channel** → **#events**. The message body is
   just the two variable chips: `headline`, a blank line, then `text`. Select
   the `headline` chip and press **⌘B**.
4. **Publish**, then copy the `https://hooks.slack.com/triggers/...` URL.

The bold has to be applied to the chip in the step, not in the text the script
sends: Workflow Builder substitutes variables as literal characters, so
`*bold*` arrives as asterisks and `<url|label>` as punctuation. Slack does
apply the step's own rich-text styling to whatever a variable resolves to,
which is the one way to get real formatting on this path.

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
| `--weekly` | Digest the seven days starting today, grouped by day |
| `--expect-hour 8` | Exit unless the current Boston hour matches; for DST-safe local cron |
| `--workflow-payload` | Force the flat Workflow Builder payload; useful with `--dry-run` |

Set the `SOURCE_URL` env var (or a GitHub Actions repo variable of that name)
to pin the job to a specific post URL if monthly discovery ever breaks.

## Notes and known edges

- **Post time drifts with DST, and scheduling is best-effort.** The target is
  ~9am Boston. GitHub cron is UTC-only with no DST handling, so `7 13 * * *`
  is 9:07am during EDT and 8:07am during EST — shift all three crons an hour
  later after the November clock change.

  **The repo is public on purpose.** While it was a free-tier *private* repo,
  scheduled runs fired 3–5.5 hours late every single day (13:07 UTC scheduled,
  16:22 / 16:45 / 18:36 actual) — they were never dropped, just starved.
  Free private repos get the lowest scheduling priority; public repos get far
  better treatment. Nothing here is sensitive: the webhooks live in Actions
  secrets, and no workflow triggers on `pull_request`, so a fork cannot reach
  them.

  Odd minutes (`:07`, `:12`, `:37`) are kept because the top of the hour is
  the most contended slot, but note that was *not* the cause of the delays —
  an earlier version of this note blamed `:00` and was wrong.
- **A late or dropped run is silent.** There is no state and no retry, so a
  skipped schedule just means no post — nothing errors and nothing emails you.
  If a morning goes quiet, check the Actions tab before suspecting the
  scrapers.
- **A dropped run means a missed day, silently.** There is no state and no
  retry, so a skipped schedule just means no post — nothing errors and nothing
  emails you. If a morning goes quiet, check the Actions tab before suspecting
  the scrapers.