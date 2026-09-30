#!/usr/bin/env python
"""MngxOps FastAPI 统一启动入口。"""

import logging
import sys


RUN_ALIASES = frozenset({"run", "runserver", "fastapi", "run-fastapi"})
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 11993


def _print_usage(stream=None):
    """打印命令行帮助。"""
    if stream is None:
        stream = sys.stdout
    print(
        "\n".join(
            [
                "Usage:",
                "  mngxops                         Start FastAPI server (0.0.0.0:11993).",
                "  mngxops run|runserver [addr]    Start FastAPI server at specified address.",
                "  mngxops fastapi [addr]          Same as run.",
                "                                  addr: port or ip:port.",
                "Environment variables:",
                "  MNGXOPS_HOME                    Data directory, defaults to current directory.",
                "  MNGXOPS_SECRET_KEY              Cookie signing key, auto-generated if unset.",
            ]
        ),
        file=stream,
    )


def _configure_logging():
    """配置控制台日志。"""
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


def _parse_bind_addr(argv):
    """解析启动地址：无参默认 0.0.0.0:11993，或端口 / ip:port。"""
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

    print("地址格式无效，请使用端口或 ip:port，例如 :11993 或 127.0.0.1:8000", file=sys.stderr)
    sys.exit(2)


def _run_web(argv):
    """使用 Uvicorn 启动 FastAPI 服务。"""
    host, port = _parse_bind_addr(argv)
    _configure_logging()
    import uvicorn

    print("MngxOps FastAPI 已启动: http://{}:{}/fastapi".format(host, port))
    print("接口文档: http://{}:{}/docs".format(host, port))
    uvicorn.run("fastops.main:app", host=host, port=port)


def main(argv=None):
    """入口：无参/run/runserver/fastapi 启动 FastAPI。"""
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        _run_web([])
        return
    if argv[0] in ("-h", "--help", "help"):
        _print_usage()
        return
    if argv[0] in RUN_ALIASES:
        _run_web(argv[1:])
        return
    print("不支持的命令: {}".format(argv[0]), file=sys.stderr)
    _print_usage(sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()

