import pytest
import chromadb
from cerebral.trading.claim_store import TradingStrategies, strip_symbol_suffix

class TestClaimStore:
    @pytest.fixture
    def client(self):
        return chromadb.EphemeralClient()

    @pytest.fixture
    def store(self, client, request):
        # A fixed collection name is NOT isolated across separate EphemeralClient()
        # instances within one process (confirmed live -- two EphemeralClient()s both
        # see the same named collection's rows), so each test needs its own name, not
        # just its own client, or state leaks between tests in file/class order.
        return TradingStrategies(chroma_client=client, collection_name=f"test_{request.node.name}")

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

    def test_separate_instances_do_not_share_a_collection(self, client):
        """Regression test: two TradingStrategies built on separate EphemeralClient()s
        (or the same one, with distinct collection_name) must not see each other's
        rows. Fails against the pre-fix hardcoded collection name, passes with an
        injectable one -- two bare `chromadb.EphemeralClient()`s were confirmed to
        share state via a fixed collection name within one process."""
        store_a = TradingStrategies(chroma_client=chromadb.EphemeralClient(), collection_name="isolation_test_a")
        store_b = TradingStrategies(chroma_client=chromadb.EphemeralClient(), collection_name="isolation_test_b")

        store_a.upsert_strategy("only_in_a", "claim only in a")
        res = store_b.retrieve_top5("claim only in a")
        assert len(res["ids"][0]) == 0
