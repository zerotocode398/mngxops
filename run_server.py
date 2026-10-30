"""启动 ngxops 的 Uvicorn 服务。"""

import argparse
import os
from typing import Optional, Sequence

import uvicorn

from ngxops.config import get_settings
from ngxops.cli_format import NonNullDefaultsHelpFormatter, parse_bool_argument
from ngxops.logging_setup import configure_logging


def main(argv: Optional[Sequence[str]] = None) -> None:
    """解析启动参数并运行 ASGI 服务。"""
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="启动 ngxops Web 服务",
        formatter_class=NonNullDefaultsHelpFormatter,
        epilog=(
            "数据目录默认为 NGXOPS_HOME；未设置时使用当前工作目录（./）。"
            "可用 --home PATH 覆盖。"
        ),
    )
    parser.add_argument("--home", help="数据存储目录")
    parser.add_argument("--host", default=settings.host, help="监听地址")
    parser.add_argument("--port", type=int, default=settings.port, help="监听端口")
    parser.add_argument("--log-level", default=settings.log_level, help="日志级别")
    parser.add_argument(
        "--reload",
        nargs="?",
        const=True,
        default=None,
        type=parse_bool_argument,
        metavar="true|false",
        help="自动重载；无参数时开启，未指定时配置默认值为{}。".format(
            "开启" if settings.reload else "关闭"
        ),
    )
    args = parser.parse_args(argv)
    if args.home:
        os.environ["NGXOPS_HOME"] = args.home
        settings = get_settings()
    reload_enabled = settings.reload if args.reload is None else args.reload

    if not 1 <= args.port <= 65535:
        parser.error("端口范围必须为 1 到 65535")

    configure_logging(settings.data_dir, settings.log_level)
    uvicorn.run(
        "ngxops.asgi:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        reload=reload_enabled,
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
