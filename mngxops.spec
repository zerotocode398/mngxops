# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 规格：跨 Win / Linux amd64 / Linux arm64 共用，请在目标本机构建。"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None
ROOT = Path(SPECPATH).resolve()


def _tree_datas(rel_dir):
    """将相对目录整棵纳入 datas（保持包内相对路径）。"""
    src = ROOT / rel_dir
    if not src.is_dir():
        return []
    return [(str(src), rel_dir.replace("\\", "/"))]


datas = []
datas += _tree_datas("templates")

# 各 app 的模板 / 迁移 / 管理命令 / 静态（若有）
for app_dir in (ROOT / "apps").iterdir():
    if not app_dir.is_dir() or app_dir.name.startswith("."):
        continue
    for sub in ("templates", "migrations", "management", "static", "templatetags"):
        datas += _tree_datas(f"apps/{app_dir.name}/{sub}")

# Django 自带模板与静态（Admin 等）
datas += collect_data_files("django")

def _pkg_modules_under(rel_root):
    """收集目录下全部可导入模块名（保证 urls/views 等被打入包）。"""
    base = ROOT / rel_root
    names = []
    if not base.is_dir():
        return names
    for path in base.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        names.append(".".join(parts))
    return names


hiddenimports = []
hiddenimports += collect_submodules("apps")
hiddenimports += collect_submodules("utils")
hiddenimports += collect_submodules("ngxops")
hiddenimports += _pkg_modules_under("apps")
hiddenimports += _pkg_modules_under("utils")
hiddenimports += _pkg_modules_under("ngxops")
hiddenimports += [
    "waitress",
    "openpyxl",
    "paramiko",
    "cryptography",
    "bcrypt",
    "nacl",
    "cffi",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.users.templatetags.permission_tags",
    "apps.upgrade.templatetags.upgrade_filters",
    "apps.configs.templatetags.config_filters",
    "apps.settings.apps",
    "ngxops.urls",
    "ngxops.wsgi",
    "ngxops.runtime_paths",
]
# 去重并保持顺序
_seen = set()
hiddenimports = [m for m in hiddenimports if not (m in _seen or _seen.add(m))]

a = Analysis(
    [str(ROOT / "run_server.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 勿把开发库打进包；运行时使用 exe 旁 / MNGXOPS_HOME 的 db.sqlite3
a.datas = [
    entry
    for entry in a.datas
    if "db.sqlite3" not in str(entry[0]).replace("\\", "/").lower()
    and not str(entry[1]).replace("\\", "/").lower().endswith("db.sqlite3")
]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="mngxops",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
