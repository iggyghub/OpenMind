import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: requires live services (Ollama, OpenClaw); run with -m integration",
    )


def pytest_collection_modifyitems(config, items):
    skip_integration = pytest.mark.skip(reason="live services not available; run with -m integration")
    for item in items:
        if "integration" in item.keywords and not config.option.markexpr:
            item.add_marker(skip_integration)
