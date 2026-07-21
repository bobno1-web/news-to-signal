"""
tests/invariants/test_survivor_judgment_conservation.py  (검증방 소유 — 루프9)

불변식(대표(survivor) 수 == 판단(judgment) 수 — Opus 경계에서 뉴스 증발 0):
총괄검증방이 전수검증에서 '뉴스 1건이 화면 어디에도 없이 증발'하는 blocker 를 실측했다
(태영건설 라이브: survivors 206 → judgments 205, 1건 증발). 원인은 Opus 배치가 입력보다
적게(또는 어긋나게) 돌려줘도 judge_items 가 그걸 검출하지 못해 조용히 사라진 것.
처방(harness/pending/new-invariants/total-verify-survivor-judgment-conservation.md #1)에 따라
검증방이 이 불변식을 세운다 — 개발방 unit 테스트(tests/unit/test_reconcile_no_evaporation.py)는
'응시자 자체 테스트'라 검증 기준이 아니다(원칙 10: 채점자·응시자 분리).

여기서 못박는 안전 성질(원칙 7 증발 0 · 원칙 4 누락 금지 · 원칙 3 날조 금지):
- judge_items 는 입력 items 의 모든 source_index 에 대해 '정확히 1건'의 Judgment 를 반환한다.
  즉 반환 source_index 집합 == 입력 집합 (누락 0 · 중복 0 · 유령 0). 이는 뺄셈이 아니라
  두 집합의 독립 대조다.
- 누락분(모델이 안 돌려준 건)은 삭제 대신 '미판단'(unjudged=True·abstained=True)으로 보존되되,
  근거·계정·규모·심각도를 지어내지 않는다(전부 비어 있음 — 원칙 3).
- 출력은 입력 순서로 재조립된다(결정론).
- 미판단은 판단을 오염시키지 않는다: 심각도 정렬에서 실제 판단 위로 올라오지 않고(rank 0),
  계정 라우팅에서 특정 담당자 큐(①)로 잘못 새지 않는다(항상 ②미배정 공용 큐).

방법: 실제 API 없이 _judge_chunk 를 몽키패치해 '부분/중복/유령/전멸' 반환을 주입한다.
      (증발은 이 경계에서만 나므로, 여기서 재현·방어를 독립 확인한다.)

수정 전 코드(reconcile 없음)에선 이 불변식이 빨강, 수정 후엔 초록이어야 한다 —
그 red→green 은 별도 검증 하네스로 실증한다(reconcile 를 identity 로 되돌리면 conservation
단언이 깨지는지). 이 파일 자체는 '수정 후=초록'을 못박는다.
"""

import pytest

from src.judge import engine
from src.judge.engine import judge_items
from src.judge.schema import AccountLink, Evidence, Judgment, Magnitude
from src.ranking import classify_and_rank, rank_by_severity
from src.sources.base import NewsItem


def _items(n, start=0):
    return [NewsItem(title=f"(가상)뉴스{i}", snippet=f"내용{i}", link=f"http://x/{i}",
                     published="2026-07-10", source_index=i) for i in range(start, start + n)]


def _judged(si, *, rank=50, account="매출액"):
    """정상적으로 '판단된' Judgment(근거·계정 동반) — 미판단과 대비용."""
    return Judgment(
        source_index=si, relevant=True, relevance_reason="r", direction="악재",
        direction_reason="dr", stage="혐의", intrinsic_risk=True, intrinsic_risk_reason="ir",
        magnitude=Magnitude(size="크다", certainty="확정", reason="x", denominator="매출액",
                            amount_krw=10, amount_is_clear=True, amount_quote="10원"),
        evidence=[Evidence(quote="원문 근거 문장", field="제목")], confidence="높음",
        abstained=False, abstain_reason="", severity_rank=rank, one_line_reason="이유",
        account_links=[AccountLink(account_group=account, quote="q", field="제목", reason="왜")],
    )


