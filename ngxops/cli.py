"""提供源码与单文件程序共用的英文命令行界面。"""

import argparse
import getpass
import logging
import os
import sys
from typing import Any, Optional, Sequence

import uvicorn

from ngxops.accounts.management import (
    create_first_superuser,
    reset_superuser_password,
    validate_username,
)
from ngxops.accounts.passwords import validate_new_password
from ngxops.config import Settings, get_settings
from ngxops.cli_format import NonNullDefaultsHelpFormatter
from ngxops.database.connection import create_database
from ngxops.database.migration_runner import upgrade_database
from ngxops.logging_setup import configure_logging, log_exception


logger = logging.getLogger("ngxops.cli")


def build_parser() -> argparse.ArgumentParser:
    """创建包含服务、数据库和管理员操作的命令解析器。"""
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="ngxops",
        description="Nginx multi-node operations platform.",
        formatter_class=NonNullDefaultsHelpFormatter,
        epilog=(
            "Data directory defaults to NGXOPS_HOME or ./ (the current working "
            "directory). Use --home PATH before the command to override it. "
            "Use 'ngxops COMMAND --help' for command options."
        ),
    )
    parser.add_argument(
        "--home",
        help="Writable data directory (default: NGXOPS_HOME or ./ when unset).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    serve_parser = _add_command_parser(
        commands,
        "serve",
        help="Run the web server in the foreground.",
    )
    _add_server_options(serve_parser, settings, allow_reload=True)

    start_parser = _add_command_parser(
        commands,
        "start",
        help="Start the web server in the background.",
    )
    _add_server_options(start_parser, settings, allow_reload=False)

    stop_parser = _add_command_parser(
        commands,
        "stop",
        help="Stop the managed web server.",
    )
    stop_parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="Seconds to wait for graceful shutdown before forcing exit.",
    )

    _add_command_parser(
        commands,
        "status",
        help="Show managed web server status.",
    )

    database_parser = _add_command_parser(
        commands,
        "database",
        aliases=["db"],
        help="Initialize or upgrade the SQLite database.",
    )
    database_commands = database_parser.add_subparsers(
        dest="database_action",
        required=True,
    )
    _add_command_parser(
        database_commands,
        "init",
        help="Create the database and apply all migrations.",
    )
    _add_command_parser(
        database_commands,
        "upgrade",
        help="Apply migrations not yet installed.",
    )

    admin_parser = _add_command_parser(
        commands,
        "admin",
        help="Manage the local superuser account.",
    )
    admin_commands = admin_parser.add_subparsers(dest="admin_action", required=True)
    create_parser = _add_command_parser(
        admin_commands,
        "create",
        help="Create the first superuser (username defaults to admin).",
    )
    create_parser.add_argument(
        "username",
        nargs="?",
        default="admin",
        help="Login username.",
    )
    reset_parser = _add_command_parser(
        admin_commands,
        "reset",
        help="Reset a superuser password (username defaults to admin) and revoke its existing sessions.",
    )
    reset_parser.add_argument(
        "username",
        nargs="?",
        default="admin",
        help="Superuser login name.",
    )

    return parser


