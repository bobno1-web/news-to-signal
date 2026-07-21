"""
tests/unit/test_account_statements.py  (개발방 소유 — 루프5b 계정 표 그룹화 단위 테스트)

원칙 10 경계: 검증방 불변식은 건드리지 않는다. 여기서는 개발방이 루프5b에서 추가한
'재무제표 구분별 계정 표'(_group_by_statement)가 DART 원본 구분·순서를 그대로 쓰고
(임의 분류/재정렬 없음), 울타리(flat 계정집합)와 같은 계정집합을 유지하는지 확인한다.
모두 비-API 결정론적 단위 테스트.
"""

from src.sources.base import FinancialSource
from src.sources.dart import _distinct_accounts, _group_by_statement

ROWS = [
    # account_id 는 _group_by_statement 이 읽지 않지만(행 위치로만 보존), 서로 '다른 계정'임을
    # 읽는 사람이 알도록 남긴다 — 유동/비유동처럼 표시명이 같아도 다른 계정이라는 실제 케이스.
    {"sj_div": "BS", "sj_nm": "재무상태표", "account_id": "ifrs-full_Assets", "account_nm": "자산총계"},
    {"sj_div": "BS", "sj_nm": "재무상태표", "account_id": "ifrs-full_CurrentAssets", "account_nm": "유동자산"},
    {"sj_div": "BS", "sj_nm": "재무상태표", "account_id": "dup_row", "account_nm": "자산총계"},        # 표 안 동명 행(보존)
    {"sj_div": "IS", "sj_nm": "손익계산서", "account_id": "ifrs-full_Revenue", "account_nm": "매출액"},
    {"sj_div": "IS", "sj_nm": "손익계산서", "account_id": "x_cogs", "account_nm": "매출원가"},
    {"sj_div": "BS", "sj_nm": "재무상태표", "account_id": "x_cash", "account_nm": "현금및현금성자산"},  # BS 가 뒤에서 또 등장
    {"sj_div": "CF", "sj_nm": "현금흐름표", "account_id": "x_cash_cf", "account_nm": "현금및현금성자산"},  # 같은 이름, 다른 표
    {"sj_div": "", "sj_nm": "", "account_nm": "   "},                        # 빈 계정 → 건너뜀
]


def test_groups_follow_dart_division_and_order():
    """표는 구분 첫 등장 순서, 표 안 계정은 원본 행 순서 그대로(표시명 겹침도 보존 — 루프13)."""
    groups = _group_by_statement(ROWS)
    assert [g["sj_div"] for g in groups] == ["BS", "IS", "CF"], "구분 첫 등장 순서가 아니다."
    bs = next(g for g in groups if g["sj_div"] == "BS")
    assert bs["sj_nm"] == "재무상태표"
    # 표시명이 겹치는 '자산총계' 두 행을 둘 다 보존한다(원칙 7 — 동명이라고 조용히 버리지 않는다).
    assert bs["accounts"] == ["자산총계", "유동자산", "자산총계", "현금및현금성자산"], \
        "표 안 순서/원본 행 보존이 깨졌다(재정렬 또는 표시명 dedup 재발 의심)."
    is_ = next(g for g in groups if g["sj_div"] == "IS")
    assert is_["accounts"] == ["매출액", "매출원가"]


def test_same_account_can_live_in_two_statements():
    """같은 계정명이 두 표에 있으면 각 표에 그대로 둔다(DART 구조 반영, 지어냄 아님)."""
    groups = _group_by_statement(ROWS)
    cf = next(g for g in groups if g["sj_div"] == "CF")
    assert cf["accounts"] == ["현금및현금성자산"]


def test_same_name_distinct_rows_both_preserved():
    """루프13: 표시명이 우연히 같은 '서로 다른 계정 행'은 한 표 안에서도 둘 다 보존된다
    (현대차 유동/비유동 리스부채·충당부채, CJ 유동/비유동 계약부채류 — 표시명 dedup 이 뒤 행을
    조용히 버리던 폴백 경로 결함 방어). _group_by_statement 는 account_id 를 안 읽지만, 원본 행을
    빠짐없이 보존하므로 서로 다른 행이면 표시명이 같아도 각각 남는다."""
    groups = _group_by_statement(ROWS)
    bs = next(g for g in groups if g["sj_div"] == "BS")
    # 표시명 겹치는 '자산총계'가 원본 2행 → 표에도 2번(드롭 0).
    assert bs["accounts"].count("자산총계") == 2, "동명 계정 행이 폴백 표에서 드롭됐다(원칙 7 위반)."
    # BS 원본 이름행 수(빈 계정 제외) == 폴백 BS 계정 수 — 행 보존 정합.
    raw_bs_named = [r for r in ROWS
                    if (r.get("sj_div") or "").strip() == "BS" and (r.get("account_nm") or "").strip()]
    assert len(bs["accounts"]) == len(raw_bs_named), (
        f"폴백 BS 계정 {len(bs['accounts'])} != 원본 BS 이름행 {len(raw_bs_named)} (드롭 발생)")


def test_grouping_covers_same_nameset_as_fence():
    """표 그룹화의 '이름집합'은 울타리(_distinct_accounts, 이름 dedup)와 같아야 한다 — 표시용이라
    판단 재료(울타리)를 오염시키지 않는다. (그룹화는 행을 보존하므로 개수는 다를 수 있으나,
    '이름집합'은 동일 = 새 이름을 지어내지도, 있는 이름을 통째로 빠뜨리지도 않는다.)"""
    grouped = {a for g in _group_by_statement(ROWS) for a in g["accounts"]}
    flat = set(_distinct_accounts(ROWS))
    assert grouped == flat, f"그룹화가 계정 이름을 추가/누락했다: {grouped ^ flat}"


def test_empty_account_names_skipped():
    groups = _group_by_statement(ROWS)
    assert all(a.strip() for g in groups for a in g["accounts"]), "빈 계정명이 표에 들어갔다."


class _Fake(FinancialSource):
    """구분 정보가 없는 소스 — base 기본 구현이 단일 그룹으로 주는지 확인."""
    def fetch_account_groups(self, corp): return ["매출채권", "재고자산"]
    def fetch_financials(self, corp): return {}


def test_base_default_single_group():
    """DART 가 아닌(구분 없는) 소스는 base 기본 구현으로 단일 '계정과목' 그룹을 준다."""
    g = _Fake().fetch_account_statements("x")
    assert len(g) == 1 and g[0]["accounts"] == ["매출채권", "재고자산"]
