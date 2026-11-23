"""Re-run the merchant cleanup rules over rows that are already stored.

Merchant names are computed once at import, and category corrections are keyed on them. After a rule
change in merchant.py, a data migration calls renormalize_merchants() so old and new imports agree.
"""

from collections import Counter, defaultdict

from sqlalchemy import Connection, bindparam, text

from app.services.merchant import normalize_merchant


# Recompute stored merchant names and move corrections to the new names
def renormalize_merchants(conn: Connection) -> dict[str, int]:
    """Recompute transactions.merchant from raw_description and rename category_corrections to match.

    A correction's old name can't just be re-normalized ("TRADER JOE" has already lost its S), so each
    user's old -> new mapping is learned from their transactions, taking the most common new name.
    A correction with no matching transactions falls back to re-normalizing its own name.
    When several of a user's corrections end up with the same name, the newest (highest id) is kept.
    Returns counts for logging: transactions renamed, corrections renamed, corrections merged away.
    """
    rows = conn.execute(text(
        "SELECT t.id, t.raw_description, t.merchant, a.user_id "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id"
    )).all()
    seen: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
    txn_updates = []
    for txn_id, raw, old, user_id in rows:
        new = normalize_merchant(raw)
        if old is not None:
            seen[(user_id, old)][new] += 1
        if new != old:
            txn_updates.append({"id": txn_id, "merchant": new})
    if txn_updates:
        conn.execute(text("UPDATE transactions SET merchant = :merchant WHERE id = :id"), txn_updates)

    corrections = conn.execute(text("SELECT id, user_id, merchant FROM category_corrections ORDER BY id")).all()
    groups: dict[tuple[int, str], list[tuple[int, str]]] = defaultdict(list)
    for corr_id, user_id, old in corrections:
        counts = seen.get((user_id, old))
        new = counts.most_common(1)[0][0] if counts else normalize_merchant(old)
        groups[(user_id, new)].append((corr_id, old))

    merged_away = [corr_id for members in groups.values() for corr_id, _ in members[:-1]]
    renames = [{"id": members[-1][0], "merchant": new}
               for (_, new), members in groups.items() if members[-1][1] != new]
    if merged_away:
        conn.execute(text("DELETE FROM category_corrections WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)), {"ids": merged_away})
    if renames:
        # Through temporary names first, so a chain like A -> B, B -> C never hits the unique (user_id, merchant) key.
        conn.execute(text("UPDATE category_corrections SET merchant = CONCAT('__renormalize_', id) WHERE id = :id"),
                     [{"id": r["id"]} for r in renames])
        conn.execute(text("UPDATE category_corrections SET merchant = :merchant WHERE id = :id"), renames)
    return {"transactions": len(txn_updates), "corrections_renamed": len(renames), "corrections_merged": len(merged_away)}