def _add_command_parser(
    subparsers: Any,
    *args: Any,
    **kwargs: Any
) -> argparse.ArgumentParser:
    """创建显示参数默认值并提示数据目录的子命令解析器。"""
    kwargs.setdefault("formatter_class", NonNullDefaultsHelpFormatter)
    kwargs.setdefault(
        "epilog",
        "Data directory defaults to NGXOPS_HOME or ./ (the current working "
        "directory). Override with --home PATH before the command.",
    )
    return subparsers.add_parser(*args, **kwargs)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """解析命令、配置日志并执行对应维护或服务操作。"""
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    if raw_arguments and raw_arguments[0] == "_managed-serve":
        return _run_managed_command(raw_arguments[1:])
    parser = build_parser()
    arguments = parser.parse_args(raw_arguments)
    if arguments.home:
        os.environ["NGXOPS_HOME"] = arguments.home
    settings = get_settings()
    log_name = "ngxops.log" if arguments.command == "serve" else "ngxops-cli.log"
    configure_logging(settings.data_dir, settings.log_level, log_name)

    try:
        return _dispatch(arguments, settings)
    except ValueError as exc:
        logger.warning(
            "cli_action command=%s result=failed username=%s "
            "exception_type=%s reason=%s",
            arguments.command,
            getattr(arguments, "username", ""),
            type(exc).__name__,
            str(exc),
        )
        print("Error: {}".format(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        log_exception(
            logger,
            "CLI command failed",
            exc,
            "command={}".format(arguments.command),
        )
        print(
            "The command failed. Review the runtime log for details.",
            file=sys.stderr,
        )
        return 1


def _dispatch(arguments: argparse.Namespace, settings: Settings) -> int:
    """根据解析结果执行唯一一项命令操作。"""
    if arguments.command == "serve":
        return _serve(settings, arguments)
    if arguments.command == "start":
        from ngxops.service_manager import start_service

        host = arguments.host or settings.host
        port = arguments.port or settings.port
        pid = start_service(
            settings,
            host,
            _validate_port(port),
            arguments.log_level or settings.log_level,
        )
        print("Server started (PID {}).".format(pid))
        return 0
    if arguments.command == "stop":
        from ngxops.service_manager import stop_service

        if arguments.timeout < 1 or arguments.timeout > 300:
            raise ValueError("Timeout must be between 1 and 300 seconds.")
        if stop_service(settings.data_dir, arguments.timeout):
            print("Server stopped.")
        else:
            print("Server is not running.")
        return 0
    if arguments.command == "status":
        from ngxops.service_manager import service_status

        record = service_status(settings.data_dir)
        if record is None:
            print("Server is stopped.")
            return 1
        print(
            "Server is running (PID {}, http://{}:{}/).".format(
                record["pid"],
                record["host"],
                record["port"],
            )
        )
        return 0
    if arguments.command in ("database", "db"):
        return _upgrade_database(settings, arguments.database_action)
    if arguments.command == "admin":
        return _manage_admin(settings, arguments.admin_action, arguments.username)
    raise ValueError("Unsupported command.")


def _run_managed_command(raw_arguments: Sequence[str]) -> int:
    """解析私有后台启动参数并运行受管服务。"""
    parser = argparse.ArgumentParser(add_help=False, prog="ngxops-service")
    parser.add_argument("--service-token", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--log-level", required=True)
    arguments = parser.parse_args(raw_arguments)
    settings = get_settings()
    configure_logging(settings.data_dir, settings.log_level, "ngxops.log")
    try:
        from ngxops.service_manager import run_managed_server

        run_managed_server(
            settings.data_dir,
            arguments.service_token,
            arguments.host,
            _validate_port(arguments.port),
            arguments.log_level,
        )
        return 0
    except Exception as exc:
        log_exception(
            logger,
            "Managed web server failed",
            exc,
            "command=start",
        )
        return 1


def _add_server_options(
    parser: argparse.ArgumentParser,
    settings: Settings,
    allow_reload: bool,
) -> None:
    """为服务启动命令添加通用监听选项。"""
    parser.add_argument(
        "--host",
        default=settings.host,
        help="Listen address.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.port,
        help="Listen port.",
    )
    parser.add_argument(
        "--log-level",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        default=settings.log_level,
        help="Uvicorn log level.",
    )
    if allow_reload:
        reload_options = parser.add_mutually_exclusive_group()
        reload_options.add_argument(
            "--reload",
            dest="reload",
            action="store_true",
            help="Enable automatic reload (configured default: {}).".format(
                "enabled" if settings.reload else "disabled"
            ),
        )
        reload_options.add_argument(
            "--no-reload",
            dest="reload",
            action="store_false",
            help="Disable automatic reload (configured default: {}).".format(
                "enabled" if settings.reload else "disabled"
            ),
        )
        parser.set_defaults(reload=None)


def _serve(settings: Settings, arguments: argparse.Namespace) -> int:
    """在当前控制台运行单 worker Uvicorn。"""
    host = arguments.host or settings.host
    port = _validate_port(arguments.port or settings.port)
    reload_enabled = settings.reload if arguments.reload is None else arguments.reload
    logger.info(
        "service_action action=serve host=%s port=%s reload=%s",
        host,
        port,
        reload_enabled,
    )
    uvicorn.run(
        "ngxops.asgi:app",
        host=host,
        port=port,
        log_level=arguments.log_level or settings.log_level,
        reload=reload_enabled,
        workers=1,
        access_log=False,
        log_config=None,
    )
    return 0


def _upgrade_database(settings: Settings, action: str) -> int:
    """初始化 SQLite 文件或补齐当前版本尚未应用的迁移。"""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = create_database(settings)
    try:
        applied = upgrade_database(database.engine)
    finally:
        database.engine.dispose()
    logger.info(
        "cli_action command=database action=%s result=success migrations_applied=%d",
        action,
        len(applied),
    )
    if applied:
        print("Database is ready; applied {} migration(s).".format(len(applied)))
    else:
        print("Database is already up to date.")
    return 0


def _manage_admin(settings: Settings, action: str, username: str) -> int:
    """以交互方式创建首个超级管理员或重置其密码。"""
    if not settings.database_path.is_file():
        raise ValueError("Database not found. Run 'database init' first.")
    account_name = username.strip()
    if not account_name:
        raise ValueError("Username cannot be empty.")
    username_error = validate_username(account_name)
    if username_error:
        raise ValueError(username_error)

    first_prompt = (
        "New admin password: "
        if action == "reset"
        else "Admin password: "
    )
    password = getpass.getpass(first_prompt)
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise ValueError("The passwords do not match.")
    password_error = validate_new_password(password, account_name)
    if password_error:
        raise ValueError(password_error)

    database = create_database(settings)
    try:
        if action == "create":
            create_first_superuser(database.session_factory, account_name, password)
        else:
            reset_superuser_password(database.session_factory, account_name, password)
    finally:
        database.engine.dispose()

    logger.info(
        "cli_action command=admin action=%s result=success username=%s",
        action,
        account_name,
    )
    if action == "create":
        print("Created superuser account: {}".format(account_name))
    else:
        print("Reset password for superuser account: {}".format(account_name))
    return 0


def _validate_port(port: int) -> int:
    """拒绝服务命令中的非法监听端口。"""
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return port
