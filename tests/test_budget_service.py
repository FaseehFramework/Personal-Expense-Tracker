"""
Unit tests for the cascade / rollover / savings logic in §6.3, §6.4, §21.

These are the highest-risk areas for off-by-one bugs and month-boundary drift,
so each invariant gets its own test. All money is in integer fils (AED * 100).
"""
import pytest

from services import budget_service as bs
from tests.conftest import set_budget, add_tx, write_history


# ===== rollover (positive remaining) — §6.3 =====

class TestPositiveRollover:
    """When the month ends with money left over: 10% rolls forward, 90% saves."""

    def test_basic_split_10_90(self, db):
        # 2500 budget, 1500 spent → 1000 net remaining
        set_budget(db, "2025-01", 250000)
        add_tx(db, date="2025-01-15", amount=150000, tx_type="expense")
        result = bs.close_month("2025-01", db)
        assert result["budget"] == 250000
        assert result["spent"] == 150000
        assert result["rollover"] == 10000   # 10% of 100000 fils
        assert result["savings"] == 90000    # 90% of 100000 fils
        assert result["cascade"] == 0

    def test_rollover_seeds_next_month_budget(self, db):
        """Closing Jan with rollover should ADD the rollover to Feb's budget.
        If Feb has no row yet, seed it with just the rollover (admin will set
        the real Feb budget separately, and the credit stacks)."""
        set_budget(db, "2025-01", 250000)
        add_tx(db, date="2025-01-10", amount=100000, tx_type="expense")  # 1000 spent → 1500 net
        bs.close_month("2025-01", db)
        feb = bs.get_monthly_budget("2025-02", db)
        # net 1500 → rollover 150 → Feb seeded at just 150
        assert feb == 15000

    def test_rollover_adds_to_existing_next_month_budget(self, db):
        """If Feb was already set to 3000, rollover should ADD to it (not replace)."""
        set_budget(db, "2025-01", 250000)
        set_budget(db, "2025-02", 300000)
        add_tx(db, date="2025-01-10", amount=200000, tx_type="expense")  # 500 net → 50 rollover
        bs.close_month("2025-01", db)
        feb = bs.get_monthly_budget("2025-02", db)
        assert feb == 300000 + 5000

    def test_savings_pot_credited(self, db):
        """90% of unspent should land in savings pot."""
        set_budget(db, "2025-01", 250000)
        add_tx(db, date="2025-01-10", amount=50000, tx_type="expense")  # 500 spent → 2000 net
        bs.close_month("2025-01", db)
        assert bs.savings_balance(db) == 180000  # 90% of 2000 AED = 1800 AED

    def test_savings_event_logged(self, db):
        set_budget(db, "2025-01", 100000)
        add_tx(db, date="2025-01-10", amount=50000, tx_type="expense")  # 500 net → 450 savings
        bs.close_month("2025-01", db)
        evts = db.execute("SELECT event_type, amount FROM savings_events ORDER BY id").fetchall()
        assert len(evts) == 1
        assert evts[0]["event_type"] == "rollover_credit"
        assert evts[0]["amount"] == 45000

    def test_audit_log_records_close(self, db):
        set_budget(db, "2025-01", 100000)
        bs.close_month("2025-01", db)
        row = db.execute("SELECT event_type, description FROM audit_log "
                         "WHERE event_type = 'month_close'").fetchone()
        assert row is not None
        assert "2025-01" in row["description"]


# ===== zero / boundary =====

