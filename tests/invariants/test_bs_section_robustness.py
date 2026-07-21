"""
tests/invariants/test_bs_section_robustness.py  (검증방 소유 — 루프11)

불변식(BS 섹션 배정: 표준 IFRS 앵커로 일반적으로 서고, 못 놓으면 정직히 미확정):
루프11에서 CJ제일제당 라이브의 비정형 BS(자본이 부채보다 먼저·grand 총계가 중간·매각예정
처분집단 부채가 소계 사이 leaf)가 산술만으로는 무너져, DART/IFRS 표준 구간합계 계정ID 집합
(_BS_SECTION_SUBTOTAL_IDS)으로 보완했다(decision-log D30). 검증방은 이 보완이 (원칙 1) 이름표
매칭이 아닌 표준 마커이고 (원칙 2) 특정 회사 맞춤이 아니라 일반 해법이며 (원칙 4·7) 못 놓은
행을 인접 섹션에 밀어넣지 않고 미확정으로 분리함을 독립 검증한다. 개발방 unit(test_bs_subtotal_
members.py)은 응시자 자체 테스트라 검증 기준이 아니다(원칙 10).

핵심 성질:
- 앵커 집합은 표준 IFRS 택소노미 ID(ifrs-full_*)뿐 — 한국어 리터럴·회사코드 없음(원칙 1·2).
- '매각예정 처분집단 부채'(비0 leaf)가 소계 사이에 껴 산술이 부채총계를 놓쳐도, 구조앵커로
  각 섹션이 제자리에 닫힌다. 이 성질은 특정 회사 데이터가 아니라 '구조'에서 나온다(합성 재현).
- 앵커를 끄면(=산술만) 같은 데이터가 무너진다 → 앵커는 실제 하중을 받는다(load-bearing).
- 섹션 순서를 가정하지 않는다(블록 순서를 바꿔도 동일 배정).
- 구조로 못 놓은 행은 '인접 섹션'이 아니라 '미확정(None)'으로 분리되고 fallback_used=True
  (원칙 4·7 — 부채가 자본에 앉는 것보다 미확정이 정직). 미확정은 그룹선택 소속을 오염 안 함.
- 섹션 앵커가 하나도 없으면 표를 만들지 않는다(None) — 억지로 세우지 않는다(원칙 4).

방법: 실제 API/캐시 없이 '구조'만 검증. 라운드넘버 pass2 우연충돌을 피하려 비-라운드 금액을 쓴다.
      (캐시된 실제 DART 5사 확인은 test_bs_subtotal_members 계열이 별도로 수행.)
"""

import pytest

from src.sources import dart

_B = 1_000_000_000  # 10억(원)


def _row(aid, nm, amt):
    return {"sj_div": "BS", "account_id": aid, "account_nm": nm, "thstrm_amount": str(amt)}


def _pathology_rows():
    """
    CJ 병리를 '구조'로 재현(회사 데이터 아님): 자본이 부채보다 먼저, grand 총계가 중간,
    '매각예정 처분집단 부채'(비0)가 부채총계의 직계 형제라 산술이 부채총계를 놓친다.
    금액은 비-라운드(서로 배수·부분합 우연일치 없음)로 골라 pass2 오검출을 피한다.
    부채총계 263 = 유동부채 124 + 매각예정 29 + 비유동부채 110.  부채 263 + 자본 507 = grand 770 = 자산총계.
    """
    return [
        _row("ifrs-full_Assets", "자산총계", 770 * _B),
        _row("ifrs-full_CurrentAssets", "유동자산", 348 * _B),
        _row("x_cash", "현금및현금성자산", 137 * _B),
        _row("x_inventory", "재고자산", 211 * _B),
        _row("ifrs-full_NoncurrentAssets", "비유동자산", 422 * _B),
        _row("x_ppe", "유형자산", 259 * _B),
        _row("x_intangible", "무형자산", 163 * _B),
        # 자본이 부채보다 먼저 온다(비정형 순서).
        _row("ifrs-full_Equity", "자본총계", 507 * _B),
        _row("x_capital", "자본금", 190 * _B),
        _row("x_retained", "이익잉여금", 317 * _B),
        # grand 총계가 중간에 낀다.
        _row("ifrs-full_EquityAndLiabilities", "부채와자본총계", 770 * _B),
        _row("ifrs-full_CurrentLiabilities", "유동부채", 124 * _B),
        _row("x_payables", "매입채무", 71 * _B),
        _row("x_st_borrow", "단기차입금", 53 * _B),
        # 부채총계가 자식(유동/비유동/매각예정)보다 먼저 표시된다.
        _row("ifrs-full_Liabilities", "부채총계", 263 * _B),
        _row("ifrs-full_LiabilitiesIncludedInDisposalGroupsClassifiedAsHeldForSale",
             "매각예정으로 분류된 처분집단의 부채", 29 * _B),
        _row("ifrs-full_NoncurrentLiabilities", "비유동부채", 110 * _B),
        _row("x_bond", "사채", 67 * _B),
        _row("x_lt_borrow", "장기차입금", 43 * _B),
    ]


