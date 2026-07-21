"""
src/api/serialize.py

판단(Judgment)·바구니(Basket)를 화면(JSON)으로 바꾸는 '표시 변환'만 한다.
여기서 판단을 다시 내리거나 순서를 다시 매기지 않는다 — 정렬은 ranking 이 이미 했고,
이 파일은 그 결과를 읽어 옮길 뿐이다.

원칙 5(내부값 비노출)의 물리적 경계:
- 화면용 dict(judgment_view)에는 severity_rank 등 '내부 계산값'을 절대 담지 않는다.
  심각도는 '숫자'가 아니라 '리스트에서의 위치 + 말로 된 이유'로만 드러난다.
- '규모 미상'은 빈칸이 아니라 글자로 표기한다(routing-rules.md).

예시 모드(키 없이 결과 보기)를 위해 full_dump/load_full 로 '내부값 포함' 전체 판단을
디스크(out/results/)에 저장·복원한다. 이 전체 덤프는 서버 내부용이며 브라우저로는
절대 나가지 않는다(브라우저로는 judgment_view 만 나간다). 덤프에 API 키는 없다.
"""

from dataclasses import asdict
from email.utils import parsedate_to_datetime

from src.judge.schema import (
    AccountLink, Evidence, Judgment, Magnitude, is_peripheral_mention,
)
from src.pipeline import _financials_line, _fmt_pct
from src.ranking import Basket, rank_by_severity
from src.sources.base import NewsItem


# ── 기본 화면 표시 규칙 (표시 렌즈 — 기존 판단값을 '읽기만' 한다) ────────────────
#
# 루프5: 기본 화면에는 '재무·감사 악재'와 '위험 해소'만 노출하고, '단순 호재'·'무관'·
# '판단 기권'은 접는다. 이 분류는 새 고정표/키워드표가 아니라 LLM이 이미 내린 판단값
# (direction·relevant·stage·abstained)을 읽어 '어디에 배치할지'만 정한다(원칙 1·5·8).
#
# '위험 해소 vs 단순 호재'의 경계: 우리 스키마에 'direction=해소' 값은 없다. 프롬프트·
# severity-axes 가 정한 대로, 리스크 해소(무혐의·취하)는 direction=호재/중립 + stage=
# '무혐의/취하'로 인코딩된다. 따라서 '위험 해소'는 stage=='무혐의/취하'(기존 판단값)로
# 읽는다 — 뉴스 본문에서 '무혐의' 같은 단어를 찾는 것이 아니다(그건 원칙 1 위반).
#   · 남는 공백(검증방 몫): 비법적 해소(예: 워크아웃 졸업)는 stage 축에 안 담겨 '단순
#     호재'로 접힐 수 있다. 이는 표시층이 아니라 판단층(새 판단 필드) 문제이므로 이번
#     스코프(표시만)에서 손대지 않고 관측으로 남긴다.

def is_resolution(j: Judgment) -> bool:
    """위험 해소 신호인가 = 기존 판단값 stage 가 '무혐의/취하'(리스크 프로세스의 favorable 종결)."""
    return j.stage == "무혐의/취하"


def default_visible(j: Judgment) -> bool:
    """기본 화면(심각도순·계정별 공통) 노출 여부. 기존 판단값만 읽는다(재계산·재판단 없음).
    노출: 재무·감사 악재 + 위험 해소 + 감사 확인 요망(루프11).  접힘: 단순 호재 + 무관 + 기권."""
    if j.abstained:
        return False            # 근거 부족으로 강등된 판단 — 접되 접근 가능(원칙 3+7)
    if not j.relevant:
        return False            # 재무·감사 무관 — 접힘
    if j.direction == "악재":
        return True             # 재무·감사 악재 — 노출
    if is_resolution(j):
        return True             # 위험 해소(호재/중립 + stage 무혐의·취하) — 노출
    if getattr(j, "audit_attention", False):
        return True             # 감사 확인 요망(루프11) — 방향이 중립/호재여도 노출(판단값 읽기, 원칙 1·5·8)
    return False                # 단순 호재 / 관련 중립(감사중요 아님) — 접힘