class TestBoundary:
    def test_exactly_zero_remaining_no_rollover_no_cascade(self, db):
        set_budget(db, "2025-01", 100000)
        add_tx(db, date="2025-01-10", amount=100000, tx_type="expense")
        r = bs.close_month("2025-01", db)
        assert r == {"month": "2025-01", "budget": 100000, "spent": 100000,
                     "rollover": 0, "savings": 0, "cascade": 0}
        assert bs.savings_balance(db) == 0
        # Feb shouldn't have been seeded with a rollover.
        assert bs.get_monthly_budget("2025-02", db) == 0

    def test_one_fil_remaining_rounds_to_zero_rollover_one_fil_savings(self, db):
        """Integer floor: 1 // 10 = 0 rollover, 1 - 0 = 1 to savings."""
        set_budget(db, "2025-01", 100000)
        add_tx(db, date="2025-01-10", amount=99999, tx_type="expense")
        r = bs.close_month("2025-01", db)
        assert r["rollover"] == 0
        assert r["savings"] == 1
        assert bs.savings_balance(db) == 1

    def test_zero_budget_zero_spend_closes_cleanly(self, db):
        r = bs.close_month("2025-01", db)
        assert r["budget"] == 0 and r["spent"] == 0
        assert r["rollover"] == 0 and r["savings"] == 0 and r["cascade"] == 0


# ===== cascade (negative remaining) — §6.4 =====

class TestNegativeCascade:
    def test_overspend_writes_cascade(self, db):
        # 2500 budget, 3000 spent → -500 → cascade 500
        set_budget(db, "2025-01", 250000)
        add_tx(db, date="2025-01-10", amount=300000, tx_type="expense")
        r = bs.close_month("2025-01", db)
        assert r["rollover"] == 0
        assert r["savings"] == 0
        assert r["cascade"] == 50000
        # And no savings credit:
        assert bs.savings_balance(db) == 0

    def test_cascade_deducts_from_next_month_remaining(self, db):
        """Per §6.4: cascade is deducted from next month's effective budget."""
        # Close Jan with -300 cascade
        set_budget(db, "2025-01", 100000)
        add_tx(db, date="2025-01-15", amount=130000, tx_type="expense")
        bs.close_month("2025-01", db)
        # Set Feb budget 1000, no spend. Effective remaining should be 1000 - 300 = 700.
        set_budget(db, "2025-02", 100000)
        assert bs.cascade_into("2025-02", db) == 30000
        assert bs.remaining_budget("2025-02", db) == 100000 - 30000

    def test_no_cascade_when_close_was_positive(self, db):
        set_budget(db, "2025-01", 100000)
        add_tx(db, date="2025-01-10", amount=50000, tx_type="expense")
        bs.close_month("2025-01", db)
        assert bs.cascade_into("2025-02", db) == 0

    def test_no_cascade_when_close_was_exactly_zero(self, db):
        set_budget(db, "2025-01", 100000)
        add_tx(db, date="2025-01-10", amount=100000, tx_type="expense")
        bs.close_month("2025-01", db)
        assert bs.cascade_into("2025-02", db) == 0

    def test_negative_month_does_not_seed_next_month_budget(self, db):
        """A negative close shouldn't try to add anything to Feb's budget."""
        set_budget(db, "2025-01", 100000)
        add_tx(db, date="2025-01-10", amount=200000, tx_type="expense")
        bs.close_month("2025-01", db)
        assert bs.get_monthly_budget("2025-02", db) == 0


# ===== multi-month cascade chain — §6.4 "cascades every month until cleared" =====

class TestCascadeChain:
    def test_cascade_compounds_when_consecutive_months_negative(self, db):
        # Jan: budget 1000, spend 1300 → cascade 300 into Feb
        set_budget(db, "2025-01", 100000)
        add_tx(db, date="2025-01-15", amount=130000, tx_type="expense")
        bs.close_month("2025-01", db)

        # Feb: budget 1000, spend 1200 → net after cascade = 1000 - 1200 - 300 = -500 → cascade 500 into Mar
        set_budget(db, "2025-02", 100000)
        add_tx(db, date="2025-02-15", amount=120000, tx_type="expense")
        r = bs.close_month("2025-02", db)
        assert r["cascade"] == 50000
        assert bs.cascade_into("2025-03", db) == 50000

    def test_cascade_clears_when_month_recovers(self, db):
        # Jan: -300 cascade into Feb
        set_budget(db, "2025-01", 100000)
        add_tx(db, date="2025-01-15", amount=130000, tx_type="expense")
        bs.close_month("2025-01", db)

        # Feb: budget 1000, spend 200 → net = 1000 - 200 - 300 = 500 → 50/450 split, no cascade
        set_budget(db, "2025-02", 100000)
        add_tx(db, date="2025-02-15", amount=20000, tx_type="expense")
        r = bs.close_month("2025-02", db)
        assert r["cascade"] == 0
        assert r["rollover"] == 5000
        assert r["savings"] == 45000
        # And no cascade should propagate to March.
        assert bs.cascade_into("2025-03", db) == 0

    def test_cascade_only_looks_at_immediately_prior_closed_month(self, db):
        """cascade_into should reference the most recent prior budget_history row."""
        # Old cascade from Nov 2024
        write_history(db, "2024-11", budget=100000, spent=130000, negative_cascade=30000)
        # Clean December (no spend, but closed positive)
        write_history(db, "2024-12", budget=100000, spent=80000, rollover=2000, savings=18000, negative_cascade=0)
        # January 2025 should see December's 0 cascade, not Nov's 300.
        assert bs.cascade_into("2025-01", db) == 0