def _names(section):
    return [a["name"] for a in section]


def _build_no_anchor(rows):
    """구조앵커를 끈 채(=수정 전 산술만) 빌드 — red 조건."""
    orig = dart._is_structural_total
    dart._is_structural_total = lambda account_id: False
    try:
        return dart._build_bs_table(rows)
    finally:
        dart._is_structural_total = orig


HELD = "매각예정으로 분류된 처분집단의 부채"


# ── 원칙 1·2: 앵커는 표준 IFRS 마커, 특정 회사 아님 ─────────────────────────────

def test_anchor_ids_are_standard_ifrs_no_korean_no_company():
    """_BS_SECTION_SUBTOTAL_IDS 는 표준 IFRS 택소노미 ID(ifrs-full_*)뿐 — 한국어·회사코드 없음."""
    ids = dart._BS_SECTION_SUBTOTAL_IDS
    assert len(ids) >= 8, "표준 구간합계 앵커가 비정상적으로 적다."
    for aid in ids:
        assert aid.startswith("ifrs-full_"), f"비표준(비 ifrs-full) 앵커: {aid!r} — 회사맞춤/비표준 의심"
        assert aid.isascii(), f"앵커에 비-ASCII(한국어 리터럴 등): {aid!r} (원칙 1 위반)"
        # 'entity'/회사코드('00126380' 같은)·DART 확장(dart_) 이 아니라 순수 표준 앵커여야 한다.
        assert "entity" not in aid.lower() and not any(c.isdigit() for c in aid), (
            f"회사·개체 특정 앵커 의심: {aid!r} (원칙 2 위반)")


# ── 일반성: 구조 병리를 회사 데이터 없이 재현해도 앵커가 고친다 ──────────────────

def test_held_for_sale_pathology_assigns_correctly():
    """매각예정 leaf 가 소계 사이에 껴도(산술이 부채총계를 놓쳐도) 앵커로 섹션이 제자리에 닫힌다."""
    rows = _pathology_rows()
    ids = [r["account_id"] for r in rows]
    amts = [int(r["thstrm_amount"]) for r in rows]
    is_sub, is_total = dart._bs_totals(ids, amts)
    # 앵커가 '실제로' 부채총계를 총계로 보완했는가(산술은 놓쳤는가) — 병리가 성립하는지 자체 검증.
    li = ids.index("ifrs-full_Liabilities")
    assert is_total[li] and not is_sub[li], "이 케이스에서 부채총계를 산술이 이미 잡음(병리 재현 실패)."

    bt = dart._build_bs_table(rows)
    assert bt is not None
    assert HELD in _names(bt["liabilities"]), "매각예정 부채가 부채 섹션에 없다(오배정)."
    # 부채 섹션이 온전(유동·비유동·매각예정·부채총계 모두 부채에), 자본 오염 0, 미확정 0.
    liab = set(_names(bt["liabilities"]))
    assert {"유동부채", "비유동부채", "부채총계", HELD, "매입채무", "사채"} <= liab
    assert liab.isdisjoint(_names(bt["equity"])), "부채 계정이 자본 섹션에 샜다(오염)."
    assert bt["unassigned"] == [] and bt["fallback_used"] is False, "정상 구조인데 미확정/폴백이 생겼다."


def test_anchor_is_load_bearing_redgreen():
    """red→green: 앵커를 끄면(산술만) 같은 데이터가 무너지고, 켜면 선다 → 앵커는 하중을 받는다."""
    rows = _pathology_rows()
    green = dart._build_bs_table(rows)
    red = _build_no_anchor(rows)
    assert HELD in _names(green["liabilities"])           # 앵커 on: 정상
    assert green["fallback_used"] is False
    # 앵커 off: 부채총계를 leaf 로 오인 → 부채 섹션이 못 닫힘 → 미확정으로 빠지거나 부채가 붕괴.
    red_ok = (HELD in _names(red["liabilities"])) and red["unassigned"] == [] and not red["fallback_used"]
    assert not red_ok, "앵커를 꺼도 정상이면(=앵커 무의미) 병리 재현/보완 증명 실패."