def fold_reason(j: Judgment) -> str:
    """접힌 이유(감사인이 '무엇이 걸러졌나'를 알도록). 판단값을 사람 말로 옮길 뿐."""
    if getattr(j, "unjudged", False):
        return "⚠ 미판단(판단 응답 누락 — 시스템이 판단 못 함, 사람 확인 필요)"
    if j.abstained:
        return "판단 기권(근거 부족)"
    if not j.relevant:
        return "재무·감사 무관"
    if j.direction == "호재":
        return "단순 호재(위험 해소 아님)"
    return "중립(위험 신호 아님)"


# ── 화면용(브라우저로 나가는) 변환 — 내부값(severity_rank) 비노출 ─────────────

def magnitude_view(m: Magnitude) -> str:
    """규모를 사람이 검증 가능한 '말'로. 미상은 빈칸이 아니라 글자로."""
    if m.ratio_pct is not None:                       # 코드가 계산한 비율 앵커
        return f"{m.denominator} 대비 {_fmt_pct(m.ratio_pct)}"
    if m.size == "미상":
        return "규모 미상(금액 불명 — 사람이 확인 필요)"
    if m.size == "해당없음":
        return "규모 해당없음"
    return f"규모 {m.size}"


def account_tags(j: Judgment) -> list[str]:
    """1층 계정 태그(가설). 중복 제거·순서 유지. 비면 미배정/특정불가."""
    return list(dict.fromkeys(link.account_group for link in j.account_links))


def _published_display(published: str) -> str:
    """RFC-822 발행일(예: 'Mon, 15 Jul 2024 09:00:00 +0900')을 'YYYY-MM-DD'로 변환.
    비었거나 못 읽으면 ''(화면에서 '날짜 미상'으로 표기 — 날짜를 지어내지 않는다, 원칙 4)."""
    if not published or not str(published).strip():
        return ""
    try:
        dt = parsedate_to_datetime(published)
    except (TypeError, ValueError, IndexError, OverflowError):
        return ""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d")


def _dedup_hypotheses(account_links) -> list[dict]:
    """계정 가설을 계정군 기준으로 중복 제거(첫 등장 유지, 순서 보존). 같은 계정을 두 번
    싣지 않는다 — 계정 연결은 AI 추론이라 계정별로 '이유+근거'가 한 번씩만 보이면 된다(원칙 3)."""
    out: list[dict] = []
    seen: set = set()
    for link in account_links:
        if link.account_group in seen:
            continue
        seen.add(link.account_group)
        out.append({"account_group": link.account_group, "reason": link.reason, "quote": link.quote})
    return out


