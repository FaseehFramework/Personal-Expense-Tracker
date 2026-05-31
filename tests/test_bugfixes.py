"""
Regression tests for three reported bugs:

  Bug 1: amount field corruption — server must accept string-form money values
         from the API exactly (no float intermediate). 200 in must store 20000
         fils; "199.91" in must store 19991 fils.
  Bug 2: editing the amount / type / source of a transaction that is linked
         to a loan / receivable / wishlist / recurring must be rejected.
         Cosmetic edits (date / description / memo) must still work.
  Bug 3: wishlist preview endpoint must respond 200 with a valid item id.
         (The frontend dataset typo was the root cause, but we also pin the
         API contract so the fix can't silently regress.)
"""
import json

import pytest

from app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Spin up the Flask test client against a temp SQLite file."""
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setenv("EXPENSE_TRACKER_DB", str(db_path))
    # Config reads the env at import time — reload it.
    import importlib
    import config as cfg_mod
    importlib.reload(cfg_mod)
    import database as db_mod
    importlib.reload(db_mod)

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        # Full setup flow: create accounts, login, onboard.
        c.post("/api/auth/first-run", json={
            "admin_username": "admin", "admin_password": "adminpass",
            "viewer_username": "viewer", "viewer_password": "viewerpass",
        })
        c.post("/api/auth/login", json={"username": "admin", "password": "adminpass"})
        c.post("/api/onboarding/complete", json={
            "opening_bank": "5000", "opening_petty": "300", "monthly_budget": "2500",
        })
        yield c


# ===== Bug 1 — money values cross the wire as strings cleanly =====

class TestStringMoney:
    def test_loan_repayment_received_with_integer_json(self, client):
        # Create 'they owe me' loan, then log received 200.
        r = client.post("/api/loans", json={
            "direction": "owed", "party_description": "Friend",
            "amount": "500", "date": "2026-05-01", "source": "bank",
        })
        assert r.status_code == 201, r.get_json()
        lid = r.get_json()["id"]

        # Send amount as a STRING (frontend now does this).
        r = client.post(f"/api/loans/{lid}/payments", json={
            "amount": "200", "date": "2026-05-10", "source": "bank",
        })
        assert r.status_code == 201, r.get_json()

        # Confirm DB stored 20000 fils (not 19991).
        r = client.get(f"/api/loans/{lid}")
        payment = r.get_json()["payments"][0]
        assert payment["amount"] == 20000
        assert payment["amount_aed"] == "200.00"

    def test_loan_repayment_with_decimal_string(self, client):
        # If user really entered 199.91, store EXACTLY 19991.
        r = client.post("/api/loans", json={
            "direction": "owed", "party_description": "Friend",
            "amount": "500", "date": "2026-05-01", "source": "bank",
        })
        lid = r.get_json()["id"]
        r = client.post(f"/api/loans/{lid}/payments", json={
            "amount": "199.91", "date": "2026-05-10", "source": "bank",
        })
        assert r.status_code == 201, r.get_json()
        payment = client.get(f"/api/loans/{lid}").get_json()["payments"][0]
        assert payment["amount"] == 19991
        assert payment["amount_aed"] == "199.91"

    def test_transaction_amount_string(self, client):
        # Most common path: a plain expense entered as "200".
        r = client.post("/api/transactions", json={
            "date": "2026-05-15", "amount": "200", "type": "expense",
            "source": "bank", "description": "Coffee run",
        })
        assert r.status_code == 201
        tx = r.get_json()["transaction"]
        assert tx["amount"] == 20000

    def test_receivable_amount_string(self, client):
        """Receivables are now created via POST /api/transactions with type='receivable'.
        The receivables row is auto-created and linked to the transaction."""
        r = client.post("/api/transactions", json={
            "description": "Work flight", "amount": "1200.50",
            "date": "2026-05-01", "type": "receivable", "source": "bank",
        })
        assert r.status_code == 201, r.get_json()
        tx = r.get_json()["transaction"]
        assert tx["amount"] == 120050
        assert tx["type"] == "receivable"
        # Auto-created receivable should appear in the list.
        recs = client.get("/api/receivables").get_json()
        assert len(recs["receivables"]) == 1
        assert recs["receivables"][0]["amount"] == 120050
        assert recs["receivables"][0]["transaction_id"] == tx["id"]

    def test_direct_receivable_create_returns_410(self, client):
        """The old POST /api/receivables entry point is removed and returns 410 Gone."""
        r = client.post("/api/receivables", json={
            "description": "Work flight", "amount": "1200.50",
            "date": "2026-05-01", "month": "2026-05",
        })
        assert r.status_code == 410

    def test_budget_set_with_string(self, client):
        r = client.put("/api/budget/monthly", json={
            "month": "2026-06", "amount": "3500",
        })
        assert r.status_code == 200
        b = client.get("/api/budget/monthly?month=2026-06").get_json()
        assert b["amount"] == 350000


# ===== Bug 2 — linked transactions reject financial-field edits =====

class TestLinkedTransactionLock:
    @pytest.fixture
    def loan_with_payment_tx(self, client):
        """Create a loan + repayment so we have a linked tx to test against."""
        client.post("/api/loans", json={
            "direction": "owed", "party_description": "Friend",
            "amount": "500", "date": "2026-05-01", "source": "bank",
        })
        client.post("/api/loans/1/payments", json={
            "amount": "200", "date": "2026-05-10", "source": "bank",
        })
        # Find the repayment transaction's id.
        days = client.get("/api/transactions?type=loan_repay_received").get_json()["days"]
        tx_id = days[0]["items"][0]["id"]
        return tx_id

    def test_amount_edit_rejected_with_helpful_error(self, client, loan_with_payment_tx):
        tx_id = loan_with_payment_tx
        r = client.put(f"/api/transactions/{tx_id}", json={
            "date": "2026-05-10", "amount": "300", "type": "loan_repay_received",
            "source": "bank", "description": "Repayment from Friend",
        })
        assert r.status_code == 409
        body = r.get_json()
        assert "linked" in body["error"].lower()
        assert body["linked_type"] == "loan"

    def test_type_edit_rejected(self, client, loan_with_payment_tx):
        tx_id = loan_with_payment_tx
        r = client.put(f"/api/transactions/{tx_id}", json={
            "date": "2026-05-10", "amount": "200", "type": "expense",
            "source": "bank", "description": "Repayment from Friend",
        })
        assert r.status_code == 409

    def test_source_edit_rejected(self, client, loan_with_payment_tx):
        tx_id = loan_with_payment_tx
        r = client.put(f"/api/transactions/{tx_id}", json={
            "date": "2026-05-10", "amount": "200", "type": "loan_repay_received",
            "source": "petty", "description": "Repayment from Friend",
        })
        assert r.status_code == 409

    def test_cosmetic_edit_still_works(self, client, loan_with_payment_tx):
        """Date / description / memo / category edits MUST still be allowed."""
        tx_id = loan_with_payment_tx
        r = client.put(f"/api/transactions/{tx_id}", json={
            "date": "2026-05-11",
            "amount": "200", "type": "loan_repay_received", "source": "bank",
            "description": "Cash from Friend (updated)",
            "memo": "noted",
        })
        assert r.status_code == 200, r.get_json()
        tx = r.get_json()["transaction"]
        assert tx["date"] == "2026-05-11"
        assert tx["description"] == "Cash from Friend (updated)"
        assert tx["memo"] == "noted"
        # Amount unchanged on the linked record.
        loan = client.get("/api/loans/1").get_json()
        assert loan["payments"][0]["amount"] == 20000

    def test_loan_lend_tx_is_locked_too(self, client):
        """The auto-created loan_lend tx (at loan creation) is also linked."""
        client.post("/api/loans", json={
            "direction": "owed", "party_description": "Friend",
            "amount": "500", "date": "2026-05-01", "source": "bank",
        })
        days = client.get("/api/transactions?type=loan_lend").get_json()["days"]
        tx_id = days[0]["items"][0]["id"]
        r = client.put(f"/api/transactions/{tx_id}", json={
            "date": "2026-05-01", "amount": "999", "type": "loan_lend",
            "source": "bank", "description": "Lent to Friend",
        })
        assert r.status_code == 409
        assert r.get_json()["linked_type"] == "loan"

    def test_unlinked_transaction_still_freely_editable(self, client):
        """Sanity: a plain expense (no linked_type) edits work end-to-end."""
        r = client.post("/api/transactions", json={
            "date": "2026-05-15", "amount": "50", "type": "expense",
            "source": "bank", "description": "Coffee",
        })
        tx_id = r.get_json()["transaction"]["id"]
        r = client.put(f"/api/transactions/{tx_id}", json={
            "date": "2026-05-15", "amount": "75", "type": "expense",
            "source": "bank", "description": "Coffee + cake",
        })
        assert r.status_code == 200
        assert r.get_json()["transaction"]["amount"] == 7500


# ===== Bug 3 — wishlist preview endpoint contract =====

class TestWishlistPreviewContract:
    def test_preview_returns_200_for_active_item(self, client):
        client.post("/api/wishlist", json={
            "item_name": "Headphones", "estimated_amount": "500",
            "target_month": "2026-07",
        })
        items = client.get("/api/wishlist").get_json()["items"]
        wid = items[0]["id"]
        r = client.get(f"/api/wishlist/{wid}/preview")
        assert r.status_code == 200
        body = r.get_json()
        assert body["item"]["item_name"] == "Headphones"
        assert "will_cover" in body and "shortfall" in body

    def test_preview_404_for_missing_id(self, client):
        # This is what the frontend would have hit with the NaN typo.
        r = client.get("/api/wishlist/999/preview")
        assert r.status_code == 404


# ===== Off-Budget Expense (expense_offbudget) =====

class TestOffBudgetExpense:
    """Verify the expense_offbudget type:
    - Deducts from source wallet.
    - Does NOT deduct from the monthly budget.
    - Appears in reports as off_budget_spend, separate from 'spent'.
    - Appears in period analysis pie with is_offbudget=True.
    - TX_TYPES and effects() return the right deltas.
    """

    def _get_summary(self, client, month="2026-05"):
        return client.get(f"/api/budget/summary?month={month}").get_json()

    def _get_report_summary(self, client, month="2026-05"):
        return client.get(f"/api/reports/month-summary/{month}").get_json()

    def test_tx_types_contains_expense_offbudget(self, client):
        from services.tx_effects import TX_TYPES, effects
        assert "expense_offbudget" in TX_TYPES

    def test_effects_bank_deducts_wallet_not_budget(self, client):
        from services.tx_effects import effects
        bank_d, petty_d, budget_d = effects("expense_offbudget", "bank", 10000)
        assert bank_d == -10000
        assert petty_d == 0
        assert budget_d == 0

    def test_effects_petty_deducts_petty_not_budget(self, client):
        from services.tx_effects import effects
        bank_d, petty_d, budget_d = effects("expense_offbudget", "petty", 10000)
        assert bank_d == 0
        assert petty_d == -10000
        assert budget_d == 0

    def test_wallet_decrements_after_offbudget_expense(self, client):
        before = self._get_summary(client)
        bank_before = before["bank"]
        # Log an off-budget expense from bank.
        r = client.post("/api/transactions", json={
            "date": "2026-05-15", "amount": "100", "type": "expense_offbudget",
            "source": "bank", "description": "Concert ticket",
        })
        assert r.status_code == 201, r.get_json()
        after = self._get_summary(client)
        assert after["bank"] == bank_before - 10000  # AED 100 = 10000 fils

    def test_budget_remaining_unchanged_after_offbudget_expense(self, client):
        before = self._get_summary(client)
        remaining_before = before["remaining"]
        # Log an off-budget expense.
        client.post("/api/transactions", json={
            "date": "2026-05-15", "amount": "200", "type": "expense_offbudget",
            "source": "bank", "description": "Holiday splurge",
        })
        after = self._get_summary(client)
        # Budget remaining must be identical — off-budget does not touch it.
        assert after["remaining"] == remaining_before

    def test_spent_tile_unchanged_offbudget_not_counted(self, client):
        """'spent' in the dashboard summary must not include off-budget expenses."""
        before = self._get_summary(client)
        spent_before = before["spent"]
        client.post("/api/transactions", json={
            "date": "2026-05-15", "amount": "150", "type": "expense_offbudget",
            "source": "bank", "description": "Side trip",
        })
        after = self._get_summary(client)
        assert after["spent"] == spent_before  # unchanged

    def test_off_budget_spend_in_dashboard_summary(self, client):
        """off_budget_spend appears in the dashboard summary response."""
        client.post("/api/transactions", json={
            "date": "2026-05-10", "amount": "300", "type": "expense_offbudget",
            "source": "bank", "description": "Discretionary",
        })
        summary = self._get_summary(client)
        assert "off_budget_spend" in summary
        assert summary["off_budget_spend"] == 30000

    def test_off_budget_spend_in_month_summary_report(self, client):
        """off_budget_spend is returned by /api/reports/month-summary."""
        client.post("/api/transactions", json={
            "date": "2026-05-12", "amount": "250", "type": "expense_offbudget",
            "source": "petty", "description": "Gift",
        })
        rpt = self._get_report_summary(client)
        assert "off_budget_spend" in rpt
        assert rpt["off_budget_spend"] == 25000
        # 'spent' must not include it.
        assert rpt["spent"] == 0

    def test_off_budget_appears_in_period_analysis(self, client):
        """expense_offbudget transactions appear in the period analysis pie."""
        client.post("/api/transactions", json={
            "date": "2026-05-08", "amount": "180", "type": "expense_offbudget",
            "source": "bank", "description": "Impulse buy",
        })
        r = client.get("/api/reports/period?from=2026-05-01&to=2026-05-31")
        assert r.status_code == 200
        body = r.get_json()
        assert body["off_budget_total"] == 18000
        # At least one slice must be marked is_offbudget.
        offbudget_slices = [s for s in body["slices"] if s["is_offbudget"]]
        assert len(offbudget_slices) >= 1

    def test_regular_expense_not_tagged_offbudget_in_period(self, client):
        """Normal expenses must not have is_offbudget=True."""
        client.post("/api/transactions", json={
            "date": "2026-05-08", "amount": "100", "type": "expense",
            "source": "bank", "description": "Groceries",
        })
        r = client.get("/api/reports/period?from=2026-05-01&to=2026-05-31")
        body = r.get_json()
        budget_slices = [s for s in body["slices"] if not s["is_offbudget"]]
        assert len(budget_slices) >= 1
        for s in budget_slices:
            assert s["is_offbudget"] is False