def test_section_order_independence():
    """섹션 블록 순서를 바꿔도 각 섹션 계정 집합은 동일하다(비정형 순서 가정 안 함)."""
    rows = _pathology_rows()
    base = dart._build_bs_table(rows)
    base_sig = {k: set(_names(base[k])) for k in ("assets", "liabilities", "equity")}
    # 블록(자산/자본/grand/부채)을 잘라 다른 순서로 재조립.
    eq_i = next(i for i, r in enumerate(rows) if r["account_id"] == "ifrs-full_Equity")
    grand_i = next(i for i, r in enumerate(rows) if r["account_id"] == "ifrs-full_EquityAndLiabilities")
    liab_i = next(i for i, r in enumerate(rows) if r["account_id"] == "ifrs-full_CurrentLiabilities")
    assets, equity, grand, liab = rows[:eq_i], rows[eq_i:grand_i], [rows[grand_i]], rows[liab_i:]
    for order in ([assets, liab, grand, equity], [assets, equity, liab, grand],
                  [assets, grand, liab, equity]):
        reordered = [r for block in order for r in block]
        bt = dart._build_bs_table(reordered)
        sig = {k: set(_names(bt[k])) for k in ("assets", "liabilities", "equity")}
        assert sig == base_sig, f"순서를 바꾸니 섹션 배정이 달라졌다(순서 의존): {order}"


# ── 원칙 4·7: 못 놓으면 인접 섹션이 아니라 미확정으로 분리 ───────────────────────

def test_undetermined_goes_to_unassigned_not_adjacent_section():
    """
    구조가 깨져(부채총계 목표를 일부러 오염) 부채 구간이 안 닫히면, 그 행들은 '자본'으로 밀려가지
    않고 '미확정(None)'으로 분리되고 fallback_used=True 가 된다(원칙 4·7).
    """
    rows = _pathology_rows()
    # 부채총계 금액을 깨서(자식 합과 불일치) 부채 구간이 목표에 도달 못 하게 만든다.
    for r in rows:
        if r["account_id"] == "ifrs-full_Liabilities":
            r["thstrm_amount"] = str(999 * _B)   # 자식 합(263)과 불일치 → 부채 못 닫힘
    bt = dart._build_bs_table(rows)
    assert bt is not None
    equity_names = set(_names(bt["equity"]))
    # 부채성 계정이 자본으로 새면 실패(인접 섹션 밀어넣기 부활 = 원칙 4·7 위반).
    assert "매입채무" not in equity_names and "사채" not in equity_names and HELD not in equity_names, (
        "못 닫은 부채 계정이 자본 섹션으로 밀려들어갔다(미확정 분리 실패 — 원칙 4·7).")
    assert bt["fallback_used"] is True, "구조 미확정인데 fallback_used 가 False(정직 표기 실패)."
    assert len(bt["unassigned"]) > 0, "미확정 바구니가 비었다(못 놓은 행을 어딘가로 밀어넣었다)."


def test_unassigned_not_polluting_group_select_members():
    """미확정(섹션 None) 계정은 어떤 섹션 총계의 '그룹선택 소속(members)'에도 들어가지 않는다(루프8 비오염)."""
    rows = _pathology_rows()
    for r in rows:
        if r["account_id"] == "ifrs-full_Liabilities":
            r["thstrm_amount"] = str(999 * _B)
    bt = dart._build_bs_table(rows)
    unassigned_names = set(_names(bt["unassigned"]))
    assert unassigned_names, "이 케이스는 미확정이 생겨야 한다(전제)."
    for key in ("assets", "liabilities", "equity"):
        for a in bt[key]:
            for m in a.get("members", []):
                assert m not in unassigned_names, f"미확정 계정 {m!r} 이 {a['name']} 그룹선택에 오염됐다."


# ── 원칙 4: 앵커 없으면 억지로 세우지 않는다 ────────────────────────────────────

def test_no_section_anchor_returns_none():
    """자산/부채/자본 총계 앵커가 하나도 없으면 표를 만들지 않는다(None) — 폴백 오염 대신 정직한 무표."""
    rows = [
        _row("x_cash", "현금", 100 * _B),
        _row("x_something", "무언가", 50 * _B),
        _row("x_other", "기타", 30 * _B),
    ]
    assert dart._build_bs_table(rows) is None, "섹션 앵커가 없는데 표를 억지로 세웠다(구조 신뢰 불가)."
