"""
tests/invariants/test_prescreen_phase_preservation.py  (검증방 소유 — 루프14 B1 후속)

■ 배경 (처방 prescreen-must-not-merge-event-phases.md §B)
루프14 개발방이 prescreen `_SYSTEM` 의 병합 단위를 '같은 사안'에서 '같은 사건의 같은 국면'으로
좁혔다(§A). 이 불변식은 그 수정이 되돌아가지 못하게 '코드 메커니즘'을 고정한다:
  서로 다른 국면(다른 event_id) → 각각 대표(survivor)로 살아남아 Opus 판단에 도달한다.
  특히 '과징금 확정'(충당부채 확정 = 감사상 가장 중대) 국면이 '중복'으로 접혀 사라지면 안 된다
  (release-criteria §1 '서로 다른 사건의 오병합' = blocker).

■ 무엇을 검증하고 무엇을 검증하지 않는가 (정직한 경계 — 원칙 4)
- 검증함(결정론): prescreen 의 '군집·대표·접힘' 코드가 event_id 를 '국면 보존'으로 다루는가.
  Haiku 호출을 합성 값으로 대체(monkeypatch)해, 판별 모델의 거동과 무관하게 코드만 실측한다.
  → 무API·무과금. 키 없이도 돈다(결정론 스위트 포함 가능).
- 검증 안 함(라이브 필요): '실제 Haiku 가 5국면에 서로 다른 event_id 를 매기는가'(프롬프트 거동).
  그건 유료 라이브 검증(test_prescreen.py 의 라이브 케이스 + 검증방 라이브 실측)의 몫이다.
  이 파일은 '코드가 국면을 보존할 준비가 됐는가'만 못박는다(프롬프트가 국면을 나눠 주면 코드가
  그걸 삼키지 않는다).

즉 이 불변식이 초록이어도 라이브 국면 거동은 별도로 확인해야 한다(억지 통과 주장 금지).
"""

import src.judge.prescreen as ps
from src.sources.base import NewsItem

# (label, event_id, relevant). 담합 5국면 + '과징금 확정' 매체중복 1 + 대조군(부산/울산 화재).
_SPEC = [
    ("심의착수",        1, True),
    ("첫재판",          2, True),
    ("과징금500억확정",  3, True),   # ★ 감사상 가장 중대 — 삼켜지면 실패
    ("과징금확정_매체B", 3, True),   # 같은 국면 중복(같은 event_id) → 접혀야(비용 억제 유지)
    ("집단소송",        4, True),
    ("정책검토",        5, True),
    ("부산공장화재",    6, True),
    ("울산공장화재",    7, True),
]


def _items():
    return [NewsItem(title=l, snippet=l + " 상세", link=f"http://x/{i}",
                     published="2026-07-10", source_index=i)
            for i, (l, _, _) in enumerate(_SPEC)]


def _run(monkeypatch):
    """더미 키로 guard 를 통과시키고 _screen 을 합성 결과로 대체(실 호출·과금 0)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "DUMMY-screen-is-patched-no-network")
    eid = {i: _SPEC[i][1] for i in range(len(_SPEC))}
    rel = {i: _SPEC[i][2] for i in range(len(_SPEC))}

    def fake_screen(client, company, batch, on_usage=None):
        return {it.source_index: {"relevant": rel[it.source_index], "event_id": eid[it.source_index]}
                for it in batch}

    monkeypatch.setattr(ps, "_screen", fake_screen)
    res = ps.prescreen("가상담합제조", _items())
    lab = {i: _SPEC[i][0] for i in range(len(_SPEC))}
    surv = {lab[it.source_index] for it in res.survivors}
    fold = {lab[it.source_index]: reason for it, reason in res.folded}
    return res, surv, fold


def test_distinct_phases_all_survive(monkeypatch):
    """서로 다른 국면(다른 event_id)은 각각 대표로 살아남아 Opus 판단으로 간다(국면 소실 금지)."""
    _, surv, fold = _run(monkeypatch)
    for phase in ("심의착수", "첫재판", "집단소송", "정책검토"):
        assert phase in surv, f"국면 '{phase}' 가 대표로 살아남지 못했다(fold={fold.get(phase)})."
    # 과징금 확정 국면: 매체중복 2건 중 '정확히 하나'가 대표로 생존(둘 다 접히면 국면 소실=blocker).
    assert ("과징금500억확정" in surv) or ("과징금확정_매체B" in surv), (
        "과징금 확정 국면이 통째로 접혔다 — 감사상 가장 중대한 국면 소실(release-criteria §1)."
    )


def test_same_phase_duplicate_folds(monkeypatch):
    """같은 국면의 매체 중복(같은 event_id)은 대표로 묶여 하나가 접힌다(비용 억제 유지)."""
    res, surv, fold = _run(monkeypatch)
    gwa_surv = {"과징금500억확정", "과징금확정_매체B"} & surv
    gwa_fold = {"과징금500억확정", "과징금확정_매체B"} & set(fold)
    assert len(gwa_surv) == 1 and len(gwa_fold) == 1, (
        f"같은 국면 중복이 안 묶였다(생존 {gwa_surv}, 접힘 {gwa_fold}) — dedup 미작동."
    )
    for k in gwa_fold:
        assert "중복" in fold[k], f"'{k}' 가 중복이 아닌 사유로 접혔다: {fold[k]}"


def test_different_events_not_merged(monkeypatch):
    """대조군: 표현이 비슷해도 다른 사건(부산/울산 화재)은 병합되지 않는다(오병합=증발 금지)."""
    _, surv, _ = _run(monkeypatch)
    assert "부산공장화재" in surv and "울산공장화재" in surv


def test_conservation(monkeypatch):
    """정합: 수집 = 대표(survivors) + 접힘(folded). 아무것도 증발하지 않는다(원칙 7)."""
    res, _, _ = _run(monkeypatch)
    assert len(res.survivors) + len(res.folded) == len(_SPEC)
    # distinct event_id 개수 == survivors 개수(국면이 event_id 단위로 정확히 보존됨).
    assert len({s[1] for s in _SPEC}) == len(res.survivors)
