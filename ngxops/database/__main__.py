"""提供显式数据库升级命令。"""

import argparse
import sys

from ngxops.config import get_settings
from ngxops.database.connection import create_database
from ngxops.database.migration_runner import LegacyDatabaseError, upgrade_database


def main() -> int:
    """显式应用已登记的 SQLite 结构迁移。"""
    parser = argparse.ArgumentParser(description="ngxops 数据库维护命令")
    parser.add_argument(
        "command",
        choices=("upgrade",),
        help="应用尚未执行的版本迁移",
    )
    args = parser.parse_args()
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = create_database(settings)

    try:
        applied = upgrade_database(database.engine)
    except LegacyDatabaseError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        database.engine.dispose()

    print("数据库升级完成，本次应用 {} 个迁移。".format(len(applied)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
