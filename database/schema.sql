-- Personal Expense Tracker — full schema
-- All monetary amounts are stored as INTEGER fils (1 AED = 100 fils).
-- All month identifiers use the string format 'YYYY-MM'.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'viewer')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    is_default  INTEGER NOT NULL DEFAULT 0,
    is_deleted  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Transaction `type` values mirror Section 5.1 of the spec:
--   income_bank, income_petty_external, petty_to_bank, expense,
--   transfer_bank_to_petty, recurring, loan_repay_owed,
--   loan_lend, loan_repay_received
-- Receivables are stored in their own table, not as transactions.
CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,          -- YYYY-MM-DD
    amount          INTEGER NOT NULL,       -- fils
    type            TEXT NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('bank', 'petty')),
    category_id     INTEGER REFERENCES categories(id),
    description     TEXT NOT NULL,
    memo            TEXT,
    attachment_path TEXT,
    -- Linkage to loan / receivable / wishlist / recurring records, if any.
    linked_type     TEXT,    -- 'loan', 'receivable', 'wishlist', 'recurring'
    linked_id       INTEGER,
    is_deleted      INTEGER NOT NULL DEFAULT 0,
    deleted_at      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_type ON transactions(type);
CREATE INDEX IF NOT EXISTS idx_tx_deleted ON transactions(is_deleted);
CREATE INDEX IF NOT EXISTS idx_tx_linked ON transactions(linked_type, linked_id);

CREATE TABLE IF NOT EXISTS transaction_splits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    category_id    INTEGER REFERENCES categories(id),
    amount         INTEGER NOT NULL,
    memo           TEXT
);
CREATE INDEX IF NOT EXISTS idx_split_tx ON transaction_splits(transaction_id);

CREATE TABLE IF NOT EXISTS transaction_edits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    field_name     TEXT NOT NULL,
    old_value      TEXT,
    new_value      TEXT,
    changed_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_edit_tx ON transaction_edits(transaction_id);

CREATE TABLE IF NOT EXISTS monthly_budgets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    month       TEXT NOT NULL UNIQUE,       -- YYYY-MM
    amount      INTEGER NOT NULL,           -- fils
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS category_budgets (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    month            TEXT NOT NULL,
    category_id      INTEGER NOT NULL REFERENCES categories(id),
    allocated_amount INTEGER NOT NULL,
    UNIQUE(month, category_id)
);

-- Per-month rollup written by month-end job. Includes cascade tracking.
CREATE TABLE IF NOT EXISTS budget_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    month             TEXT NOT NULL UNIQUE,
    budget_set        INTEGER NOT NULL,
    actual_spend      INTEGER NOT NULL,
    rollover_amount   INTEGER NOT NULL DEFAULT 0,  -- carried to next month
    savings_amount    INTEGER NOT NULL DEFAULT 0,  -- pushed to savings pot
    negative_cascade  INTEGER NOT NULL DEFAULT 0,  -- if month went negative
    closed_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recurring_payments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    description  TEXT NOT NULL,
    base_amount  INTEGER NOT NULL,
    source       TEXT NOT NULL CHECK (source IN ('bank', 'petty')),
    category_id  INTEGER REFERENCES categories(id),
    start_date   TEXT NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recurring_overrides (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recurring_id    INTEGER NOT NULL REFERENCES recurring_payments(id) ON DELETE CASCADE,
    month           TEXT NOT NULL,
    override_amount INTEGER,
    confirmed       INTEGER NOT NULL DEFAULT 0,
    confirmed_at    TEXT,
    transaction_id  INTEGER REFERENCES transactions(id),
    UNIQUE(recurring_id, month)
);

CREATE TABLE IF NOT EXISTS receivables (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    description              TEXT NOT NULL,
    amount                   INTEGER NOT NULL,
    date_logged              TEXT NOT NULL,
    month                    TEXT NOT NULL,
    status                   TEXT NOT NULL DEFAULT 'outstanding'
                                 CHECK (status IN ('outstanding', 'partial', 'settled', 'converted')),
    settlement_date          TEXT,
    settlement_destination   TEXT,   -- 'bank' or 'petty'
    converted_at             TEXT,
    created_at               TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS loans (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    direction         TEXT NOT NULL CHECK (direction IN ('owe', 'owed')),
    party_description TEXT NOT NULL,
    total_amount      INTEGER NOT NULL,
    date              TEXT NOT NULL,
    notes             TEXT,
    status            TEXT NOT NULL DEFAULT 'outstanding'
                          CHECK (status IN ('outstanding', 'partial', 'settled')),
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS loan_payments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_id           INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    amount            INTEGER NOT NULL,
    date              TEXT NOT NULL,
    is_budget_expense INTEGER NOT NULL DEFAULT 0,
    transaction_id    INTEGER REFERENCES transactions(id),
    notes             TEXT
);

CREATE TABLE IF NOT EXISTS wishlist (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    item_name        TEXT NOT NULL,
    estimated_amount INTEGER NOT NULL,
    target_month     TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'purchased', 'abandoned')),
    notes            TEXT,
    priority_order   INTEGER NOT NULL,
    transaction_id   INTEGER REFERENCES transactions(id),
    savings_drawn    INTEGER NOT NULL DEFAULT 0,
    budget_charged   INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Single-row table — id = 1 always.
CREATE TABLE IF NOT EXISTS savings_pot (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    balance    INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS savings_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,   -- 'rollover_credit', 'wishlist_debit'
    amount      INTEGER NOT NULL,
    description TEXT,
    date        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS quick_add_templates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    source      TEXT NOT NULL,
    category_id INTEGER REFERENCES categories(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type   TEXT NOT NULL,
    description  TEXT NOT NULL,
    related_id   INTEGER,
    related_type TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_log(event_type);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS streak_dismissals (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    signature                     TEXT NOT NULL UNIQUE,
    occurrence_count_at_dismiss   INTEGER NOT NULL,
    dismissed_at                  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Seed default categories (idempotent).
INSERT OR IGNORE INTO categories (name, is_default) VALUES
    ('Food & Dining', 1),
    ('Transport', 1),
    ('Utilities', 1),
    ('Groceries', 1),
    ('Healthcare', 1),
    ('Shopping', 1),
    ('Entertainment', 1),
    ('Subscriptions', 1);

-- Initialise savings pot row.
INSERT OR IGNORE INTO savings_pot (id, balance) VALUES (1, 0);

-- App-level settings defaults.
INSERT OR IGNORE INTO app_settings (key, value) VALUES
    ('onboarded', '0'),
    ('app_version', '1.0.0');