def judgment_view(j: Judgment, item: NewsItem | None) -> dict:
    """
    뉴스 1건의 화면 표현(1층 + 2층). severity_rank 등 내부값은 담지 않는다(원칙 5).
    심각도 순위는 이 dict 에 숫자로 넣지 않고, 리스트에서의 '위치'로만 전달된다.

    카드 다이어트:
    - 1층 뱃지는 '상태(악재/호재/위험해소/기권/중립)'와 '계정'만 남긴다. 규모(magnitude)
      뱃지는 카드에서 뺀다 — 규모는 판단·정렬에는 그대로 쓰되 뱃지로 강조하지 않는다.
    - 발행일(published_display)을 1층에 함께 싣는다(못 읽으면 '' → 화면 '날짜 미상').
    - 2층은 핵심 둘만: (a) 방향 근거 문장 하나(왜 악재/호재인가) = evidence[0].quote,
      (b) 계정 가설(왜 이 계정인가) = 계정별 이유+근거(같은 계정 중복 없음 — 원칙 3, 검증 필요).
      방향 사유 문장·규모 문장/비율 상세는 뺀다(방향은 뱃지에, 규모는 의도적으로 비강조).
    """
    published = item.published if item else ""
    return {
        "source_index": j.source_index,
        # 1층(항상): 상태 뱃지 + 제목 + 발행일 + 한 줄 이유 + 계정 뱃지
        "title": item.title if item else f"(원문 없음 #{j.source_index})",
        "link": item.link if item else "",
        "published": published,
        "published_display": _published_display(published),
        "direction": j.direction,
        "resolution": is_resolution(j),                        # 위험 해소 신호(1층 뱃지용)
        "account_tags": account_tags(j),                       # 상태: 연결된 계정(빈 배열=미배정)
        "account_unassigned": not j.account_links,             # 상태: 미배정(뱃지 한 단어)
        "one_line_reason": j.one_line_reason,
        "abstained": j.abstained,
        "unjudged": getattr(j, "unjudged", False),             # 미판단(판단 응답 누락) — 화면 뱃지용
        # 주체성(루프10): C(타사 기사 내 언급) 여부 + 설명. 강등은 정렬층이 하고, 여기선 라벨·설명만 노출.
        "peripheral_mention": is_peripheral_mention(j),        # 1층 뱃지 '타사 기사 내 언급'용
        "subject_note": getattr(j, "subject_role_reason", ""), # 2층: 기사 주체가 누구·이 회사가 어떻게 언급됐나
        # 감사중요(루프11): '감사 확인 요망' 뱃지 + 2층 설명(방향 중립이어도 노출된 이유).
        "audit_attention": getattr(j, "audit_attention", False),
        "audit_note": getattr(j, "audit_attention_reason", ""),
        # 2층(펼침) — 핵심 둘만: (a) 방향 근거 문장, (b) 계정 가설(이유+근거, 계정 중복 제거)
        "evidence": [{"quote": e.quote, "field": e.field} for e in j.evidence],
        "account_hypotheses": _dedup_hypotheses(j.account_links),
    }


def basket_view(b: Basket, items_by_index: dict) -> dict:
    """한 바구니를 화면용으로. items 는 ranking 이 정한 순서 그대로(재정렬 금지)."""
    return {
        "key": b.key,
        "label": b.label,
        "count": len(b.items),
        "items": [judgment_view(j, items_by_index.get(j.source_index)) for j in b.items],
    }


def severity_list_view(ordered: list[Judgment], items_by_index: dict) -> list[dict]:
    """
    심각도순 표시 리스트(뷰 B). 입력은 이미 rank_for_display 로 정렬된 리스트.

    루프13(사건군 도배 완화): 같은 '사건군(event_group)'의 연속 보도는 대표 1건만 최상위에 남기고
    나머지는 그 대표의 cluster_members 로 접는다(삭제 아님 — 펼치면 전부 보인다, 원칙 7). 이렇게
    하면 담합 9건이 다른 축의 악재(자회사 청산·세무조사·세일앤리스백)를 밀어내는 '표시 도배'를 막되,
    서로 다른 국면(확정/재판/소송)을 '합치지'는 않는다(각 판단은 그대로 남고 묶여 보일 뿐).

    대표 선정은 새 공식이 아니라 '이미 매겨진 심각도 순서'를 재활용한다(원칙 1·8): 입력이 이미
    정렬돼 있으므로 각 사건군의 '첫 등장'이 그 군에서 감사 관점상 가장 앞선(대표) 건이다. 이는
    확정·금액·법적 단계 진전 등을 LLM 이 이미 severity_rank 에 녹여 정렬에 반영한 결과를 그대로
    쓰는 것이지, 표시층이 새 규칙표로 대표를 고르는 것이 아니다.

    event_group 이 빈 값(단독 사건)이면 묶지 않고 제자리에 둔다. 사건군이 1건뿐이면 라벨을 붙이지
    않는다(잡음 방지). shown(표시) 카운트는 여전히 visible 전체다 — cluster_members 도 화면에 남아
    접근 가능하므로 아무것도 증발하지 않는다(정합 항등식 불변).
    반환: 최상위 표시 항목 리스트. 대표 항목엔 cluster_label·cluster_count·cluster_members 를 얹는다.
    """
    out: list[dict] = []
    rep_pos: dict[str, int] = {}     # event_group → out 리스트 내 대표 인덱스
    for j in ordered:
        view = judgment_view(j, items_by_index.get(j.source_index))
        grp = (getattr(j, "event_group", "") or "").strip()
        if not grp:
            out.append(view)                     # 단독 사건(사건군 없음) — 제자리
            continue
        if grp not in rep_pos:
            view["cluster_label"] = grp          # 이 사건군의 대표(정렬상 첫 등장 = 심각도 최상위)
            view["cluster_members"] = []
            rep_pos[grp] = len(out)
            out.append(view)
        else:
            out[rep_pos[grp]]["cluster_members"].append(view)   # 나머지 국면은 대표 아래로 접힘
    # 대표에 사건군 전체 건수 표기(대표 1 + 접힌 나머지). 군이 1건뿐이면 라벨/멤버를 지워 잡음 없앤다.
    for view in out:
        if "cluster_members" in view:
            if not view["cluster_members"]:
                view.pop("cluster_label", None)
                view.pop("cluster_members", None)
            else:
                view["cluster_count"] = len(view["cluster_members"]) + 1
    return out


