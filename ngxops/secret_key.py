"""解析或生成 Django SECRET_KEY（环境变量优先，否则写入数据目录）。"""
import os
import secrets
from pathlib import Path


def load_or_create_secret_key(data_dir):
    """优先 MNGXOPS_SECRET_KEY，否则读写 DATA_DIR/.secret_key。"""
    env_key = (os.environ.get("MNGXOPS_SECRET_KEY") or "").strip()
    if env_key:
        return env_key
    path = Path(data_dir) / ".secret_key"
    if path.is_file():
        stored = path.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    key = secrets.token_urlsafe(50)
    path.write_text(key, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def parse_csv_env(name):
    """将逗号分隔环境变量拆成非空列表。"""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def env_flag_true(name):
    """环境变量是否为开启（1/true/yes/on）。"""
    return (os.environ.get(name) or "").strip().lower() in (
        "1", "true", "yes", "on",
    )
