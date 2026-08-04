#!/usr/bin/env python
"""MngxOps 统一入口：默认启动 Web；支持 migrate / createsuperuser 等管理命令。"""

import argparse
import logging
import os
import sys
from datetime import datetime


# 允许的 Django 管理命令（与交付约定对齐，避免暴露任意 manage 面）
ALLOWED_MANAGE_COMMANDS = frozenset(
    {
        "migrate",
        "createsuperuser",
        "showmigrations",
        "collectstatic",
    }
)

SERVE_ALIASES = frozenset({"serve", "run", "runserver"})
ACCESS_LOGGER = logging.getLogger("mngxops.access")


def _setup_django_env():
    """设置 Django 配置模块。"""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ngxops.settings")


def _configure_logging():
    """配置控制台 INFO 日志（Waitress + 访问行）；兼容 Python 3.6.8 / 3.9.6。"""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def _run_manage(argv):
    """转发到 Django 管理命令。"""
    from django.core.management import execute_from_command_line

    execute_from_command_line(["mngxops", *argv])


def _parse_serve_args(argv):
    """解析 serve 专用参数。"""
    parser = argparse.ArgumentParser(prog="mngxops", description="MngxOps 服务入口")
    parser.add_argument("--host", default=os.environ.get("MNGXOPS_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MNGXOPS_PORT", "1988")),
    )
    parser.add_argument(
        "--no-migrate",
        action="store_true",
        help="启动前不自动执行 migrate --noinput",
    )
    parser.add_argument(
        "--no-access-log",
        action="store_true",
        help="关闭控制台 HTTP 访问日志",
    )
    return parser.parse_args(argv)


def _auto_migrate():
    """启动前静默迁移数据库。"""
    from django.core.management import call_command

    call_command("migrate", interactive=False, verbosity=1)


class _AccessLogMiddleware:
    """WSGI 访问日志：请求结束后打印一行。"""

    def __init__(self, app):
        """包装下游 WSGI 应用。"""
        self.app = app

    def __call__(self, environ, start_response):
        """记录方法、路径、状态码后转发。"""
        status_holder = {"code": "-"}

        def _start_response(status, headers, exc_info=None):
            """捕获响应状态码。"""
            status_holder["code"] = (status or "-").split(" ", 1)[0]
            return start_response(status, headers, exc_info)

        try:
            return self.app(environ, _start_response)
        finally:
            remote = environ.get("REMOTE_ADDR") or "-"
            method = environ.get("REQUEST_METHOD") or "-"
            path = environ.get("PATH_INFO") or "/"
            query = environ.get("QUERY_STRING") or ""
            if query:
                path = f"{path}?{query}"
            protocol = environ.get("SERVER_PROTOCOL") or "HTTP/1.0"
            stamp = datetime.now().strftime("%d/%b/%Y:%H:%M:%S")
            ACCESS_LOGGER.info(
                '%s - - [%s] "%s %s %s" %s',
                remote,
                stamp,
                method,
                path,
                protocol,
                status_holder["code"],
            )


def _run_serve(argv):
    """使用 Waitress 启动 WSGI 服务。"""
    args = _parse_serve_args(argv)
    _configure_logging()
    _setup_django_env()
    import django

    django.setup()
    if not args.no_migrate:
        print("执行数据库迁移 (migrate --noinput)...")
        _auto_migrate()

    from waitress import serve
    from ngxops.wsgi import application

    app = application if args.no_access_log else _AccessLogMiddleware(application)
    print(f"MngxOps 已启动: http://{args.host}:{args.port}/")
    print("管理命令示例: mngxops migrate | mngxops createsuperuser")
    if not args.no_access_log:
        print("访问日志已开启（可用 --no-access-log 关闭）")
    serve(app, host=args.host, port=args.port)


def main(argv=None):
    """入口：无参/serve 启服务，白名单管理命令转发 Django。"""
    if argv is None:
        argv = sys.argv[1:]

    _setup_django_env()

    if not argv or argv[0] in SERVE_ALIASES:
        serve_argv = argv[1:] if argv and argv[0] in SERVE_ALIASES else argv
        # 兼容误传 runserver 的旧习惯：mngxops runserver 0.0.0.0:8000
        if serve_argv and ":" in serve_argv[0] and not serve_argv[0].startswith("-"):
            host_port = serve_argv[0]
            serve_argv = serve_argv[1:]
            if ":" in host_port:
                host, port = host_port.rsplit(":", 1)
                serve_argv = ["--host", host or "0.0.0.0", "--port", port, *serve_argv]
        _run_serve(serve_argv)
        return

    cmd = argv[0]
    if cmd in ("-h", "--help", "help"):
        print(
            "用法:\n"
            "  mngxops                 启动 Web 服务（默认 0.0.0.0:1988）\n"
            "  mngxops serve|run|runserver [--host H] [--port P] [--no-migrate] [--no-access-log]\n"
            "  mngxops migrate\n"
            "  mngxops createsuperuser\n"
            "环境变量: MNGXOPS_HOME MNGXOPS_HOST MNGXOPS_PORT MNGXOPS_DEBUG MNGXOPS_SECRET_KEY"
        )
        return

    if cmd not in ALLOWED_MANAGE_COMMANDS:
        print(
            f"不支持的命令: {cmd}\n"
            f"允许: {', '.join(sorted(ALLOWED_MANAGE_COMMANDS))}，"
            f"或 serve/run/runserver / 无参启动服务",
            file=sys.stderr,
        )
        sys.exit(2)

    _run_manage(argv)


if __name__ == "__main__":
    main()
