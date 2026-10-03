"""读取服务配置，并定位只读资源与可写数据目录。"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple


_TRUE_VALUES = frozenset(("1", "true", "yes", "on"))
_FALSE_VALUES = frozenset(("0", "false", "no", "off"))
_LOG_LEVELS = frozenset(("critical", "error", "warning", "info", "debug", "trace"))


def is_frozen() -> bool:
    """判断程序是否运行在 PyInstaller 等冻结环境中。"""
    return bool(getattr(sys, "frozen", False))


def get_resource_dir() -> Path:
    """返回模板与静态文件所在的只读资源目录。"""
    if is_frozen():
        bundle_dir = getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)
        return Path(bundle_dir)
    return Path(__file__).resolve().parent.parent


def get_data_dir() -> Path:
    """返回由环境变量覆盖的可写数据目录，默认使用当前工作目录。"""
    configured_home = (_environment_value("NGXOPS_HOME") or "").strip()
    if configured_home:
        return Path(configured_home).expanduser().resolve()
    return Path.cwd().resolve()


def _environment_value(name: str) -> Optional[str]:
    """优先读取 NGXOPS 配置，并兼容旧的 MNGXOPS 环境变量。"""
    value = os.environ.get(name)
    if value is not None:
        return value
    if name.startswith("NGXOPS_"):
        legacy_name = "MNGXOPS_{}".format(name[len("NGXOPS_"):])
        return os.environ.get(legacy_name)
    return None


def _read_bool(name: str, default: bool) -> bool:
    """读取并校验布尔环境变量。"""
    raw_value = _environment_value(name)
    if raw_value is None or not raw_value.strip():
        return default

    normalized_value = raw_value.strip().lower()
    if normalized_value in _TRUE_VALUES:
        return True
    if normalized_value in _FALSE_VALUES:
        return False
    raise ValueError("{} 必须是 1/true/yes/on 或 0/false/no/off".format(name))


def _read_port(default: int) -> int:
    """读取并校验 HTTP 监听端口。"""
    raw_value = (_environment_value("NGXOPS_PORT") or str(default)).strip()
    try:
        port = int(raw_value)
    except ValueError as exc:
        raise ValueError("NGXOPS_PORT 必须是 1 到 65535 的整数") from exc
    if not 1 <= port <= 65535:
        raise ValueError("NGXOPS_PORT 必须是 1 到 65535 的整数")
    return port


def _read_positive_int(name: str, default: int, maximum: int) -> int:
    """读取指定范围内的正整数环境配置。"""
    raw_value = (_environment_value(name) or str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError("{} 必须是有效整数".format(name)) from exc
    if not 1 <= value <= maximum:
        raise ValueError("{} 必须在 1 到 {} 之间".format(name, maximum))
    return value


@dataclass(frozen=True)
class Settings:
    """保存进程级服务配置。"""

    app_name: str
    version: str
    host: str
    port: int
    debug: bool
    reload: bool
    log_level: str
    resource_dir: Path
    data_dir: Path
    secret_key: Optional[str] = field(repr=False)
    https_only: bool
    csrf_trusted_origins: Tuple[str, ...]
    release_backup_dir: str
    upgrade_package_max_size_mb: int

    @property
    def template_dir(self) -> Path:
        """返回 Jinja2 模板目录。"""
        return self.resource_dir / "templates"

    @property
    def static_dir(self) -> Path:
        """返回静态资源目录。"""
        return self.resource_dir / "static"

    @property
    def database_path(self) -> Path:
        """返回 SQLite 数据库文件路径。"""
        return self.data_dir / "db.sqlite3"

    @property
    def upgrade_package_dir(self) -> Path:
        """返回源码和第三方模块包的持久化目录。"""
        return self.data_dir / "nginx_packages"

    @property
    def log_dir(self) -> Path:
        """返回运行日志目录。"""
        return self.data_dir / "logs"


def get_settings() -> Settings:
    """从环境变量生成经过校验的服务配置。"""
    frozen = is_frozen()
    resource_dir = get_resource_dir()
    default_host = "0.0.0.0" if frozen else "127.0.0.1"
    default_port = 11993 if frozen else 8000
    debug_default = not frozen
    debug = _read_bool("NGXOPS_DEBUG", debug_default)
    log_level = (_environment_value("NGXOPS_LOG_LEVEL") or "").strip().lower()
    if not log_level:
        log_level = "debug" if debug else "info"
    if log_level not in _LOG_LEVELS:
        raise ValueError("NGXOPS_LOG_LEVEL 不是有效的日志级别")

    return Settings(
        app_name="ngxops",
        version="0.1.0",
        host=(_environment_value("NGXOPS_HOST") or default_host).strip() or default_host,
        port=_read_port(default_port),
        debug=debug,
        reload=_read_bool("NGXOPS_RELOAD", debug_default),
        log_level=log_level,
        resource_dir=resource_dir,
        data_dir=get_data_dir(),
        secret_key=(_environment_value("NGXOPS_SECRET_KEY") or "").strip() or None,
        https_only=_read_bool("NGXOPS_HTTPS", False),
        csrf_trusted_origins=tuple(
            item.strip()
            for item in (
                _environment_value("NGXOPS_CSRF_TRUSTED_ORIGINS") or ""
            ).split(",")
            if item.strip()
        ),
        release_backup_dir=(
            _environment_value("NGXOPS_RELEASE_BACKUP_DIR") or ""
        ).strip() or "/opt/app/mascloud/ansible/mngxops",
        upgrade_package_max_size_mb=_read_positive_int(
            "NGXOPS_UPGRADE_PACKAGE_MAX_SIZE_MB", 20, 1024
        ),
    )
