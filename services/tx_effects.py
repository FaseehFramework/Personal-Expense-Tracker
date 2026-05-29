"""
Single source of truth for how each transaction type moves money.

Convention for the `source` field:
- Outflows (expense, loan_lend, loan_repay_owed, recurring): wallet money leaves FROM
- Inflows (income_*, loan_repay_received): wallet money lands IN
- Transfers: the FROM wallet (the destination is implied by the type)

Returns a tuple of integer fils deltas to apply on commit:
    (bank_delta, petty_delta, budget_delta)

`budget_delta` is the change to the **unified monthly bucket**:
  - Negative = consumes budget (expense, recurring, repaying a loan you owe)
  - Positive = adds to budget (petty-to-bank deposit; per spec §5.1)
  - Zero    = off-budget (real income, transfers, lending out, receiving repayment)
"""
from typing import Tuple

# All known transaction types (kept in sync with the UI dropdown).
TX_TYPES = (
    "income_bank",
    "income_petty_external",
    "petty_to_bank",
    "expense",
    "transfer_bank_to_petty",
    "recurring",
    "loan_repay_owed",
    "loan_lend",
    "loan_repay_received",
    "receivable",   # off-budget reimbursable spend; deducts from wallet, zero budget impact
)

# Types whose `amount` reduces the unified monthly budget.
BUDGET_CONSUMING_TYPES = ("expense", "recurring", "loan_repay_owed")

# Types whose `amount` increases the unified monthly budget.
BUDGET_INCOME_TYPES = ("petty_to_bank",)

# Convenience: types that count as on-budget "spend" for dashboard "Total spent this month".
ON_BUDGET_SPEND_TYPES = BUDGET_CONSUMING_TYPES


def effects(tx_type: str, source: str, amount: int) -> Tuple[int, int, int]:
    """Return (bank_delta, petty_delta, budget_delta) in fils for a transaction."""
    if amount < 0:
        raise ValueError("amount must be non-negative; the type encodes direction")

    if tx_type == "income_bank":
        return (amount, 0, 0)

    if tx_type == "income_petty_external":
        return (0, amount, 0)

    if tx_type == "petty_to_bank":
        # Petty wallet pays, bank receives, and the budget is credited.
        return (amount, -amount, amount)

    if tx_type == "transfer_bank_to_petty":
        return (-amount, amount, 0)

    if tx_type in ("expense", "recurring", "loan_repay_owed"):
        if source == "bank":
            return (-amount, 0, -amount)
        return (0, -amount, -amount)

    if tx_type == "loan_lend":
        # Money leaves a wallet, but does not reduce budget.
        if source == "bank":
            return (-amount, 0, 0)
        return (0, -amount, 0)

    if tx_type == "loan_repay_received":
        # Money lands in a wallet; not budget income.
        if source == "bank":
            return (amount, 0, 0)
        return (0, amount, 0)

    if tx_type == "receivable":
        # Money leaves a wallet (the spend happened) but is off-budget.
        # If/when settled the reimbursement is credited back as income.
        # If converted to expense the budget impact is applied retroactively.
        if source == "bank":
            return (-amount, 0, 0)
        return (0, -amount, 0)

    raise ValueError(f"unknown transaction type: {tx_type}")


def is_budget_consuming(tx_type: str) -> bool:
    return tx_type in BUDGET_CONSUMING_TYPES


def is_on_budget_spend(tx_type: str) -> bool:
    return tx_type in ON_BUDGET_SPEND_TYPES
