import os

from cryptography.fernet import Fernet

_KEY_FILE = os.path.join(os.path.dirname(__file__), ".fernet_key")


def _load_or_create_key():
    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, "rb") as f:
            return f.read()
    key = Fernet.generate_key()
    with open(_KEY_FILE, "wb") as f:
        f.write(key)
    return key


_fernet = Fernet(_load_or_create_key())


def encrypt_value(plaintext):
    if not plaintext:
        return ""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext):
    if not ciphertext:
        return ""
    return _fernet.decrypt(ciphertext.encode()).decode()
