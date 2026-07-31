import os
import sys

from cryptography.fernet import Fernet


def _key_file_path():
    """凭证加密密钥路径：冻结时用数据目录，开发时用 utils/.fernet_key。"""
    if getattr(sys, "frozen", False):
        env_home = (os.environ.get("MNGXOPS_HOME") or "").strip()
        data = env_home if env_home else os.path.dirname(sys.executable)
        os.makedirs(data, exist_ok=True)
        return os.path.join(data, ".fernet_key")
    return os.path.join(os.path.dirname(__file__), ".fernet_key")


def _load_or_create_key():
    """加载或生成 Fernet 密钥。"""
    key_file = _key_file_path()
    if os.path.exists(key_file):
        with open(key_file, "rb") as f:
            return f.read()
    key = Fernet.generate_key()
    with open(key_file, "wb") as f:
        f.write(key)
    return key


_fernet = Fernet(_load_or_create_key())


def encrypt_value(plaintext):
    """加密明文字符串。"""
    if not plaintext:
        return ""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext):
    """解密密文字符串。"""
    if not ciphertext:
        return ""
    return _fernet.decrypt(ciphertext.encode()).decode()
