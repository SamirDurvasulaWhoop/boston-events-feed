"""Tests for the Boston cheap-events digest.

The important one is test_parses_real_page_fixture: the fixture is a verbatim
excerpt of a live Boston Calendar post, `<strong>`-wrapped labels and all.
A hand-written "When: Tue. 9/1" fixture would be a shape the real site never
emits, and would happily pass while the scraper returned nothing.
"""

import sys
import unittest
from datetime import date, timedelta
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


class TestWorkflowPayload(unittest.TestCase):
    """Workflow Builder takes flat data variables, not Block Kit."""

    def events(self, count=3):
        return [
            bf.Event(
                number=i,
                title=f"Event {i}",
                url=f"/events/event-{i}",
                when_raw="Thu. 9/17",
                where="Somerville",
                cost="Free",
                info="x",
                dates=[date(2026, 9, 17)],
            )
            for i in range(count)
        ]

    def payload(self, count=3):
        return bf.build_workflow_payload(
            self.events(count), date(2026, 9, 17), "https://example.com/post", "Source"
        )

    def test_sends_exactly_the_two_declared_variables(self):
        payload = self.payload()
        self.assertEqual(sorted(payload), ["headline", "text"])
        for value in payload.values():
            self.assertIsInstance(value, str)

    def test_headline_is_separate_so_the_step_can_bold_it(self):
        payload = self.payload(5)
        self.assertIn("5 cheap things in Boston today — Thu 9/17", payload["headline"])
        self.assertNotIn(
            "cheap things", payload["text"], "headline must not be duplicated in body"
        )

    def test_body_holds_every_event_and_the_attribution(self):
        text = self.payload(5)["text"]
        for i in range(5):
            self.assertIn(f"Event {i}", text)
        self.assertIn("https://example.com/post", text)

    def test_carries_no_markup_because_workflow_builder_renders_it_literally(self):
        text = self.payload()["text"]
        self.assertNotIn("<http", text, "link syntax would show as punctuation")
        self.assertNotIn("|", text, "link syntax would show as punctuation")
        self.assertNotIn("*", text, "asterisks would show as asterisks")
        self.assertNotIn("&amp;", text, "escapes would show as themselves")

    def test_source_link_is_a_bare_auto_linkable_url(self):
        self.assertIn("Full list: https://example.com/post", self.payload()["text"])

    def test_ampersands_survive_unescaped(self):
        event = self.events(1)[0]
        event.where = "Downtown & Jamaica Plain"
        text = bf.build_workflow_payload(
            [event], date(2026, 9, 17), "https://example.com/post", "Source"
        )["text"]
        self.assertIn("Downtown & Jamaica Plain", text)

    def test_classic_webhook_is_not_workflow_mode(self):
        self.assertFalse(
            bf.is_workflow_webhook("https://hooks.slack.com/services/T00/B00/xyz")
        )

    def test_workflow_trigger_shapes_are_detected(self):
        for url in (
            "https://hooks.slack.com/triggers/E045/1208/510743dc",
            "https://hooks.slack.com/workflows/T00/A00/123/abc",
            "https://slack.com/shortcuts/Ft0C27GKF86T/13732ee7",
        ):
            with self.subTest(url):
                self.assertTrue(bf.is_workflow_webhook(url))

    def test_empty_webhook_is_not_workflow_mode(self):
        self.assertFalse(bf.is_workflow_webhook(""))

    def test_stays_under_slack_message_limit_when_overloaded(self):
        many = [
            bf.Event(
                number=i,
                title=f"A Really Quite Long Event Name Number {i} To Force Overflow",
                url=f"/events/a-really-quite-long-event-slug-number-{i}",
                when_raw="Sat. 9/12",
                where="Somerville",
                cost="Free",
                info="x",
                dates=[date(2026, 9, 12)],
            )
            for i in range(60)
        ]
        payload = bf.build_workflow_payload(
            many, date(2026, 9, 12), "https://example.com/post", "Source"
        )
        self.assertLessEqual(len(payload["text"]), bf.WORKFLOW_TEXT_LIMIT)
        self.assertIn("more", payload["text"], "should say how many were trimmed")

    def test_real_busiest_day_needs_no_trimming(self):
        """Sep 12 2026 (26 events) is the worst real day; it must fit whole."""
        many = [
            bf.Event(
                number=i,
                title=f"Event {i}",
                url=f"/events/event-{i}",
                when_raw="Sat. 9/12",
                where="Somerville",
                cost="Free",
                info="x",
                dates=[date(2026, 9, 12)],
            )
            for i in range(26)
        ]
        text = bf.build_workflow_payload(
            many, date(2026, 9, 12), "https://example.com/post", "Source"
        )["text"]
        self.assertNotIn("more_ —", text)
        for i in range(26):
            self.assertIn(f"Event {i}", text)

    def test_both_modes_list_the_same_events(self):
        events, today = self.events(4), date(2026, 9, 17)
        flat = bf.build_workflow_payload(events, today, "https://x", "S")["text"]
        blocks = bf.build_message(events, today, "https://x", "S")
        block_text = " ".join(
            b["text"]["text"] for b in blocks["blocks"] if b["type"] == "section"
        )
        for i in range(4):
            self.assertIn(f"Event {i}", flat)
            self.assertIn(f"Event {i}", block_text)


