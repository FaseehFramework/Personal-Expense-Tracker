# Personal Expense Tracker — Full Build Specification
---

## 1. Infrastructure & Stack

| Layer | Choice |
|---|---|
| Backend | Python — Flask |
| Database | SQLite |
| Frontend | Vanilla JS + HTML + CSS (no heavy frameworks) |
| Hosting | Raspberry Pi (low RAM — keep it lightweight) |
| Access | Local network, via port e.g. `http://raspberrypi.local:5000` |
| Auth | Simple login screen (see Section 9) |

The app must run as a persistent service on the RPi. Provide a `systemd` service file and setup instructions in a `README.md`.

---

## 2. Design System

- **Aesthetic**: Neomorphism / Soft UI
- **Primary interface**: Desktop (1200px+ viewport)
- **Secondary**: Mobile responsive — must work reasonably on phone/iPad, touch-friendly
- **Default theme**: Light mode
- **Toggle**: Dark mode switch available at all times
- **Currency**: AED (all amounts displayed as `AED X,XXX.XX`)
- **Font**: Clean sans-serif (Inter or system-ui)

Neomorphism implementation notes:
- Soft shadows: two shadows per element (light top-left, dark bottom-right)
- Muted background colour (e.g. `#e0e5ec`)
- Cards appear extruded or inset using box-shadow only, no hard borders
- Buttons have pressed state (inset shadow on active)

---

## 3. Navigation

Eight tabs, always visible in sidebar (desktop) or bottom nav (mobile):

1. Dashboard
2. Transactions
3. Budget
4. Loans & Receivables
5. Wishlist
6. Reports
7. Audit Log
8. Settings

---

## 4. Dashboard

Display the following at a glance:

- **Remaining monthly budget** (unified bucket minus all budget-affecting spend so far)
- **Per-day budget remaining** — smoothed calculation:
  - Take remaining budget
  - Subtract sum of all upcoming recurring payments due before month end
  - Divide by days remaining in month
  - Display as `AED X per day`
- **Total spent this month** (budget-affecting transactions only)
- **Current bank balance**
- **Current petty cash balance**
- **Savings accumulated this month** — conceptual display only (see Section 6 for savings logic)
- **Spending streak insight card** — dismissable (swipe/close), dismissed cards go to Audit Log (see Section 12)

---

## 5. Transactions

### 5.1 Transaction Types

| Type | Budget Impact | Notes |
|---|---|---|
| Income (Bank) | Off-budget | Real income always via bank |
| Income (Petty Cash — external) | Off-budget | Cash handed to you from outside |
| Petty Cash → Bank deposit | On-budget (income) | Counts as budget income |
| Expense | On-budget | Standard spend |
| Transfer (Bank → Petty Cash) | Budget neutral | Just moves money between sources |
| Recurring Payment | On-budget | Auto-deducted on scheduled date |
| Receivable | Off-budget while outstanding | See Section 7 |
| Loan — you owe others (repayment) | On-budget | Paying back = expense |
| Loan — others owe you (lending) | Off-budget | Giving money out = not your expense |
| Loan — others owe you (repayment received) | Off-budget | Getting money back = not income |

### 5.2 Transaction Fields

| Field | Required | Notes |
|---|---|---|
| Date | Yes | Defaults to today |
| Amount (AED) | Yes | |
| Type | Yes | From list above |
| Source | Yes | Bank or Petty Cash |
| Category | No | Optional, from default list or custom |
| Description | Yes | Short label |
| Memo | No | Freeform longer note |
| Attachment | No | Single image upload per transaction |
| Split | No | Divide one transaction across multiple categories (single source only) |

### 5.3 Transaction List View

- Grouped by day, newest first (like a banking app)
- Each day group shows date header + subtotal for that day
- Each transaction row shows: amount, type icon, description, category badge, source pill (Bank / Petty Cash)
- Tap/click to expand: shows memo, attachment thumbnail, split details, edit/delete options

### 5.4 Search & Filter

Always visible above the transaction list:

- Search by keyword (description, memo)
- Filter by: date range, type, source, category, amount range
- When filtered: show mini summary bar — `X transactions — AED X,XXX total`

### 5.5 Quick-Add Templates

- After a transaction with identical amount + category + source + description is logged 3+ times, the system prompts: *"Save this as a quick-add template?"*
- Templates appear as one-tap buttons on the transaction entry screen
- When tapping a template: prompt appears — *"Use saved amount (AED X) or enter new amount?"*
- Templates can be deleted in Settings

### 5.6 Duplicate Detection

- On save, check for: same amount + same category + date within ±2 days
- If match found: show confirmation modal — *"A similar transaction exists. Add anyway?"*
- User must explicitly confirm to proceed

### 5.7 Soft Delete

- Deleted transactions move to a recoverable trash
- Permanently deleted after 5 days
- If transaction is linked (loan payment, receivable settlement): warn user and display all linked records before allowing delete

### 5.8 Edit History

- Every field change on a transaction is logged (old value → new value + timestamp)
- Accessible from the transaction detail view
- User can revert to any previous version

### 5.9 Attachments

- Single image upload per transaction (JPG, PNG)
- Stored on server filesystem, path saved in DB
- Displayed as thumbnail in transaction detail view

### 5.10 Split Transactions

- One transaction can be split across multiple categories
- All splits share a single source (Bank or Petty Cash)
- Each split line has: category, amount, optional memo
- Split amounts must sum to total transaction amount (enforce on save)

---

## 6. Budget

### 6.1 Unified Monthly Bucket

- Set once at the start of each month
- Default starting budget: **AED 2,500**
- Can be force-changed mid-month — recalculates per-day budget **from that point forward only** (does not retroactively change anything)
- Budget history (what budget was set in each past month) is viewable in Reports

### 6.2 Category Sub-Buckets

- Optional per-category budget allocations
- When a category budget is set (e.g. AED 500 for Groceries): unified bucket reduces by that amount
- Category budget is a slider — adjustable at any time during the month
- Changing the category slider does NOT change the total unified budget, only redistributes within it
- Spending in a category: deducts from both the category bucket AND the unified total simultaneously

### 6.3 Rollover & Savings

At month end (midnight on last day of month), run automatically:

1. Calculate unspent budget = (unified budget) − (total budget-affecting spend)
2. **10% of unspent** rolls over to next month's budget (added on top)
3. **90% of unspent** goes to the **savings pot** (cumulative, persists across months)
4. Dashboard shows: *"You saved AED X this month"* — conceptual display, not a real account
5. Running savings pot total is tracked in DB and displayed in Wishlist section

### 6.4 Negative Budget & Cascade

- If budget goes negative (e.g. due to receivable converted to expense, or overspend):
  - Display on dashboard: *"AED X carried from [Month]"*
  - That negative amount is deducted from next month's budget automatically
  - Cascades month to month until cleared
  - Audit Log records every cascade event

### 6.5 Recurring Payments

- Fields: description, amount (variable), source, category (optional), start date, active/inactive
- Trigger date: same day each month as the start date
- Edge case: if trigger date is 29/30/31 and month is shorter → use last day of that month
- On trigger date: if app is opened, prompt — *"[Description] of AED X is due today. Confirm it occurred?"*
- If app not opened that day: prompt appears next time app is opened
- Amount override: each month, last month's amount is pre-filled but can be changed for that month only (does not change the recurring template)
- Recurring payments belong to the unified budget
- Cannot be paused — must be deleted and re-created
- Upcoming recurring payments (within month) are shown in Budget tab

---

## 7. Receivables

Receivables are company reimbursements — money you spent that will be paid back.

### 7.1 Logging

- Logged directly as a Receivable from the start (not as expense first)
- Fields: description, amount, date, month (which month it belongs to)
- Off-budget while outstanding — does NOT deduct from monthly budget
- Appears in its own table, not mixed into expense list

### 7.2 Receivables Table View

Columns: Description | Amount | Date Logged | Month | Status | Actions

