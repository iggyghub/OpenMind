"""Hold suspicious strategies out of every ranking and sweep for a human to look at.

First rule: a strategy whose id is pasted web-search text (the `<<<EXTERNAL_UNTRUSTED_CONTENT` wrapper
the search tool adds). Measured 2026-09-20: 53 such strategies shared only 4 distinct code bodies that do
not implement the claim they are named after, so they are duplicates that inflated every strategy count.
Nothing is deleted: rows and results stay, `clear_review` releases one.

Informational only -- never feeds check_graduation, check_retirement, run_gauntlet or auto_promote.
"""
UNTRUSTED_TEXT = "untrusted_text"   # category name shown in the review list
_MARKER = "EXTERNAL_UNTRUSTED_CONTENT"


def hold_untrusted_text_strategies(strategy_store, cross_stock_store) -> int:
    """Move every not-yet-held strategy whose id is untrusted web text into review. Idempotent.
    Returns how many were newly held."""
    held = {r["strategy_id"] for r in cross_stock_store.get_review_rows()}
    n = 0
    for spec in strategy_store.list_all():
        if _MARKER in spec.strategy_id and spec.strategy_id not in held:
            cross_stock_store.set_review(
                spec.strategy_id, UNTRUSTED_TEXT,
                "id is pasted web-search text; code does not implement the named claim",
            )
            strategy_store.update_cross_test_eligible(spec.strategy_id, False)
            strategy_store.update_cross_stock_consistency(spec.strategy_id, None)
            n += 1
    return n