class TestSlackResponseHandling(unittest.TestCase):
    """Classic hooks reply 'ok'; workflow triggers reply JSON."""

    def test_accepts_both_success_shapes(self):
        for body in ("ok", "ok\n", '{"ok": true}', '{"ok":true,"trigger_id":"Ft123"}'):
            with self.subTest(body):
                self.assertTrue(bf.slack_response_ok(body))

    def test_rejects_failure_shapes(self):
        for body in (
            '{"ok": false, "error": "bad_payload"}',
            "invalid_payload",
            "",
            "null",
            "[]",
        ):
            with self.subTest(body):
                self.assertFalse(bf.slack_response_ok(body))


class TestReactionMarkers(unittest.TestCase):
    """Events are numbered so readers can react with the matching keycap."""

    def test_first_ten_get_keycaps(self):
        import slack_digest as sd

        self.assertEqual(sd.marker(0), "1️⃣")
        self.assertEqual(sd.marker(8), "9️⃣")
        self.assertEqual(sd.marker(9), "🔟")

    def test_past_ten_falls_back_to_a_bullet(self):
        import slack_digest as sd

        self.assertEqual(sd.marker(10), "•")
        self.assertEqual(sd.marker(25), "•")

    def test_hint_stays_short_to_protect_the_char_budget(self):
        import slack_digest as sd

        for count in (2, 5, 26):
            with self.subTest(count):
                self.assertIn("number", sd.react_hint(count))
                self.assertLessEqual(len(sd.react_hint(count)), 60)

    def test_hint_is_singular_for_one_event(self):
        import slack_digest as sd

        self.assertEqual(sd.react_hint(1), "React with 1️⃣ if you're going.")

    def test_daily_digest_numbers_its_events(self):
        events = [
            bf.Event(number=i, title=f"Event {i}", url=f"/e{i}", when_raw="",
                     where="Somerville", cost="Free", info="",
                     dates=[date(2026, 9, 12)])
            for i in range(12)
        ]
        lines = bf.digest_lines(events, date(2026, 9, 12), plain=True)
        self.assertTrue(lines[0].startswith("1️⃣"))
        self.assertTrue(lines[9].startswith("🔟"))
        self.assertTrue(lines[10].startswith("•"), "eleventh falls back")

    def test_weekly_digest_is_not_numbered(self):
        """Numbering across day groups would restart or mislead, so it is left off."""
        events = [
            bf.Event(number=i, title=f"Event {i}", url=f"/e{i}", when_raw="",
                     where="Somerville", cost="Free", info="",
                     dates=[date(2026, 9, 21)])
            for i in range(3)
        ]
        lines = bf.weekly_lines(events, date(2026, 9, 21), plain=True)
        self.assertNotIn("1️⃣", "\n".join(lines))

    def test_numbering_does_not_push_a_busy_day_over_the_limit(self):
        import slack_digest as sd

        events = [
            bf.Event(number=i, title=f"Event {i} with a realistic length title",
                     url=f"/events/event-number-{i}", when_raw="", where="Somerville",
                     cost="Free", info="", dates=[date(2026, 9, 12)])
            for i in range(26)
        ]
        payload = bf.build_workflow_payload(
            events, date(2026, 9, 12), "https://example.com/post", "Source"
        )
        self.assertLessEqual(
            len(payload["headline"]) + len(payload["text"]), sd.WORKFLOW_TEXT_LIMIT
        )


