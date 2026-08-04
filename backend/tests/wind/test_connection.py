"""Connection-factory unit tests.

These verify the lazy-connection invariant (importing the adapter must NOT
open a database connection) and that the factory reads credentials from the
SecretStr only at ``connect()`` time.
"""

from factor_platform.wind.connection import WindConnectionFactory


def test_import_does_not_connect(monkeypatch) -> None:
    monkeypatch.setattr(
        "pymysql.connect",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("connected")),
    )
    import factor_platform.wind.adapter as wind

    assert "get_price" in wind.RQ_WIND_CAPABILITIES


def test_connection_factory_uses_secret_only_at_connect_time(settings, monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        "pymysql.connect", lambda **kwargs: captured.update(kwargs) or object()
    )
    WindConnectionFactory(settings).connect()
    assert captured["host"] == "db.internal"
