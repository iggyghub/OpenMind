import pytest

from cerebral.llm import tool_index
from cerebral.llm.tool_index import rank

@pytest.fixture(autouse=True)
def _isolated_index(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_index, "INDEX_PATH", tmp_path / "idx")
    monkeypatch.setitem(tool_index._state, "coll", None)


TOOLS = [
    {"name": "weather_forecast", "description": "Get the weather forecast"},
    {"name": "restaurant_finder", "description": "Find restaurants and places to eat nearby"},
    {"name": "calc_bmi", "description": "Calculate BMI"},
    {"name": "random_tool", "description": "Does random stuff"},
]


def test_rank_returns_semantically_related_first():
    assert rank("find me somewhere to eat tonight", TOOLS, limit=2)[0]["name"] == "restaurant_finder"
    assert rank("will it rain tomorrow", TOOLS, limit=2)[0]["name"] == "weather_forecast"


def test_rank_only_embeds_new_or_changed_tools(monkeypatch):
    rank("anything", TOOLS, limit=2)
    coll = tool_index._collection()
    calls = []
    real = coll.upsert
    monkeypatch.setattr(coll, "upsert", lambda **k: (calls.append(k["ids"]), real(**k)))
    rank("anything", TOOLS, limit=2)
    assert calls == []
    rank("anything", TOOLS + [{"name": "new_tool", "description": "x"}], limit=2)
    assert calls == [["new_tool"]]


def test_rank_ignores_tools_no_longer_registered():
    rank("anything", TOOLS, limit=2)
    other = [{"name": "pizza_order", "description": "Order a pizza"}, TOOLS[2]]
    assert {t["name"] for t in rank("pepperoni", other, limit=2)} == {"pizza_order", "calc_bmi"}