class TestWeeklyDigest(unittest.TestCase):
    MONDAY = date(2026, 9, 21)

    def ev(self, number, title, dates, end_date=None, where="Somerville"):
        return bf.Event(
            number=number, title=title, url=f"/events/e{number}", when_raw="",
            where=where, cost="Free", info="", dates=dates, end_date=end_date,
        )

    def test_week_days_is_seven_from_the_start(self):
        days = bf.week_days(self.MONDAY)
        self.assertEqual(len(days), 7)
        self.assertEqual(days[0], self.MONDAY)
        self.assertEqual(days[-1], date(2026, 9, 27))

    def test_headline_span_within_one_month(self):
        self.assertIn("Sep 21–27", bf.weekly_headline([], self.MONDAY))

    def test_headline_span_across_a_month_boundary(self):
        self.assertIn("Sep 28–Oct 4", bf.weekly_headline([], date(2026, 9, 28)))

    def test_headline_pluralisation(self):
        one = bf.weekly_headline([self.ev(1, "A", [self.MONDAY])], self.MONDAY)
        self.assertIn("1 cheap thing in Boston this week", one)
        two = bf.weekly_headline(
            [self.ev(1, "A", [self.MONDAY]), self.ev(2, "B", [self.MONDAY])], self.MONDAY
        )
        self.assertIn("2 cheap things in Boston this week", two)

    def test_multi_day_event_is_listed_once_under_its_first_day(self):
        run = self.ev(
            1, "Festival",
            [date(2026, 9, 25), date(2026, 9, 26), date(2026, 9, 27)],
            end_date=date(2026, 9, 27),
        )
        lines = bf.weekly_lines([run], self.MONDAY, plain=True)
        self.assertEqual(
            sum(1 for l in lines if "Festival" in l), 1, "\n".join(lines)
        )
        self.assertIn("Fri 9/25", lines)
        self.assertNotIn("Sat 9/26", lines)

    def test_multi_day_event_shows_its_end_date(self):
        run = self.ev(
            1, "Festival", [date(2026, 9, 25), date(2026, 9, 26)],
            end_date=date(2026, 9, 26),
        )
        text = "\n".join(bf.weekly_lines([run], self.MONDAY, plain=True))
        self.assertIn("through 9/26", text)

    def test_recurring_series_lists_its_other_dates(self):
        weekly = self.ev(1, "Matcha Meetup", [date(2026, 9, 23), date(2026, 9, 30)])
        text = "\n".join(bf.weekly_lines([weekly], self.MONDAY, plain=True))
        self.assertEqual(text.count("Matcha Meetup"), 1)
        self.assertNotIn("9/30", text, "a date outside the window is not 'also'")

    def test_event_starting_before_the_window_appears_on_its_first_day_inside(self):
        run = self.ev(
            1, "Long Run",
            [date(2026, 9, 19), date(2026, 9, 20), date(2026, 9, 21)],
            end_date=date(2026, 9, 21),
        )
        lines = bf.weekly_lines([run], self.MONDAY, plain=True)
        self.assertEqual(lines[0], "Mon 9/21")
        self.assertEqual(sum(1 for l in lines if "Long Run" in l), 1)

    def test_days_with_nothing_are_omitted(self):
        lines = bf.weekly_lines([self.ev(1, "A", [date(2026, 9, 23)])], self.MONDAY, plain=True)
        self.assertNotIn("Mon 9/21", lines)
        self.assertIn("Wed 9/23", lines)

    def test_plain_mode_has_no_markup_or_per_event_urls(self):
        """A week of URLs would overflow the message cap every single time."""
        events = [self.ev(i, f"Event {i}", [self.MONDAY]) for i in range(5)]
        text = "\n".join(bf.weekly_lines(events, self.MONDAY, plain=True))
        self.assertNotIn("http", text)
        self.assertNotIn("<", text)
        self.assertNotIn("*", text)

    def test_block_mode_keeps_per_event_links(self):
        events = [self.ev(i, f"Event {i}", [self.MONDAY]) for i in range(5)]
        text = "\n".join(bf.weekly_lines(events, self.MONDAY, plain=False))
        self.assertIn(f"<{bf.SITE}/events/e0|Event 0>", text)

    def test_a_full_real_week_fits_both_transports(self):
        import slack_digest as sd

        events = [
            self.ev(i, f"Event number {i} with a realistically long title", [
                self.MONDAY + timedelta(days=i % 7)
            ])
            for i in range(60)
        ]
        plain = bf.build_weekly(events, self.MONDAY, "https://x", "S", workflow_mode=True)
        self.assertLessEqual(
            len(plain["headline"]) + len(plain["text"]), sd.WORKFLOW_TEXT_LIMIT
        )
        blocks = bf.build_weekly(events, self.MONDAY, "https://x", "S", workflow_mode=False)
        self.assertLessEqual(len(blocks["blocks"]), 50)
        for block in blocks["blocks"]:
            if block["type"] == "section":
                self.assertLessEqual(len(block["text"]["text"]), 3000)


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
