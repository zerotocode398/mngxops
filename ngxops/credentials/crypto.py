"""使用独立 Fernet 密钥保护 SSH 密码和私钥。"""

import os
import time
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


class CredentialDecryptionError(ValueError):
    """表示凭证明文无法使用当前数据目录密钥解密。"""


def load_or_create_fernet_key(data_dir: Path) -> bytes:
    """读取或原子创建凭证加密密钥文件。"""
    path = data_dir / ".fernet_key"
    try:
        with path.open("xb") as key_file:
            key = Fernet.generate_key()
            key_file.write(key)
    except FileExistsError:
        key = b""
        for _ in range(20):
            key = path.read_bytes().strip()
            if key:
                break
            time.sleep(0.01)
        if not key:
            raise RuntimeError("凭证加密密钥文件为空，请检查数据目录后重启")

    try:
        Fernet(key)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("凭证加密密钥文件无效，请恢复正确备份后重启") from exc
    try:
        os.chmod(str(path), 0o600)
    except OSError:
        pass
    return key


def encrypt_secret(key: bytes, plaintext: Optional[str]) -> str:
    """将非空凭证值加密为 Fernet 文本。"""
    if not plaintext:
        return ""
    return Fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(key: bytes, ciphertext: Optional[str]) -> str:
    """使用当前数据目录密钥解密凭证值。"""
    if not ciphertext:
        return ""
    try:
        return Fernet(key).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise CredentialDecryptionError("凭证解密失败，请确认加密密钥文件未更换") from exc
