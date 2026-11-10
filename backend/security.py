"""Local administrator login, expiring sessions, and same-origin CSRF checks."""
import hashlib
import hmac
import secrets
import threading
import time
from urllib.parse import urlsplit
from fastapi import HTTPException, Request

COOKIE = 'airspace_session'


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f'scrypt${salt.hex()}${digest.hex()}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        kind, salt, digest = encoded.split('$')
        if kind != 'scrypt' or len(salt) != 32 or len(digest) != 64:
            return False
        actual = hash_password(password, bytes.fromhex(salt)).split('$')[2]
        return hmac.compare_digest(actual, digest)
    except (ValueError, TypeError):
        return False


class Sessions:
    def __init__(self):
        self._sessions = {}
        self._attempts = {}
        self._lock = threading.Lock()

    def login(self, request: Request, password: str, password_hash: str):
        host = request.client.host if request.client else 'local'
        now = time.monotonic()
        with self._lock:
            self._attempts = {k: v for k, v in self._attempts.items() if v[1] > now - 300}
            count, last = self._attempts.get(host, (0, now))
            if count >= 8:
                raise HTTPException(429, 'Too many attempts. Try again in five minutes.')
            if not password_hash:
                raise HTTPException(503, 'Administrator password is not configured. Run the installer.')
            if not verify_password(password, password_hash):
                self._attempts[host] = (count + 1, now)
                raise HTTPException(401, 'Incorrect administrator password.')
            self._attempts.pop(host, None)
            self._sessions = {k: v for k, v in self._sessions.items() if v[1] > now}
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self._sessions[token] = (csrf, now + 8 * 3600)
            return token, csrf

    def get(self, request: Request):
        with self._lock:
            item = self._sessions.get(request.cookies.get(COOKIE, ''))
            if item and item[1] > time.monotonic():
                return item[0]
        return None

    def require(self, request: Request):
        csrf = self.get(request)
        if not csrf:
            raise HTTPException(401, 'Administrator sign-in required.')
        if not hmac.compare_digest(request.headers.get('x-csrf-token', ''), csrf):
            raise HTTPException(403, 'Invalid CSRF token. Refresh and sign in again.')
        same_origin(request)

    def logout(self, request):
        with self._lock:
            self._sessions.pop(request.cookies.get(COOKIE, ''), None)


def same_origin(request: Request):
    origin = request.headers.get('origin')
    # Non-browser CLI requests have no Origin and still need cookie + CSRF token.
    if origin:
        parsed = urlsplit(origin)
        if parsed.netloc.lower() != request.headers.get('host', '').lower() or parsed.scheme != request.url.scheme:
            raise HTTPException(403, 'Cross-origin changes are forbidden.')
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Cross-site changes are forbidden.')
