"""
tests/invariants/test_prescreen.py  (검증방 소유 — 루프6b)

불변식(입구 깔때기의 거짓 탈락·오병합 방지 — 원칙 7·1):
루프6b는 Opus 풀판단 '앞'에 더 싼 모델(Haiku)의 깔때기(prescreen)를 두었다 — 중복 정리 +
간이 관련성 관문. 더 약한 모델이 앞단에서 거르므로 '거짓 탈락'과 '거짓 병합'의 새 표면이
생긴다. prescreen.py 스스로 "이 불변식은 검증방이 세워야 한다"고 넘겼다(원칙 10).

여기서 못박는 안전 성질(효율이 아니라 안전):
- 명백한 재무·감사 악재는 깔때기에서 살아남아 Opus 로 간다(간이 관문이 떨구지 않는다).
- 서로 다른 사건(부산공장/울산공장 화재)은 하나로 병합되지 않는다(오병합=증발 금지).
- 아무것도 삭제되지 않는다: survivors + folded == collected(수집=대표+접힘).
- (효율, 소프트) 같은 사건 중복은 대표로 묶이고, 명백 무관은 접힌다.

실제 Anthropic(Haiku) 호출. 키 없으면 skip 아닌 실패로 둔다(검증 못 함을 드러냄).
"""

from src.judge.prescreen import prescreen
from src.sources.base import NewsItem

_CO = "가상깔때기제조"

# (A) 같은 하나의 사건(워크아웃 신청)을 여러 매체가 보도한 중복 3건 — 대표로 묶여야(효율).
_DUP_EVENT = [
    ("워크A", "(가상)가상깔때기제조, 주채권은행에 워크아웃 신청",
     "가상깔때기제조가 유동성 악화로 주채권은행에 워크아웃(기업개선작업)을 신청했다."),
    ("워크B", "(가상)가상깔때기제조 워크아웃 개시 신청…채권단 검토 착수",
     "가상깔때기제조가 워크아웃을 신청하면서 채권단이 기업개선작업 개시 여부 검토에 들어갔다."),
    ("워크C", "[속보](가상)가상깔때기제조, 워크아웃 신청서 제출",
     "가상깔때기제조가 오늘 주채권은행에 워크아웃 신청서를 제출했다고 밝혔다."),
]
# (B) 각기 다른 명백 악재 5건 — 전부 살아남아야(거짓 탈락 금지, 원칙 7).
_BAD = [
    ("분식", "(가상)가상깔때기제조 '가공매출로 실적 부풀렸다' 내부폭로",
     "전직 재무임원이 회사가 가공매출로 실적을 부풀리도록 지시했다고 폭로했고 감독당국이 회계처리 점검에 착수했다."),
    ("소송", "(가상)가상깔때기제조, 3천억원 손해배상 소송 피소",
     "핵심 거래처가 공급계약 위반을 이유로 3천억원 규모 손해배상 청구 소송을 제기했다."),
    ("감사의견", "(가상)가상깔때기제조 감사인, 계속기업 불확실성에 '의견거절' 검토",
     "외부감사인이 계속기업 존속능력에 중대한 불확실성이 있다며 감사의견 거절 가능성을 내비쳤다."),
    ("횡령", "(가상)가상깔때기제조 자금팀장 200억 횡령 혐의 구속",
     "자금팀장이 회사 자금 약 200억원을 빼돌린 혐의로 구속됐고 내부통제 부실 정황이 드러났다."),
    ("디폴트", "(가상)가상깔때기제조 회사채 차환 실패…디폴트 우려",
     "만기 도래 회사채 차환에 실패하면서 채무불이행(디폴트) 우려가 커지고 있다."),
]
# (C) 표현은 비슷하나 '다른 사건' — 병합되면 안 된다(오병합=증발, 원칙 7).
_DIFF = [
    ("부산화재", "(가상)가상깔때기제조 부산공장 화재로 생산라인 전소",
     "가상깔때기제조 부산공장에서 화재가 발생해 생산라인이 전소됐다. 재고·유형자산 손실이 예상된다."),
    ("울산화재", "(가상)가상깔때기제조 울산공장서도 화재…이틀 새 두 번째",
     "가상깔때기제조 울산공장에서도 화재가 발생했다. 부산공장 화재와는 별개 사건으로 원인은 조사 중이다."),
]
# (D) 명백 무관(간이 관문이 접어야 — 단, 접힘=삭제 아님).
_NOISE = [
    ("봉사", "(가상)가상깔때기제조 대표, 연말 연탄나눔 봉사활동 참여",
     "가상깔때기제조 대표가 지역 저소득층을 위한 연탄나눔 봉사활동에 참여했다."),
    ("광고", "(가상)가상깔때기제조, 신제품 광고모델에 인기 아이돌 발탁",
     "가상깔때기제조가 신제품 홍보를 위해 인기 아이돌을 광고모델로 발탁했다."),
]
_ALL = _DUP_EVENT + _BAD + _DIFF + _NOISE