Status values: Outstanding, Partially Settled, Settled

### 7.3 Settlement

- Settlement can be applied retroactively to the month it was logged in
- On settlement: choose destination (Bank or Petty Cash)
- Full settlement only (receivables are company reimbursements — partial not needed)
- Mark as Settled → record settlement date and destination

### 7.4 Convert to Expense

- If a receivable will not be reimbursed: option to *"Convert to Expense"*
- On conversion: that amount is deducted from the month it originally belonged to
- If that causes the month's budget to go negative → cascade logic applies (Section 6.4)
- Prior month's data is editable for this purpose

---

## 8. Loans

Two directions tracked separately.

### 8.1 Money Others Owe You

- Fields: party name (or description), amount lent, date, notes
- Lending money out = off-budget
- Partial repayments supported:
  - Each payment logged: amount + date
  - Progress bar: amount repaid / total owed
  - Payment history list: date, amount, running remaining balance
- Status: Outstanding / Partially Repaid / Settled
- Repayments received = off-budget (not income)

### 8.2 Money You Owe Others

- Fields: party name (or description), amount owed, date, notes
- Same partial repayment tracking as above
- Each repayment you make = on-budget expense (deducted from monthly budget)
- Progress bar + payment history same as above

### 8.3 View

- Two sections on the Loans & Receivables tab: *"They Owe Me"* and *"I Owe Them"*
- Receivables table is also on this tab (Section 7)

---

## 9. Wishlist

Planned future expenses, not yet committed to budget.

### 9.1 Fields

- Item name
- Estimated amount (AED)
- Target month
- Notes

### 9.2 Budget Impact

- When target month arrives: system checks savings pot balance
- If savings pot ≥ item amount: savings pot covers it fully, month budget unaffected
- If savings pot < item amount: show warning — *"Savings cover AED X of AED Y. AED Z will be added to [Month]'s budget. Confirm?"*
- User must confirm before the budget is adjusted
- On confirmation: that month's unified budget reduces by the shortfall amount
- Wishlist items are prioritised by order entered (first in = first covered by savings)
- Multiple active wishlist items are all visible simultaneously

### 9.3 When Purchased

- Log as a normal expense transaction
- Mark wishlist item as purchased
- System reconciles: savings pot reduces by however much it contributed
- Audit Log records the reconciliation event

### 9.4 Abandonment

- Wishlist item can be marked as Abandoned at any time
- Any budget reservation for that month is released back
- Savings pot is unaffected (it was never actually drawn from until purchase)

### 9.5 Savings Pot Display

- Visible on Wishlist tab: *"Savings pot: AED X,XXX"*
- Shows how much is available to cover wishlist items
- Reduces visibly when a wishlist item is purchased (reconciliation)

---

## 10. Petty Cash

### 10.1 Sources of Petty Cash

| Event | Budget Impact |
|---|---|
| External cash received (salary, gift, etc.) | Off-budget |
| Transferred from Bank | Budget neutral |
| Deposited into Bank | On-budget income |

### 10.2 Top-Up Log

- Dedicated filter in Transactions tab: filter by type = "Petty Cash Top-Up"
- Shows: date, amount, source (external or bank transfer), running petty cash balance after each event
- This is not a separate screen — it is a filtered view of the main transaction list

---

## 11. Reports Tab

### 11.1 End-of-Month Summary

Auto-generated at month end. Contains:
- Total income (budget-affecting)
- Total spent (budget-affecting)
- Budget set vs actual spend
- Amount saved (sent to savings pot)
- Rollover amount added to next month
- Outstanding receivables total
- Loan balances (both directions)
- Any negative budget cascaded to next month

Past months' summaries are stored and accessible in this tab.

### 11.2 Month Comparison

- Side-by-side view of up to 3 months
- Metrics: total spent, budget vs actual, savings
- Displayed as bar chart (one grouped bar cluster per month)

### 11.3 Period Analysis

