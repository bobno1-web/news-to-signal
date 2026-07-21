"""
src/api/server.py

로컬 웹 서버(127.0.0.1). 화면에서 회사명을 넣으면 기존 파이프라인을 '호출만' 해서
뉴스→감사위험 판단을 심각도순/계정별로 보여준다. 판단은 여기서 재구현하지 않는다.

보안:
- API 키 4개는 '화면 입력 → 서버 메모리'에만 둔다(_KEYS). 파일·로그에 쓰지 않는다.
  서버 프로세스가 죽으면 사라진다. 실행 직전 os.environ 에 주입(프로세스 메모리)해
  기존 어댑터(네이버·DART)·anthropic 클라이언트가 읽게 한다.
- 에러 메시지·로그에 키가 새지 않도록 _redact_secrets 로 항상 가린다
  (특히 DART URL 의 crtfc_key 쿼리파라미터).

화면이 판단을 왜곡하지 않는 구조(핵심):
- /api/analyze 는 '한 번' 판단하고 그 결과(불변 Judgment 리스트)를 _RESULTS 에 캐시한다.
- /api/route 는 판단을 절대 다시 하지 않는다. 캐시된 판단을 classify_and_rank 로
  '다시 담기만' 한다(표시 렌즈). 계정을 무엇을 고르든 각 뉴스의 심각도·방향·계정가설·
  근거는 그대로다.
"""

import logging
import os
import re
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from src.api import serialize
from src.pipeline import run
from src.ranking import classify_and_rank, rank_for_display

_ROOT = Path(__file__).resolve().parents[2]
_RESULTS_DIR = _ROOT / "out" / "results"
_HERE = Path(__file__).resolve().parent

_KEY_NAMES = ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "ANTHROPIC_API_KEY", "OPENDART_API_KEY")

# ── 서버 메모리(프로세스가 죽으면 소멸) ──────────────────────────────────────
_KEYS: dict[str, str] = {}                 # 화면에서 입력한 키(디스크·로그 기록 금지)
_RESULTS: dict[str, dict] = {}             # company → {items, accounts, financials, judgments}

app = Flask(__name__, static_folder=None)


# ── 비밀 가리기(에러·로그가 키를 흘리지 않게) ─────────────────────────────────
def _redact_secrets(text: str) -> str:
    """문자열에서 (1) 메모리에 있는 실제 키 값, (2) URL 의 crtfc_key 파라미터를 가린다."""
    if not text:
        return text
    for v in _KEYS.values():
        if v and len(v) >= 6:
            text = text.replace(v, "***REDACTED***")
    text = re.sub(r"(crtfc_key=)[^&\s\"']+", r"\1***REDACTED***", text)
    return text


def _inject_keys_to_env() -> None:
    """화면 키를 프로세스 환경변수(메모리)에 올린다. 어댑터가 os.environ 에서 읽으므로.
    override 로 넣어 '화면 입력'이 항상 우선(제품엔 .env 가 없고, 개발 .env 는 무시)."""
    for name in _KEY_NAMES:
        val = _KEYS.get(name)
        if val:
            os.environ[name] = val


def _missing_keys() -> list[str]:
    return [n for n in _KEY_NAMES if not _KEYS.get(n)]


def _err(exc: Exception, status: int = 500):
    name = type(exc).__name__
    msg = _redact_secrets(str(exc))
    return jsonify({"error": name, "message": msg}), status


# ── 결과 → 화면 응답 만들기(라이브·예시 공용) ─────────────────────────────────
def _split_visible(judgments):
    """기본 화면 표시분 / 접힘분으로 가른다(표시 렌즈 — 기존 판단값만 읽음).
    정렬은 rank_for_display: 심각도순 위에 주체성 강등을 얹어 타사 언급(C)이 표시분의
    하단으로 간다(루프10). C 는 삭제·접힘이 아니라 '위치'만 내려간다 — default_visible 은
    그대로라 C 악재도 표시분에 남되(스크롤로 접근) 맨 아래에 배치된다(원칙 7)."""
    ordered = rank_for_display(judgments)                 # 원본은 그대로, 순서만 매김(C는 하단)
    visible = [j for j in ordered if serialize.default_visible(j)]
    folded = [j for j in ordered if not serialize.default_visible(j)]
    return visible, folded


