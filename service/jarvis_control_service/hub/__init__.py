"""hub(control): TCP 서버 + 이벤트 관리(event_manager) + SQLite 기록(db)."""

import sys
from pathlib import Path

# 최상위 common/ 을 찾을 수 있게 저장소 최상위(aiot_repo_3/)를 import 경로 맨 앞에 넣는다
_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
