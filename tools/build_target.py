"""提供单文件构建目标的平台名称映射。"""

import platform
from typing import Optional, Tuple


def normalize_target(
    system: Optional[str] = None,
    machine: Optional[str] = None,
) -> Tuple[str, str]:
    """将当前或传入的平台信息转换为发行目标标识。"""
    normalized_system = (system or platform.system()).strip().lower()
    normalized_machine = (machine or platform.machine()).strip().lower()

    if normalized_machine in ("x86_64", "amd64", "x64"):
        normalized_machine = "amd64"
    elif normalized_machine in ("aarch64", "arm64"):
        normalized_machine = "arm64"

    if normalized_system in ("windows", "win32", "win64"):
        if normalized_machine == "amd64":
            return "windows", "ngxops-win10-amd"
        raise ValueError("Windows builds are supported only on amd64 hosts.")
    if normalized_system == "linux":
        if normalized_machine in ("amd64", "arm64"):
            return "linux", "ngxops-linux-{}".format(normalized_machine)
        raise ValueError("Linux builds are supported only on amd64 and arm64 hosts.")
    if normalized_system in ("darwin", "macos"):
        if normalized_machine in ("amd64", "arm64"):
            return "macos", "ngxops-macos-{}".format(normalized_machine)
        raise ValueError("macOS builds are supported only on amd64 and arm64 hosts.")
    raise ValueError(
        "Builds are supported on Windows amd64, Linux amd64/arm64, "
        "and macOS amd64/arm64."
    )


def target_filename(
    system: Optional[str] = None,
    machine: Optional[str] = None,
) -> str:
    """返回当前或传入构建平台对应的可执行文件名。"""
    normalized_system, target_name = normalize_target(system, machine)
    if normalized_system == "windows":
        return target_name + ".exe"
    return target_name


def target_name(
    system: Optional[str] = None,
    machine: Optional[str] = None,
) -> str:
    """返回不带平台扩展名的 PyInstaller 目标名称。"""
    return normalize_target(system, machine)[1]