def folded_view(folded_ordered: list[Judgment], items_by_index: dict) -> list[dict]:
    """접힌(제외된) 뉴스 리스트. 각 항목에 '왜 접혔는지' 라벨을 붙인다(투명성).
    데이터는 지우지 않는다 — 화면에서 접되 펼치면 전부 보인다(원칙 7)."""
    return [
        {**judgment_view(j, items_by_index.get(j.source_index)), "fold_reason": fold_reason(j)}
        for j in folded_ordered
    ]


def prescreen_folded_view(pairs: list) -> list[dict]:
    """판단 전 단계(깔때기)에서 접힌 뉴스의 경량 표현. 중복(대표에 통합)·간이 무관 등.
    아직 7축 판단을 받지 않았으므로 상태 뱃지·2층 근거가 없다 — 제목·링크·접힘사유만 준다.
    삭제가 아니라 접힘이다: 화면 하단에서 펼쳐 전부 접근할 수 있다(원칙 7)."""
    out = []
    for it, reason in pairs:
        published = getattr(it, "published", "")
        out.append({
            "source_index": getattr(it, "source_index", -1),
            "title": getattr(it, "title", ""),
            "link": getattr(it, "link", ""),
            "published": published,
            "published_display": _published_display(published),   # 못 읽으면 '' → 화면 '날짜 미상'
            "one_line_reason": "",
            "fold_reason": reason,
            "screened_out": True,      # 화면: 경량(컴팩트) 렌더 신호
        })
    return out


def bs_table_from_statements(statements: list[dict] | None) -> dict | None:
    """statements 흐름에 실려온 재무상태표(BS) T계정 표를 꺼낸다(dart.py 가 붙여 보낸 합성
    마커 sj_div=='BS_TTABLE'). 없으면 None — 예전 예시 파일 등에서는 index.html 이 옛
    재무제표 표로 폴백한다. 벤더(dart)를 직접 import 하지 않고 데이터 흐름으로만 받는다(원칙 2)."""
    for g in statements or []:
        if g.get("sj_div") == "BS_TTABLE":
            return g.get("bs_table")
    return None


def statements_without_bs_table(statements: list[dict] | None) -> list[dict]:
    """브라우저로 보낼 account_statements 에서 합성 마커(BS_TTABLE)를 뺀다(폴백 표 전용 목록)."""
    return [g for g in (statements or []) if g.get("sj_div") != "BS_TTABLE"]


def linked_account_options(judgments: list[Judgment]) -> list[str]:
    """
    '내 계정 선택' 화면에 띄울 후보. 이 회사 뉴스에 실제로 걸린 계정군들의 합집합.
    (전체 소계정 121개를 다 띄우지 않는다 — 담당자가 실제 나눠 맡는, 뉴스가 건드린
    계정 수준만. 이는 '고정 중요계정 목록'이 아니라 데이터에서 유도된 집합이라 원칙 1을
    지킨다. 전체 DART 울타리는 별도로 함께 넘겨 '전체 보기'를 허용한다.)
    """
    seen: dict[str, None] = {}
    for j in judgments:
        for link in j.account_links:
            seen.setdefault(link.account_group, None)
    return list(seen.keys())


