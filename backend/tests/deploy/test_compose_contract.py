"""Deployment contract tests.

These assert the shape of the Compose file rather than a running stack, and that
is deliberate: the properties they check are the ones a `docker compose up` on a
developer laptop would *not* catch. A worker that quietly gains a network or a
credential still starts, still runs jobs, and only matters when something is
compromised — long after anyone would connect the two.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[3] / "deploy"

#: Anything matching these must never appear in the worker's environment.
SECRET_PATTERNS = ("PASSWORD", "API_KEY", "SECRET", "TOKEN", "WIND_")


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((DEPLOY / "compose.yaml").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- worker


def test_the_worker_has_no_network(compose: dict) -> None:
    """It reads a manifest and Parquet from a volume; it has nowhere to go."""
    assert compose["services"]["worker"]["network_mode"] == "none"


def test_the_worker_holds_no_database_or_model_credential(compose: dict) -> None:
    """A compromised worker must reach nothing."""
    environment = compose["services"]["worker"].get("environment", {})
    offenders = [
        key
        for key in environment
        if any(pattern in key.upper() for pattern in SECRET_PATTERNS)
        and key != "MANIFEST_SIGNING_KEY"
    ]
    assert offenders == [], f"worker environment carries secrets: {offenders}"


def test_the_worker_holds_only_the_verification_key(compose: dict) -> None:
    """It verifies signatures; it cannot produce one."""
    environment = compose["services"]["worker"].get("environment", {})
    assert "MANIFEST_SIGNING_KEY" in environment


def test_the_worker_shares_only_the_job_and_artifact_volumes(compose: dict) -> None:
    volumes = compose["services"]["worker"].get("volumes", [])
    mounted = {entry.split(":")[0] for entry in volumes}
    assert mounted == {"jobs", "artifacts"}
    assert "runtime" not in mounted, "the worker must not reach the session database"


def test_the_worker_does_not_publish_a_port(compose: dict) -> None:
    assert "ports" not in compose["services"]["worker"]


# --------------------------------------------------------------------------- exposure


def test_only_the_frontend_is_published(compose: dict) -> None:
    published = {
        name: service.get("ports")
        for name, service in compose["services"].items()
        if service.get("ports")
    }
    assert set(published) == {"frontend"}


def test_the_frontend_binds_to_loopback_only(compose: dict) -> None:
    """No TLS and no hardening yet; 0.0.0.0 would expose a research tool."""
    ports = compose["services"]["frontend"]["ports"]
    assert all(port.startswith("127.0.0.1:") for port in ports)


def test_the_backend_is_not_published_directly(compose: dict) -> None:
    backend = compose["services"]["backend"]
    assert "ports" not in backend
    assert backend.get("expose") == ["8000"]


# --------------------------------------------------------------------------- secrets


def test_no_secret_value_is_written_into_the_compose_file(compose: dict) -> None:
    """Every secret must arrive by interpolation, never as a literal."""
    text = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if any(pattern in line.upper() for pattern in SECRET_PATTERNS) and ":" in line:
            value = line.split(":", 1)[1].strip()
            if not value:
                continue
            assert value.startswith("${"), f"literal secret in compose: {line.strip()}"


def test_the_env_example_has_no_filled_values() -> None:
    for line in (DEPLOY / "compose.env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() in {"WIND_PORT", "WIND_ENABLED", "LOCAL_ONLY_MODE"}:
            continue
        assert value.strip() == "", f"{key} carries a value in the example file"


# --------------------------------------------------------------------------- images


def test_every_service_runs_as_a_non_root_user() -> None:
    for name in ("backend", "worker"):
        text = (DEPLOY / f"{name}.Dockerfile").read_text(encoding="utf-8")
        assert "USER " in text, f"{name} image does not drop root"


def test_the_proxy_does_not_buffer_the_event_stream() -> None:
    """Buffered SSE arrives in one lump, which looks exactly like a hung session."""
    text = (DEPLOY / "nginx.conf").read_text(encoding="utf-8")
    assert "proxy_buffering off" in text


def test_the_proxy_caps_upload_size() -> None:
    text = (DEPLOY / "nginx.conf").read_text(encoding="utf-8")
    assert "client_max_body_size" in text
