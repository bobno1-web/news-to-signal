"""
src/ranking/__init__.py

정렬·라우팅 계층.
- rank_by_severity: 단일 리스트를 LLM 종합 신호(severity_rank)로 정렬.
- classify_and_rank: 3바구니 라우팅(routing-rules.md) — 바구니로 먼저 나누고,
  각 바구니 '안에서만' 심각도로 정렬한다.

원칙 준수:
- 코드는 축에 가중치를 곱해 심각도를 계산하지 않는다(원칙 1·8). 오직 LLM이 사건을
  이해해 내린 종합 신호(severity_rank)로 정렬한다.
- 어떤 뉴스도 버리거나 숨기지 않는다(원칙 7). 낮은 것·남의 계정도 아래로 접힐 뿐 다 남는다.
- 양 조절은 라우팅(계정 렌즈)이 아니라 심각도 임계치로 한다(여기서 계정 하드 필터 금지).
- 계정 태그는 '숨기는 게이트'가 아니라 '정렬하는 렌즈'다. 특히 '미배정'은 공용 큐로
  항상 접근 가능해야 한다(routing-rules.md).
"""

from collections import namedtuple
from collections.abc import Iterable

from src.judge.schema import Judgment, is_peripheral_mention

Basket = namedtuple("Basket", ["key", "label", "items"])


def rank_by_severity(judgments: list[Judgment]) -> list[Judgment]:
    """LLM의 종합 정렬 신호(severity_rank) 내림차순으로 정렬한다. 동점은 입력 순서 유지.
    이 함수는 순수 심각도 신호만 본다 — 주체성(A/B/C)은 여기서 섞지 않는다(원칙 8:
    severity_rank 의 의미를 오염시키지 않는다). 주체성 강등은 rank_for_display 가 얹는다."""
    return sorted(judgments, key=lambda j: (-j.severity_rank, j.source_index))


def demote_peripheral(ordered: list[Judgment]) -> list[Judgment]:
    """이미 심각도순인 리스트에서 '타사 언급(C)'만 안정적으로 맨 뒤로 내린다(표시 강등, 루프10).
    A/B(핵심 주체)의 상대 순서는 그대로다 — 주체성으로 순위를 재계산하지 않는다(원칙 8).
    삭제·접힘이 아니라 '위치'만 내린다(원칙 7): C도 리스트에 남아 스크롤로 접근 가능하다.
    ""(미분류)·A·B 는 core 로 남고 명백한 C 만 뒤로 간다(애매하면 안 내림 — 놓침 방지)."""
    core = [j for j in ordered if not is_peripheral_mention(j)]
    peripheral = [j for j in ordered if is_peripheral_mention(j)]
    return core + peripheral


def rank_for_display(judgments: list[Judgment]) -> list[Judgment]:
    """표시용 정렬 = 심각도순(rank_by_severity) 위에 주체성 강등(demote_peripheral)을 얹은 것.
    severity_rank(순수 LLM 신호)는 건드리지 않고, 타사 언급(C)만 하단으로 옮긴다.
    표시층의 단일 정렬 진입점 — 심각도 단일 리스트와 계정 바구니가 같은 규칙을 쓴다."""
    return demote_peripheral(rank_by_severity(judgments))


def _norm(name: str) -> str:
    return "".join(name.split())


def _viewer_list(viewer_account: "str | Iterable[str] | None") -> list[str]:
    """
    담당 계정군 입력을 문자열 리스트로 정규화한다.
    - None → []            (담당 미지정)
    - str  → [그 문자열]    (기존 단일 계정 경로 — 하위호환, CLI/기존 테스트)
    - iterable[str] → 공백 아닌 항목들 (화면에서 담당자가 여러 계정을 클릭 선택)
    계정 '선택'은 표시 렌즈일 뿐이다 — 어떤 판단(심각도·방향·계정가설·근거)도 바꾸지 않고,
    오직 '어느 바구니에 담아 보여줄지'만 정한다(routing-rules.md, 원칙 5·7).
    """
    if viewer_account is None:
        return []
    if isinstance(viewer_account, str):
        return [viewer_account] if viewer_account.strip() else []
    return [v for v in viewer_account if isinstance(v, str) and v.strip()]


