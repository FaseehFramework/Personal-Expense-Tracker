"""
Phase 3 unit tests — receivable retro-cascade, loan repayment direction math,
wishlist savings-pot reconciliation.

These cover the trickiest interactions because they reach into Phase 2's
close_month, savings pot, and budget machinery.
"""
import pytest

from services import budget_service as bs
from tests.conftest import set_budget, add_tx, write_history


# ===================================================================
#                       RECEIVABLES — §7
# ===================================================================

class TestReceivableReopenReclose:
    """The reopen/reapply mechanics, which back convert-to-expense."""

    def test_reopen_reverses_savings_and_rollover(self, db):
        # Jan: budget 1000, spend 500 → 50 rollover, 450 savings.
        set_budget(db, "2025-01", 100000)
        add_tx(db, date="2025-01-10", amount=50000, tx_type="expense")
        bs.close_month("2025-01", db)
        assert bs.savings_balance(db) == 45000
        assert bs.get_monthly_budget("2025-02", db) == 5000  # seeded with rollover only

        # Reopen should undo both effects.
        opened = bs.reopen_closed_month("2025-01", db)
        assert opened is True
        assert bs.savings_balance(db) == 0
        # Feb's seeded 5000 should now be 0.
        assert bs.get_monthly_budget("2025-02", db) == 0
        row = db.execute("SELECT 1 FROM budget_history WHERE month = ?", ("2025-01",)).fetchone()
        assert row is None

    def test_reopen_unclosed_month_is_noop(self, db):
        assert bs.reopen_closed_month("2025-01", db) is False

    def test_reopen_negative_month_only_drops_history(self, db):
        # Negative month had no savings credit and no rollover seeding.
        set_budget(db, "2025-01", 100000)
        add_tx(db, date="2025-01-10", amount=150000, tx_type="expense")
        bs.close_month("2025-01", db)
        assert bs.savings_balance(db) == 0
        assert bs.get_monthly_budget("2025-02", db) == 0
        # No seed by Jan (it was negative), so we should have just the history row.
        bs.reopen_closed_month("2025-01", db)
        # Nothing to reverse, just history gone.
        row = db.execute("SELECT 1 FROM budget_history WHERE month = ?", ("2025-01",)).fetchone()
        assert row is None
        assert bs.savings_balance(db) == 0

    def test_reapply_replays_chain_of_closes(self, db):
        # Two consecutive positive closes.
        # Jan: 1000 budget - 500 spend = 500 net → 50 rollover, 450 savings.
        set_budget(db, "2025-01", 100000)
        add_tx(db, date="2025-01-10", amount=50000, tx_type="expense")
        bs.close_month("2025-01", db)
        # Admin sets Feb's actual budget; rollover stacks on top → 1050.
        db.execute("UPDATE monthly_budgets SET amount = amount + 100000 WHERE month = '2025-02'")
        # Feb: 1050 budget - 300 spend = 750 net → 75 rollover, 675 savings.
        add_tx(db, date="2025-02-10", amount=30000, tx_type="expense")
        bs.close_month("2025-02", db)

        # Now add a retroactive expense in Jan and replay.
        add_tx(db, date="2025-01-15", amount=20000, tx_type="expense")
        bs.reapply_closed_months_from("2025-01", db)

        # Jan: budget 1000, spend 500+200=700 → 300 net → 30 rollover, 270 savings.
        jan = db.execute("SELECT * FROM budget_history WHERE month = ?", ("2025-01",)).fetchone()
        assert jan["actual_spend"] == 70000
        assert jan["rollover_amount"] == 3000
        assert jan["savings_amount"] == 27000

        # Feb: when reopening, we subtracted Jan's old 5000 rollover off Feb's
        # budget (1050 → 1000) and Feb's saved 67500 from the pot. Then we
        # re-closed Jan (added new 3000 rollover to Feb → 1030) and re-closed
        # Feb (1030 - 300 = 730 net → 7300 rollover, 65700 savings).
        feb = db.execute("SELECT * FROM budget_history WHERE month = ?", ("2025-02",)).fetchone()
        assert feb["budget_set"] == 103000
        assert feb["rollover_amount"] == 7300
        assert feb["savings_amount"] == 65700
        # Pot = Jan 27000 + Feb 65700 = 92700.
        assert bs.savings_balance(db) == 27000 + 65700


