"""Tests for authentication and the two-role model.

Three properties, each guarding something that fails silently:

* **The cookie carries an id, never a role.** A role in the cookie is a
  permission the client can edit, and nothing would notice.
* **Unknown user and wrong password give the same error.** Distinguishing them
  hands an attacker the list of valid usernames — the expensive half of a guess.
* **Sessions expire.** A session that does not is a stolen laptop with permanent
  access.
"""

from __future__ import annotations

import pytest

from factor_platform.auth.service import (
    SESSION_MAX_AGE_SECONDS,
    AuthError,
    AuthService,
    PermissionDeniedError,
    Role,
    cookie_settings,
)

PASSWORD = "unit-test-only-password"  # pragma: allowlist secret
SECRET = "unit-test-only-secret"  # pragma: allowlist secret


@pytest.fixture
def service() -> AuthService:
    auth = AuthService(SECRET)
    auth.create_user("admin", PASSWORD, Role.ADMIN)
    auth.create_user("researcher", PASSWORD, Role.RESEARCHER)
    return auth


# --------------------------------------------------------------------------- passwords


def test_a_password_is_never_stored_in_the_clear(service: AuthService) -> None:
    user = service.get_user("researcher")
    assert user is not None
    assert PASSWORD not in user.password_hash
    assert user.password_hash.startswith("$argon2")


def test_the_correct_password_authenticates(service: AuthService) -> None:
    assert service.authenticate("researcher", PASSWORD).role is Role.RESEARCHER


def test_a_wrong_password_is_refused(service: AuthService) -> None:
    with pytest.raises(AuthError, match="用户名或密码错误"):
        service.authenticate("researcher", "wrong-password")  # pragma: allowlist secret


def test_an_unknown_user_gives_the_same_error_as_a_wrong_password(
    service: AuthService,
) -> None:
    """Distinguishing them would enumerate valid usernames."""
    with pytest.raises(AuthError, match="用户名或密码错误"):
        service.authenticate("nobody", PASSWORD)


def test_a_disabled_user_cannot_log_in(service: AuthService) -> None:
    user = service.get_user("researcher")
    assert user is not None
    user.disabled = True
    with pytest.raises(AuthError):
        service.authenticate("researcher", PASSWORD)


def test_a_short_password_is_refused(service: AuthService) -> None:
    with pytest.raises(AuthError, match="8"):
        service.create_user("weak", "short", Role.RESEARCHER)  # pragma: allowlist secret


def test_a_duplicate_username_is_refused(service: AuthService) -> None:
    with pytest.raises(AuthError, match="already exists"):
        service.create_user("admin", PASSWORD, Role.ADMIN)


# --------------------------------------------------------------------------- sessions


def test_a_token_round_trips_to_its_user(service: AuthService) -> None:
    user = service.authenticate("researcher", PASSWORD)
    assert service.resolve(service.issue_token(user)).username == "researcher"


def test_the_token_does_not_carry_the_role(service: AuthService) -> None:
    """A role in the cookie is a permission the client can edit."""
    token = service.issue_token(service.authenticate("admin", PASSWORD))
    assert "admin" not in token.lower()

    # And the role is re-read server-side, so changing it takes effect at once.
    user = service.get_user("admin")
    assert user is not None
    user.role = Role.RESEARCHER
    assert service.resolve(token).role is Role.RESEARCHER


def test_a_tampered_token_is_refused(service: AuthService) -> None:
    token = service.issue_token(service.authenticate("admin", PASSWORD))
    with pytest.raises(AuthError, match="无效"):
        service.resolve(token[:-4] + "AAAA")


def test_an_expired_token_is_refused() -> None:
    """A session that never expires is a stolen laptop with permanent access."""
    service = AuthService(SECRET, max_age=-1)
    service.create_user("researcher", PASSWORD, Role.RESEARCHER)
    token = service.issue_token(service.authenticate("researcher", PASSWORD))
    with pytest.raises(AuthError, match="过期"):
        service.resolve(token)


def test_a_token_for_a_deleted_user_is_refused(service: AuthService) -> None:
    token = service.issue_token(service.authenticate("researcher", PASSWORD))
    user = service.get_user("researcher")
    assert user is not None
    user.disabled = True
    with pytest.raises(AuthError, match="停用"):
        service.resolve(token)


def test_an_empty_secret_is_refused() -> None:
    with pytest.raises(AuthError, match="secret"):
        AuthService("")


# --------------------------------------------------------------------------- roles


def test_a_researcher_cannot_act_as_an_admin(service: AuthService) -> None:
    researcher = service.authenticate("researcher", PASSWORD)
    with pytest.raises(PermissionDeniedError, match="admin"):
        AuthService.require_role(researcher, Role.ADMIN)


def test_an_admin_passes_an_admin_check(service: AuthService) -> None:
    admin = service.authenticate("admin", PASSWORD)
    assert AuthService.require_role(admin, Role.ADMIN) is admin


def test_a_role_check_can_accept_either_role(service: AuthService) -> None:
    researcher = service.authenticate("researcher", PASSWORD)
    assert AuthService.require_role(researcher, Role.ADMIN, Role.RESEARCHER)


# --------------------------------------------------------------------------- cookies


def test_the_cookie_is_http_only_and_same_site() -> None:
    """httponly keeps an XSS bug from reading it; lax stops cross-site POSTs."""
    settings = cookie_settings(secure=True)
    assert settings["httponly"] is True
    assert settings["samesite"] == "lax"
    assert settings["secure"] is True
    assert settings["max_age"] == SESSION_MAX_AGE_SECONDS


def test_secure_is_configurable_rather_than_hard_coded() -> None:
    """Hard-coding it on breaks local HTTP login silently instead of loudly."""
    assert cookie_settings(secure=False)["secure"] is False
