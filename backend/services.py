import json
import subprocess

from .store import ROOT


def demo_service(action, data):
    result = subprocess.run(
        ["node", str(ROOT / "backend/bridge.mjs"), action],
        input=json.dumps(data), encoding="utf-8", capture_output=True, timeout=30,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "规则服务失败")
    return json.loads(result.stdout)
