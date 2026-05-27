"""Tests for the rule-based spending streak detector (§13)."""
from datetime import date, timedelta

import pytest

from services import streak_service
from tests.conftest import add_tx


def _recent(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


class TestThreshold:
    def test_below_3_occurrences_returns_nothing(self, db):
        # Two Friday lunches at 50 AED — under the 3-occurrence threshold.
        for d_ago in (7, 14):
            add_tx(db, date=_recent(d_ago), amount=5000, tx_type="expense", category_id=1)
        assert streak_service.detect_streaks(db) == []

    def test_exactly_3_occurrences_triggers(self, db):
        for d_ago in (7, 14, 21):
            add_tx(db, date=_recent(d_ago), amount=5000, tx_type="expense", category_id=1)
        streaks = streak_service.detect_streaks(db)
        assert any(s["occurrence_count"] >= 3 for s in streaks)

    def test_strictly_outside_60_days_excluded(self, db):
        # 60 days ago is borderline; 65 days ago is excluded.
        for d_ago in (7, 14, 65):
            add_tx(db, date=_recent(d_ago), amount=5000, tx_type="expense", category_id=1)
        # Only two within 60d → no streak.
        assert streak_service.detect_streaks(db) == []


class TestAmountTolerance:
    def test_within_20_percent_band_counts(self, db):
        # 3 weekday matches: 100, 110, 95 (median 100, all within ±20%)
        for d_ago, amt in ((7, 10000), (14, 11000), (21, 9500)):
            add_tx(db, date=_recent(d_ago), amount=amt, tx_type="expense", category_id=1)
        assert len(streak_service.detect_streaks(db)) >= 1

    def test_outside_20_percent_excluded(self, db):
        # 100, 100, 200 — 200 is 100% above median.
        for d_ago, amt in ((7, 10000), (14, 10000), (21, 20000)):
            add_tx(db, date=_recent(d_ago), amount=amt, tx_type="expense", category_id=1)
        assert streak_service.detect_streaks(db) == []


class TestCadence:
    def test_weekday_pattern_detected(self, db):
        # Same weekday: pick days a multiple of 7 apart.
        for d_ago in (7, 14, 21, 28):
            add_tx(db, date=_recent(d_ago), amount=5000, tx_type="expense", category_id=1)
        streaks = streak_service.detect_streaks(db)
        assert any(s["cadence_type"] == "weekday" for s in streaks)

    def test_day_of_month_pattern_detected_when_calendar_permits(self, db):
        """The DOM bucket exists to catch monthly bills. Because 3 same-DOM
        dates span ~60 days, whether they fit in the 60-day window depends on
        which calendar months are involved. This test only asserts the bucket
        works WHEN the dates happen to fit — calendar-aware so it's stable.
        """
        from calendar import monthrange
        today = date.today()

        # Build candidate same-DOM dates: this month, last month, month-before-last.
        # Skip if the oldest would fall outside 60 days.
        def shifted(d: date, months_back: int) -> date:
            m = d.month - months_back
            y = d.year
            while m <= 0:
                m += 12; y -= 1
            day = min(d.day, monthrange(y, m)[1])
            return date(y, m, day)

        anchor = today.replace(day=min(today.day, 28))
        oldest = shifted(anchor, 2)
        if (today - oldest).days > 60:
            pytest.skip("calendar window too tight for 3 same-DOM dates in 60d (today's date dependent)")

        dates = [shifted(anchor, k).isoformat() for k in (0, 1, 2)]
        for d in dates:
            add_tx(db, date=d, amount=5000, tx_type="expense", category_id=1)
        streaks = streak_service.detect_streaks(db)
        assert any(
            s["category_id"] == 1 and s["cadence_type"] == "dom" and s["cadence_value"] == anchor.day
            for s in streaks
        )


class TestCategoryIsolation:
    def test_different_categories_dont_merge(self, db):
        # 2 in cat 1, 2 in cat 2, same weekday — neither hits 3.
        for d_ago in (7, 14):
            add_tx(db, date=_recent(d_ago), amount=5000, tx_type="expense", category_id=1)
            add_tx(db, date=_recent(d_ago), amount=5000, tx_type="expense", category_id=2)
        assert streak_service.detect_streaks(db) == []


class TestDismissal:
    def test_dismissed_signature_hidden_until_2_more_occurrences(self, db):
        for d_ago in (7, 14, 21):
            add_tx(db, date=_recent(d_ago), amount=5000, tx_type="expense", category_id=1)
        first = streak_service.detect_streaks(db)
        assert first, "should detect 3-occurrence streak"
        sig = first[0]["signature"]

        # Dismiss at count=3.
        assert streak_service.dismiss(sig, db) is True
        # Same view: should now be hidden.
        assert all(s["signature"] != sig for s in streak_service.detect_streaks(db))

        # Add one more occurrence (now 4 total) — still hidden (need 3+2 = 5).
        add_tx(db, date=_recent(28), amount=5000, tx_type="expense", category_id=1)
        assert all(s["signature"] != sig for s in streak_service.detect_streaks(db))

        # Add another (5 total) — should reappear.
        add_tx(db, date=_recent(35), amount=5000, tx_type="expense", category_id=1)
        again = streak_service.detect_streaks(db)
        assert any(s["signature"] == sig for s in again)

    def test_dismiss_writes_audit_event(self, db):
        for d_ago in (7, 14, 21):
            add_tx(db, date=_recent(d_ago), amount=5000, tx_type="expense", category_id=1)
        sig = streak_service.detect_streaks(db)[0]["signature"]
        streak_service.dismiss(sig, db)
        row = db.execute(
            "SELECT event_type, description FROM audit_log WHERE event_type='streak_dismiss'"
        ).fetchone()
        assert row is not None
        assert sig in row["description"]

    def test_dismiss_unknown_signature_returns_false(self, db):
        assert streak_service.dismiss("cat=99|weekday=2", db) is False


class TestEnrichment:
    def test_enrichment_attaches_human_labels(self, db):
        for d_ago in (7, 14, 21):
            add_tx(db, date=_recent(d_ago), amount=5000, tx_type="expense", category_id=1)
        raw = streak_service.detect_streaks(db)
        enriched = streak_service.enrich_with_category_names(raw, db)
        assert all("category_name" in s and "cadence_label" in s and "message" in s for s in enriched)
        # category_id=1 is "Food & Dining" per seed data.
        assert enriched[0]["category_name"] == "Food & Dining"


class TestExcludedTypes:
    def test_non_budget_consuming_types_ignored(self, db):
        # transfers, income, etc. shouldn't trigger streaks.
        for d_ago in (7, 14, 21):
            add_tx(db, date=_recent(d_ago), amount=5000, tx_type="transfer_bank_to_petty",
                   source="bank", category_id=1)
        assert streak_service.detect_streaks(db) == []

    def test_recurring_counts_as_spend_for_streaks(self, db):
        """Recurring payments are on-budget spend, so they should count."""
        for d_ago in (7, 14, 21):
            add_tx(db, date=_recent(d_ago), amount=5000, tx_type="recurring", category_id=1)
        assert len(streak_service.detect_streaks(db)) >= 1