def _build_analyze_response(company: str, items, accounts, financials, statements, judgments,
                            prejudge_folded=None, funnel=None) -> dict:
    by_index = {it.source_index: it for it in items}
    visible, folded = _split_visible(judgments)           # 판단된 대표: 표시분 / 판단후 접힘
    prejudge = serialize.prescreen_folded_view(prejudge_folded or [])  # 깔때기 접힘(중복·간이무관)
    all_folded = serialize.folded_view(folded, by_index) + prejudge     # 하단에 전부 보존(원칙 7)
    default_baskets = classify_and_rank(visible, None)    # 바구니엔 '표시분'만(접힘은 하단 공용줄로)

    collected = funnel.get("collected") if funnel else len(judgments)
    shown = len(visible)
    resp_funnel = None
    if funnel:
        # 깔때기 각 단계를 투명하게(블랙박스 금지, 원칙 7). 화면은 이 값으로 흐름을 보여준다.
        resp_funnel = {
            "collected": collected, "after_dedup": funnel.get("after_dedup"),
            "survivors": funnel.get("survivors"),                 # 판단에 보낸 대표 수
            "judged": funnel.get("judged"), "shown": shown, "judged_folded": len(folded),
            "unjudged": funnel.get("unjudged", 0),               # 판단 응답 누락(미판단) 보존 수 — 증발 방어
            "dup_folded": funnel.get("dup_folded"), "irrelevant_folded": funnel.get("irrelevant_folded"),
            "truncated": funnel.get("truncated"),
            "hit_ceiling": bool((funnel.get("collection_meta") or {}).get("hit_naver_ceiling")),
            "months": (funnel.get("collection_meta") or {}).get("months"),
        }
    # 미판단(판단 응답 누락)은 접힘분 안의 강제기권으로 보존된다 — 정합: 수집 = 표시 + 접힘(그중 미판단 K).
    unjudged = (funnel.get("unjudged", 0) if funnel else 0)
    return {
        "company": company,
        # 투명성: 수집·표시·접힘 카운트를 함께(collected = shown + folded 로 정합). unjudged=접힘 중 미판단 수.
        "counts": {"collected": collected, "shown": shown, "folded": collected - shown, "unjudged": unjudged},
        "funnel": resp_funnel,
        "financials_text": serialize.financials_text(financials),
        # 계정 선택 = 재무상태표(BS) T계정 표(자산 좌 / 부채 우상 / 자본 우하). 합계 계정은 금액
        # 산술구조로 탐지해 선택 불가, 말단만 선택 가능. bs_table 은 금액 없이 계정 '이름'만 담는다.
        # 옛 예시 파일엔 bs_table 이 없을 수 있어 account_statements(구분별 표)를 폴백으로 함께 보낸다.
        "bs_table": serialize.bs_table_from_statements(statements),
        "account_statements": serialize.statements_without_bs_table(statements),
        "linked_accounts": serialize.linked_account_options(visible),
        "severity_view": serialize.severity_list_view(visible, by_index),   # 뷰 B(표시분)
        "baskets": [serialize.basket_view(b, by_index) for b in default_baskets],
        "folded_view": all_folded,                                   # 하단 접이식(판단후 접힘 + 깔때기 접힘)
    }


def _route_response(company: str, selected: list[str]) -> dict:
    cached = _RESULTS[company]
    by_index = {it.source_index: it for it in cached["items"]}
    visible, folded = _split_visible(cached["judgments"])
    baskets = classify_and_rank(visible, selected)   # 판단 재실행 없음 — 표시분을 재배치만
    return {
        "company": company,
        "selected": selected,
        "baskets": [serialize.basket_view(b, by_index) for b in baskets],
    }


# ── 라우트 ────────────────────────────────────────────────────────────────
@app.get("/")
def index():
    return send_from_directory(_HERE, "index.html")


@app.get("/api/state")
def state():
    return jsonify({
        "keys_set": {n: bool(_KEYS.get(n)) for n in _KEY_NAMES},
        "all_keys_set": not _missing_keys(),
        "examples": _list_examples(),
    })


