"""FastAPI 认证安全工具。"""

import base64
import hashlib
import hmac
import json
import time
from typing import Optional

from .config import get_settings


def verify_django_password(raw_password: str, encoded: str) -> bool:
    """校验 Django pbkdf2_sha256 密码哈希。"""
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            raw_password.encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        )
        actual = base64.b64encode(dk).decode("ascii").strip()
        return hmac.compare_digest(actual, expected)
    except (AttributeError, TypeError, ValueError):
        return False


def _sign(payload: str) -> str:
    """对 session payload 生成 HMAC 签名。"""
    digest = hmac.new(
        get_settings().secret_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def create_session_token(user_id: int) -> str:
    """创建包含用户 ID 和过期时间的签名 cookie。"""
    settings = get_settings()
    payload = json.dumps(
        {"uid": user_id, "exp": int(time.time()) + settings.session_max_age},
        separators=(",", ":"),
    )
    body = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{body}.{_sign(body)}"


def read_session_token(token: str) -> Optional[int]:
    """读取并校验 session token，成功返回用户 ID。"""
    try:
        body, signature = token.split(".", 1)
        if not hmac.compare_digest(_sign(body), signature):
            return None
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if int(payload.get("exp") or 0) < int(time.time()):
            return None
        return int(payload["uid"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None

