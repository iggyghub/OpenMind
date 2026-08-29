import pytest
import chromadb
from cerebral.trading.claim_store import TradingStrategies, strip_symbol_suffix

class TestClaimStore:
    @pytest.fixture
    def client(self):
        return chromadb.EphemeralClient()

    @pytest.fixture
    def store(self, client):
        return TradingStrategies(chroma_client=client)

    def test_suffix_stripping(self):
        assert strip_symbol_suffix("algo@BTC") == "algo"
        assert strip_symbol_suffix("algo@ETH_USD") == "algo"
        assert strip_symbol_suffix("algo") == "algo"

    def test_empty_collection_retrieval(self, store):
        res = store.retrieve_top5("test query")
        assert len(res["ids"][0]) == 0

    def test_fewer_than_5_entries(self, store):
        for i in range(3):
            store.upsert_strategy(f"strat{i}@SYM", f"claim {i}")
        res = store.retrieve_top5("claim 1")
        assert len(res["ids"][0]) == 3

    def test_tally_all_positive(self):
        ids = ["a", "b", "c"]
        weights = {"a": 1.0, "b": 0.5, "c": 0.1}
        pos, total = TradingStrategies.compute_tally(ids, weights)
        assert pos == 3
        assert total == 3

    def test_tally_all_negative(self):
        ids = ["x", "y"]
        weights = {"x": -0.5, "y": -0.2}
        pos, total = TradingStrategies.compute_tally(ids, weights)
        assert pos == 0
        assert total == 2

    def test_tally_mixed(self):
        ids = ["p", "q", "r"]
        weights = {"p": 1.0, "q": -0.5, "r": 0.3}
        pos, total = TradingStrategies.compute_tally(ids, weights)
        assert pos == 2
        assert total == 3

    def test_suffix_stripped_text_is_embedded(self, store):
        store.upsert_strategy("my_algo@ETH", "my_algo claim")
        res = store.retrieve_top5("my_algo claim")
        assert res["ids"][0] == ["my_algo"]
