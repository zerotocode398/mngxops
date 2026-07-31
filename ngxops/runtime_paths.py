"""解析冻结运行与源码运行下的资源目录、可写数据目录。"""

import os
import sys
from pathlib import Path


def is_frozen():
    """是否处于 PyInstaller 等冻结环境。"""
    return bool(getattr(sys, "frozen", False))


def resource_dir():
    """只读资源根目录（模板等；冻结时为 _MEIPASS）。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def data_dir():
    """可写数据根目录（数据库、media、密钥；冻结时为 exe 旁或 MNGXOPS_HOME）。"""
    env_home = (os.environ.get("MNGXOPS_HOME") or "").strip()
    if env_home:
        path = Path(env_home).expanduser().resolve()
    elif is_frozen():
        path = Path(sys.executable).resolve().parent
    else:
        path = Path(__file__).resolve().parent.parent
    path.mkdir(parents=True, exist_ok=True)
    return path
