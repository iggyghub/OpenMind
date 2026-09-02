import chromadb
import re
from typing import Optional

from cerebral.paths import data_dir

_CHROMA_PATH = data_dir() / "chroma_trading_strategies"


def strip_symbol_suffix(text: str) -> str:
    """Remove trailing @SYMBOL suffix (e.g., @BTC, @ETH) from a strategy ID or claim.

    NOT the same delimiter convention as strategy_store.strip_expansion_suffix
    (which requires a leading SPACE before "@", matching
    mint_expansion_strategy_id's "claim @SYMBOL" format) -- this module's own
    real usage (upsert_strategy, and its own pre-existing tests) has no
    space, e.g. "algo@BTC". Delegating to strategy_store's function would
    silently stop stripping every real claim_store id. Handles BOTH: an
    optional single space before "@" (`\\s?`) so "claim text @BRK.B" ->
    "claim text" (no trailing space) same as strategy_store's own callers
    would expect, and widened from `[\\w]+$` to `[\\w.]+$` to also handle
    dotted tickers like "algo@BRK.B" (AF18/#1012, the original bug this
    exists to fix) -- without changing the no-space convention this module
    actually uses for its own ids."""
    return re.sub(r'\s?@[\w.]+$', '', text)

class TradingStrategies:
    def __init__(self, chroma_client: Optional[chromadb.Client] = None, collection_name: str = "trading_strategies"):
        # Follows cerebral/memory/manager.py convention: injectable client, defaults to
        # PersistentClient anchored under data_dir() (not a cwd-relative path -- a relative
        # path here would put real data wherever the process happened to be launched from).
        self.chroma_client = chroma_client or chromadb.PersistentClient(path=str(_CHROMA_PATH))
        # collection_name is injectable too: chromadb.EphemeralClient() instances do NOT
        # isolate a fixed collection name from each other within one process (confirmed --
        # two separately-constructed EphemeralClient()s both see the same "trading_strategies"
        # collection's rows), so tests need a unique name per instance, not just a fresh client.
        self.collection = self.chroma_client.get_or_create_collection(
            name=collection_name,
            embedding_function=chromadb.utils.embedding_functions.DefaultEmbeddingFunction()
        )

    def upsert_strategy(self, strategy_id: str, claim_text: str) -> None:
        """Upsert a validated strategy. Embeds the bare claim (suffix-stripped ID)."""
        cleaned = strip_symbol_suffix(strategy_id)
        self.collection.upsert(ids=[cleaned], documents=[claim_text])

    def retrieve_top5(self, query_text: str) -> dict:
        """Returns top-5 nearest neighbors for the given claim text."""
        return self.collection.query(query_texts=[query_text], n_results=5)

    @staticmethod
    def compute_tally(strategy_ids: list[str], weights: dict[str, float] | None = None) -> tuple[int, int]:
        """Compute win/loss tally based on confidence weights (S38).
        Returns (positive_count, total_count).
        """
        if weights is None:
            weights = {}
        pos = sum(1 for sid in strategy_ids if weights.get(sid, 0) > 0)
        return (pos, len(strategy_ids))