class TestReceivableConvertToExpense:
    """Section 7.4 — convert receivable to expense, retroactively in original month."""

    def _add_receivable(self, db, month, amount_fils):
        cur = db.execute(
            "INSERT INTO receivables (description, amount, date_logged, month) VALUES (?, ?, ?, ?)",
            (f"Reimburse {month}", amount_fils, f"{month}-10", month),
        )
        return cur.lastrowid

    def test_convert_in_open_month_just_books_expense(self, db):
        """If the month isn't closed yet, conversion just inserts the expense."""
        set_budget(db, "2025-03", 100000)
        rid = self._add_receivable(db, "2025-03", 30000)
        # Simulate the conversion: insert expense + status update (the route does both).
        db.execute(
            "INSERT INTO transactions (date, amount, type, source, description, linked_type, linked_id) "
            "VALUES (?, ?, 'expense', 'bank', ?, 'receivable', ?)",
            ("2025-03-10", 30000, "converted", rid),
        )
        db.execute("UPDATE receivables SET status = 'converted' WHERE id = ?", (rid,))
        # Remaining drops by 300.
        assert bs.remaining_budget("2025-03", db) == 70000

    def test_convert_in_closed_month_replays_cascade(self, db):
        """If the receivable's month was already closed, we must replay."""
        # Mar: budget 1000, spend 200, close → 80 rollover, 720 savings.
        set_budget(db, "2025-03", 100000)
        add_tx(db, date="2025-03-15", amount=20000, tx_type="expense")
        bs.close_month("2025-03", db)
        assert bs.savings_balance(db) == 72000
        # Apr seeded with the rollover only (8000); no prior Apr budget.
        assert bs.get_monthly_budget("2025-04", db) == 8000

        # Add a receivable that we then convert.
        rid = self._add_receivable(db, "2025-03", 30000)
        # Mirror the route logic: insert expense, update status, replay.
        db.execute(
            "INSERT INTO transactions (date, amount, type, source, description, linked_type, linked_id) "
            "VALUES ('2025-03-10', 30000, 'expense', 'bank', 'Converted', 'receivable', ?)",
            (rid,),
        )
        db.execute("UPDATE receivables SET status = 'converted' WHERE id = ?", (rid,))
        bs.reapply_closed_months_from("2025-03", db)

        # Mar: 1000 budget, spend 200+300=500 → 500 net → 50/450
        row = db.execute("SELECT * FROM budget_history WHERE month = ?", ("2025-03",)).fetchone()
        assert row["actual_spend"] == 50000
        assert row["rollover_amount"] == 5000
        assert row["savings_amount"] == 45000
        # Savings pot reset to 45000 (only Mar; nothing else closed).
        assert bs.savings_balance(db) == 45000
        # After replay: Apr was reset to 0 then re-seeded with the new 5000 rollover.
        assert bs.get_monthly_budget("2025-04", db) == 5000

    def test_retro_convert_logs_rich_audit_when_pot_goes_negative(self, db):
        """When a retro-convert pushes the savings pot below zero, an audit
        entry must record the receivable id, old + new pot balances, and the
        wishlist draws that contributed to the over-draw."""
        # March: 1000 budget, no spend → 1000 net → 100 rollover, 900 savings.
        set_budget(db, "2025-03", 100000)
        bs.close_month("2025-03", db)
        assert bs.savings_balance(db) == 90000

        # A wishlist purchase in April drains 800 of the pot.
        db.execute(
            "INSERT INTO wishlist (item_name, estimated_amount, target_month, status, "
            "priority_order, savings_drawn, transaction_id) "
            "VALUES ('Camera', 80000, '2025-04', 'purchased', 1, 80000, NULL)"
        )
        wid = db.execute("SELECT id FROM wishlist WHERE item_name='Camera'").fetchone()["id"]
        # Insert a linking expense transaction dated in April so the audit
        # query has something to match against.
        tx_cur = db.execute(
            "INSERT INTO transactions (date, amount, type, source, description, linked_type, linked_id) "
            "VALUES ('2025-04-10', 80000, 'expense', 'bank', 'Camera wishlist', 'wishlist', ?)",
            (wid,),
        )
        db.execute("UPDATE wishlist SET transaction_id = ? WHERE id = ?", (tx_cur.lastrowid, wid))
        bs.debit_savings(80000, "Camera", db=db)
        assert bs.savings_balance(db) == 10000

        # Now add and "convert" a receivable belonging to March. This will be
        # 600 expense in March → new net 400 → 40/360 savings, down from 900.
        cur = db.execute(
            "INSERT INTO receivables (description, amount, date_logged, month) "
            "VALUES ('March bad reimb', 60000, '2025-03-15', '2025-03')"
        )
        rid = cur.lastrowid
        # Mirror the route: insert expense, mark converted, replay.
        db.execute(
            "INSERT INTO transactions (date, amount, type, source, description, linked_type, linked_id) "
            "VALUES ('2025-03-15', 60000, 'expense', 'bank', 'Converted', 'receivable', ?)",
            (rid,),
        )
        db.execute("UPDATE receivables SET status='converted' WHERE id = ?", (rid,))

        # Snapshot + replay (this is what the route does).
        old_pot = bs.savings_balance(db)
        bs.reapply_closed_months_from("2025-03", db)
        new_pot = bs.savings_balance(db)

        # Pot dynamics: was 10000 (after Camera draw); reopen reverses 90000 credit
        # → -80000; re-close credits new 36000 → -44000.
        assert old_pot == 10000
        assert new_pot == -44000

        # Mirror the route's negative-pot audit helper.
        from routes.receivables import _log_negative_pot
        r_row = db.execute("SELECT * FROM receivables WHERE id = ?", (rid,)).fetchone()
        _log_negative_pot(db, rid, r_row, old_pot, new_pot, ["2025-03"])

        entry = db.execute(
            "SELECT description, related_id, related_type FROM audit_log "
            "WHERE event_type = 'savings_pot_negative' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert entry is not None
        assert entry["related_type"] == "receivable"
        assert entry["related_id"] == rid
        desc = entry["description"]
        # Must mention the triggering receivable id, both balances, and the wishlist draw.
        assert f"Receivable #{rid}" in desc
        assert "100.00" in desc           # old pot
        assert "-440.00" in desc          # new pot
        assert "wishlist #" in desc
        assert "Camera" in desc
        assert "800.00" in desc           # the draw amount

    def test_retro_convert_no_audit_when_pot_stays_nonnegative(self, db):
        """No 'savings_pot_negative' event should be written if the pot
        stays non-negative after a retro-convert."""
        set_budget(db, "2025-03", 100000)
        bs.close_month("2025-03", db)
        # No wishlist draw, so re-close just changes the credit.
        cur = db.execute(
            "INSERT INTO receivables (description, amount, date_logged, month) "
            "VALUES ('small', 20000, '2025-03-15', '2025-03')"
        )
        rid = cur.lastrowid
        db.execute(
            "INSERT INTO transactions (date, amount, type, source, description, linked_type, linked_id) "
            "VALUES ('2025-03-15', 20000, 'expense', 'bank', 'Converted', 'receivable', ?)",
            (rid,),
        )
        old_pot = bs.savings_balance(db)
        bs.reapply_closed_months_from("2025-03", db)
        new_pot = bs.savings_balance(db)
        # Pot just shrunk from 90000 to (1000-200)*0.9 = 72000 — still positive.
        assert new_pot == 72000
        # No negative-pot audit entry.
        n = db.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE event_type = 'savings_pot_negative'"
        ).fetchone()["c"]
        assert n == 0

    def test_convert_can_drag_month_into_cascade(self, db):
        """Big enough conversion can flip a positive closed month negative."""
        set_budget(db, "2025-03", 100000)
        add_tx(db, date="2025-03-15", amount=20000, tx_type="expense")
        bs.close_month("2025-03", db)  # 80 net, all positive

        rid = self._add_receivable(db, "2025-03", 90000)  # huge — will overshoot
        db.execute(
            "INSERT INTO transactions (date, amount, type, source, description, linked_type, linked_id) "
            "VALUES ('2025-03-10', 90000, 'expense', 'bank', 'Converted', 'receivable', ?)",
            (rid,),
        )
        bs.reapply_closed_months_from("2025-03", db)

        # Spend now 200+900 = 1100 → net -100 → cascade 100, no savings, no rollover.
        row = db.execute("SELECT * FROM budget_history WHERE month = ?", ("2025-03",)).fetchone()
        assert row["negative_cascade"] == 10000
        assert row["rollover_amount"] == 0
        assert row["savings_amount"] == 0
        assert bs.savings_balance(db) == 0
        # April: no longer has the rollover bump.
        assert bs.get_monthly_budget("2025-04", db) == 0
        # And the cascade propagates into April's remaining-budget calc.
        set_budget(db, "2025-04", 100000)
        assert bs.remaining_budget("2025-04", db) == 100000 - 10000


