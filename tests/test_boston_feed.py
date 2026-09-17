"""Tests for the Boston cheap-events digest.

The important one is test_parses_real_page_fixture: the fixture is a verbatim
excerpt of a live Boston Calendar post, `<strong>`-wrapped labels and all.
A hand-written "When: Tue. 9/1" fixture would be a shape the real site never
emits, and would happily pass while the scraper returned nothing.
"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boston_feed as bf

FIXTURE = Path(__file__).parent / "fixture_september_excerpt.html"


class TestParseWhen(unittest.TestCase):
    def when(self, text):
        return bf.parse_when(text, 9, 2026)

    def test_single_day(self):
        dates, end = self.when("Tue. 9/1")
        self.assertEqual(dates, [date(2026, 9, 1)])
        self.assertIsNone(end)

    def test_inclusive_range_sets_end_date(self):
        dates, end = self.when("Fri. 9/18 - Sun. 9/20")
        self.assertEqual(dates, [date(2026, 9, 18), date(2026, 9, 19), date(2026, 9, 20)])
        self.assertEqual(end, date(2026, 9, 20))

    def test_weekly_recurring_through_month_end(self):
        dates, _ = self.when("Thursdays, starting Thu. 9/3")
        self.assertEqual(
            dates,
            [date(2026, 9, d) for d in (3, 10, 17, 24)],
        )

    def test_explicit_two_date_list_is_not_a_range(self):
        dates, end = self.when("Tuesdays, 9/8 & 9/22")
        self.assertEqual(dates, [date(2026, 9, 8), date(2026, 9, 22)])
        self.assertIsNone(end, "an ampersand list must not fill in the days between")

    def test_explicit_three_date_list(self):
        dates, _ = self.when("Wednesdays, 9/9, 9/16, & 9/30")
        self.assertEqual(dates, [date(2026, 9, 9), date(2026, 9, 16), date(2026, 9, 30)])

    def test_unparseable_is_reported_not_guessed(self):
        self.assertEqual(self.when("Various dates, check website"), ([], None))
        self.assertEqual(self.when(""), ([], None))

    def test_year_rolls_over_for_december_to_january_span(self):
        dates, _ = bf.parse_when("Wed. 12/30 - Sat. 1/2", 12, 2026)
        self.assertEqual(dates[0], date(2026, 12, 30))
        self.assertEqual(dates[-1], date(2027, 1, 2))

    def test_invalid_calendar_date_is_dropped(self):
        self.assertEqual(bf.parse_when("Mon. 2/30", 2, 2026), ([], None))


class TestParseRealPage(unittest.TestCase):
    def setUp(self):
        self.events = bf.parse_post(FIXTURE.read_text(), 9, 2026)

    def test_parses_real_page_fixture(self):
        self.assertEqual(len(self.events), 6)

    def test_every_entry_has_a_title_link_and_dates(self):
        for event in self.events:
            with self.subTest(event.number):
                self.assertTrue(event.title, "title must not be empty")
                self.assertTrue(event.url, "each entry links to its event page")
                self.assertTrue(event.dates, f"#{event.number} {event.when_raw!r} gave no dates")

    def test_strong_wrapped_labels_are_read(self):
        """The failure mode this whole fixture exists to catch."""
        first = self.events[0]
        self.assertEqual(first.title, "Allston Christmas")
        self.assertEqual(first.when_raw, "Tue. 9/1")
        self.assertEqual(first.where, "Allston")
        self.assertEqual(first.cost, "Free")
        self.assertTrue(first.info.startswith("The college kids are back"))

    def test_multi_day_entry_spans_its_range(self):
        swan = next(e for e in self.events if "Swan Boats" in e.title)
        self.assertEqual(swan.dates[0], date(2026, 9, 1))
        self.assertEqual(swan.end_date, date(2026, 9, 7))
        self.assertEqual(len(swan.dates), 7)

    def test_layout_change_fails_loudly(self):
        with self.assertRaises(SystemExit):
            bf.parse_post("<html><body>nothing here</body></html>", 9, 2026)

    def test_missing_entries_fails_loudly(self):
        with self.assertRaises(SystemExit):
            bf.parse_post('<div id="event_description"><p>prose only</p></div>', 9, 2026)


class TestSlackPayload(unittest.TestCase):
    def make(self, count):
        return [
            bf.Event(
                number=i,
                title=f"Event {i} with a fairly long name to pad the block out",
                url=f"/events/event-{i}",
                when_raw="Sat. 9/12",
                where="Somerville",
                cost="Free",
                info="x",
                dates=[date(2026, 9, 12)],
            )
            for i in range(count)
        ]

    def test_busy_day_stays_within_slack_limits(self):
        payload = bf.build_message(
            self.make(40), date(2026, 9, 12), "https://example.com", "Source"
        )
        sections = [b for b in payload["blocks"] if b["type"] == "section"]
        for block in sections:
            self.assertLessEqual(len(block["text"]["text"]), 3000)
        self.assertLessEqual(len(payload["blocks"]), 50)

    def test_no_events_are_dropped_on_a_busy_day(self):
        payload = bf.build_message(
            self.make(40), date(2026, 9, 12), "https://example.com", "Source"
        )
        rendered = " ".join(
            b["text"]["text"] for b in payload["blocks"] if b["type"] == "section"
        )
        for i in range(40):
            self.assertIn(f"Event {i} with", rendered)

    def test_multi_day_event_is_tagged_with_end_date(self):
        event = self.make(1)[0]
        event.end_date = date(2026, 9, 14)
        payload = bf.build_message(
            [event], date(2026, 9, 12), "https://example.com", "Source"
        )
        self.assertIn("through 9/14", payload["blocks"][1]["text"]["text"])

    def test_slack_special_characters_are_escaped(self):
        event = self.make(1)[0]
        event.title = "Rock & Roll <Live>"
        payload = bf.build_message(
            [event], date(2026, 9, 12), "https://example.com", "Source"
        )
        body = payload["blocks"][1]["text"]["text"]
        self.assertIn("Rock &amp; Roll &lt;Live&gt;", body)

    def test_relative_urls_are_made_absolute(self):
        payload = bf.build_message(
            self.make(1), date(2026, 9, 12), "https://example.com", "Source"
        )
        self.assertIn(f"{bf.SITE}/events/event-0", payload["blocks"][1]["text"]["text"])

    def test_cost_is_normalized(self):
        self.assertEqual(bf.normalize_cost("$FREE"), "Free")
        self.assertEqual(bf.normalize_cost("free."), "Free")
        self.assertEqual(bf.normalize_cost("$4.75"), "$4.75")
        self.assertEqual(bf.normalize_cost(""), "")


class TestPackLines(unittest.TestCase):
    def test_respects_limit_and_keeps_every_line(self):
        lines = [f"line-{i}" * 5 for i in range(50)]
        chunks = bf.pack_lines(lines, limit=100)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 100)
        self.assertEqual("\n".join(chunks).split("\n"), lines)

    def test_oversized_single_line_is_not_lost(self):
        chunks = bf.pack_lines(["x" * 500], limit=100)
        self.assertEqual(chunks, ["x" * 500])


if __name__ == "__main__":
    unittest.main(verbosity=2)