# ===== budget income (petty_to_bank) — §5.1 marks this as budget income =====

class TestBudgetIncome:
    def test_petty_to_bank_increases_net_for_close(self, db):
        # 2500 budget + 200 petty_to_bank + 0 spend → net 2700 → 270/2430 split
        set_budget(db, "2025-01", 250000)
        add_tx(db, date="2025-01-10", amount=20000, tx_type="petty_to_bank", source="petty")
        r = bs.close_month("2025-01", db)
        assert r["rollover"] == 27000
        assert r["savings"] == 243000

    def test_petty_to_bank_can_pull_negative_month_to_positive(self, db):
        # 1000 budget, 1100 spend = -100, but 200 deposit → +100 net
        set_budget(db, "2025-01", 100000)
        add_tx(db, date="2025-01-15", amount=110000, tx_type="expense")
        add_tx(db, date="2025-01-20", amount=20000, tx_type="petty_to_bank", source="petty")
        r = bs.close_month("2025-01", db)
        assert r["cascade"] == 0
        assert r["rollover"] + r["savings"] == 10000


# ===== idempotency =====

class TestIdempotency:
    def test_close_month_runs_only_once(self, db):
        set_budget(db, "2025-01", 100000)
        add_tx(db, date="2025-01-10", amount=50000, tx_type="expense")
        first = bs.close_month("2025-01", db)
        assert first.get("already_closed") is None

        second = bs.close_month("2025-01", db)
        assert second == {"already_closed": True, "month": "2025-01"}

        # No double-credit to savings pot.
        assert bs.savings_balance(db) == 45000
        # Only one budget_history row.
        n = db.execute("SELECT COUNT(*) AS c FROM budget_history WHERE month = ?", ("2025-01",)).fetchone()["c"]
        assert n == 1
        # Next-month seed wasn't applied twice (just the 5000 rollover).
        assert bs.get_monthly_budget("2025-02", db) == 5000


# ===== savings pot accumulation =====

class TestSavingsAccumulation:
    def test_savings_pot_grows_across_months(self, db):
        # Close two consecutive positive months.
        # Jan: 1000 budget - 500 spend = 500 net → 50 rollover, 450 savings.
        # Jan's close auto-seeds Feb with the rollover only (50).
        set_budget(db, "2025-01", 100000)
        add_tx(db, date="2025-01-10", amount=50000, tx_type="expense")
        bs.close_month("2025-01", db)
        assert bs.get_monthly_budget("2025-02", db) == 5000

        # Admin sets Feb's actual budget; the 50 rollover stacks → 1050.
        db.execute("UPDATE monthly_budgets SET amount = amount + 100000 WHERE month = '2025-02'")
        assert bs.get_monthly_budget("2025-02", db) == 105000

        # Feb: 1050 budget - 300 spend = 750 net → 75 rollover, 675 savings.
        add_tx(db, date="2025-02-10", amount=30000, tx_type="expense")
        bs.close_month("2025-02", db)

        assert bs.savings_balance(db) == 45000 + 67500

    def test_savings_events_separate_per_month(self, db):
        set_budget(db, "2025-01", 100000)
        bs.close_month("2025-01", db)
        set_budget(db, "2025-02", 100000)
        bs.close_month("2025-02", db)
        evts = db.execute("SELECT description FROM savings_events ORDER BY id").fetchall()
        # Both months had 0 spend → 100k net → 10k/90k split — should have 2 credit events.
        assert len(evts) == 2
        assert "2025-01" in evts[0]["description"]
        assert "2025-02" in evts[1]["description"]


