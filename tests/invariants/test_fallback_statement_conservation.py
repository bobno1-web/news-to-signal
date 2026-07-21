"""
tests/invariants/test_fallback_statement_conservation.py  (검증방 소유 — 루프13 검증)

폴백 계정표(_group_by_statement) 계정 보존 불변식.

배경: 루프12는 주경로 _build_bs_table 의 표시명 dedup 을 없앴으나, 폴백 경로
_group_by_statement(옛 예시 덤프를 /api/example 로 열 때 index.html 이 쓰는 표)는 여전히
account_nm 으로 중복 제거해 동명 계정을 드롭했다(검증방 루프12 발의:
harness/pending/new-invariants/fallback-statement-table-still-evaporates.md). 루프13 개발방이
'선택지 2(폴백 자체를 행 보존으로)'로 dedup 을 제거했다. 검증방이 원본 raw 로 독립 검증한다.

성질: 폴백표의 재무상태표(BS) 그룹 계정 수 == DART 원본 BS '이름 있는 행 전부'
(폴백은 grand 총계·소계를 포함한 raw 그룹화라 T계정 주경로(54/57)보다 큰 55/58 이 기준 —
개발방 주장 기준의 타당성도 이 대조로 함께 검증된다). 표시명이 겹쳐도 서로 다른 행이면
각각 하나로 센다. 내부 가공물이 아니라 원본 행 기준(하네스 맹점 재발 방지).

★ _distinct_accounts(판단 울타리)는 이 검증 대상이 아니다 — 그건 이름 단위 dedup 이 '의도된'
프롬프트 문자열·판단 캐시 키다(바꾸면 판단 캐시 전량 무효). 폴백(표시)만 행 보존이어야 한다.
"""
import json
from pathlib import Path

import pytest

from src.sources import dart

_ROOT = Path(__file__).resolve().parents[2]
_CACHE = _ROOT / ".cache" / "dart"
_ALL = [("00111704", "한화오션"), ("00126380", "삼성전자"), ("00153861", "태영건설"),
        ("00164742", "현대자동차"), ("00635134", "CJ제일제당")]


def _rows(code: str) -> list[dict]:
    p = _CACHE / f"statement_{code}.json"
    if not p.exists():
        pytest.skip(f"DART 캐시 없음: {code}")
    return json.loads(p.read_text(encoding="utf-8"))


def _raw_named_bs(rows: list[dict]) -> int:
    return sum(
        1 for r in rows
        if (r.get("sj_div") or "").strip() == "BS" and (r.get("account_nm") or "").strip()
    )


@pytest.mark.parametrize("code,name", _ALL)
def test_fallback_group_conserves_every_named_bs_row(code, name):
    """폴백표 BS 그룹이 DART 원본 BS 이름행을 빠짐없이 담는다(동명 계정 드롭 0)."""
    rows = _rows(code)
    groups = dart._group_by_statement(rows)
    bsg = next((g for g in groups if g["sj_div"] == "BS"), None)
    assert bsg is not None, f"{name}: 폴백표에 BS 그룹이 없다."
    expected = _raw_named_bs(rows)
    actual = len(bsg["accounts"])
    assert actual == expected, (
        f"{name}({code}): 폴백표 BS {actual} != 원본 BS 이름행 {expected}. "
        f"동명 계정이 폴백에서 드롭됨(원칙 7). 차이 {expected - actual}건."
    )


def test_distinct_accounts_fence_still_name_dedups():
    """판단 울타리(_distinct_accounts)는 이름 dedup 을 '유지'해야 한다 — 이건 판단 캐시 키다.
    폴백을 행 보존으로 고치면서 울타리까지 바꾸면 판단 payload/캐시가 전량 무효가 된다(회귀 방어)."""
    rows = _rows("00635134")   # CJ: 유동/비유동 계약부채 동명 존재
    fence = dart._distinct_accounts(rows)
    assert fence.count("계약부채") == 1, (
        "판단 울타리가 동명 계정을 행 보존해버렸다 — 울타리는 이름 단위여야 한다(판단 캐시 보호)."
    )
