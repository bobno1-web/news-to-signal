"""
tests/invariants/test_parallel_invariance.py  (검증방 소유 — 루프7)

불변식(병렬이 판단을 바꾸지 않는다 — D24): 판단 청크를 동시에 돌려도 결과 Judgment
리스트는 순차 실행과 정확히 같아야 한다(심각도·방향·계정·근거·순서 동일). 그리고 어떤
청크가 실패하면 조용히 건너뛰지 않고 예외가 전파돼야 한다(뉴스 유실 0 — 원칙 4·7).

방법:
- 같은 다중 청크 입력을 동시성 6(병렬)과 1(순차)로 각각 judge_items 에 태워 결과를 비교.
  LLM 청크 결과는 (모델·system·payload) 캐시로 결정론적이라, 두 실행의 차이는 오직
  '오케스트레이션'(재조립 순서·유실 여부)뿐 — 그게 이 테스트의 대상이다.
- 실패 전파: _judge_chunk 를 한 청크에서 예외를 던지게 바꿔치기해, judge_items 가
  부분 결과를 조용히 반환하지 않고 예외를 올리는지 본다.

실제 Anthropic 호출(작은 다중 청크, 캐시됨). 키 없으면 실패로 둔다.
"""

import dataclasses

import pytest

from src.judge import engine
from src.judge.engine import judge_items
from src.sources.base import NewsItem

_CO = "가상병렬제조"
# 5건 + chunk=2 → 3청크(2·2·1). 동시성이 실제로 개입하는 다중 청크.
_CASES = [
    ("적자", "(가상)가상병렬제조 3분기 영업손실 전환", "가상병렬제조가 3분기 영업손실로 돌아섰다."),
    ("소송", "(가상)가상병렬제조 특허 침해 소송 피소", "경쟁사가 특허 침해로 손해배상 소송을 냈다."),
    ("증설", "(가상)가상병렬제조 신규 라인 500억 투자", "가상병렬제조가 신규 생산라인에 500억원을 투자한다."),
    ("무혐의", "(가상)가상병렬제조 회계 의혹 무혐의 종결", "검찰이 회계 의혹을 무혐의로 종결했다."),
    ("봉사", "(가상)가상병렬제조 임직원 봉사활동", "임직원들이 지역 봉사활동에 참여했다."),
]
_ACCOUNTS = ["매출액", "매출원가", "유형자산", "매출채권및기타채권", "이익잉여금"]
_FIN = {"매출액": 500_000_000_000, "자산총계": 800_000_000_000, "자기자본": 300_000_000_000}


def _items():
    return [NewsItem(title=t, snippet=s, link="", published="2026-07-10", source_index=i)
            for i, (k, t, s) in enumerate(_CASES)]


def _run(concurrency: int):
    orig = engine._MAX_CONCURRENCY
    engine._MAX_CONCURRENCY = concurrency
    try:
        return judge_items(_CO, _items(), _ACCOUNTS, _FIN, chunk=2)
    finally:
        engine._MAX_CONCURRENCY = orig


def test_parallel_equals_sequential():
    parallel = _run(6)     # 병렬(동시 6)
    sequential = _run(1)   # 순차(동시 1)

    print("\n[parallel_invariance] 병렬 %d건 vs 순차 %d건" % (len(parallel), len(sequential)))
    # 1) 건수·순서(source_index) 동일
    assert [j.source_index for j in parallel] == [j.source_index for j in sequential] == [0, 1, 2, 3, 4], (
        f"재조립 순서/유실 이상: 병렬 {[j.source_index for j in parallel]} vs 순차 {[j.source_index for j in sequential]}"
    )
    # 2) 각 뉴스의 판단 전체(심각도·방향·계정·근거·규모)가 바이트 단위 동일
    for p, s in zip(parallel, sequential):
        assert dataclasses.asdict(p) == dataclasses.asdict(s), (
            f"병렬이 판단을 바꿨다 (source_index={p.source_index}): "
            f"dir {p.direction}/{s.direction} rank {p.severity_rank}/{s.severity_rank} "
            f"accts {[l.account_group for l in p.account_links]}/{[l.account_group for l in s.account_links]}"
        )
    print("  전 건 판단 동일(dir/rank/계정/근거/규모): OK")
    print("  예) rank들 병렬=%s 순차=%s" % ([j.severity_rank for j in parallel], [j.severity_rank for j in sequential]))


def test_failed_chunk_propagates_no_silent_loss():
    """한 청크가 실패하면 부분 결과를 조용히 반환하지 않고 예외가 전파된다(뉴스 유실 0)."""
    calls = {"n": 0}
    orig_chunk = engine._judge_chunk

    def boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("모의 청크 실패(429 소진 등)")

    engine._judge_chunk = boom
    try:
        with pytest.raises(RuntimeError, match="모의 청크 실패"):
            _run(6)
    finally:
        engine._judge_chunk = orig_chunk
    print("\n[failure] 청크 실패 시 judge_items 가 예외 전파(부분 반환 없음). 호출된 청크수=%d" % calls["n"])
    assert calls["n"] >= 1
