"""
루프8 개발방 단위 테스트(표시 계층 — tests/unit, 원칙 10: 개발방 자체 테스트는 여기).

합계 계정을 '그룹 전체 선택' 버튼으로 되살리기 위해 _build_bs_table 이 각 합계에
'말단 소속(members)'을 붙인다. 소속은 새 규칙·이름표가 아니라 루프7의 산술 판별
(_detect_subtotals / _pass1_subtotals / _assign_sections)을 재활용한다(원칙 1).

이 테스트는 캐시된 실제 DART 재무제표(.cache/dart/statement_*.json)로 표시 불변식만
검사한다. 판단·수집·계정 매칭은 건드리지 않는다. 캐시가 없으면 skip(라운드 넘버
합성 데이터는 pass2 부분합 우연 충돌로 합계 탐지가 왜곡되므로 쓰지 않는다).
"""
import json
from pathlib import Path

import pytest

from src.sources import dart

_ROOT = Path(__file__).resolve().parents[2]
_CACHE = _ROOT / ".cache" / "dart"
_STMTS = sorted(_CACHE.glob("statement_*.json")) if _CACHE.exists() else []

pytestmark = pytest.mark.skipif(not _STMTS, reason="캐시된 DART 재무제표가 없어 skip")


def _tables():
    for p in _STMTS:
        rows = json.loads(p.read_text(encoding="utf-8"))
        bt = dart._build_bs_table(rows)
        if bt:
            yield p.stem, bt


def _leaf_names(section):
    return {a["name"] for a in section if a["selectable"]}


def _leaf_keys(section):
    return {a["key"] for a in section if a["selectable"]}


def _subtotal_names(section):
    return {a["name"] for a in section if not a["selectable"]}


def test_pass1_refactor_preserves_detection():
    """_detect_subtotals 를 _pass1_subtotals 로 리팩터해도 합계 탐지 결과는 불변이어야 한다."""
    for p in _STMTS:
        rows = json.loads(p.read_text(encoding="utf-8"))
        bs = [r for r in rows if (r.get("sj_div") or "").strip() == "BS"]
        amounts = [(dart._parse_amount(r.get("thstrm_amount", "")) or 0) for r in bs]
        is_sub = dart._detect_subtotals(amounts)
        is_sub_p1, end = dart._pass1_subtotals(amounts)
        # pass1 이 잡은 합계는 최종 is_sub 의 부분집합(나머지는 pass2 가 추가).
        assert all((not a) or b for a, b in zip(is_sub_p1, is_sub))
        # end 는 각 인덱스에서 자기 다음 이상(반개구간 유효).
        assert all(end[i] >= i + 1 for i in range(len(amounts)))


def test_group_members_are_real_leaves_in_same_section():
    """모든 합계의 members 는 '같은 섹션의 실제 말단(체크박스)'이어야 한다(허깨비 금지).
    루프12: 표시명(members)은 서로 다른 계정이 우연히 같은 이름을 쓰면 중복될 수 있으므로
    '고유성'은 안정 키(member_keys, 행 위치)로 검사한다 — 이름 중복은 결함이 아니라 동명이인
    계정의 정직한 보존이다(원칙 7)."""
    for name, bt in _tables():
        for key in ("assets", "liabilities", "equity"):
            section = bt[key]
            leaves = _leaf_names(section)
            leaf_keys = _leaf_keys(section)
            for a in section:
                mem = a.get("members")
                if mem is None:
                    continue
                assert not a["selectable"], f"{name}:{a['name']} 말단인데 members 를 가짐"
                assert set(mem) <= leaves, f"{name}:{a['name']} members 에 체크박스 없는 항목"
                mk = a.get("member_keys") or []
                assert len(mk) == len(mem), f"{name}:{a['name']} members 와 member_keys 길이 불일치"
                assert set(mk) <= leaf_keys, f"{name}:{a['name']} member_keys 에 체크박스 없는 키"
                assert len(mk) == len(set(mk)), f"{name}:{a['name']} member_keys 중복(같은 행 두 번)"


def test_group_members_exclude_middle_subtotals():
    """★ 중첩: 합계 members 에는 '중간 합계' 이름이 섞이면 안 된다(말단만 — 담당 계정만)."""
    for name, bt in _tables():
        for key in ("assets", "liabilities", "equity"):
            section = bt[key]
            subs = _subtotal_names(section)
            for a in section:
                mem = a.get("members")
                if not mem:
                    continue
                assert subs.isdisjoint(mem), (
                    f"{name}:{a['name']} members 에 중간 합계 포함: {subs & set(mem)}")


def test_section_total_selects_all_section_leaves():
    """섹션 총계(자산/부채/자본총계)는 그 섹션 '말단 전부'를 거느린다 —
    섹션당 members 가 그 섹션 말단 전체와 정확히 일치하는 합계가 하나 존재해야 한다."""
    for name, bt in _tables():
        for key in ("assets", "liabilities", "equity"):
            section = bt[key]
            leaves = _leaf_names(section)
            if not leaves:
                continue
            member_sets = [set(a["members"]) for a in section if a.get("members")]
            assert any(s == leaves for s in member_sets), (
                f"{name}:{key} 섹션 말단 전부를 선택하는 총계 버튼이 없음")
