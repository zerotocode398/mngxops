"""从环境变量或数据目录读取服务端签名密钥。"""

import os
import secrets
import time
from pathlib import Path
from typing import Optional


def load_or_create_secret_key(data_dir: Path, configured_key: Optional[str]) -> str:
    """优先使用环境密钥，否则安全创建并读取数据目录密钥。"""
    if configured_key:
        return configured_key

    path = data_dir / ".secret_key"
    try:
        with path.open("x", encoding="utf-8") as secret_file:
            key = secrets.token_urlsafe(50)
            secret_file.write(key)
    except FileExistsError:
        key = ""
        for _ in range(20):
            key = path.read_text(encoding="utf-8").strip()
            if key:
                break
            time.sleep(0.01)
        if not key:
            raise RuntimeError("服务密钥文件为空，请检查数据目录后重启")

    try:
        os.chmod(str(path), 0o600)
    except OSError:
        pass
    return key
