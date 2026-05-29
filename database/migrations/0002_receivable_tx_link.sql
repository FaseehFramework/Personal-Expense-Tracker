-- Migration 0002: link receivables to their originating transaction.
-- New receivables are now created through the Transactions tab (type='receivable').
-- The transaction_id column stores the FK back to that row so settlement and
-- convert-to-expense can update both records atomically.
-- Legacy receivables (created before this migration) will have transaction_id = NULL.

PRAGMA foreign_keys = OFF;

ALTER TABLE receivables ADD COLUMN transaction_id INTEGER REFERENCES transactions(id);

CREATE INDEX IF NOT EXISTS idx_rec_tx ON receivables(transaction_id);

PRAGMA foreign_keys = ON;
