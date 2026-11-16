"""JSON Schema 导出与一致性检查的命令行入口。

用法::

    python -m backend.app.contracts.export_schema --write   # 重新导出 schema/
    python -m backend.app.contracts.export_schema --check   # CI：验证导出无漂移
"""

from __future__ import annotations

import argparse
import sys

from .schema import SCHEMA_DIR, check_schemas, write_schemas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出或检查共享契约 JSON Schema")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="把 Schema 写入 backend/app/contracts/schema/")
    group.add_argument("--check", action="store_true", help="检查磁盘 Schema 与模型导出是否一致")
    args = parser.parse_args(argv)

    if args.write:
        written = write_schemas()
        for path in written:
            print(f"已写入 {path}")
        print(f"共 {len(written)} 份 Schema，输出目录 {SCHEMA_DIR}")
        return 0

    problems = check_schemas()
    if problems:
        for problem in problems:
            print(f"[失败] {problem}", file=sys.stderr)
        return 1
    print("Schema 与模型导出一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
