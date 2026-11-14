"""Admin panel login: one password, a short session cookie, and a lock-out after wrong guesses.

The password is ADMIN_PASSWORD in config.py (default 1234, change it with the environment variable ACCIALERT_ADMIN_PASSWORD).
A session lives in memory, so restarting the server logs the admin out.
"""

import hmac
import secrets
import threading
import time
from typing import Dict, Optional, Tuple

try:
    from . import config
except ImportError:
    import config

_lock = threading.Lock()
_sessions: Dict[str, float] = {}          # token -> expiry
_failures: Dict[str, list] = {}           # client address -> times of recent wrong passwords


def check_password(given) -> bool:
    return hmac.compare_digest(str(given or "").encode(), str(config.ADMIN_PASSWORD).encode())


def login(password, client: str, now: Optional[float] = None) -> Tuple[Optional[str], Optional[str], int]:
    """(token, error message, HTTP status). Too many wrong passwords from one address lock it for a minute."""
    now = time.time() if now is None else now
    with _lock:
        recent = [t for t in _failures.get(client, []) if now - t < config.ADMIN_LOCKOUT_S]
        _failures[client] = recent
        if len(recent) >= config.ADMIN_MAX_WRONG:
            wait = int(config.ADMIN_LOCKOUT_S - (now - recent[0])) + 1
            return None, f"Too many wrong passwords. Try again in {wait} seconds.", 429
        if not check_password(password):
            recent.append(now)
            return None, "Wrong password.", 401
        _failures.pop(client, None)
        for token in [t for t, exp in _sessions.items() if exp <= now]:
            _sessions.pop(token, None)
        token = secrets.token_urlsafe(24)
        _sessions[token] = now + config.ADMIN_SESSION_TTL_S
        return token, None, 200


def is_valid(token, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    with _lock:
        exp = _sessions.get(token) if isinstance(token, str) else None
        if exp is None:
            return False
        if exp <= now:
            _sessions.pop(token, None)
            return False
        return True


def logout(token) -> None:
    with _lock:
        _sessions.pop(token, None)


def reset_for_tests() -> None:
    with _lock:
        _sessions.clear()
        _failures.clear()
