# Personal Expense Tracker

Lightweight Flask + SQLite app intended to run on a Raspberry Pi, accessed over the local network. Built to the spec in `expense_tracker_spec.md`.


## Quick start (local dev — Windows or any OS)

```bash
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # Linux/Mac
pip install -r requirements.txt
python app.py
```

Open <http://localhost:5000>. On first launch you'll be asked to create the **admin** and **viewer** accounts; after admin login the onboarding modal collects opening balances and the starting monthly budget.

## Deploying to a new device (clean slate)

The SQLite file and uploaded attachments are runtime state — they're listed in `.gitignore` and should never travel with the code. A fresh device gets a fresh DB automatically on first boot.

```bash
# On the new device:
git clone <repo> ~/expense-tracker
cd ~/expense-tracker
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
python app.py
```

Visit the URL — the first-launch wizard creates the Admin + Viewer accounts and runs onboarding. The schema (`database/schema.sql`) is applied automatically by `init_db()` in `database/__init__.py`, so there is no manual migration step.

## Wiping an existing deployment back to factory state

Use the reset script when you want to clear all data from a running installation (e.g. before handing the device to someone else, or to scrap a test setup):

```bash
# Prompts for confirmation:
python -m scripts.reset_db

# Or non-interactively, with a timestamped backup saved under database/backups/:
python -m scripts.reset_db --yes --backup
```

This deletes `database/expense_tracker.sqlite` (plus any WAL/journal siblings) and every file under `static/uploads/`. Stop the running app first so it isn't holding the DB open. Next boot recreates a fresh DB from `schema.sql` and re-runs the first-launch wizard.

## Raspberry Pi deployment

Target path: `/home/fishfrombrazil/expense-tracker`.

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip
git clone <repo> /home/fishfrombrazil/expense-tracker
cd /home/fishfrombrazil/expense-tracker
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Strong secret for sessions:
sudo sed -i "s|change-this-in-prod|$(openssl rand -hex 32)|" expense_tracker.service

sudo cp expense_tracker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now expense_tracker.service
sudo systemctl status expense_tracker.service
```

Access from any device on the LAN at `http://raspberrypi.local:5000`.

To stop:
```bash
sudo systemctl stop expense_tracker.service
sudo systemctl disable expense_tracker.service
```

bring it back up:
```bash
sudo systemctl enable --now expense_tracker.service
```

## Configuration

Override via env vars (or edit `config.py`):

| Variable | Purpose |
|---|---|
| `EXPENSE_TRACKER_SECRET` | Flask session signing key (set this in prod) |
| `EXPENSE_TRACKER_DB`     | Override SQLite file path |

Timezone is hard-coded to **Asia/Dubai** for month-end rollover and recurring-payment triggers (per spec).

## Money representation

All monetary values are stored in the database as **integer fils** (1 AED = 100 fils). Conversion to/from decimal AED happens only at API and UI boundaries (`services/money.py`). This avoids floating-point rounding bugs in budget math.

## Database

- SQLite file: `database/expense_tracker.sqlite` (created on first run)
- Schema lives as numbered migrations under `database/migrations/`
- Backups: download on demand from the Reports tab, or use `scripts/reset_db.py --backup`, or use `scripts/migrate.py --backup`

## Schema migrations

The schema is maintained as numbered `.sql` files under `database/migrations/`. The runner records which files have been applied in a `schema_migrations` table and skips them on subsequent runs. `init_db()` runs the runner on every Flask boot, so just deploying new code is enough — but for risky changes you should run migrations manually with the service stopped.

**Applying migrations on a deployed Pi:**

```bash
sudo systemctl stop expense_tracker.service
cd ~/expense-tracker
git pull
.venv/bin/pip install -r requirements.txt    # only if requirements.txt changed
.venv/bin/python -m scripts.migrate --status # see what would apply
.venv/bin/python -m scripts.migrate --backup --yes
sudo systemctl start expense_tracker.service
```

The `--backup` flag drops a timestamped copy under `database/backups/` before applying. For purely additive migrations (new tables, indexes) you can skip the stop/start step — SQLite handles concurrent reads and writes are unaffected. For column changes or backfills, always stop the service.

**Writing a new migration:**

Drop a new file in `database/migrations/` named with the next four-digit sequence number, snake_case body, `.sql` extension. The runner enforces this format. Example — adding a `tag` column to receivables and backfilling existing rows:

```sql
-- database/migrations/0002_add_receivable_tag.sql
ALTER TABLE receivables ADD COLUMN tag TEXT;
UPDATE receivables SET tag = 'legacy' WHERE tag IS NULL;
```

Guidelines:
- Prefer idempotent SQL — `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`. SQLite does not support `ALTER TABLE … IF NOT EXISTS COLUMN`, so column additions only run once; the tracking table prevents re-execution.
- Each file runs as a single `executescript()` — multiple statements separated by `;` are fine.
- Keep migrations small and focused so a failure is easier to triage. The runner aborts on the first failing migration; statements before the failure may have committed, so write recoverable SQL.
- Never edit a migration that has already been applied to any deployment. Add a new migration that corrects it.

**Useful CLI flags:**

```bash
python -m scripts.migrate                # apply pending (prompts to confirm)
python -m scripts.migrate --yes          # no prompt
python -m scripts.migrate --backup       # snapshot DB to database/backups/ first
python -m scripts.migrate --dry-run      # list pending, apply nothing
python -m scripts.migrate --status       # show applied history + pending count
```

## File layout

See `expense_tracker_spec.md`.