def _matches_viewer(j: Judgment, viewers: list[str]) -> bool:
    """이 판단의 계정 연결이 담당 계정군(하나 이상) 중 어느 하나와 (느슨히) 일치하는가.
    여러 계정을 골라도 판단은 그대로다 — 담기는 바구니만 달라진다."""
    for vv in viewers:
        v = _norm(vv)
        if len(v) < 2:
            continue
        for link in j.account_links:
            g = _norm(link.account_group)
            if g and (g == v or g in v or v in g):
                return True
    return False


def classify_and_rank(judgments: list[Judgment],
                      viewer_account: "str | Iterable[str] | None" = None) -> list[Basket]:
    """
    3바구니로 나눈 뒤 각 바구니 안에서 심각도로 정렬한다(routing-rules.md).
    ① 내 계정 의심 / ② 계정 미배정(공용) / ③ 타 계정.
    - viewer_account(담당 계정군)는 문자열 하나 또는 여러 계정의 리스트다(화면 다중 선택).
      주어지면 ①/③을 가른다. 없으면 배정된 뉴스는 관점 주체가 없어 ③(타 계정)으로 모이고
      ①은 비어 '담당 계정 미지정'으로 표시된다.
    - 심각도 정렬은 바구니를 가로지르지 않는다(남의 계정 심각 뉴스가 내 계정 위로
      올라오면 라우팅이 깨진다).
    - 각 바구니 '안에서'의 순서는 rank_for_display 를 쓴다 — 심각도순 위에 주체성 강등을
      얹어 타사 언급(C)이 바구니 안에서도 하단으로 간다(루프10). 바구니 '배정(라우팅)'은
      불변이다: C 도 계정이 걸리면 원래 바구니(①/③)에 그대로 담기고, 순서만 내려간다.
    - 어떤 바구니도 뉴스를 삭제하지 않는다(원칙 7). ②③은 접히되 접근 가능하다.
    ※ 이 함수는 '표시 렌즈'다: 입력 judgments 를 읽기만 하고 절대 수정하지 않는다.
      같은 뉴스라도 어떤 계정을 골랐느냐에 따라 담기는 바구니만 바뀔 뿐, 각 판단의
      심각도·방향·계정가설·근거는 불변이다.
    """
    viewers = _viewer_list(viewer_account)
    mine: list[Judgment] = []
    unassigned: list[Judgment] = []
    others: list[Judgment] = []

    for j in judgments:
        if j.account_abstained or not j.account_links:
            unassigned.append(j)             # ② 계정 특정 불가/미배정 — 공용 큐, 숨기지 않음
        elif viewers and _matches_viewer(j, viewers):
            mine.append(j)                   # ① 담당 계정군과 관련 의심
        elif viewers:
            others.append(j)                 # ③ 담당 선택됨 + 내 계정 아님 → 남의 계정
        else:
            # 담당 계정 미선택(루프11 C-4): '남의 계정'은 아직 성립하지 않는다 — 계정이 붙었어도
            # 누구 책상인지 정해지지 않았다. 선택 전에는 ③(남의 계정)에 넣지 않고 ② 공용에 둔다.
            # (각 카드의 계정 태그는 그대로 보여, 어떤 계정 가설이 붙었는지는 여전히 드러난다.)
            unassigned.append(j)

    viewer_disp = ", ".join(viewers) if viewers else None
    if viewer_disp:
        mine_label = f"① 내 계정으로 의심되는 뉴스 (담당 계정군: {viewer_disp})"
        unassigned_label = "② 계정 미배정(공용 큐) — 접힘, 그러나 모두에게 보임"
        others_label = "③ 명확히 남의 계정 — 접힘, 무시 가능"
    else:
        # 선택 전 상태를 정직하게 표시(루프11 C-4): ③은 비고, 계정 붙은 뉴스도 ② 공용에 모인다.
        mine_label = "① 내 계정으로 의심되는 뉴스 (계정 선택 전 — 아래에서 담당 계정을 고르세요)"
        unassigned_label = "② 전체 공용 (계정 선택 전 — 계정을 고르면 관련 뉴스가 ①로 옮겨집니다)"
        others_label = "③ 명확히 남의 계정 (계정 선택 전이라 아직 없음)"
    return [
        Basket("mine", mine_label, rank_for_display(mine)),
        Basket("unassigned", unassigned_label, rank_for_display(unassigned)),
        Basket("others", others_label, rank_for_display(others)),
    ]
