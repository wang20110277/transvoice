"""pytest 公共配置：把 src/ 加入 sys.path，使 `from outbound.xxx` / `from config` 等顶层导入可用。

与 CLAUDE.md 运行约定一致：PYTHONPATH=$(pwd):$(pwd)/src pytest。
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
_ROOT = os.path.dirname(_SRC)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
