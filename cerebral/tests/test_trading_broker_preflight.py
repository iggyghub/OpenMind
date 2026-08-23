"""AlpacaBrokerClient.preflight() (S21/#874): checks the live path can
actually work before ever routing an order to it. Never a real network
call -- credentials/account are mocked in every test.
"""
from cerebral.trading.broker import Account, AlpacaBrokerClient


def test_preflight_fails_when_package_missing(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "alpaca.trading.client" or name.startswith("alpaca."):
            raise ImportError("no alpaca-py")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    client = AlpacaBrokerClient(env="paper")

    ok, reason = client.preflight()

    assert ok is False
    assert "not installed" in reason


def test_preflight_fails_with_no_credentials(monkeypatch):
    monkeypatch.setattr("keyring.get_password", lambda service, key: None)
    client = AlpacaBrokerClient(env="paper")

    ok, reason = client.preflight()

    assert ok is False
    assert "Missing Alpaca credentials" in reason


def test_preflight_fails_when_account_unreachable(monkeypatch):
    creds = {"alpaca_paper_key": "k", "alpaca_paper_secret": "s"}
    monkeypatch.setattr("keyring.get_password", lambda service, key: creds.get(key))
    client = AlpacaBrokerClient(env="paper")
    monkeypatch.setattr(client, "get_account", lambda: (_ for _ in ()).throw(ConnectionError("down")))

    ok, reason = client.preflight()

    assert ok is False
    assert "unreachable" in reason


def test_preflight_fails_when_account_not_active(monkeypatch):
    creds = {"alpaca_paper_key": "k", "alpaca_paper_secret": "s"}
    monkeypatch.setattr("keyring.get_password", lambda service, key: creds.get(key))
    client = AlpacaBrokerClient(env="paper")
    monkeypatch.setattr(client, "get_account", lambda: Account(
        cash=1000.0, equity=1000.0, status="ACCOUNT_CLOSED",
        buying_power=1000.0, day_trades_remaining=3,
    ))

    ok, reason = client.preflight()

    assert ok is False
    assert "ACCOUNT_CLOSED" in reason


def test_preflight_succeeds_when_everything_is_fine(monkeypatch):
    creds = {"alpaca_paper_key": "k", "alpaca_paper_secret": "s"}
    monkeypatch.setattr("keyring.get_password", lambda service, key: creds.get(key))
    client = AlpacaBrokerClient(env="paper")
    monkeypatch.setattr(client, "get_account", lambda: Account(
        cash=1000.0, equity=1000.0, status="ACTIVE",
        buying_power=1000.0, day_trades_remaining=3,
    ))

    ok, reason = client.preflight()

    assert ok is True
    assert reason == "ok"