- Filter by: time period, category, source (Bank vs Petty Cash)
- Displayed as pie chart
- Shows where money went in the selected period

### 11.4 Budget History

- Table of every month: budget set, actual spend, variance, rollover, savings
- Viewable in Reports tab

### 11.5 CSV Export

Two export types, both filterable by date range and category:

1. **Raw export**: every transaction with all fields (date, amount, type, source, category, description, memo, split details)
2. **Summary export**: monthly aggregates per category, per source, budget vs actual

Download triggers a file save in browser.

### 11.6 SQLite Backup

- Button in Reports (or Settings): *"Download Database Backup"*
- Downloads the raw `.sqlite` file on demand
- No scheduled auto-backup

---

## 12. Audit Log Tab

Read-only log of all system-generated events. Includes:

- Budget changes (who changed it, old value, new value)
- Month-end rollover events
- Savings pot additions
- Wishlist reconciliation events
- Negative budget cascade events
- Recurring payment confirmations (and skips)
- Dismissed spending streak cards
- Transaction edit events (field, old value, new value, timestamp)
- Soft delete and restoration events
- Receivable conversions to expense

Display: date/time | event type | description — newest first, paginated.

---

## 13. Spending Streaks

Rule-based pattern detection. No AI or ML required.

### Detection Logic

Look for: same category + similar amount (within ±20%) + similar day of week or day of month, occurring 3+ times in the past 60 days.

### Display

- Appears as a dismissable card on the Dashboard
- Example: *"You've spent on Dining Out every Friday for 5 weeks."*
- Card can be dismissed (swipe or close button)
- Dismissed cards are archived in the Audit Log
- Dismissed cards do NOT reappear unless the pattern continues for 2+ more occurrences

### History

- All streak cards (past and dismissed) visible in Audit Log filtered by type = "Streak"

---

## 14. Authentication

### Users

Two accounts, created on first launch:

| Role | Permissions |
|---|---|
| Admin | Full read/write access to everything |
| Viewer | Read-only — can see all data but cannot add, edit, or delete anything |

### Login Screen

- Simple username + password form
- Credentials stored hashed (bcrypt) in SQLite
- Session-based auth (Flask session or JWT — your choice)
- No password reset flow needed for v1
- First-launch onboarding (see Section 15) only accessible after Admin login

---

## 15. Onboarding (First Launch)

Shown once on first launch after Admin login:

1. Enter opening Bank balance (AED)
2. Enter opening Petty Cash balance (AED)
3. Set monthly budget (pre-filled: AED 2,500)
4. Confirm → go to Dashboard

These opening balances are the starting point. No historical transaction import in v1.

---

## 16. Default Categories

Pre-loaded on first launch. Admin can add, rename, or delete categories.

- Food & Dining
- Transport
- Utilities
- Groceries
- Healthcare
- Shopping
- Entertainment
- Subscriptions

Categories are optional on every transaction. If no category is assigned, transaction appears as "Uncategorised."

---

## 17. Settings Tab

- Change Admin password / Viewer password
- Manage categories (add, rename, delete)
- Manage quick-add templates (view, delete)
- Dark mode toggle
- View app version

---

## 18. "Not Enough Data" States

The following features show a friendly empty state (not errors) until sufficient data exists:

| Feature | Threshold |
|---|---|
| Month comparison | At least 2 completed months |
| Spending streaks | At least 3 matching pattern occurrences in 60 days |
| Period analysis pie chart | At least 1 transaction in selected period |
| Reports summaries | At least 1 completed month |

Empty state message format: *"Not enough data yet — check back after [condition]."*

---

## 19. Database Schema (Suggested)

Design your schema around these entities. Adjust as needed but preserve all relationships:

- `users` — id, username, password_hash, role
- `transactions` — id, date, amount, type, source, category_id, description, memo, attachment_path, is_deleted, deleted_at, created_at, updated_at
- `transaction_splits` — id, transaction_id, category_id, amount, memo
- `transaction_edits` — id, transaction_id, field_name, old_value, new_value, changed_at
- `categories` — id, name, is_default, created_at
- `category_budgets` — id, month (YYYY-MM), category_id, allocated_amount
- `monthly_budgets` — id, month (YYYY-MM), amount, created_at, updated_at
- `budget_history` — id, month, budget_set, actual_spend, rollover_amount, savings_amount
- `recurring_payments` — id, description, base_amount, source, category_id, start_date, is_active
- `recurring_overrides` — id, recurring_id, month (YYYY-MM), override_amount, confirmed, confirmed_at
- `receivables` — id, description, amount, date_logged, month (YYYY-MM), status, settlement_date, settlement_destination
- `loans` — id, direction (owe/owed), party_description, total_amount, date, notes, status
- `loan_payments` — id, loan_id, amount, date, is_budget_expense
- `wishlist` — id, item_name, estimated_amount, target_month, status, notes, priority_order
- `savings_pot` — id, balance, updated_at (single row, updated on events)
- `savings_events` — id, event_type, amount, description, date
- `quick_add_templates` — id, description, amount, source, category_id, created_at
- `audit_log` — id, event_type, description, related_id, related_type, created_at
- `app_settings` — key, value

---

## 20. File Structure (Suggested)

```
expense-tracker/
├── app.py                  # Flask entry point
├── config.py               # Config (DB path, secret key, etc.)
├── requirements.txt
├── README.md               # Setup + run instructions
├── expense_tracker.service # systemd service file
├── database/
│   └── schema.sql          # Full schema + seed data (default categories, admin/viewer users)
├── routes/
│   ├── auth.py
│   ├── transactions.py
│   ├── budget.py
│   ├── loans.py
│   ├── wishlist.py
│   ├── reports.py
│   └── settings.py
├── services/
│   ├── budget_service.py   # Rollover, cascade, savings logic
│   ├── streak_service.py   # Pattern detection
│   └── export_service.py   # CSV generation
├── static/
│   ├── css/
│   │   ├── main.css        # Neomorphism design system
│   │   └── dark.css        # Dark mode overrides
│   ├── js/
│   │   ├── app.js          # Router, global state
│   │   ├── dashboard.js
│   │   ├── transactions.js
│   │   ├── budget.js
│   │   ├── loans.js
│   │   ├── wishlist.js
│   │   ├── reports.js
│   │   └── audit.js
│   └── uploads/            # Attachment images stored here
└── templates/
    └── index.html          # Single HTML shell (SPA-style)
```

---

## 21. Key Business Logic Summary

This section exists to prevent misimplementation of the trickier rules.

**Petty cash external income** → never touches budget under any circumstance.

**Petty cash → bank deposit** → IS budget income (unlike petty cash external income).

**Bank → petty cash transfer** → budget neutral (money hasn't left your system).

**Receivables** → off-budget while outstanding. Converting to expense triggers cascade if it makes a past month go negative.

**Negative budget cascade** → the negative amount is carried forward and deducted from the next month's budget. This cascades every month until the deficit is cleared. Always displayed visibly on dashboard and in that month's report.

**Savings rollover** → 10% of unspent budget joins next month's budget. 90% goes to the cumulative savings pot. This runs at month end automatically.

**Wishlist vs savings pot** → wishlist items draw from savings pot first. If savings pot is insufficient, the shortfall reduces that month's unified budget. User must confirm before this happens.

**Category budget slider** → adjusting it does not change the unified total. It only redistributes the internal allocation. The unified total only changes when the category budget is first created (reduces unified by that amount) or deleted (releases that amount back to unified).

**Recurring payment override** → overriding an amount for a specific month does not change the recurring template. The template retains its base amount and pre-fills it next month.

**Soft delete with links** → before deleting, show the user every record linked to the transaction (loan payment, receivable settlement, split lines, etc.). Require explicit confirmation.

---

## 22. Out of Scope for v1

Do not build these — they are noted for future reference only:

- Bank statement CSV import
- Multi-currency support
- Push notifications
- External API integrations
- Spending velocity predictions
- Net worth tracking