# ===== year-boundary key arithmetic =====

class TestMonthKeys:
    def test_close_dec_rolls_into_jan_next_year(self, db):
        set_budget(db, "2025-12", 100000)
        add_tx(db, date="2025-12-15", amount=50000, tx_type="expense")  # 500 net → 50 rollover
        bs.close_month("2025-12", db)
        jan = bs.get_monthly_budget("2026-01", db)
        # No prior Jan budget → seeded with just the rollover.
        assert jan == 5000


# ===== savings direct manipulation (for wishlist Phase 3) =====

class TestSavingsHelpers:
    def test_credit_savings_writes_event_and_increments(self, db):
        bs.credit_savings(50000, "test credit", db=db)
        assert bs.savings_balance(db) == 50000

    def test_credit_savings_ignores_non_positive(self, db):
        bs.credit_savings(0, "noop", db=db)
        bs.credit_savings(-100, "noop", db=db)
        assert bs.savings_balance(db) == 0
        n = db.execute("SELECT COUNT(*) AS c FROM savings_events").fetchone()["c"]
        assert n == 0

    def test_debit_savings_caps_at_balance(self, db):
        bs.credit_savings(10000, "seed", db=db)
        drawn = bs.debit_savings(15000, "wishlist purchase", db=db)
        assert drawn == 10000
        assert bs.savings_balance(db) == 0

    def test_debit_savings_partial(self, db):
        bs.credit_savings(10000, "seed", db=db)
        drawn = bs.debit_savings(3000, "wishlist purchase", db=db)
        assert drawn == 3000
        assert bs.savings_balance(db) == 7000

    def test_debit_savings_writes_negative_event(self, db):
        bs.credit_savings(5000, "seed", db=db)
        bs.debit_savings(2000, "wishlist", db=db)
        evts = db.execute("SELECT amount FROM savings_events ORDER BY id").fetchall()
        assert evts[0]["amount"] == 5000
        assert evts[1]["amount"] == -2000


# ===== remaining_budget interaction with cascade =====

class TestRemainingWithCascade:
    def test_remaining_subtracts_prior_cascade(self, db):
        write_history(db, "2025-01", budget=100000, spent=130000, negative_cascade=30000)
        set_budget(db, "2025-02", 100000)
        add_tx(db, date="2025-02-10", amount=20000, tx_type="expense")
        # 1000 - 200 spend - 300 cascade = 500 remaining
        assert bs.remaining_budget("2025-02", db) == 50000

    def test_remaining_includes_budget_income(self, db):
        set_budget(db, "2025-02", 100000)
        add_tx(db, date="2025-02-10", amount=20000, tx_type="expense")
        add_tx(db, date="2025-02-11", amount=5000, tx_type="petty_to_bank", source="petty")
        # 1000 - 200 + 50 = 850
        assert bs.remaining_budget("2025-02", db) == 85000

    def test_remaining_ignores_deleted_transactions(self, db):
        set_budget(db, "2025-02", 100000)
        tx = add_tx(db, date="2025-02-10", amount=20000, tx_type="expense")
        db.execute("UPDATE transactions SET is_deleted = 1, deleted_at = datetime('now') WHERE id = ?", (tx,))
        assert bs.remaining_budget("2025-02", db) == 100000

    def test_remaining_zero_when_no_budget_set(self, db):
        """remaining_budget short-circuits to 0 if no monthly_budgets row exists."""
        add_tx(db, date="2025-02-10", amount=20000, tx_type="expense")
        assert bs.remaining_budget("2025-02", db) == 0
