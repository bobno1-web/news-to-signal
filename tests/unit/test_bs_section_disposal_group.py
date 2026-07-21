"""
루프11 개발방 단위 테스트(BS 섹션 붕괴 회귀 — tests/unit, 원칙 10: 개발방 자체 테스트는 여기).

증상(CJ제일제당 라이브): 매각예정 처분집단 항목 + '자본이 부채보다 먼저 오고 grand 총계가
중간에 낀' 비정형 BS 순서 때문에, 부채총계가 leaf 로 오인돼 running 이 오염 → 부채 섹션이
못 닫혀 전부 폴백으로 '자본'에 밀려들어감(부채 0개, 자본에 부채 오염, 2차: 자본총계 그룹
버튼이 부채 말단까지 선택).

수정: DART/IFRS 표준 구간합계 계정ID(마커)를 is_total 로 보완해 누적에서 제외 + 폴백을
'인접 밀어넣기' 대신 '구분 미확정(None)'으로. 이름표(한국어 리터럴) 매칭은 쓰지 않는다(원칙 1).

CJ 케이스는 캐시가 있으면 회귀로 고정한다(총괄검증 pending loop7-bs-fallback 처방 반영).
미확정 거동은 캐시 없이 _assign_sections 직접 단위로 못박는다.
검증방 소유 불변식(tests/invariants/)은 별개다 — 여기선 개발방이 자기 시공을 못박는다.
"""
import json
from pathlib import Path

import pytest

from src.sources import dart

_ROOT = Path(__file__).resolve().parents[2]
_CJ = _ROOT / ".cache" / "dart" / "statement_00635134.json"   # CJ제일제당(처분집단 있는 BS)


# ── 미확정 거동(캐시 불필요 — 직접 단위) ──────────────────────────────────────

def test_unresolved_row_becomes_unassigned_not_pushed():
    """구조로 못 놓은 말단은 인접 섹션에 밀어넣지 않고 None(미확정)으로 남는다(원칙 4·7)."""
    ids = [dart._BS_ANCHOR_ASSETS, "leafA1", "leafA2", "stray"]
    amounts = [100, 60, 40, 5]                 # 자산=100 닫힘, stray 5 는 어느 섹션도 아님
    _is_sub, is_total = dart._bs_totals(ids, amounts)
    sections, has_unassigned = dart._assign_sections(ids, amounts, is_total)
    assert sections == ["자산", "자산", "자산", None]   # stray 가 '자산'으로 밀리지 않음
    assert has_unassigned is True


def test_structural_total_ids_marked_even_if_arithmetic_misses():
    """IFRS 구조앵커 ID 는 금액 산술이 놓쳐도 is_total(합계)로 표시된다(누적·선택에서 제외)."""
    # 부채총계가 유동+비유동+ (중간 leaf) 로 산술 미탐지여도 구조앵커라 is_total=True.
    ids = ["ifrs-full_Liabilities", "x1", "x2"]
    amounts = [999, 10, 20]                     # 999 != 10+20 → 산술로는 합계 아님
    is_sub, is_total = dart._bs_totals(ids, amounts)
    assert is_sub[0] is False and is_total[0] is True


# ── CJ 회귀(캐시 있으면) ──────────────────────────────────────────────────────

@pytest.mark.skipif(not _CJ.exists(), reason="CJ제일제당 DART 캐시 없음")
def test_cj_liabilities_land_in_liabilities_not_equity():
    rows = json.loads(_CJ.read_text(encoding="utf-8"))
    bt = dart._build_bs_table(rows)
    assert bt is not None

    # (1) 부채 섹션이 비지 않는다(원 증상: 부채 선택가능 0).
    liab_sel = [a["name"] for a in bt["liabilities"] if a["selectable"]]
    assert len(liab_sel) >= 15, f"부채 말단이 비었거나 적음: {liab_sel}"

    # (2) 알려진 부채 계정(IFRS id 로 조회 — 이름표 매칭 아님)이 '부채' 섹션에 있고 '자본'엔 없다.
    bs = [r for r in rows if (r.get("sj_div") or "").strip() == "BS"]
    id_by_name = {(r.get("account_nm") or "").strip(): (r.get("account_id") or "").strip() for r in bs}
    liab_names = {a["name"] for a in bt["liabilities"]}
    equity_names = {a["name"] for a in bt["equity"]}
    for liab_id in ("ifrs-full_ShorttermBorrowings", "ifrs-full_Liabilities",
                    "ifrs-full_LiabilitiesIncludedInDisposalGroupsClassifiedAsHeldForSale",
                    "ifrs-full_CurrentLiabilities", "ifrs-full_NoncurrentLiabilities"):
        nm = next((n for n, i in id_by_name.items() if i == liab_id), None)
        if nm is None:
            continue
        assert nm in liab_names, f"{nm}({liab_id}) 가 부채 섹션에 없음"
        assert nm not in equity_names, f"{nm}({liab_id}) 가 자본 섹션에 오염됨"

    # (3) 미확정 없음 · 폴백 없음(구조로 깔끔히 닫힘).
    assert bt["unassigned"] == []
    assert bt["fallback_used"] is False


@pytest.mark.skipif(not _CJ.exists(), reason="CJ제일제당 DART 캐시 없음")
def test_cj_equity_total_group_excludes_liabilities():
    """2차 피해: '자본총계' 그룹 버튼이 부채 말단까지 선택하면 안 된다(members 에 부채 없음)."""
    rows = json.loads(_CJ.read_text(encoding="utf-8"))
    bt = dart._build_bs_table(rows)
    liab_leaves = {a["name"] for a in bt["liabilities"] if a["selectable"]}
    for a in bt["equity"]:
        mem = a.get("members")
        if not mem:
            continue
        assert set(mem).isdisjoint(liab_leaves), f"자본 합계 {a['name']} members 에 부채 말단 오염: {set(mem) & liab_leaves}"
