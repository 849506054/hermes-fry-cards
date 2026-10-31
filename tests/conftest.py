"""测试隔离：把 HERMES_HOME 指到临时目录，绝不写生产 state。

插件用 ``hermes_home()`` 解析 profile home（``HERMES_HOME`` → ``~/.hermes``），
活跃卡登记落在 ``<home>/state/fry-cards-live.json``。测试里如果沿用生产 home，
每跑一次套件就会把 ``msg_*`` 之类的夹具 id 写进生产登记表，网关下次重启会
逐个尝试封卡并刷 ``stale card close failed``。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 仓库根入 sys.path：tests/test_clarify_buttons.py 依赖随仓的 tools 包
# （真实部署里该模块由 Hermes 的 tools 包提供），裸 ``pytest`` 不带 CWD 时也要能导入。
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_TMP_HOME = Path(tempfile.mkdtemp(prefix="fry-cards-tests-"))
os.environ["HERMES_HOME"] = str(_TMP_HOME)
os.environ.setdefault("HOME", str(_TMP_HOME))
