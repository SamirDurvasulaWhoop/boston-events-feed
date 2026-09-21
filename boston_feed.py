#!/usr/bin/env python3
"""Post a daily digest of cheap Boston events to Slack.

Source: The Boston Calendar's monthly "N things to do in Boston for $10 or less"
post. The monthly URL is discovered from the site sitemap (the slug's leading
count changes every month), the post body is parsed into structured events, and
whatever is happening today gets posted to a Slack incoming webhook.

Stdlib only. No state between runs -- the source is re-fetched each morning.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import slack_digest as slack

SITE = "https://www.thebostoncalendar.com"
SITEMAP = f"{SITE}/sitemap.xml"
# The leading count changes monthly (108, 100, 106, 102...) and is optional
# here, so a change to that prefix doesn't break sitemap discovery.
SLUG_RE = r"[a-z0-9-]*?things-to-do-in-boston-for-10-or-less-{month}-{year}"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) boston-events-feed/1.0"
EASTERN = ZoneInfo("America/New_York")

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def url_ok(url: str) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False


# --------------------------------------------------------------------------
# Finding the current month's post
# --------------------------------------------------------------------------

def find_monthly_post(today: date) -> tuple[str, str]:
    """Return (url, title) for the month's list post.

    Tries the sitemap first (one request), then brute-forces the slug's event
    count, which is the only part of the URL that varies.
    """
    # Escape hatch: if discovery ever breaks, point the job at a URL directly.
    override = os.environ.get("SOURCE_URL", "").strip()
    if override:
        log("using SOURCE_URL override")
        return override, slug_title(override)

    month = today.strftime("%B").lower()
    year = today.year
    pattern = SLUG_RE.format(month=month, year=year)

    try:
        sitemap = fetch(SITEMAP, timeout=60)
        match = re.search(rf"{SITE}/events/{pattern}", sitemap)
        if match:
            return match.group(0), slug_title(match.group(0))
        log(f"note: no {month} {year} post in sitemap, falling back to slug scan")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        log(f"note: sitemap fetch failed ({exc}), falling back to slug scan")

    # The count has run 100-108 recently; scan a generous window around that.
    for count in range(60, 161):
        url = f"{SITE}/events/{count}-things-to-do-in-boston-for-10-or-less-{month}-{year}"
        if url_ok(url):
            return url, slug_title(url)

    raise SystemExit(
        f"could not find the '$10 or less' post for {month} {year}. "
        "The Boston Calendar may not have published it yet, or the slug format changed."
    )


def slug_title(url: str) -> str:
    slug = url.rsplit("/", 1)[-1]
    match = re.match(r"(\d+)-things-to-do-in-boston-for-10-or-less-([a-z]+)-(\d{4})", slug)
    if not match:
        return "things to do in Boston for $10 or less"
    count, month, year = match.groups()
    return f"{count} things to do in Boston for $10 or less: {month.capitalize()} {year}"


# --------------------------------------------------------------------------
# Parsing the post body
# --------------------------------------------------------------------------

@dataclass
class Event:
    number: int
    title: str
    url: str | None
    when_raw: str
    where: str
    cost: str
    info: str
    dates: list[date] = field(default_factory=list)
    end_date: date | None = None  # set only for multi-day runs


def strip_tags(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", fragment)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_post(page: str, list_month: int, list_year: int) -> list[Event]:
    start = page.find('id="event_description"')
    if start == -1:
        raise SystemExit("page layout changed: no #event_description block found")
    end = page.find("<!-- /share -->", start)
    body = page[start:end if end != -1 else len(page)]

    # Entries are numbered "1)", "2)", ... inside <strong> tags.
    chunks = re.split(r"<strong>\s*(\d+)\)\s*</strong>", body)
    events: list[Event] = []

    for i in range(1, len(chunks) - 1, 2):
        number, blob = int(chunks[i]), chunks[i + 1]

        link = re.search(r'href="([^"]+)"[^>]*>(.*?)</a>', blob, re.S)
        title = strip_tags(link.group(2)) if link else ""
        url = link.group(1) if link else None
        if not title:
            heading = re.search(r"<strong>(.*?)</strong>", blob, re.S)
            title = strip_tags(heading.group(1)) if heading else f"Event #{number}"

        def field_value(name: str) -> str:
            match = re.search(
                rf"<strong>\s*{name}:?\s*</strong>\s*(.*?)</p>", blob, re.S | re.I
            )
            return strip_tags(match.group(1)) if match else ""

        event = Event(
            number=number,
            title=title,
            url=url,
            when_raw=field_value("When"),
            where=field_value("Where"),
            cost=field_value("Cost"),
            info=field_value("Info"),
        )
        event.dates, event.end_date = parse_when(event.when_raw, list_month, list_year)
        events.append(event)

    if not events:
        raise SystemExit("page layout changed: parsed zero events from the post")
    return events


def parse_when(when: str, list_month: int, list_year: int) -> tuple[list[date], date | None]:
    """Turn a 'When:' string into concrete dates.

    Handles the formats the column actually uses:
      "Tue. 9/1"                        -> one day
      "Tue. 9/1 - Mon. 9/7"             -> inclusive range
      "Thursdays, starting Thu. 9/3"    -> weekly through end of month
      "Tuesdays, 9/8 & 9/22"            -> explicit list
      "Wednesdays, 9/9, 9/16, & 9/30"   -> explicit list
    """
    if not when:
        return [], None

    found = [
        to_date(int(m.group(1)), int(m.group(2)), list_month, list_year)
        for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})\b", when)
    ]
    found = [d for d in found if d]
    if not found:
        return [], None

    lowered = when.lower()

    # "Thursdays, starting Thu. 9/3" -- weekly from that date to end of month.
    if "starting" in lowered:
        weekday = next(
            (n for name, n in WEEKDAYS.items() if name in lowered), found[0].weekday()
        )
        cursor, out = found[0], []
        while cursor.month == found[0].month:
            if cursor.weekday() == weekday:
                out.append(cursor)
            cursor += timedelta(days=1)
        return (out or [found[0]]), None

    # A dash between exactly two dates means an inclusive multi-day run.
    is_range = len(found) == 2 and re.search(
        r"\d/\d{1,2}\s*(?:-|–|—|to|through)\s*(?:[A-Za-z]{3,9}\.?,?\s*)?\d{1,2}/", when
    )
    if is_range and found[0] <= found[1]:
        span = (found[1] - found[0]).days
        if span <= 62:
            days = [found[0] + timedelta(days=i) for i in range(span + 1)]
            return days, found[1]

    # Otherwise every date mentioned is its own occurrence.
    return sorted(set(found)), None


def to_date(month: int, day: int, list_month: int, list_year: int) -> date | None:
    """Resolve an M/D token, rolling the year over for Dec -> Jan spans."""
    year = list_year + 1 if month < list_month - 6 else list_year
    try:
        return date(year, month, day)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Slack message
# --------------------------------------------------------------------------

slack_escape = slack.slack_escape
pack_lines = slack.pack_lines
is_workflow_webhook = slack.is_workflow_webhook
slack_response_ok = slack.slack_response_ok
post_to_slack = slack.post_to_slack
WORKFLOW_TEXT_LIMIT = slack.WORKFLOW_TEXT_LIMIT


def absolute(url: str | None) -> str | None:
    if not url:
        return None
    return url if url.startswith("http") else f"{SITE}{url}"


def normalize_cost(cost: str) -> str:
    cleaned = cost.strip().rstrip(".")
    if not cleaned:
        return ""
    return "Free" if cleaned.lower() in {"free", "$free", "free!", "$0"} else cleaned


def digest_headline(events: list[Event], today: date) -> str:
    day_label = today.strftime("%a %-m/%-d")
    count = len(events)
    return f"{count} cheap thing{'s' if count != 1 else ''} in Boston today — {day_label}"


def digest_lines(events: list[Event], today: date, plain: bool = False) -> list[str]:
    """One bullet per event.

    `plain=True` is for Workflow Builder, which substitutes data variables as
    literal text: mrkdwn is not interpreted, so `*bold*` and `<url|label>`
    would show up as punctuation and `&amp;` as itself. So no markup and no
    escaping in that mode.
    """
    lines = []
    for event in events:
        if plain:
            line = f"•  {event.title}"
        else:
            title = slack_escape(event.title)
            url = absolute(event.url)
            line = f"• <{url}|{title}>" if url else f"• *{title}*"

        where = event.where if plain else slack_escape(event.where)
        details = [where] if event.where else []
        cost = normalize_cost(event.cost)
        if cost:
            details.append(cost if plain else slack_escape(cost))
        if event.end_date and event.end_date > today:
            details.append(f"through {event.end_date.strftime('%-m/%-d')}")
        if details:
            line += " — " + " · ".join(details)
        if plain and absolute(event.url):
            # Bare URL on its own indented line: Slack auto-links it, and
            # there is no link-text syntax available in this mode. One entry
            # per event (newline included) keeps the trimming logic honest.
            line += f"\n     {absolute(event.url)}"
        lines.append(line)
    return lines


def week_days(start: date) -> list[date]:
    return [start + timedelta(days=i) for i in range(7)]


def weekly_headline(events: list[Event], start: date) -> str:
    end = start + timedelta(days=6)
    span = (
        f"{start.strftime('%b %-d')}–{end.strftime('%-d')}"
        if start.month == end.month
        else f"{start.strftime('%b %-d')}–{end.strftime('%b %-d')}"
    )
    count = len(events)
    return f"{count} cheap thing{'s' if count != 1 else ''} in Boston this week — {span}"


def weekly_lines(events: list[Event], start: date, plain: bool) -> list[str]:
    """Group the week's events under a heading per day.

    Deliberately no per-event links on the plain path: a week runs 32-48
    events, and adding Boston Calendar URLs takes the body to 4100-6200
    characters against a 3900 cap, so every single week would be truncated.
    Block Kit has multiple sections to spread across and keeps its links.
    """
    days = week_days(start)
    # Each event appears once, on the first day it runs inside the window --
    # otherwise a Fri-Sun festival is listed three times and a month-long
    # weekly series shows up every day, which swamps the digest.
    first_day: dict[int, date] = {}
    for event in events:
        within = [d for d in days if d in event.dates]
        if within:
            first_day[id(event)] = min(within)

    lines: list[str] = []
    for day in days:
        todays = sorted(
            (e for e in events if first_day.get(id(e)) == day),
            key=lambda e: (bool(e.end_date and e.end_date > day), e.number),
        )
        if not todays:
            continue
        label = day.strftime("%a %-m/%-d")
        lines.append(f"{label}" if plain else f"*{label}*")
        for event in todays:
            if plain:
                line = f"   •  {event.title}"
            else:
                title = slack_escape(event.title)
                url = absolute(event.url)
                line = f"• <{url}|{title}>" if url else f"• *{title}*"
            details = []
            if event.where:
                details.append(event.where if plain else slack_escape(event.where))
            cost = normalize_cost(event.cost)
            if cost:
                details.append(cost if plain else slack_escape(cost))
            # Since the event is listed only once, say how long it runs.
            span = [d for d in event.dates if d in days]
            if len(span) > 1:
                details.append(
                    f"through {max(span).strftime('%-m/%-d')}"
                    if event.end_date
                    else f"also {', '.join(d.strftime('%-m/%-d') for d in span[1:])}"
                )
            if details:
                line += " — " + " · ".join(details)
            lines.append(line)
        lines.append("")
    return lines[:-1] if lines and lines[-1] == "" else lines


def build_weekly(
    events: list[Event], start: date, source_url: str, source_title: str,
    workflow_mode: bool,
) -> dict:
    headline = f"🗓️  {weekly_headline(events, start)}"
    return slack.build_payload(
        headline=headline,
        lines=weekly_lines(events, start, plain=workflow_mode),
        footer=f"From <{source_url}|{slack_escape(source_title)}>",
        workflow_mode=workflow_mode,
        plain_footer=f"Full list: {source_url}",
        overflow_footer=f"+{{dropped}} more — full list: {source_url}",
    )


def build_message(events: list[Event], today: date, source_url: str, source_title: str) -> dict:
    """Block Kit payload, for a classic incoming webhook."""
    headline = digest_headline(events, today)
    lines = digest_lines(events, today)

    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"🎟️  *{headline}*"}}]
    # Slack caps a section's text at 3000 chars, and a busy Saturday (26 events
    # in Sep 2026) overruns that, so pack the list into as many sections as it
    # takes rather than truncating the day.
    for chunk in pack_lines(lines, limit=2900):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
    blocks.append(
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"From <{source_url}|{slack_escape(source_title)}>"}
            ],
        }
    )
    return {"text": f"🎟️ {headline}", "blocks": blocks}




def build_workflow_payload(
    events: list[Event], today: date, source_url: str, source_title: str
) -> dict:
    """Flat payload for a Workflow Builder webhook trigger.

    Workflow Builder does not accept Block Kit -- the trigger takes the data
    variables the workflow declares, and the workflow's own "send a message"
    step does the posting. Variables are substituted as literal text, so mrkdwn
    in them renders as punctuation.

    Hence two variables rather than one: `headline` and `text`. The workflow's
    message step applies bold to the `headline` chip, which is the only way to
    get real formatting in this mode -- Slack applies the step's own rich-text
    styling to whatever the variable resolves to.
    """
    headline = f"🎟️  {digest_headline(events, today)}"
    lines = digest_lines(events, today, plain=True)

    def assemble_body(shown: list[str], dropped: int) -> str:
        # A bare URL is the only thing Slack will still auto-link here, so the
        # source goes on its own line rather than behind link text. Per-event
        # links are dropped in this mode: 26 full Boston Calendar URLs would
        # dwarf the event names and blow the message limit.
        tail = f"+{dropped} more — full list: {source_url}" if dropped else (
            f"Full list: {source_url}"
        )
        return "\n".join([*shown, "", tail])

    # A single Slack message caps at 4000 characters and there are no blocks to
    # spread across in this mode. A 26-event Saturday runs ~3750, so trim from
    # the tail rather than risk the whole post being rejected. Multi-day runs
    # sort last, so they are dropped first -- they recur tomorrow anyway.
    body = assemble_body(lines, 0)
    dropped = 0
    while (
        len(headline) + len(body) > WORKFLOW_TEXT_LIMIT and len(lines) - dropped > 1
    ):
        dropped += 1
        body = assemble_body(lines[: len(lines) - dropped], dropped)
    if dropped:
        log(f"warning: trimmed {dropped} events to fit Slack's message limit")
    return {"headline": headline, "text": body}



def log(message: str) -> None:
    print(message, file=sys.stderr)


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="run for this YYYY-MM-DD instead of today")
    parser.add_argument("--dry-run", action="store_true", help="print the message, do not post")
    parser.add_argument(
        "--expect-hour", type=int, default=None,
        help="exit quietly unless the current Eastern hour matches (DST-safe scheduling)",
    )
    parser.add_argument(
        "--quiet-when-empty", action="store_true", default=True,
        help="skip posting on days with no events (default)",
    )
    parser.add_argument(
        "--post-when-empty", dest="quiet_when_empty", action="store_false",
        help="post a 'nothing today' message instead of staying silent",
    )
    parser.add_argument(
        "--workflow-payload", action="store_true",
        help="force the flat Workflow Builder payload (auto-detected from the "
             "webhook URL otherwise); useful with --dry-run",
    )
    parser.add_argument(
        "--weekly", action="store_true",
        help="digest the seven days starting today, grouped by day, instead "
             "of just today",
    )
    args = parser.parse_args()

    if args.expect_hour is not None:
        from datetime import datetime

        current = datetime.now(EASTERN).hour
        if current != args.expect_hour:
            log(f"skipping: Eastern hour is {current}, waiting for {args.expect_hour}")
            return 0

    today = date.fromisoformat(args.date) if args.date else date.today()

    source_url, source_title = find_monthly_post(today)
    log(f"source: {source_url}")

    page = fetch(source_url)
    events = parse_post(page, today.month, today.year)
    log(f"parsed {len(events)} events")

    undated = [e for e in events if not e.dates]
    if undated:
        log(f"warning: {len(undated)} entries had unrecognized dates:")
        for event in undated:
            log(f"  #{event.number} {event.title!r} When={event.when_raw!r}")

    todays = [e for e in events if today in e.dates]
    todays.sort(key=lambda e: (bool(e.end_date and e.end_date > today), e.number))
    log(f"{len(todays)} happening on {today}")

    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    workflow_mode = args.workflow_payload or is_workflow_webhook(webhook)
    log(f"payload mode: {'workflow-builder' if workflow_mode else 'block-kit'}")

    if args.weekly:
        span = week_days(today)
        # An event running Fri-Sun counts once for the week, not three times.
        week = [e for e in events if any(d in e.dates for d in span)]
        log(f"{len(week)} events over {span[0]}..{span[-1]}")
        if not week:
            if args.quiet_when_empty:
                log("nothing this week; staying quiet")
                return 0
            log("nothing this week")
        payload = build_weekly(week, today, source_url, source_title, workflow_mode)
        if args.dry_run:
            print(slack.render_preview(payload))
            return 0
        if not webhook:
            raise SystemExit("SLACK_WEBHOOK_URL is not set")
        post_to_slack(webhook, payload)
        log("posted")
        return 0

    empty_text = (
        f"🎟️  Nothing on the cheap list for *{today.strftime('%a %-m/%-d')}* — "
        f"see <{source_url}|the full month>."
    )

    if not todays:
        if args.quiet_when_empty:
            log("nothing today; staying quiet")
            return 0
        payload = (
            {
                "headline": f"🎟️  Nothing on the cheap list for "
                            f"{today.strftime('%a %-m/%-d')}",
                "text": f"Full list: {source_url}",
            }
            if workflow_mode
            else {
                "text": f"No $10-or-less picks listed for {today.strftime('%a %-m/%-d')}.",
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": empty_text}}
                ],
            }
        )
    elif workflow_mode:
        payload = build_workflow_payload(todays, today, source_url, source_title)
    else:
        payload = build_message(todays, today, source_url, source_title)

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        print("\n--- rendered ---")
        if "blocks" not in payload:
            # Mirrors how the workflow step renders it: bold headline, then body.
            print(f"[bold] {payload['headline']}")
            print(payload["text"])
        else:
            for block in payload["blocks"]:
                if block["type"] == "section":
                    print(block["text"]["text"])
                elif block["type"] == "context":
                    print(block["elements"][0]["text"])
        return 0

    if not webhook:
        raise SystemExit("SLACK_WEBHOOK_URL is not set")
    post_to_slack(webhook, payload)
    log("posted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
