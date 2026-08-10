"""Local authentication: Argon2 password hashing and signed session cookies.

Two roles, because that is what this platform actually distinguishes: an
``admin`` manages users and reviews metric definitions; a ``researcher`` builds
and publishes factors. Adding more would invent distinctions nobody has asked for
and that nothing enforces.

The session cookie is signed, not encrypted, and carries only a user id. A cookie
containing a role would let a client change its own permissions by editing a
value the server never re-checks — so the role is looked up server-side on every
request, and the cookie proves identity only.

Cookies expire. A session that never expires is a stolen laptop with permanent
access, and the max-age here is the one thing standing between that and a fresh
login.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any, Final

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pwdlib import PasswordHash
from pydantic import BaseModel

from factor_platform.domain.errors import DomainError

#: Eight hours: a working day, then log in again.
SESSION_MAX_AGE_SECONDS: Final = 8 * 60 * 60

_SALT: Final = "factor-platform-session"


class Role(StrEnum):
    ADMIN = "admin"
    RESEARCHER = "researcher"


class AuthError(DomainError):
    """Raised when authentication fails or a session is not usable."""


class PermissionDeniedError(DomainError):
    """Raised when an authenticated user lacks the required role."""


class User(BaseModel):
    user_id: str
    username: str
    role: Role
    password_hash: str
    disabled: bool = False


class AuthService:
    """Verifies credentials and issues signed session tokens."""

    def __init__(self, secret: str, *, max_age: int = SESSION_MAX_AGE_SECONDS) -> None:
        if not secret:
            raise AuthError("session secret is required; refusing to sign with an empty key")
        self._serializer = URLSafeTimedSerializer(secret, salt=_SALT)
        self._hasher = PasswordHash.recommended()
        self._max_age = max_age
        self._users: dict[str, User] = {}

    # ------------------------------------------------------------------ users

    def create_user(self, username: str, password: str, role: Role) -> User:
        if username in self._users:
            raise AuthError(f"user already exists: {username}")
        if len(password) < 8:
            raise AuthError("password must be at least 8 characters")
        user = User(
            user_id=f"u-{len(self._users) + 1}",
            username=username,
            role=role,
            password_hash=self._hasher.hash(password),
        )
        self._users[username] = user
        return user

    def get_user(self, username: str) -> User | None:
        return self._users.get(username)

    def get_by_id(self, user_id: str) -> User | None:
        return next((u for u in self._users.values() if u.user_id == user_id), None)

    # ------------------------------------------------------------------ login

    def authenticate(self, username: str, password: str) -> User:
        """Verify a password.

        The same error for an unknown user and a wrong password: distinguishing
        them tells an attacker which usernames exist, and that is the expensive
        half of the guess.
        """
        user = self._users.get(username)
        if user is None or user.disabled:
            # Hash anyway so the timing does not reveal whether the user exists.
            self._hasher.hash(password)
            raise AuthError("用户名或密码错误")
        if not self._hasher.verify(password, user.password_hash):
            raise AuthError("用户名或密码错误")
        return user

    def issue_token(self, user: User) -> str:
        """Sign a token carrying the user id only.

        Deliberately not the role: a client that could edit its own role in a
        value the server never rechecks would be granting its own permissions.
        """
        return self._serializer.dumps({"user_id": user.user_id})

    def resolve(self, token: str) -> User:
        """Return the user for a token, re-reading the role from the store."""
        try:
            payload: Any = self._serializer.loads(token, max_age=self._max_age)
        except SignatureExpired as exc:
            raise AuthError("会话已过期，请重新登录") from exc
        except BadSignature as exc:
            raise AuthError("会话无效") from exc

        user = self.get_by_id(str(payload.get("user_id", "")))
        if user is None or user.disabled:
            raise AuthError("会话对应的用户不存在或已停用")
        return user

    # ------------------------------------------------------------------ roles

    @staticmethod
    def require_role(user: User, *allowed: Role) -> User:
        if user.role not in allowed:
            raise PermissionDeniedError(
                f"需要 {'/'.join(role.value for role in allowed)} 角色，"
                f"当前为 {user.role.value}"
            )
        return user


def cookie_settings(*, secure: bool) -> dict[str, Any]:
    """Session cookie attributes.

    ``httponly`` keeps the token out of JavaScript, so an XSS bug cannot read it.
    ``samesite=lax`` stops another site from riding the cookie on a POST.
    ``secure`` is on wherever TLS exists — it is off only for local HTTP, and
    hard-coding it on would silently break the development login instead of
    failing loudly.
    """
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": secure,
        "max_age": SESSION_MAX_AGE_SECONDS,
        "path": "/",
    }


def _now() -> float:
    return time.time()


__all__ = [
    "SESSION_MAX_AGE_SECONDS",
    "AuthError",
    "AuthService",
    "PermissionDeniedError",
    "Role",
    "User",
    "cookie_settings",
]
