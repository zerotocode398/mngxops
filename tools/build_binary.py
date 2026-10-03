"""为当前操作系统和 CPU 架构构建 ngxops 单文件程序。"""

import argparse
import subprocess
import sys
from pathlib import Path

from build_target import target_filename


def main() -> int:
    """显示构建目标或调用 PyInstaller 生成发行文件。"""
    root_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        prog="build-binary",
        description="Build the ngxops one-file executable for this host.",
    )
    parser.add_argument(
        "--print-target",
        action="store_true",
        help="Print the output filename without building.",
    )
    arguments = parser.parse_args()

    try:
        output_name = target_filename()
    except ValueError as exc:
        parser.error(str(exc))
    if arguments.print_target:
        print(output_name)
        return 0

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        str(root_dir / "ngxops.spec"),
    ]
    print("Building {}".format(output_name))
    subprocess.run(command, cwd=str(root_dir), check=True)
    print("Build complete: {}".format(root_dir / "dist" / output_name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
