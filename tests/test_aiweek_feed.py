"""Tests for the Boston AI Week digest.

As with the Boston Calendar feed, the parser test runs against a verbatim
excerpt of the live schedule page rather than hand-written HTML, because the
markup that actually breaks scrapers is the markup you would not think to
invent (React comment markers inside the date line, RSVP counts appended to
the location field, price badges distinguished only by a utility class).
"""

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiweek_feed as aw

FIXTURE = Path(__file__).parent / "fixture_aiweek_cards.html"
ICS_FIXTURE = Path(__file__).parent / "fixture_aiweek.ics"


def make(**kw):
    defaults = dict(
        title="Some Event", url="https://aiweek.boston/schedule/x",
        start=datetime(2026, 9, 29, 18, 0), end=datetime(2026, 9, 29, 20, 0),
        day=date(2026, 9, 29), price="Free", fmt="Meetup", host="Host",
        location="Venture Lane", when_raw="Tue, Sep 29 · 6:00 PM–8:00 PM ET",
    )
    return aw.Event(**{**defaults, **kw})


class TestParseWhen(unittest.TestCase):
    def test_evening_range(self):
        start, end, day = aw.parse_when("Thu, Sep 17 · 6:00 PM–8:30 PM ET", 2026)
        self.assertEqual(start, datetime(2026, 9, 17, 18, 0))
        self.assertEqual(end, datetime(2026, 9, 17, 20, 30))
        self.assertEqual(day, date(2026, 9, 17))

    def test_morning_range_is_not_shifted(self):
        start, _, _ = aw.parse_when("Thu, Sep 17 · 9:45 AM–4:30 PM ET", 2026)
        self.assertEqual(start, datetime(2026, 9, 17, 9, 45))

    def test_noon_and_midnight_convert_correctly(self):
        self.assertEqual(
            aw.parse_when("Sat, Sep 26 · 12:00 PM–1:00 PM ET", 2026)[0].hour, 12
        )
        self.assertEqual(
            aw.parse_when("Sat, Sep 26 · 12:30 AM–1:00 AM ET", 2026)[0].hour, 0
        )

    def test_time_tba_keeps_the_day(self):
        start, end, day = aw.parse_when("Wed, Sep 16 · Time: TBA", 2026)
        self.assertIsNone(start)
        self.assertIsNone(end)
        self.assertEqual(day, date(2026, 9, 16))

    def test_unparseable_yields_nothing(self):
        self.assertEqual(aw.parse_when("coming soon", 2026), (None, None, None))


class TestAfterHours(unittest.TestCase):
    """Weekday 5pm+, or any time at the weekend."""

    def test_weekday_evening_included(self):
        self.assertTrue(make(start=datetime(2026, 9, 29, 17, 0)).after_hours)
        self.assertTrue(make(start=datetime(2026, 9, 29, 23, 0)).after_hours)

    def test_weekday_boundary_is_inclusive_at_five(self):
        self.assertTrue(make(start=datetime(2026, 9, 29, 17, 0)).after_hours)
        self.assertFalse(make(start=datetime(2026, 9, 29, 16, 59)).after_hours)

    def test_weekday_daytime_excluded(self):
        self.assertFalse(make(start=datetime(2026, 9, 29, 12, 0)).after_hours)
        self.assertFalse(make(start=datetime(2026, 9, 29, 8, 0)).after_hours)

    def test_weekend_any_time_included(self):
        saturday = date(2026, 9, 26)
        self.assertEqual(saturday.weekday(), 5)
        self.assertTrue(
            make(day=saturday, start=datetime(2026, 9, 26, 9, 0)).after_hours
        )
        sunday = date(2026, 9, 27)
        self.assertTrue(
            make(day=sunday, start=datetime(2026, 9, 27, 11, 0)).after_hours
        )

    def test_unknown_start_is_not_silently_after_hours(self):
        self.assertFalse(make(start=None, end=None).after_hours)


