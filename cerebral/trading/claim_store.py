import chromadb
import re
from typing import Optional

def strip_symbol_suffix(text: str) -> str:
    """Remove trailing @SYMBOL suffix (e.g., @BTC, @ETH) from a strategy ID or claim."""
    return re.sub(r'@[\w]+$', '', text)

class TradingStrategies:
    def __init__(self, chroma_client: Optional[chromadb.Client] = None):
        # Follows cerebral/memory/manager.py convention: injectable client, defaults to PersistentClient
        self.chroma_client = chroma_client or chromadb.PersistentClient(path="./chroma_data/trading_strategies")
        self.collection = self.chroma_client.get_or_create_collection(
            name="trading_strategies",
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