# ===================================================================
#                          LOANS — §8
# ===================================================================

class TestLoanDirections:
    """Money math for the two loan directions. The route-level wallet effects
    are exercised through the tx_effects table; here we just confirm a payment
    is correctly classified as on-budget vs off-budget."""

    def test_owed_repayment_is_off_budget(self, db):
        # Someone owes me 1000. They pay back 300. That 300 should NOT eat budget.
        set_budget(db, "2025-04", 100000)
        # The repayment_received tx represents the inflow.
        add_tx(db, date="2025-04-10", amount=30000, tx_type="loan_repay_received", source="bank")
        assert bs.remaining_budget("2025-04", db) == 100000  # unchanged

    def test_owe_repayment_is_on_budget(self, db):
        # I owe someone 1000. I pay back 300. That 300 SHOULD eat budget.
        set_budget(db, "2025-04", 100000)
        add_tx(db, date="2025-04-10", amount=30000, tx_type="loan_repay_owed", source="bank")
        assert bs.remaining_budget("2025-04", db) == 70000

    def test_lending_out_is_off_budget(self, db):
        # I lent 500. My budget isn't affected.
        set_budget(db, "2025-04", 100000)
        add_tx(db, date="2025-04-10", amount=50000, tx_type="loan_lend", source="bank")
        assert bs.remaining_budget("2025-04", db) == 100000
        # But my bank balance is.
        assert bs.bank_balance(db) == -50000


