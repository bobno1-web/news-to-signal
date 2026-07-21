"""
tests/invariants/test_screen_vs_raw_account_conservation.py  (검증방 소유 — 긴급조사 loop11 후속)

■ 왜 이 불변식이 필요한가 (하네스 자기점검)
루프5b·7·11 의 계정 표 검증은 "표에 담긴 계정집합 == 울타리 집합, 추가·누락 0"으로
통과시켰다(test_account_statements.test_grouping_neither_adds_nor_drops_accounts). 그러나
그 대조의 두 항 — _group_by_statement(그룹화)와 _distinct_accounts(울타리) — 은 '둘 다'
account_nm 으로 중복을 제거한다. 같은 규칙으로 만든 두 내부 가공물이라 서로 '항상' 같고,
'같은(잘못된) 출처를 두 번 본' 검증이라 아무 결함도 잡지 못한다.

그 맹점 때문에, 회사가 DART 에 '서로 다른 계정'으로 보고했으나 '표시명(account_nm)이
우연히 같은' 계정(예: 유동/비유동 계약부채, 유동/비유동 리스부채·충당부채)이 선택 화면에서
'조용히' 사라지는 결함이 오래 통과됐다. 이는 원칙 7(숨기지 않는다) 위반이다 — 회사가 보고한
실제 계정을 감사인이 화면에서 볼 수도 고를 수도 없다.

■ 이 불변식이 하는 일
검증 기준을 '내부 가공물'이 아니라 'DART 원본 행'으로 되돌린다. 화면(bs_table)에 실린
계정 개수가 DART 원본 재무상태표(sj_div=='BS')의 '이름 있는·grand총계 아닌 행' 개수와
같아야 한다. 표시명이 겹쳐도 서로 다른 계정 행이면 각각 하나로 센다 — 이것이 핵심이다.

■ 현 상태(루프12 개발방 수정 후 — 검증방 strict 승격 2026-07-20)
- 개발방이 루프12에서 표시명 dedup 을 제거하고 '행 위치(안정 키)'로 각 원본 행을 보존했다.
  검증방이 원본 raw 로 재확인: 5사 전부 원본행 수 == 화면 항목 수, 키 1:1, 키 중복 0
  (현대차 49→57 로 8개, CJ 50→51 로 1개 복구, 한화·삼성·태영 불변). 이에 xfail 을 제거하고
  전 회사를 strict 로 승격한다. 개발방은 이 파일을 건드리지 않았다(원칙 10 — 자기 검증기준 불변).
- 만약 앞으로 드롭이 재발하면 이 불변식이 '초록→빨강'으로 즉시 잡는다(회귀 방어선).

방법: 실제 캐시(.cache/dart/statement_*.json)로만 검증. 캐시 없으면 skip. 원본 raw(행) 기준 —
      내부 가공물(_distinct_accounts, _group_by_statement)끼리 비교하지 않는다(그 둘은 같은
      account_nm dedup 이라 서로 항상 같고 이 결함을 구조적으로 못 잡는다 — 하네스 맹점 재발 방지).
"""
import json
from pathlib import Path

import pytest

from src.sources import dart

_ROOT = Path(__file__).resolve().parents[2]
_CACHE = _ROOT / ".cache" / "dart"
_GRAND = "ifrs-full_EquityAndLiabilities"   # 자본과부채총계 — 어느 섹션도 아니므로 표에서 의도적 제외

# 캐시 보유 5사 전부 원본==화면이어야 한다(strict). 회사별 표시명 충돌 유무와 무관하게 보존.
_ALL = [("00111704", "한화오션"), ("00126380", "삼성전자"), ("00153861", "태영건설"),
        ("00164742", "현대자동차"), ("00635134", "CJ제일제당")]


def _rows(code: str) -> list[dict]:
    p = _CACHE / f"statement_{code}.json"
    if not p.exists():
        pytest.skip(f"DART 캐시 없음: {code}")
    return json.loads(p.read_text(encoding="utf-8"))


def _expected_account_rows(rows: list[dict]) -> list[dict]:
    """회사가 보고한 '실제 계정 행' = BS 행 중 이름 있고 grand 총계가 아닌 행.
    표시명(account_nm)이 겹쳐도 서로 다른 행이면 각각 하나로 센다 — 원본(행) 기준이 핵심."""
    return [
        r for r in rows
        if (r.get("sj_div") or "").strip() == "BS"
        and (r.get("account_nm") or "").strip()
        and (r.get("account_id") or "").strip() != _GRAND
    ]


def _screen_entries(rows: list[dict]) -> list[dict]:
    bt = dart._build_bs_table(rows)
    assert bt is not None, "BS 표가 만들어지지 않았다(앵커 부재?)."
    return [e for sec in ("assets", "liabilities", "equity", "unassigned") for e in bt[sec]]


@pytest.mark.parametrize("code,name", _ALL)
def test_screen_conserves_every_raw_bs_account(code, name):
    """화면 계정집합이 DART 원본 BS 행을 빠짐없이 대표한다(원본 기준 대조). 표시명이 겹쳐도 드롭 없음."""
    rows = _rows(code)
    expected = _expected_account_rows(rows)
    screen = _screen_entries(rows)
    assert len(screen) == len(expected), (
        f"{name}({code}): 화면 계정 {len(screen)} != DART 원본 계정행 {len(expected)}. "
        f"회사가 보고한 계정이 화면에서 조용히 사라졌다(원칙 7). 차이 {len(expected) - len(screen)}건 — "
        f"표시명(account_nm) 충돌로 인한 드롭 의심."
    )


@pytest.mark.parametrize("code,name", _ALL)
def test_screen_keys_map_one_to_one_to_raw_rows(code, name):
    """안정 키(행 위치)가 각 DART 원본 BS 행과 정확히 1:1 인가 — 중복·유령 키 0(선택 무결성의 토대)."""
    rows = _rows(code)
    bs = [r for r in rows if (r.get("sj_div") or "").strip() == "BS"]
    expected_idx = {
        i for i, r in enumerate(bs)
        if (r.get("account_nm") or "").strip()
        and (r.get("account_id") or "").strip() != _GRAND
    }
    screen = _screen_entries(rows)
    keys = [e["key"] for e in screen]
    assert len(keys) == len(set(keys)), f"{name}: 화면 키 중복 있음(같은 행이 두 번) — 선택 무결성 훼손."
    assert {int(k) for k in keys} == expected_idx, (
        f"{name}: 화면 키 집합 != 원본 계정 행 위치 집합. "
        f"화면에만 있는 키(유령)={ {int(k) for k in keys} - expected_idx }, "
        f"원본에만 있는 행(드롭)={ expected_idx - {int(k) for k in keys} }."
    )
    # 각 화면 항목의 name·account_id 가 그 키(행 위치)의 원본 행과 일치(키가 다른 행을 가리키지 않음).
    for e in screen:
        r = bs[int(e["key"])]
        assert e["name"] == (r.get("account_nm") or "").strip(), f"{name}: 키 {e['key']} 이름 불일치."
        assert e["account_id"] == (r.get("account_id") or "").strip(), f"{name}: 키 {e['key']} account_id 불일치."
