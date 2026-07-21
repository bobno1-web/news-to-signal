"""
conftest.py (프로젝트 루트)

하네스(검증방)용 pytest 부트스트랩. 두 가지만 한다:
1. 프로젝트 루트를 sys.path에 넣어 `import src.*` 가 되게 한다.
2. .env의 키를 os.environ에 로드한다(코드는 .env를 자동 로드하지 않음 — 엔진은
   os.environ을 직접 읽으므로, 불변식 테스트가 실제 LLM을 호출하려면 여기서 주입).

이 파일은 채점 도구(하네스)의 일부다. 판단 코어(src/)가 아니라 검증 자산이므로
원칙 9(코어/하네스 분리)에 어긋나지 않는다.
"""

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_dotenv() -> None:
    env = _ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip())


_load_dotenv()