# ===================================================================
#                         WISHLIST — §9
# ===================================================================

class TestWishlistReconciliation:
    """The savings-pot-vs-budget interplay at purchase time."""

    def _add(self, db, name, amount, target, priority=None):
        if priority is None:
            row = db.execute("SELECT COALESCE(MAX(priority_order), 0) AS m FROM wishlist").fetchone()
            priority = int(row["m"] or 0) + 1
        cur = db.execute(
            "INSERT INTO wishlist (item_name, estimated_amount, target_month, priority_order) "
            "VALUES (?, ?, ?, ?)",
            (name, amount, target, priority),
        )
        return cur.lastrowid

    def test_pot_covers_full_amount_budget_untouched(self, db):
        bs.credit_savings(50000, "seed", db=db)
        set_budget(db, "2025-06", 100000)
        wid = self._add(db, "Headphones", 30000, "2025-06")

        # Mirror the route's purchase logic.
        drawn = bs.debit_savings(30000, "test purchase", db=db)
        assert drawn == 30000
        assert bs.savings_balance(db) == 20000
        # Budget untouched.
        assert bs.get_monthly_budget("2025-06", db) == 100000

    def test_partial_pot_creates_shortfall_against_target_month_budget(self, db):
        bs.credit_savings(10000, "seed", db=db)
        set_budget(db, "2025-06", 100000)
        wid = self._add(db, "Laptop", 25000, "2025-06")

        # Pot covers 100, shortfall 150 → budget for 2025-06 should drop by 150.
        drawn = bs.debit_savings(10000, "wishlist", db=db)
        shortfall = 25000 - drawn
        db.execute(
            "UPDATE monthly_budgets SET amount = amount - ? WHERE month = ?",
            (shortfall, "2025-06"),
        )
        assert bs.savings_balance(db) == 0
        assert bs.get_monthly_budget("2025-06", db) == 100000 - 15000

    def test_priority_reserves_pot_for_earlier_items(self, db):
        """Two active items: earlier one must reserve from pot before the later one."""
        bs.credit_savings(50000, "seed", db=db)
        first = self._add(db, "First", 40000, "2025-06", priority=1)
        second = self._add(db, "Second", 20000, "2025-06", priority=2)

        # Available for `second` = 50000 - 40000 = 10000. So second has shortfall 100.
        # We model `_projected_savings_coverage` manually:
        from routes.wishlist import _projected_savings_coverage
        row = db.execute("SELECT * FROM wishlist WHERE id = ?", (second,)).fetchone()
        earlier, cover, shortfall = _projected_savings_coverage(db, row)
        assert earlier == 40000
        assert cover == 10000
        assert shortfall == 10000

    def test_priority_first_item_sees_full_pot(self, db):
        bs.credit_savings(50000, "seed", db=db)
        first = self._add(db, "First", 40000, "2025-06", priority=1)
        from routes.wishlist import _projected_savings_coverage
        row = db.execute("SELECT * FROM wishlist WHERE id = ?", (first,)).fetchone()
        earlier, cover, shortfall = _projected_savings_coverage(db, row)
        assert earlier == 0
        assert cover == 40000
        assert shortfall == 0

    def test_abandon_does_not_touch_pot(self, db):
        """§9.4 — abandoning an active item never debited the pot, so leaves it alone."""
        bs.credit_savings(50000, "seed", db=db)
        wid = self._add(db, "Maybe", 30000, "2025-06")
        db.execute("UPDATE wishlist SET status = 'abandoned' WHERE id = ?", (wid,))
        assert bs.savings_balance(db) == 50000