class TestEventFlags(unittest.TestCase):
    def test_virtual_detection(self):
        for loc in ("Virtual", "Online webinar", "Zoom link to follow", "Virtual2 going"):
            with self.subTest(loc):
                self.assertTrue(make(location=loc).is_virtual)
        self.assertFalse(make(location="Venture Lane").is_virtual)

    def test_at_capacity_detection(self):
        self.assertTrue(make(location="Fan Pier8 going · 4 waitlisted · At capacity").at_capacity)
        self.assertFalse(make(location="Fan Pier").at_capacity)

    def test_free_detection(self):
        self.assertTrue(make(price="Free").is_free)
        self.assertTrue(make(price="").is_free, "ics has no price field")
        self.assertFalse(make(price="$15").is_free)


class TestShortLocation(unittest.TestCase):
    def test_strips_rsvp_counts(self):
        self.assertEqual(
            aw.short_location(make(location="Fan Pier8 going · 4 waitlisted · At capacity")),
            "Fan Pier",
        )

    def test_blanks_placeholders(self):
        for loc in (
            "TBA", "To be determined", "Location TBA",
            "Private — location shared with invited guests",
            "(venue revealed upon registration approval)",
            "Location will be disclosed to confirmed guests.",
            "Downtown Boston (venue shared upon approval)",
            "https://davios.com/bos",
        ):
            with self.subTest(loc):
                self.assertEqual(aw.short_location(make(location=loc)), "")

    def test_strips_trailing_placeholder_clause(self):
        self.assertEqual(
            aw.short_location(make(location="Greater Boston — venue TBA")),
            "Greater Boston",
        )

    def test_never_leaves_a_dangling_opener_or_dash(self):
        for loc in (
            "The Foundry -",
            "Hult International Business School Hult Center (",
            "MassRobotics (Large Tents on parking lot)",
        ):
            with self.subTest(loc):
                out = aw.short_location(make(location=loc))
                self.assertFalse(out.endswith(("(", "-", "–", "—", ",")), out)


class TestParseRealSchedulePage(unittest.TestCase):
    def setUp(self):
        self.events = aw.parse_schedule(FIXTURE.read_text(), 2026)

    def test_parses_the_fixture_cards(self):
        self.assertGreaterEqual(len(self.events), 3)

    def test_extracts_price_badge_which_the_ics_lacks_entirely(self):
        prices = {e.price for e in self.events}
        self.assertTrue(
            any(p.startswith("$") for p in prices) or "Free" in prices,
            f"expected a price badge, got {prices}",
        )

    def test_every_card_has_title_url_and_day(self):
        for event in self.events:
            with self.subTest(event.title):
                self.assertTrue(event.title)
                self.assertTrue(event.url.startswith("https://aiweek.boston/schedule/"))
                self.assertIsNotNone(event.day)

    def test_react_comment_markers_do_not_leak_into_the_date_line(self):
        for event in self.events:
            self.assertNotIn("<!--", event.when_raw)
            self.assertNotIn("-->", event.when_raw)

    def test_layout_change_fails_loudly(self):
        with self.assertRaises(SystemExit):
            aw.parse_schedule("<html><body>no cards here</body></html>", 2026)


class TestParseIcs(unittest.TestCase):
    def setUp(self):
        self.events = aw.parse_ics(ICS_FIXTURE.read_text())

    def test_parses_events(self):
        self.assertGreaterEqual(len(self.events), 2)

    def test_unfolds_wrapped_lines(self):
        """ICS folds long lines with a leading space; titles must rejoin."""
        titles = [e.title for e in self.events]
        self.assertTrue(
            any(len(t) > 60 for t in titles), f"expected a long unfolded title: {titles}"
        )
        for title in titles:
            self.assertNotIn("\n", title)

    def test_unescapes_commas(self):
        self.assertFalse(any("\\," in e.title + e.location for e in self.events))

    def test_reads_tzid_start_times(self):
        starts = [e.start for e in self.events if e.start]
        self.assertTrue(starts)
        for start in starts:
            self.assertIsInstance(start, datetime)

    def test_has_no_price_data(self):
        """The reason the ics is only a fallback."""
        self.assertTrue(all(e.price == "" for e in self.events))

    def test_empty_calendar_fails_loudly(self):
        with self.assertRaises(SystemExit):
            aw.parse_ics("BEGIN:VCALENDAR\nEND:VCALENDAR\n")