@pytest.fixture
def _dummy_key(monkeypatch):
    # judge_items 는 키가 없으면 즉시 멈춘다(원칙 4). 실제 호출은 _judge_chunk 패치로 차단하므로
    # 더미 키만 넣어 그 관문을 통과시킨다(네트워크로 나가지 않는다).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-used-because-chunk-is-patched")


def _returned_indices(res):
    return [j.source_index for j in res]


def _assert_honest_missing(j: Judgment):
    """미판단 항목은 보존되되 아무것도 지어내지 않는다(원칙 3)."""
    assert j.unjudged is True, "누락 보존분은 unjudged=True 여야 한다(화면에 미판단으로 드러남)."
    assert j.abstained is True, "미판단은 강제기권이어야 한다(비-기권으로 위장 금지)."
    assert j.relevant is False, "미판단이 relevant=True 로 위험을 주장하면 안 된다(지어낸 판단)."
    assert j.evidence == [], "미판단에 근거를 지어내면 안 된다(원칙 3)."
    assert j.account_links == [], "미판단에 계정 연결을 지어내면 안 된다(원칙 3)."
    assert j.account_abstained is True, "미판단은 계정 기권이어야 한다."
    assert j.magnitude.size in ("미상", "해당없음"), f"미판단 규모를 지어냈다: size={j.magnitude.size}"
    assert j.magnitude.amount_krw == 0 and j.magnitude.ratio_pct is None, "미판단에 규모 수치를 지어냈다."
    assert j.severity_rank == 0, f"미판단에 심각도를 지어냈다: rank={j.severity_rank}"


# ── 핵심 불변식: 반환 집합 == 입력 집합 (누락 0·중복 0·유령 0) ────────────────────

def test_returned_set_equals_input_set_on_partial_return(_dummy_key, monkeypatch):
    """
    배치가 일부만 반환해도 judge_items 반환 source_index 집합이 입력 집합과 '정확히' 같다.
    (독립 집합 대조 — collected-shown 같은 뺄셈이 아니다.) 누락분은 미판단으로 보존.
    """
    drop = {2, 5}  # 여러 청크에 걸쳐 떨군다
    monkeypatch.setattr(engine, "_judge_chunk",
                        lambda c, s, co, sl, a, f, on_usage=None:
                        [_judged(it.source_index) for it in sl if it.source_index not in drop])

    res = judge_items("가상증발제조", _items(8), ["매출액"], {}, chunk=3)   # 3청크(3·3·2)

    got = set(_returned_indices(res))
    assert got == set(range(8)), f"반환 집합≠입력 집합(증발/유령): got={sorted(got)}"
    assert len(res) == 8, f"정확히 1:1 이 아니다(중복/누락): len={len(res)}"
    for si in drop:
        _assert_honest_missing(next(j for j in res if j.source_index == si))
    for j in res:
        if j.source_index not in drop:
            assert not j.unjudged, f"정상 반환분을 미판단으로 오염: si={j.source_index}"


def test_whole_chunk_dropped_all_preserved(_dummy_key, monkeypatch):
    """한 청크가 통째로 빈 응답이어도 그 청크의 모든 뉴스가 미판단으로 보존된다(전멸 방어)."""
    # chunk=2, 8건 → 4청크. 두 번째 청크(2,3)를 통째로 비운다.
    def fn(c, s, co, sl, a, f, on_usage=None):
        if {it.source_index for it in sl} == {2, 3}:
            return []
        return [_judged(it.source_index) for it in sl]

    monkeypatch.setattr(engine, "_judge_chunk", fn)
    res = judge_items("가상증발제조", _items(8), ["매출액"], {}, chunk=2)

    assert _returned_indices(res) == list(range(8)), "전멸 청크에서 뉴스가 증발했다(입력 순서 보존 실패)."
    for si in (2, 3):
        _assert_honest_missing(next(j for j in res if j.source_index == si))