@app.post("/api/keys")
def set_keys():
    """4개 키를 받아 메모리에만 저장. 값은 응답·로그에 담지 않는다(설정 여부만)."""
    data = request.get_json(silent=True) or {}
    for name in _KEY_NAMES:
        v = (data.get(name) or "").strip()
        if v:
            _KEYS[name] = v
    return jsonify({
        "keys_set": {n: bool(_KEYS.get(n)) for n in _KEY_NAMES},
        "all_keys_set": not _missing_keys(),
    })


@app.post("/api/analyze")
def analyze():
    data = request.get_json(silent=True) or {}
    company = (data.get("company") or "").strip()
    # 수집 개수는 사용자가 고르지 않는다(루프6b) — 최근 1년치를 넓게 자동 수집한다.
    if not company:
        return jsonify({"error": "BadRequest", "message": "회사명을 입력하세요."}), 400
    missing = _missing_keys()
    if missing:
        return jsonify({"error": "KeysMissing",
                        "message": f"먼저 API 키를 입력하세요(미설정: {', '.join(missing)})."}), 400

    _inject_keys_to_env()
    try:
        r = run(company, viewer_account=None)
    except Exception as exc:                    # 키·rate limit·조회 실패 등 정직하게(키는 가려서)
        return _err(exc, 502)

    _RESULTS[company] = {"items": r.items, "accounts": r.accounts, "financials": r.financials,
                         "statements": r.statements, "judgments": r.judgments,
                         "prejudge_folded": r.prejudge_folded, "funnel": r.funnel}
    return jsonify(_build_analyze_response(
        company, r.items, r.accounts, r.financials, r.statements, r.judgments,
        prejudge_folded=r.prejudge_folded, funnel=r.funnel))


@app.post("/api/route")
def route():
    """캐시된 판단을 계정 선택으로 '다시 담기만' 한다(판단 불변)."""
    data = request.get_json(silent=True) or {}
    company = (data.get("company") or "").strip()
    selected = data.get("selected") or []
    if not isinstance(selected, list):
        selected = []
    selected = [s for s in selected if isinstance(s, str) and s.strip()]
    if company not in _RESULTS:
        return jsonify({"error": "NotAnalyzed",
                        "message": "먼저 이 회사를 분석하세요(또는 예시를 불러오세요)."}), 404
    return jsonify(_route_response(company, selected))


# ── 예시(키 없이 결과 보기) ───────────────────────────────────────────────────
def _example_files() -> list[Path]:
    if not _RESULTS_DIR.exists():
        return []
    return sorted(_RESULTS_DIR.glob("*.json"))


def _list_examples() -> list[str]:
    out = []
    for p in _example_files():
        try:
            import json
            company = json.loads(p.read_text(encoding="utf-8")).get("company") or p.stem
        except Exception:
            company = p.stem
        out.append(company)
    return out


@app.get("/api/example")
def example():
    """저장된 예시를 메모리로 복원해 라이브와 '동일 코드'로 렌더한다(키 불필요)."""
    import json
    company = (request.args.get("company") or "").strip()
    for p in _example_files():
        data = json.loads(p.read_text(encoding="utf-8"))
        if (data.get("company") or p.stem) == company or not company:
            items, accounts, financials, statements, judgments = serialize.load_full(data)
            comp = data.get("company") or p.stem
            _RESULTS[comp] = {"items": items, "accounts": accounts, "financials": financials,
                              "statements": statements, "judgments": judgments}
            resp = _build_analyze_response(comp, items, accounts, financials, statements, judgments)
            resp["example"] = True
            return jsonify(resp)
    return jsonify({"error": "NoExample", "message": "예시 결과가 없습니다."}), 404


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> None:
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # 요청 로그 최소화(키는 본문에만)
    host, port = "127.0.0.1", 8765
    url = f"http://{host}:{port}/"
    print(f"[기업 위험 뉴스 모니터링] 로컬 서버 시작 → {url}")
    print("  · API 키는 화면에서 입력합니다. 키는 서버 메모리에만 있고 파일·로그에 저장되지 않습니다.")
    print("  · 종료하려면 이 창에서 Ctrl+C 를 누르세요.")
    threading.Timer(1.0, _open_browser, args=(url,)).start()
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
