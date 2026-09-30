"""FastAPI 应用配置与路径解析。"""

import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path


def is_frozen() -> bool:
    """判断当前是否为 PyInstaller 冻结运行。"""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """返回只读资源根目录。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """返回可写数据根目录。"""
    env_home = (os.environ.get("MNGXOPS_HOME") or "").strip()
    if env_home:
        path = Path(env_home).expanduser().resolve()
    elif is_frozen():
        path = Path(sys.executable).resolve().parent
    else:
        path = resource_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_or_create_secret_key(root: Path) -> str:
    """读取或创建 FastAPI cookie 签名密钥。"""
    env_value = (os.environ.get("MNGXOPS_SECRET_KEY") or "").strip()
    if env_value:
        return env_value
    key_file = root / ".fastapi_secret_key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(48)
    key_file.write_text(value, encoding="utf-8")
    return value


@dataclass(frozen=True)
class Settings:
    """FastAPI 运行时设置。"""

    app_name: str
    data_path: Path
    resource_path: Path
    database_path: Path
    template_path: Path
    static_path: Path
    secret_key: str
    session_cookie: str = "mngxops_session"
    session_max_age: int = 60 * 60 * 12


def get_settings() -> Settings:
    """构造 FastAPI 设置对象。"""
    data_path = data_dir()
    resource_path = resource_dir()
    return Settings(
        app_name="MngxOps",
        data_path=data_path,
        resource_path=resource_path,
        database_path=data_path / "db.sqlite3",
        template_path=resource_path / "fastops" / "templates",
        static_path=resource_path / "static",
        secret_key=_load_or_create_secret_key(data_path),
    )

