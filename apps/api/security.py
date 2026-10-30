import hashlib
import hmac
import secrets
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from apps.api.db import get_db
from apps.api.models import LoginSession, User


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"{salt}:{digest}"


def check_password(password: str, stored: str) -> bool:
    return hmac.compare_digest(hash_password(password, stored.split(":")[0]), stored)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def current_actor(request: Request, db: Session = Depends(get_db)) -> User:
    session = db.get(LoginSession, token_hash(request.cookies.get("tracelens_session", "")))
    if not session or session.expires_at <= int(time.time()):
        raise HTTPException(401, "Vui lòng đăng nhập")
    actor = db.get(User, session.user_id)
    if not actor or not actor.active:
        raise HTTPException(401, "Phiên không còn hiệu lực")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(csrf, session.csrf_token):
            raise HTTPException(403, "CSRF token không hợp lệ")
    request.state.login_session = session
    return actor
