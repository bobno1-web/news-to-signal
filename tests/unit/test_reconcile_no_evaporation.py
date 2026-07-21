"""
루프9 개발방 단위 테스트(증발 방어 — tests/unit, 원칙 10: 개발방 자체 테스트는 여기).

blocker: Opus 배치가 보낸 대표 수보다 적게 반환하면 뉴스가 조용히 증발했다(원칙 7 위반).
방어(engine._reconcile_returns / judge_items): 입력 items 전부에 정확히 1건의 Judgment 가
되도록 대조·복구한다 — 누락은 삭제 대신 강제기권(unjudged=True)으로 보존, 중복은 첫 것만,
유령(입력에 없는 번호)은 채택 안 함.

API 없이 검증: _judge_chunk 를 몽키패치해 '일부만/중복/유령을 반환하는 배치'를 주입한다.
검증방 소유 불변식(tests/invariants/)은 별도다 — 여기선 개발방이 자기 시공을 못박는다.
"""
import os

import pytest

from src.judge import engine
from src.judge.engine import judge_items
from src.judge.schema import AccountLink, Evidence, Judgment, Magnitude
from src.sources.base import NewsItem


def _items(n):
    return [NewsItem(title=f"뉴스{i}", snippet=f"내용{i}", link=f"http://x/{i}",
                     published="2026-07-18", source_index=i) for i in range(n)]


def _good(si):
    """정상(판단된) Judgment — 근거·계정 동반."""
    return Judgment(
        source_index=si, relevant=True, relevance_reason="r", direction="악재",
        direction_reason="dr", stage="미상", intrinsic_risk=False, intrinsic_risk_reason="",
        magnitude=Magnitude(size="미상", certainty="불명", reason="x"),
        evidence=[Evidence(quote="근거문장", field="제목")], confidence="보통",
        abstained=False, abstain_reason="", severity_rank=50, one_line_reason="이유",
        account_links=[AccountLink(account_group="매출액", quote="q", field="제목", reason="왜")],
    )


@pytest.fixture
def _key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-monkeypatched")  # 실호출은 _judge_chunk 패치로 차단


def _patch_chunk(monkeypatch, fn):
    monkeypatch.setattr(engine, "_judge_chunk", fn)


def test_missing_row_becomes_unjudged_not_evaporated(_key, monkeypatch):
    """배치가 5건 중 4건만 반환(하나 드롭) → 누락은 증발 대신 '미판단'으로 보존."""
    drop = 3
    _patch_chunk(monkeypatch, lambda c, s, co, sl, a, f, on_usage=None:
                 [_good(it.source_index) for it in sl if it.source_index != drop])

    res = judge_items("모의사", _items(5), ["매출액"], {}, chunk=5)

    assert [j.source_index for j in res] == [0, 1, 2, 3, 4]      # 보존(증발 0)·입력 순서
    assert len(res) == 5                                          # 입력 == 반환
    miss = next(j for j in res if j.source_index == drop)
    assert miss.unjudged and miss.abstained                      # 미판단 강제기권
    assert miss.evidence == [] and miss.account_links == []      # 지어냄 없음(원칙 3)
    assert all(not j.unjudged for j in res if j.source_index != drop)


def test_duplicate_and_phantom_returns_are_reconciled(_key, monkeypatch):
    """중복(같은 번호 2번)·유령(입력에 없는 번호)·드롭이 섞여도 입력에 1:1로 맞춘다."""
    def fn(c, s, co, sl, a, f, on_usage=None):
        r = [_good(it.source_index) for it in sl]
        r.append(_good(1))       # 중복
        r.append(_good(99))      # 유령
        return [j for j in r if j.source_index != 2]   # 2 드롭

    _patch_chunk(monkeypatch, fn)
    res = judge_items("모의사", _items(4), ["매출액"], {}, chunk=4)

    assert sorted(j.source_index for j in res) == [0, 1, 2, 3]   # 정확히 입력 4건
    assert len(res) == 4                                          # 중복/유령으로 안 늘어남
    assert next(j for j in res if j.source_index == 2).unjudged  # 드롭 → 미판단
    assert not next(j for j in res if j.source_index == 1).unjudged  # 중복 첫 것은 정상 채택


def test_all_returned_is_unchanged(_key, monkeypatch):
    """전건 정상 반환이면 아무것도 미판단이 아니고 그대로 통과(방어가 정상 경로를 안 건드림)."""
    _patch_chunk(monkeypatch, lambda c, s, co, sl, a, f, on_usage=None:
                 [_good(it.source_index) for it in sl])

    res = judge_items("모의사", _items(6), ["매출액"], {}, chunk=2)   # 3청크

    assert [j.source_index for j in res] == [0, 1, 2, 3, 4, 5]
    assert all(not j.unjudged for j in res)
