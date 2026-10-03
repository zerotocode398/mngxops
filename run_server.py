"""启动 ngxops 的 Uvicorn 服务。"""

import argparse
from typing import Optional, Sequence

import uvicorn

from ngxops.config import get_settings
from ngxops.cli_format import NonNullDefaultsHelpFormatter
from ngxops.logging_setup import configure_logging


def main(argv: Optional[Sequence[str]] = None) -> None:
    """解析启动参数并运行 ASGI 服务。"""
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="启动 ngxops Web 服务",
        formatter_class=NonNullDefaultsHelpFormatter,
        epilog="数据目录默认为 NGXOPS_HOME；未设置时使用当前工作目录（./）。",
    )
    parser.add_argument("--host", default=settings.host, help="监听地址")
    parser.add_argument("--port", type=int, default=settings.port, help="监听端口")
    parser.add_argument("--log-level", default=settings.log_level, help="日志级别")
    reload_group = parser.add_mutually_exclusive_group()
    reload_group.add_argument(
        "--reload",
        dest="reload",
        action="store_true",
        help="启用自动重载（配置默认值：{}）".format(
            "开启" if settings.reload else "关闭"
        ),
    )
    reload_group.add_argument(
        "--no-reload",
        dest="reload",
        action="store_false",
        help="关闭自动重载（配置默认值：{}）".format(
            "开启" if settings.reload else "关闭"
        ),
    )
    parser.set_defaults(reload=None)
    args = parser.parse_args(argv)
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
