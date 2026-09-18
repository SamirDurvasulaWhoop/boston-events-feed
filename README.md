# Boston events Slack feeds

Two daily Slack digests of cheap and free things happening in Boston *today*:

| Feed | Source | Filter | Posts |
|---|---|---|---|
| `boston_feed.py` | The Boston Calendar's monthly ["$10 or less"][example] column | everything the column lists | 9:07am |
| `aiweek_feed.py` | [Boston AI Week schedule](https://aiweek.boston/schedule?free=true) | free **and** outside work hours | 9:37am |

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
python3 -m unittest discover -s tests -v   # 76 tests, no dependencies
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
| `--expect-hour 8` | Exit unless the current Boston hour matches; for DST-safe local cron |
| `--workflow-payload` | Force the flat Workflow Builder payload; useful with `--dry-run` |

Set the `SOURCE_URL` env var (or a GitHub Actions repo variable of that name)
to pin the job to a specific post URL if monthly discovery ever breaks.

## Notes and known edges

- **Post time drifts with DST, and never schedule on the hour.** The target is
  ~9am Boston. GitHub cron is UTC-only with no DST handling, so `7 13 * * *`
  is 9:07am during EDT and 8:07am during EST — shift to `7 14 * * *` after the
  November clock change. The odd minutes are deliberate: the first version used
  `0 13 * * *` and GitHub silently skipped it entirely, producing no run at
  all rather than a late one. GitHub sheds scheduled-workflow load at the top
  of the hour, so `:00` and `:30` are the worst slots to pick.
- **A dropped run means a missed day, silently.** There is no state and no
  retry, so a skipped schedule just means no post — nothing errors and nothing
  emails you. If a morning goes quiet, check the Actions tab before suspecting
  the scrapers.