# ── 예시 모드용(서버 내부) 전체 덤프/복원 — 브라우저로 나가지 않음 ──────────────

def full_dump(company: str, items: list[NewsItem], accounts: list[str],
              financials: dict, judgments: list[Judgment],
              statements: list[dict] | None = None) -> dict:
    """
    실행 결과 전체를 디스크 저장용 dict 로(예시 모드 복원용). 키는 없다(뉴스/판단/재무숫자뿐).

    루프5(loop4 처방 반영): 커밋되는 예시 파일에 LLM 내부 정렬 점수(severity_rank 원값)를
    남기지 않는다. 대신 심각도순으로 정렬해 저장하고 severity_rank 를 '위치 서수'(N..1)로
    덮어쓴다 — load_full→rank_by_severity 가 저장된 순서를 그대로 복원하되(기능 유지),
    파일에는 모델의 내부 홀리스틱 원값이 남지 않는다(원칙 5의 정합성 정리).
    """
    ordered = rank_by_severity(judgments)
    n = len(ordered)
    dumped = []
    for pos, j in enumerate(ordered):
        d = asdict(j)
        d["severity_rank"] = n - pos       # 내부 원값 제거, 순서만 보존(서수)
        dumped.append(d)
    return {
        "company": company,
        "items": [asdict(it) for it in items],
        "accounts": accounts,
        "financials": financials,
        "account_statements": statements or [],
        "judgments": dumped,
    }


def _news_from_dict(d: dict) -> NewsItem:
    return NewsItem(**d)


def _judgment_from_dict(d: dict) -> Judgment:
    return Judgment(
        source_index=d["source_index"], relevant=d["relevant"],
        relevance_reason=d["relevance_reason"], direction=d["direction"],
        direction_reason=d["direction_reason"], stage=d["stage"],
        intrinsic_risk=d["intrinsic_risk"], intrinsic_risk_reason=d["intrinsic_risk_reason"],
        magnitude=Magnitude(**d["magnitude"]),
        evidence=[Evidence(**e) for e in d["evidence"]],
        confidence=d["confidence"], abstained=d["abstained"],
        abstain_reason=d["abstain_reason"], severity_rank=d["severity_rank"],
        one_line_reason=d["one_line_reason"],
        account_links=[AccountLink(**a) for a in d["account_links"]],
        account_abstained=d["account_abstained"],
        account_abstain_reason=d["account_abstain_reason"],
        unjudged=d.get("unjudged", False),                    # 옛 덤프엔 없을 수 있음(하위호환)
        subject_role=d.get("subject_role", ""),               # 루프10 이전 덤프엔 없음 → 미분류(강등 안 함)
        subject_role_reason=d.get("subject_role_reason", ""),
        audit_attention=d.get("audit_attention", False),      # 루프11 이전 덤프엔 없음 → False(노출 강제 안 함)
        audit_attention_reason=d.get("audit_attention_reason", ""),
        event_group=d.get("event_group", ""),                 # 루프13 이전 덤프엔 없음 → ""(사건군 묶기 안 함)
    )


def load_full(data: dict) -> tuple[list[NewsItem], list[str], dict, list[dict], list[Judgment]]:
    """full_dump 로 저장한 결과를 복원한다. 예시 모드에서 코어를 다시 호출하지 않고도
    라우팅/직렬화가 라이브와 '동일 코드'로 동작하게 한다.
    account_statements 가 없는 옛 예시 파일은 빈 리스트로 복원된다(하위호환)."""
    items = [_news_from_dict(x) for x in data.get("items", [])]
    judgments = [_judgment_from_dict(x) for x in data.get("judgments", [])]
    return (items, data.get("accounts", []), data.get("financials", {}),
            data.get("account_statements", []), judgments)


def financials_text(financials: dict) -> str:
    """재무 규모(분모) 한 줄 — pipeline 과 같은 표현을 재사용(단일 출처)."""
    return _financials_line(financials)