def _items():
    return [NewsItem(title=t, snippet=s, link=f"http://x/{k}", published="2026-07-10", source_index=i)
            for i, (k, t, s) in enumerate(_ALL)]


_KEY_BY_IDX = {i: k for i, (k, _, _) in enumerate(_ALL)}


def _run():
    res = prescreen(_CO, _items())
    surv = {_KEY_BY_IDX[it.source_index] for it in res.survivors}
    fold = {_KEY_BY_IDX[it.source_index]: reason for it, reason in res.folded}
    return res, surv, fold


def test_prescreen_conserves_all():
    """수집 = 대표(survivors) + 접힘(folded). 아무것도 증발하지 않는다(원칙 7)."""
    res, surv, fold = _run()
    print("\n[prescreen] collected=%d survivors=%d folded=%d counts=%s"
          % (len(_ALL), len(res.survivors), len(res.folded), res.counts))
    print("  survivors:", sorted(surv))
    print("  folded:", {k: v[:14] for k, v in fold.items()})
    assert len(res.survivors) + len(res.folded) == len(_ALL), (
        f"증발: survivors {len(res.survivors)} + folded {len(res.folded)} ≠ collected {len(_ALL)}"
    )


def test_clear_bad_survives_prescreen():
    """명백한 재무·감사 악재는 간이 관문에서 떨어지지 않고 Opus 로 간다(거짓 탈락 금지)."""
    _, surv, fold = _run()
    for k, _, _ in _BAD:
        assert k in surv, f"거짓 탈락: 명백 악재 '{k}' 가 간이 관문에서 접혔다(사유={fold.get(k)})."
    # 워크아웃 사건도 '대표'로 반드시 하나는 살아남는다(사건 자체가 사라지면 안 된다).
    assert surv & {"워크A", "워크B", "워크C"}, "워크아웃 사건이 대표 없이 통째로 사라졌다."


def test_different_events_not_merged():
    """표현이 비슷해도 다른 사건(부산/울산 화재)은 병합되지 않는다(오병합=증발, 원칙 7)."""
    _, surv, fold = _run()
    assert "부산화재" in surv and "울산화재" in surv, (
        f"다른 사건이 병합됐다: 부산 in surv={'부산화재' in surv}, 울산 in surv={'울산화재' in surv} "
        f"(부산 fold={fold.get('부산화재')}, 울산 fold={fold.get('울산화재')})"
    )


def test_pure_noise_folded_but_preserved():
    """명백 무관(봉사·광고)은 접히되(간이 무관), 삭제가 아니라 folded 로 보존된다."""
    res, surv, fold = _run()
    for k in ("봉사", "광고"):
        assert k in fold, f"명백 무관 '{k}' 가 접히지 않고 통과했다(간이 관문이 너무 헐거움)."
        assert "무관" in fold[k], f"'{k}' 접힘 사유가 '무관'이 아니다: {fold[k]}"
    all_keys = {k for k, _, _ in _ALL}
    assert (surv | set(fold)) == all_keys, "일부 뉴스가 survivors/folded 어디에도 없다(증발)."


def test_same_event_duplicates_collapse():
    """같은 사건 중복(워크아웃 3건)은 대표로 묶인다(dedup 실재 — 효율). 과분할은 안전상 허용하되,
    '전부 생존'(=dedup 미작동)이면 실패로 드러낸다."""
    _, surv, fold = _run()
    dup_surv = surv & {"워크A", "워크B", "워크C"}
    dup_folded = {k for k in ("워크A", "워크B", "워크C") if k in fold}
    print("\n[dedup] 워크아웃 3건 → 생존 %s / 접힘(중복) %s" % (sorted(dup_surv), sorted(dup_folded)))
    assert len(dup_surv) <= 2, "같은 사건 3건이 하나도 안 묶였다(dedup 미작동)."
    for k in dup_folded:
        assert "중복" in fold[k], f"'{k}' 가 중복이 아닌 사유로 접혔다: {fold[k]}"