def test_duplicate_and_phantom_do_not_inflate_or_evaporate(_dummy_key, monkeypatch):
    """중복(같은 번호 2번)·유령(입력에 없는 번호)이 섞여도 입력에 1:1. 중복 첫 것만, 유령 불채택, 드롭→미판단."""
    def fn(c, s, co, sl, a, f, on_usage=None):
        r = [_judged(it.source_index) for it in sl]
        r.append(_judged(1))     # 중복
        r.append(_judged(999))   # 유령(입력에 없음)
        return [j for j in r if j.source_index != 4]   # 4 드롭

    monkeypatch.setattr(engine, "_judge_chunk", fn)
    res = judge_items("가상증발제조", _items(6), ["매출액"], {}, chunk=6)

    assert sorted(_returned_indices(res)) == list(range(6)), "중복/유령/드롭이 1:1 을 깼다."
    assert len(res) == 6, "중복/유령으로 건수가 부풀거나 줄었다."
    assert _returned_indices(res).count(1) == 1, "중복 번호가 두 번 실렸다."
    assert 999 not in _returned_indices(res), "유령(입력에 없는 번호)이 채택됐다(가짜 항목)."
    _assert_honest_missing(next(j for j in res if j.source_index == 4))
    assert not next(j for j in res if j.source_index == 1).unjudged, "중복의 첫 것은 정상 판단이어야 한다."


def test_all_returned_untouched(_dummy_key, monkeypatch):
    """전건 정상 반환이면 방어가 정상 경로를 건드리지 않는다(미판단 0, 순서·판단 보존)."""
    monkeypatch.setattr(engine, "_judge_chunk",
                        lambda c, s, co, sl, a, f, on_usage=None:
                        [_judged(it.source_index) for it in sl])
    res = judge_items("가상증발제조", _items(7), ["매출액"], {}, chunk=2)   # 4청크
    assert _returned_indices(res) == list(range(7))
    assert all(not j.unjudged for j in res), "정상 경로에서 미판단이 생겼다(과잉 방어)."


# ── 미판단이 판단을 오염하지 않는다(원칙 3·5·7 — 검증 3) ──────────────────────────

def test_missing_does_not_outrank_real_findings(_dummy_key, monkeypatch):
    """미판단(rank 0)은 심각도 정렬에서 실제 판단 위로 올라오지 못한다(맨 아래)."""
    monkeypatch.setattr(engine, "_judge_chunk",
                        lambda c, s, co, sl, a, f, on_usage=None:
                        [_judged(it.source_index, rank=10 + it.source_index * 10)
                         for it in sl if it.source_index != 0])   # 0 드롭 → 미판단

    res = judge_items("가상증발제조", _items(4), ["매출액"], {}, chunk=4)
    ranked = rank_by_severity(res)
    assert ranked[-1].source_index == 0 and ranked[-1].unjudged, (
        "미판단이 심각도 정렬에서 실제 판단보다 위로 올라왔다(rank 0 인데 상단 노출)."
    )


def test_missing_routes_to_unassigned_not_analyst_queue(_dummy_key, monkeypatch):
    """미판단은 계정 라우팅에서 특정 담당자 큐(①)로 새지 않고 항상 ②미배정(공용)에 남는다."""
    monkeypatch.setattr(engine, "_judge_chunk",
                        lambda c, s, co, sl, a, f, on_usage=None:
                        [_judged(it.source_index, account="매출액")
                         for it in sl if it.source_index != 1])   # 1 드롭 → 미판단

    res = judge_items("가상증발제조", _items(3), ["매출액"], {}, chunk=3)
    # 담당자가 '매출액'을 골라도, 미판단(계정 없음)은 ①이 아니라 ②미배정으로 가야 한다.
    baskets = {b.key: b for b in classify_and_rank(res, ["매출액"])}
    mine_idx = {j.source_index for j in baskets["mine"].items}
    unassigned_idx = {j.source_index for j in baskets["unassigned"].items}
    assert 1 not in mine_idx, "미판단이 담당자 큐(①)로 잘못 라우팅됐다(계정을 지어낸 셈)."
    assert 1 in unassigned_idx, "미판단이 ②미배정 공용 큐에 보존되지 않았다(숨김 — 원칙 7 위반)."
