import pytest

from cerebral.llm.tool_index import rank


def test_rank_returns_semantically_related_first():
    tools = [
        {"name": "weather_forecast", "description": "Get the weather forecast"},
        {"name": "pizza_order", "description": "Order a pizza"},
        {"name": "calc_bmi", "description": "Calculate BMI"},
        {"name": "random_tool", "description": "Does random stuff"},
    ]
    
    ranked = rank("What's the weather like?", tools, limit=3)
    assert ranked[0]["name"] == "weather_forecast"
    assert ranked[1]["name"] != "random_tool"
    
    ranked = rank("I want to order a pepperoni pizza", tools, limit=3)
    assert ranked[0]["name"] == "pizza_order"