class TestWishlistEdgeCases:
    def test_empty_pot_shortfall_equals_full_amount(self, db):
        cur = db.execute(
            "INSERT INTO wishlist (item_name, estimated_amount, target_month, priority_order) "
            "VALUES (?, ?, ?, 1)",
            ("Big", 50000, "2025-06"),
        )
        wid = cur.lastrowid
        from routes.wishlist import _projected_savings_coverage
        row = db.execute("SELECT * FROM wishlist WHERE id = ?", (wid,)).fetchone()
        earlier, cover, shortfall = _projected_savings_coverage(db, row)
        assert earlier == 0
        assert cover == 0
        assert shortfall == 50000

    def test_pot_exceeds_all_items_no_shortfall(self, db):
        bs.credit_savings(200000, "fat pot", db=db)
        a = db.execute("INSERT INTO wishlist (item_name, estimated_amount, target_month, priority_order) "
                       "VALUES ('a', 30000, '2025-06', 1)", ()).lastrowid
        b = db.execute("INSERT INTO wishlist (item_name, estimated_amount, target_month, priority_order) "
                       "VALUES ('b', 40000, '2025-06', 2)", ()).lastrowid
        from routes.wishlist import _projected_savings_coverage
        for wid, expected_earlier in [(a, 0), (b, 30000)]:
            row = db.execute("SELECT * FROM wishlist WHERE id = ?", (wid,)).fetchone()
            earlier, cover, shortfall = _projected_savings_coverage(db, row)
            assert earlier == expected_earlier
            assert shortfall == 0
