"""提供与 Django 默认 PBKDF2 摘要兼容的密码工具。"""

import base64
import hashlib
import hmac
import secrets
import string
from difflib import SequenceMatcher
from typing import Optional


PASSWORD_HASH_ITERATIONS = 600000
PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
_SALT_ALPHABET = string.ascii_letters + string.digits
_DUMMY_PASSWORD_HASH = (
    "pbkdf2_sha256$600000$ngxops_dummy_salt$"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)
_COMMON_PASSWORDS = frozenset(
    (
        "12345678",
        "123456789",
        "1234567890",
        "abc12345",
        "admin123",
        "changeme",
        "iloveyou",
        "letmein",
        "password",
        "password1",
        "password123",
        "qwerty123",
        "welcome1",
    )
)


def make_password(password: str) -> str:
    """按 Django 默认编码格式生成 PBKDF2-SHA256 密码摘要。"""
    salt = "".join(secrets.choice(_SALT_ALPHABET) for _ in range(22))
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("ascii"),
        PASSWORD_HASH_ITERATIONS,
    )
    encoded = base64.b64encode(digest).decode("ascii")
    return "{}${}${}${}".format(
        PASSWORD_HASH_ALGORITHM,
        PASSWORD_HASH_ITERATIONS,
        salt,
        encoded,
    )


def check_password(password: str, encoded: str) -> bool:
    """校验 Django PBKDF2-SHA256 格式的密码摘要。"""
    if len(password) > 4096:
        return False
    try:
        algorithm, raw_iterations, salt, raw_digest = encoded.split("$", 3)
        iterations = int(raw_iterations)
        expected = base64.b64decode(raw_digest.encode("ascii"), validate=True)
    except (AttributeError, UnicodeEncodeError, ValueError):
        return False
    if (
        algorithm != PASSWORD_HASH_ALGORITHM
        or not salt
        or not 1 <= iterations <= 2000000
        or len(expected) != hashlib.sha256().digest_size
    ):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    return hmac.compare_digest(actual, expected)


def burn_unknown_user_password_check(password: str) -> None:
    """对不存在的用户名执行同等摘要计算以减少登录耗时差异。"""
    check_password(password, _DUMMY_PASSWORD_HASH)


def validate_new_password(password: str, username: str) -> Optional[str]:
    """检查最短长度、用户名相似度、常见口令和纯数字口令。"""
    if len(password) > 4096:
        return "密码不能超过 4096 个字符。"
    normalized = password.casefold()
    similarity = SequenceMatcher(None, normalized, username.casefold())
    similar = bool(username) and (
        similarity.quick_ratio() >= 0.7 and similarity.ratio() >= 0.7
    )
    if len(password) < 8 or similar:
        return "密码不能与用户名过于相似，且长度不能少于 8 个字符。"
    if normalized in _COMMON_PASSWORDS:
        return "密码过于常见，请使用更难猜测的密码。"
    if password.isdigit():
        return "密码不能全部由数字组成。"
    return None
