#!/usr/bin/env python3
"""Post a daily digest of free, after-hours Boston AI Week events to Slack.

Source of record is the festival's own schedule page with its free filter
applied: https://aiweek.boston/schedule?free=true -- that filter is applied
server-side, and the page carries the price badge for each event. The .ics
export does *not* contain price data at all, so it is only a fallback for
pointing this at other calendars.

"After hours" means a weekday start at or after 5pm, or any time on a weekend.

Stdlib only.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import slack_digest as slack

SITE = "https://aiweek.boston"
SCHEDULE_URL = f"{SITE}/schedule?free=true"
EASTERN = ZoneInfo("America/New_York")

# A weekday event counts as after-hours if it starts at or after this time.
AFTER_HOURS_START = time(17, 0)

MONTHS = {
    m: i + 1
    for i, m in enumerate(
        "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    )
}


@dataclass
class Event:
    title: str
    url: str
    start: datetime | None  # None when the schedule says "Time: TBA"
    end: datetime | None
    day: date | None
    price: str
    fmt: str
    host: str
    location: str
    when_raw: str

    @property
    def is_virtual(self) -> bool:
        return bool(re.search(r"virtual|online|zoom|remote|webinar", self.location, re.I))

    @property
    def is_free(self) -> bool:
        return self.price.strip().lower() in {"free", "$0", ""}

    @property
    def at_capacity(self) -> bool:
        return "at capacity" in self.location.lower()

    @property
    def after_hours(self) -> bool:
        """Weekend any time, or a weekday start at/after 5pm."""
        if self.start is None or self.day is None:
            return False
        return self.day.weekday() >= 5 or self.start.time() >= AFTER_HOURS_START


# --------------------------------------------------------------------------
# Fetching and parsing the schedule page
# --------------------------------------------------------------------------

def fetch(url: str, timeout: int = 45) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": slack.UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def text_of(fragment: str) -> str:
    """Strip tags, React comment markers and entities down to plain text."""
    out = re.sub(r"<!--.*?-->", "", fragment, flags=re.S)
    out = re.sub(r"<[^>]+>", " ", out)
    return re.sub(r"\s+", " ", html.unescape(out)).strip()


def parse_schedule(page: str, year: int) -> list[Event]:
    cards = re.split(r'<div class="format-accent group relative', page)[1:]
    if not cards:
        raise SystemExit(
            "page layout changed: no event cards found on the schedule page"
        )

    events: list[Event] = []
    for card in cards:
        link = re.search(r'href="(/schedule/[a-z0-9-]+)"[^>]*>(.*?)</a>', card, re.S)
        when = re.search(
            r'<p class="mt-2 text-xs font-medium[^"]*">(.*?)</p>', card, re.S
        )
        if not (link and when):
            continue

        price = re.search(
            r'<span class="ml-auto rounded-full[^"]*"[^>]*>([^<]{1,16})</span>', card
        )
        fmt = re.search(
            r'<span class="rounded-full px-2\.5[^"]*"[^>]*>([^<]{1,24})</span>', card
        )
        host = re.search(
            r'<p class="mt-1 text-xs text-muted-foreground">(.*?)</p>', card, re.S
        )
        loc = re.search(
            r'<div class="mt-auto pt-3 text-xs text-muted-foreground">(.*?)</div>',
            card,
            re.S,
        )

        when_raw = text_of(when.group(1))
        start, end, day = parse_when(when_raw, year)
        events.append(
            Event(
                title=text_of(link.group(2)),
                url=f"{SITE}{link.group(1)}",
                start=start,
                end=end,
                day=day,
                price=text_of(price.group(1)) if price else "",
                fmt=text_of(fmt.group(1)) if fmt else "",
                host=re.sub(r"^by\s+", "", text_of(host.group(1))) if host else "",
                location=text_of(loc.group(1)) if loc else "",
                when_raw=when_raw,
            )
        )
    return events


def parse_when(raw: str, year: int) -> tuple[datetime | None, datetime | None, date | None]:
    """Parse the card's date/time line.

    Real shapes on the page:
      "Thu, Sep 17 · 6:00 PM-8:30 PM ET"
      "Thu, Sep 17 · 9:45 AM-4:30 PM ET"
      "Wed, Sep 16 · Time: TBA"
    """
    day_match = re.search(r"\b([A-Z][a-z]{2}) (\d{1,2})\b", raw)
    if not day_match or day_match.group(1) not in MONTHS:
        return None, None, None
    day = date(year, MONTHS[day_match.group(1)], int(day_match.group(2)))

    times = re.findall(r"(\d{1,2}):(\d{2})\s*([AP]M)", raw, re.I)
    if not times:
        return None, None, day  # "Time: TBA"

    def at(index: int) -> datetime | None:
        if index >= len(times):
            return None
        hh, mm, ap = times[index]
        hour = int(hh) % 12 + (12 if ap.upper() == "PM" else 0)
        return datetime.combine(day, time(hour, int(mm)))

    return at(0), at(1), day


# --------------------------------------------------------------------------
# .ics fallback
# --------------------------------------------------------------------------

def parse_ics(text: str) -> list[Event]:
    """Parse a calendar export.

    Only a fallback: an .ics has no price field, so `is_free` will read as
    True for everything. That is sound only when the export was already
    filtered (aiweek.boston generates its .ics from the filtered view), and
    the caller is warned.
    """
    # Unfold: continuation lines begin with a space or tab.
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    events: list[Event] = []

    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfolded, re.S):
        def prop(name: str) -> str:
            m = re.search(rf"^{name}[^:\r\n]*:(.*)$", block, re.M)
            if not m:
                return ""
            raw = m.group(1).strip()
            return raw.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ")

        start = parse_ics_datetime(block, "DTSTART")
        end = parse_ics_datetime(block, "DTEND")
        if not prop("SUMMARY"):
            continue
        events.append(
            Event(
                title=prop("SUMMARY"),
                url=prop("URL"),
                start=start,
                end=end,
                day=start.date() if start else None,
                price="",  # not present in iCalendar
                fmt="",
                host="",
                location=prop("LOCATION"),
                when_raw=prop("DTSTART"),
            )
        )
    if not events:
        raise SystemExit("no VEVENT blocks found in the calendar")
    return events


def parse_ics_datetime(block: str, name: str) -> datetime | None:
    m = re.search(rf"^{name}[^:\r\n]*:(\d{{8}})T(\d{{6}})", block, re.M)
    if not m:
        return None
    ymd, hms = m.groups()
    return datetime(
        int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]),
        int(hms[:2]), int(hms[2:4]),
    )


# --------------------------------------------------------------------------
# Digest
# --------------------------------------------------------------------------

def time_label(event: Event) -> str:
    if event.start is None:
        return "time TBA"
    label = event.start.strftime("%-I:%M%p").lower().replace(":00", "")
    if event.end:
        end = event.end.strftime("%-I:%M%p").lower().replace(":00", "")
        return f"{label}–{end}"
    return label


PLACEHOLDER_LOCATION = re.compile(
    r"^(tba|tbd|to be (determined|confirmed|announced)|location tba|private\b)"
    r"|\b(disclosed|revealed|shared (upon|with))\b"
    r"|\bupon (registration|approval)\b"
    r"|\bto be (announced|determined|confirmed)\b"
    r"|\b(tba|tbd)\b",
    re.I,
)

# Some titles run to 150+ characters, which wraps into a paragraph in Slack.
TITLE_LIMIT = 88


def short_title(title: str) -> str:
    if len(title) <= TITLE_LIMIT:
        return title
    return title[:TITLE_LIMIT].rsplit(" ", 1)[0].rstrip(" ,:;-–—") + "…"


def short_location(event: Event) -> str:
    """A short, clean venue name, or "" when the page has nothing useful.

    The page appends RSVP counts to this field ("Fan Pier8 going · 4
    waitlisted · At capacity"), sometimes puts a bare URL there, and often
    carries placeholders like "To be confirmed. A venue in Kendall Square".
    """
    loc = re.split(r"\s*\d+ going|\s*\d+ waitlisted|\s*At capacity", event.location)[0]
    loc = loc.split(",")[0].split("\n")[0].strip()
    # Drop a trailing placeholder clause, e.g. "Greater Boston — venue TBA".
    head = re.split(r"\s+[—–-]\s+", loc)
    if len(head) > 1 and PLACEHOLDER_LOCATION.search(head[-1]):
        loc = " — ".join(head[:-1]).strip()
    # search, not match: several read "(venue revealed upon approval)", so the
    # giveaway phrase is not always at position 0.
    if not loc or loc.lower().startswith("http") or PLACEHOLDER_LOCATION.search(loc):
        return ""
    # Truncate on a word boundary and drop any dangling opener or dash.
    if len(loc) > 44:
        loc = loc[:44].rsplit(" ", 1)[0]
    return loc.strip(" -–—([{&/|").strip()


def digest_lines(events: list[Event], plain: bool) -> list[str]:
    lines = []
    for event in events:
        if plain:
            head = f"•  {short_title(event.title)}"
        else:
            # A pipe inside <url|text> would truncate the link label, and
            # several titles contain one ("Maven AGI | Boston AI Week | ...").
            title = slack.slack_escape(short_title(event.title)).replace("|", "·")
            head = f"• <{event.url}|{title}>" if event.url else f"• *{title}*"

        details = [time_label(event)]
        location = short_location(event)
        if event.is_virtual:
            details.append("virtual")
        elif location:
            details.append(location if plain else slack.slack_escape(location))
        if event.at_capacity:
            details.append("at capacity")
        line = f"{head} — {' · '.join(details)}"

        if plain and event.url:
            line += f"\n     {event.url}"
        lines.append(line)
    return lines


def build(events: list[Event], today: date, workflow_mode: bool) -> dict:
    count = len(events)
    headline = (
        f"🤖  {count} free after-hours AI Week event"
        f"{'s' if count != 1 else ''} — {today.strftime('%a %-m/%-d')}"
    )
    footer_url = SCHEDULE_URL
    return slack.build_payload(
        headline=headline,
        lines=digest_lines(events, plain=workflow_mode),
        footer=f"From <{footer_url}|the free Boston AI Week schedule>",
        workflow_mode=workflow_mode,
        plain_footer=f"Full schedule: {footer_url}",
        overflow_footer=f"+{{dropped}} more — full schedule: {footer_url}",
    )


def sort_key(event: Event) -> tuple:
    """Chronological, with unknown start times last.

    Deliberately not sinking at-capacity events: trimming only engages past
    3900 characters, which no real day comes close to, so a readable timeline
    is worth more than optimising which entry gets dropped first.
    """
    return (event.start is None, event.start or datetime.max, event.title)


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="run for this YYYY-MM-DD instead of today")
    parser.add_argument("--dry-run", action="store_true", help="print, do not post")
    parser.add_argument(
        "--ics", help="parse this .ics file instead of scraping the schedule page"
    )
    parser.add_argument(
        "--include-work-hours", action="store_true",
        help="skip the after-hours filter and list everything free that day",
    )
    parser.add_argument(
        "--no-virtual", action="store_true", help="drop virtual events"
    )
    parser.add_argument(
        "--post-when-empty", dest="quiet_when_empty", action="store_false",
        default=True, help="post a 'nothing tonight' note instead of staying silent",
    )
    parser.add_argument(
        "--workflow-payload", action="store_true",
        help="force the flat Workflow Builder payload (auto-detected otherwise)",
    )
    args = parser.parse_args()

    today = date.fromisoformat(args.date) if args.date else datetime.now(EASTERN).date()

    if args.ics:
        slack.log(f"source: {args.ics} (calendar has no price data; trusting export)")
        events = parse_ics(open(args.ics, encoding="utf-8", errors="replace").read())
    else:
        slack.log(f"source: {SCHEDULE_URL}")
        events = parse_schedule(fetch(SCHEDULE_URL), today.year)
    slack.log(f"parsed {len(events)} free events")

    tba = [e for e in events if e.day and e.start is None]
    if tba:
        slack.log(f"note: {len(tba)} events have no start time (Time: TBA)")

    todays = [e for e in events if e.day == today]
    slack.log(f"{len(todays)} on {today}")

    selected = todays if args.include_work_hours else [e for e in todays if e.after_hours]
    if not args.include_work_hours:
        slack.log(f"{len(selected)} after-hours (weekday 5pm+, any weekend)")
    # A TBA event on a weekend is still after-hours by definition; on a weekday
    # we cannot know, so surface it rather than silently dropping it.
    if not args.include_work_hours:
        selected += [e for e in todays if e.start is None and e not in selected]
    if args.no_virtual:
        selected = [e for e in selected if not e.is_virtual]
    selected.sort(key=sort_key)

    # Its own Slack workflow (so the posts are attributed to "Boston AI Week
    # Events" and can be retired after the festival without touching the other
    # feed), falling back to the shared one if that is not configured.
    webhook = (
        os.environ.get("SLACK_WEBHOOK_URL_AIWEEK", "").strip()
        or os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    )
    workflow_mode = args.workflow_payload or slack.is_workflow_webhook(webhook)
    slack.log(f"payload mode: {'workflow-builder' if workflow_mode else 'block-kit'}")

    if not selected:
        if args.quiet_when_empty:
            slack.log("nothing tonight; staying quiet")
            return 0
        headline = f"🤖  No free after-hours AI Week events — {today.strftime('%a %-m/%-d')}"
        payload = (
            {"headline": headline, "text": f"Full schedule: {SCHEDULE_URL}"}
            if workflow_mode
            else slack.build_block_payload(
                headline, [], f"<{SCHEDULE_URL}|Full schedule>"
            )
        )
    else:
        payload = build(selected, today, workflow_mode)

    if args.dry_run:
        print(slack.render_preview(payload))
        return 0

    if not webhook:
        raise SystemExit("SLACK_WEBHOOK_URL is not set")
    slack.post_to_slack(webhook, payload)
    slack.log("posted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
