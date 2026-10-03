"""提供首次超级管理员初始化命令。"""

import argparse
import getpass
import logging
import sys

from ngxops.accounts.management import create_first_superuser
from ngxops.config import get_settings
from ngxops.database.connection import create_database
from ngxops.logging_setup import configure_logging, log_exception


logger = logging.getLogger(__name__)


def main() -> int:
    """交互式创建数据库中的首个超级管理员。"""
    parser = argparse.ArgumentParser(
        description="Create the first ngxops superuser."
    )
    parser.add_argument("username", help="Login username")
    arguments = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.data_dir, settings.log_level, "ngxops-cli.log")
    if not settings.database_path.is_file():
        parser.error("Database not found. Run 'python -m ngxops.database upgrade' first.")

    password = getpass.getpass("Admin password: ")
    confirmation = getpass.getpass("Confirm admin password: ")
    if password != confirmation:
        parser.error("The passwords do not match.")

    database = create_database(settings)
    try:
        create_first_superuser(database.session_factory, arguments.username, password)
    except ValueError as exc:
        logger.warning(
            "cli_action action=create_admin result=failed username=%s reason=%s",
            arguments.username,
            str(exc),
        )
        parser.error(str(exc))
    except Exception as exc:
        log_exception(
            logger,
            "CLI operation failed",
            exc,
            "action=create_admin username={}".format(arguments.username),
        )
        parser.error("Could not create the administrator. See the CLI log for details.")
    finally:
        database.engine.dispose()

    logger.info(
        "cli_action action=create_admin result=success username=%s",
        arguments.username,
    )
    print("Created superuser account: {}".format(arguments.username))
    return 0


if __name__ == "__main__":
    sys.exit(main())
