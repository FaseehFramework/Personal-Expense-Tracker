# Personal Expense Tracker

Lightweight Flask + SQLite app intended to run on a Raspberry Pi, accessed over the local network. Built to the spec in `expense_tracker_spec.md`.

## Status

**Phase 1 complete:** project scaffold, schema, auth (admin + viewer), first-launch onboarding, neomorphism SPA shell with 8-tab navigation, light/dark theme toggle.

**Phase 2 next:** transactions (CRUD, splits, attachments, soft delete, edit history, duplicates, quick-add templates) + budget engine (unified bucket, category sub-buckets, recurring payments, rollover, savings pot, cascade).

## Quick start (local dev — Windows or any OS)

```bash
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # Linux/Mac
pip install -r requirements.txt
python app.py
```

Open <http://localhost:5000>. On first launch you'll be asked to create the **admin** and **viewer** accounts; after admin login the onboarding modal collects opening balances and the starting monthly budget.

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
- Schema: `database/schema.sql`
- Backups: download on demand from the Reports tab (Phase 4)

## File layout

See `expense_tracker_spec.md` §20.
