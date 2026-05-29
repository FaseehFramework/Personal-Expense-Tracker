from app import create_app
app = create_app()
with app.app_context():
    from database import get_db
    db = get_db()
    # Check migration applied
    cols = db.execute("PRAGMA table_info(receivables)").fetchall()
    col_names = [c[1] for c in cols]
    print("receivables columns:", col_names)
    assert "transaction_id" in col_names, "MISSING transaction_id column!"
    print("Migration OK")

    # Check TX_TYPES
    from services.tx_effects import TX_TYPES, effects
    print("TX_TYPES:", TX_TYPES)
    assert "receivable" in TX_TYPES, "MISSING receivable in TX_TYPES!"
    # Test effects for receivable
    b, p, budget = effects("receivable", "bank", 10000)
    assert b == -10000 and p == 0 and budget == 0, f"bank receivable effects wrong: {b},{p},{budget}"
    b, p, budget = effects("receivable", "petty", 10000)
    assert b == 0 and p == -10000 and budget == 0, f"petty receivable effects wrong: {b},{p},{budget}"
    print("tx_effects OK")
    print("ALL CHECKS PASSED")