class TestDigest(unittest.TestCase):
    def test_time_label_drops_redundant_zero_minutes(self):
        self.assertEqual(
            aw.time_label(make(start=datetime(2026, 9, 29, 18, 0),
                               end=datetime(2026, 9, 29, 20, 0))),
            "6pm–8pm",
        )

    def test_time_label_keeps_real_minutes(self):
        self.assertEqual(
            aw.time_label(make(start=datetime(2026, 9, 29, 17, 15),
                               end=datetime(2026, 9, 29, 18, 30))),
            "5:15pm–6:30pm",
        )

    def test_time_label_for_tba(self):
        self.assertEqual(aw.time_label(make(start=None, end=None)), "time TBA")

    def test_long_titles_are_truncated_on_a_word_boundary(self):
        long = (
            "The Underutilization Hypothesis: On the Allocation of Existing "
            "Artificial Intelligence, Human Comparative Advantage, and the Question"
        )
        out = aw.short_title(long)
        self.assertLessEqual(len(out), aw.TITLE_LIMIT + 1)
        self.assertTrue(out.endswith("…"))
        self.assertNotIn(" …", out)

    def test_short_titles_untouched(self):
        self.assertEqual(aw.short_title("Robot Block Party"), "Robot Block Party")

    def test_pipes_in_titles_do_not_break_the_block_kit_link(self):
        event = make(title="Maven AGI | Boston AI Week | VIP Dinner")
        line = aw.digest_lines([event], plain=False)[0]
        self.assertEqual(line.count("|"), 1, f"exactly the link separator: {line}")

    def test_plain_mode_carries_no_markup(self):
        line = aw.digest_lines([make(title="A & B")], plain=True)[0]
        self.assertNotIn("&amp;", line)
        self.assertNotIn("<http", line)
        self.assertIn("https://aiweek.boston/schedule/x", line)

    def test_virtual_is_labelled(self):
        line = aw.digest_lines([make(location="Virtual")], plain=True)[0]
        self.assertIn("virtual", line)

    def test_at_capacity_is_labelled(self):
        line = aw.digest_lines(
            [make(location="Fan Pier8 going · At capacity")], plain=True
        )[0]
        self.assertIn("at capacity", line)

    def test_sort_is_chronological_with_tba_last(self):
        events = [
            make(title="tba", start=None, end=None),
            make(title="late", start=datetime(2026, 9, 29, 19, 0)),
            make(title="early", start=datetime(2026, 9, 29, 17, 0)),
        ]
        order = [e.title for e in sorted(events, key=aw.sort_key)]
        self.assertEqual(order, ["early", "late", "tba"])

    def test_headline_counts_and_pluralises(self):
        one = aw.build([make()], date(2026, 9, 29), workflow_mode=True)
        self.assertIn("1 free after-hours AI Week event —", one["headline"])
        two = aw.build([make(), make()], date(2026, 9, 29), workflow_mode=True)
        self.assertIn("2 free after-hours AI Week events —", two["headline"])

    def test_busy_day_stays_within_slack_limits(self):
        payload = aw.build(
            [make(title=f"Event {i} with a long enough name to add up") for i in range(60)],
            date(2026, 9, 29), workflow_mode=True,
        )
        import slack_digest as slack

        self.assertLessEqual(
            len(payload["headline"]) + len(payload["text"]), slack.WORKFLOW_TEXT_LIMIT
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
