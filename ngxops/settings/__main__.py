"""提供系统设置相关的维护命令。"""

import argparse
import json
import sys

from ngxops.config import get_settings
from ngxops.database.connection import create_database
from ngxops.settings.service import purge_expired_data


def main() -> int:
    """按当前数据目录执行一次过期数据清理。"""
    parser = argparse.ArgumentParser(description="执行 ngxops 系统维护操作")
    parser.add_argument("command", choices=("purge",), help="purge：清理过期历史记录")
    parser.parse_args()
    settings = get_settings()
    if not settings.database_path.is_file():
        parser.error("数据库文件不存在；请先执行 python -m ngxops.database upgrade")

    database = create_database(settings)
    try:
        result = purge_expired_data(database.session_factory)
    except Exception as exc:
        print(
            "过期数据清理失败 type={}".format(type(exc).__name__),
            file=sys.stderr,
        )
        return 1
    finally:
        database.engine.dispose()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
