"""本地跑一遍，看四种状态各自长什么样。

用法：
    python -m planner.cli --spec fixtures/spec_chat.json --mode offline
    python -m planner.cli --spec fixtures/spec_chat.json --mode replay --fixture fixtures/bad_plan.json
    python -m planner.cli --spec fixtures/spec_chat.json --mode live
"""
import argparse
import json
import os
import sys

from . import fields as F
from .models import ProviderError
from .providers import HttpProvider, OfflineProvider, ReplayProvider
from .service import generate, summary

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_TEMPLATE = {
    "template_id": "se-default",
    "version": "1",
    "stages": ["implement", "verify", "integrate"],
    "allowed_roles": ["backend", "frontend", "qa"],
    "require_verification": True,
}

DEFAULT_CONFIG = {"max_concurrency": 2, "role_capacity": {"backend": 1, "frontend": 1, "qa": 1}}


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PM 规划模块本地试跑")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--mode", choices=["offline", "replay", "live"], default="offline")
    ap.add_argument("--fixture", help="replay 模式要回放的文件")
    ap.add_argument("--template")
    ap.add_argument("--out", help="把结果写到文件")
    args = ap.parse_args(argv)

    spec = load(args.spec)
    template = load(args.template) if args.template else DEFAULT_TEMPLATE

    if args.mode == "offline":
        provider = OfflineProvider()
    elif args.mode == "replay":
        if not args.fixture:
            ap.error("replay 模式需要 --fixture")
        provider = ReplayProvider.from_file(args.fixture)
    else:
        try:
            provider = HttpProvider.from_env()
        except ProviderError as e:
            print(f"无法进入实时模式：{e}")
            print("请设置 PM_BASE_URL / PM_API_KEY / PM_MODEL，或改用 --mode offline。")
            return 2

    result = generate(spec, template, DEFAULT_CONFIG, provider)
    print(summary(result))

    for g in result.gaps:
        print(f"  缺口 {g.code} {g.field_path}: {g.message}")
    for i in result.issues:
        print(f"  {i.severity} {i.code} {i.object_id or i.field_path}: {i.message}")

    if result.plan:
        tasks = F.pick(result.plan, F.PLAN, "tasks", [])
        print(f"\n前 4 个任务：")
        for t in tasks[:4]:
            print(f"  {F.pick(t, F.TASK, 'id')} | {F.pick(t, F.TASK, 'kind'):<14} | "
                  f"{F.pick(t, F.TASK, 'role'):<8} | {F.pick(t, F.TASK, 'title')}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"\n已写入 {args.out}")

    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
