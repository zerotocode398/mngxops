#!/usr/bin/env python
"""MngxOps 统一入口：默认启动 Web；支持 migrate / createsuperuser 等管理命令。"""

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

RUN_ALIASES = frozenset({"run", "runserver"})
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 1988
ACCESS_LOGGER = logging.getLogger("mngxops.access")


def _setup_django_env():
    """设置 Django 配置模块。"""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ngxops.settings")


def _print_usage(stream=None):
    """打印命令行用法。"""
    if stream is None:
        stream = sys.stdout
    print(
        "用法:\n"
        "  mngxops                         启动 Web（默认 0.0.0.0:1988）\n"
        "  mngxops run|runserver [addr]    启动 Web；addr 为端口或 ip:port\n"
        "  mngxops migrate                 初始化 / 升级数据库\n"
        "  mngxops createsuperuser         创建管理员\n"
        "环境变量: MNGXOPS_HOME MNGXOPS_DEBUG MNGXOPS_SECRET_KEY",
        file=stream,
    )


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


def _is_uninitialized_db(exc):
    """判断是否为未 migrate 导致的缺表错误。"""
    msg = str(exc).lower()
    return "no such table" in msg or "does not exist" in msg


def _run_manage(argv):
    """转发到 Django 管理命令；缺表时给出友好提示。"""
    from django.core.management import execute_from_command_line
    from django.db.utils import OperationalError, ProgrammingError

    try:
        execute_from_command_line(["mngxops", *argv])
    except (OperationalError, ProgrammingError) as exc:
        if _is_uninitialized_db(exc):
            print("数据库尚未初始化，请先执行: mngxops migrate", file=sys.stderr)
            sys.exit(1)
        raise


def _parse_bind_addr(argv):
    """解析 run/runserver 地址：无参默认 0.0.0.0:1988，或端口 / ip:port。"""
    if not argv:
        return DEFAULT_HOST, DEFAULT_PORT
    if len(argv) != 1 or argv[0].startswith("-"):
        print("不支持的启动参数。请使用: mngxops runserver 或 mngxops runserver ip:port", file=sys.stderr)
        _print_usage(sys.stderr)
        sys.exit(2)

    token = argv[0]
    if token.isdigit():
        return DEFAULT_HOST, int(token)
    if ":" in token:
        host, port_text = token.rsplit(":", 1)
        if not port_text.isdigit():
            print("端口无效: {}".format(token), file=sys.stderr)
            sys.exit(2)
        return host or DEFAULT_HOST, int(port_text)

    print("地址格式无效，请使用端口或 ip:port，例如 1988 或 127.0.0.1:8000", file=sys.stderr)
    sys.exit(2)


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
                path = "{}?{}".format(path, query)
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


def _run_web(argv):
    """使用 Waitress 启动 WSGI 服务（不执行 migrate）。"""
    host, port = _parse_bind_addr(argv)
    _configure_logging()
    _setup_django_env()
    import django

    django.setup()

    from waitress import serve
    from ngxops.wsgi import application

    print("MngxOps 已启动: http://{}:{}/".format(host, port))
    print("管理命令示例: mngxops migrate | mngxops createsuperuser")
    serve(_AccessLogMiddleware(application), host=host, port=port)


def main(argv=None):
    """入口：无参/run/runserver 启服务，白名单管理命令转发 Django。"""
    if argv is None:
        argv = sys.argv[1:]

    _setup_django_env()

    if not argv or argv[0] in RUN_ALIASES:
        web_argv = argv[1:] if argv and argv[0] in RUN_ALIASES else argv
        _run_web(web_argv)
        return

    cmd = argv[0]
    if cmd in ("-h", "--help", "help"):
        _print_usage()
        return

    if cmd not in ALLOWED_MANAGE_COMMANDS:
        print(
            "不支持的命令: {}\n允许: {}，或 run/runserver / 无参启动服务".format(
                cmd, ", ".join(sorted(ALLOWED_MANAGE_COMMANDS))
            ),
            file=sys.stderr,
        )
        sys.exit(2)

    _run_manage(argv)


if __name__ == "__main__":
    main()
