"""
tests/invariants/test_account_grounding.py  (검증방 소유 — 루프2 반날조)

검증 2 (반날조·근거 시험):
1) test_account_no_fabrication: 사건이 명백히 A 계정에 관한 것인데 울타리(DART 후보)에
   A 가 없으면, 시스템은 A 를 지어내면 안 된다. 최종 출력의 모든 계정 연결은 울타리
   안(계정군 수준)에 있어야 하고, A 는 나타나지 않아야 한다(엔진의 enforce_account_fence
   end-to-end 확인). 억지 특정 대신 기권/무형자산류 in-fence 판단이 정답.
2) test_account_link_grounding: 시스템이 만든 각 계정 연결의 근거 문장이 실제로 그
   연결을 뒷받침하는지 '독립' 판정한다. 우리 의견과의 일치가 아니라, 근거가 주장을
   지지하는지를 본다. 독립성을 위해 판단 모델(opus)과 다른 모델(haiku)을 회의자로 쓴다.
   또한 근거가 원문에서 추적되는지(구조적 반날조)도 함께 본다.

실제 API 호출. conftest 가 키를 주입한다.
"""

import json
import os

import anthropic

from tests.invariants._harness import (
    SAMPLE_ACCOUNTS, Case, judge_cases, quote_traceable,
)

_SKEPTIC_MODEL = "claude-haiku-4-5-20251001"  # 판단 모델(opus)과 다른 모델 = 독립성
_SUPPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"supports": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["supports", "reason"],
}


def _independent_supports(news_title: str, news_snippet: str,
                          account_group: str, quote: str) -> tuple[bool, str]:
    """
    독립 회의자(다른 모델): 이 근거 문장이 '이 뉴스가 이 계정군과 관련 있다'는 가설을
    뒷받침하는가? 뒷받침하면 supports=true. (우리 결론과 무관하게 근거→주장 지지만 본다.)
    """
    client = anthropic.Anthropic()
    system = (
        "너는 독립 검증자다. 주어진 뉴스와 '계정군 연결 가설', 그 근거 문장을 보고, "
        "근거 문장이 그 계정군과의 관련성을 합리적으로 뒷받침하는지만 판정한다. "
        "결론을 새로 만들지 말고, 오직 '이 근거가 이 계정군 연결을 지지하는가'만 본다. "
        "지지하면 supports=true, 근거가 무관하거나 계정군을 뒷받침 못 하면 false."
    )
    user = (
        f"[뉴스 제목] {news_title}\n[뉴스 스니펫] {news_snippet}\n\n"
        f"[계정군 연결 가설] {account_group}\n[제시된 근거 문장] \"{quote}\"\n\n"
        f"이 근거 문장이 '{account_group}' 계정군과의 관련성을 뒷받침하는가?"
    )
    resp = client.messages.create(
        model=_SKEPTIC_MODEL,
        max_tokens=1000,
        output_config={"format": {"type": "json_schema", "schema": _SUPPORT_SCHEMA}},
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = next(b.text for b in resp.content if b.type == "text")
    data = json.loads(text)
    return bool(data["supports"]), data.get("reason", "")


# ── 1) 날조 방지: 울타리 밖 계정을 지어내지 않는가 ────────────────────────────
_GOODWILL = Case(
    key="영업권손상",
    title="(가상)태산홀딩스, 인수한 자회사 실적 부진에 영업권 손상 가능성 부각",
    snippet="태산홀딩스가 수년 전 인수한 자회사의 실적 부진이 이어지면서, 인수 당시 계상한 "
            "영업권에 대한 손상차손 인식 가능성이 거론된다.",
)
# 울타리에서 '영업권'을 일부러 뺀다(무형자산은 남긴다 → in-fence 대안 판단은 허용).
_ACCOUNTS_NO_GOODWILL = [a for a in SAMPLE_ACCOUNTS if a != "영업권"]


def _in_fence(group: str, allowed: list[str]) -> bool:
    g = "".join(group.split())
    if len(g) < 2:
        return False
    for a in allowed:
        an = "".join(a.split())
        if g == an or g in an or an in g:
            return True
    return False


def test_account_no_fabrication():
    by_key, _ = judge_cases("태산홀딩스", [_GOODWILL], accounts=_ACCOUNTS_NO_GOODWILL)
    j = by_key["영업권손상"]
    groups = [l.account_group for l in j.account_links]
    print("\n[test_account_no_fabrication] (울타리에 '영업권' 없음)")
    print("  relevant=%s account_abstained=%s links=%s" % (j.relevant, j.account_abstained, groups))

    # end-to-end 울타리: 살아남은 모든 연결은 후보 안(계정군 수준)에 있어야 한다.
    out_of_fence = [g for g in groups if not _in_fence(g, _ACCOUNTS_NO_GOODWILL)]
    assert not out_of_fence, (
        f"울타리 밖 계정이 최종 출력에 살아남았다: {out_of_fence}. enforce_account_fence 가 뚫렸다."
    )
    # 특히 후보에 없는 '영업권'을 정확히 지어내지 않았는가.
    assert all("영업권" != "".join(g.split()) for g in groups), (
        "후보에 없는 '영업권'을 그대로 지어냈다(날조)."
    )


# ── 2) 근거→주장 지지 독립 판정 + 원문 추적 ──────────────────────────────────
_CLEAR_DEBT = Case(
    key="차입금",
    title="(가상)금호정밀, 주채권은행과 단기차입금 만기 연장 협상 결렬",
    snippet="금호정밀이 주채권은행과의 단기차입금 만기 연장 협상이 결렬되면서 차입금 상환 부담이 "
            "커졌다. 회사는 자금 조달 방안을 재검토하고 있다.",
)


def test_account_link_grounding():
    by_key, _ = judge_cases("금호정밀", [_CLEAR_DEBT], accounts=SAMPLE_ACCOUNTS)
    j = by_key["차입금"]
    print("\n[test_account_link_grounding] links=%d" % len(j.account_links))
    assert j.account_links, "명확한 차입금 사건인데 계정 연결이 없다(대조 성립 실패)."

    for l in j.account_links:
        traced = quote_traceable(l.quote, _CLEAR_DEBT)
        supports, reason = _independent_supports(
            _CLEAR_DEBT.title, _CLEAR_DEBT.snippet, l.account_group, l.quote
        )
        print(f"  연결 [{l.account_group}] 근거=\"{l.quote}\"")
        print(f"     원문추적={traced} / 독립회의자 지지={supports} ({reason[:50]})")

        # 구조적 반날조: 근거는 원문에서 추적돼야 한다(지어낸 문장 금지).
        assert traced, (
            f"계정 연결 근거가 원문에서 추적되지 않는다(날조 가능): [{l.account_group}] \"{l.quote}\""
        )
        # 독립 판정: 근거가 그 계정군 연결을 뒷받침해야 한다.
        assert supports, (
            f"독립 회의자가 근거→계정군 지지를 부정: [{l.account_group}] \"{l.quote}\" — {reason}"
        )
