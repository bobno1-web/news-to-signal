"""
src/sources/dart.py

OpenDART 어댑터. FinancialSource 구현.
이번 루프에서 쓰는 것은 '계정과목 목록'뿐이다 — 재무 '숫자'가 아니라 '이 회사가
실제 가진 계정군 후보'다(account-linkage.md의 울타리). LLM이 없는 계정을 지어내지
못하게 막는 후보 목록으로만 쓰며, 규모 비율 계산(뉴스금액 ÷ 재무규모)은 하지 않는다.

동작:
  1) corpCode.xml(전체 고유번호 목록)을 받아 회사명 → corp_code 로 찾는다.
  2) fnlttSinglAcntAll(전체 재무제표)에서 계정과목명(account_nm) 목록을 뽑는다.
둘 다 캐시해 중복 호출을 막는다(.cache/dart/). rate limit·쿼터 초과는 실패로 보되
정상 동작으로 간주하고 재시도한 뒤, 끝내 막히면 DartRateLimited 로 알린다.
키가 없으면 임의값으로 대체하지 않고 즉시 멈춘다(원칙 4).

원칙 1 주의: 이 파일에는 '키워드→계정' 고정표나 '이벤트→계정' 매핑을 두지 않는다.
계정 연결의 판단 주체는 LLM이고, 여기서는 회사가 실제 가진 계정 '후보'만 제공한다.
"""

import io
import json
import os
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

import requests

from src.sources.base import FinancialSource

_CORPCODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
_FNLTT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
_ROOT = Path(__file__).resolve().parents[2]
_CACHE_DIR = _ROOT / ".cache" / "dart"

# 조회 연도는 '현재 연도부터 역순'으로 동적 계산한다(하드코딩 금지 — 원칙 1). 특정 연도를 박으면
# 새 사업보고서(예: FY2025)가 공시돼도 영구히 조회하지 못해 분모가 낡는다(release-criteria §1
# '재무 분모가 최신 보고서가 아닌 것' = blocker). 아직 공시 전인 최신 연도는 status 013 으로
# 자동 탈락하고 직전 연도로 내려간다.
_YEARS_BACK = 5                  # 현재 연도부터 몇 해 뒤까지 역순으로 시도할지(넉넉히)
_REPRT_ANNUAL = "11011"          # ★연간 사업보고서만 사용(설계 결정, magnitude-rules.md). 반기·분기 금지:
                                 #   매출액은 기간 누적이라 분기 수치를 분모로 쓰면 비율이 과대해지고,
                                 #   분모끼리 기준(시점 vs 기간)이 엇갈린다.
_FS_DIVS = ["CFS", "OFS"]        # 연결 우선, 없으면 별도


def _years() -> list[str]:
    """현재 연도부터 역순으로 시도할 사업연도 목록(신년도 먼저). 특정 연도 하드코딩 금지(원칙 1).
    아직 사업보고서가 공시되지 않은 최신 연도는 DART status 013 으로 자동 탈락한다."""
    y = datetime.now().year
    return [str(y - k) for k in range(_YEARS_BACK)]


class DartConfigError(RuntimeError):
    """OPENDART_API_KEY 미설정 등 설정 오류. 임의값 대체 없이 즉시 멈춘다(원칙 4)."""


class DartRateLimited(RuntimeError):
    """DART 호출 제한/쿼터 초과. 코드 결함이 아니라 정상 동작으로 간주한다."""


class DartNotFound(RuntimeError):
    """회사를 고유번호 목록에서 못 찾거나 계정 목록이 조회되지 않음(원칙 4 — 지어내지 않음)."""


class DartSource(FinancialSource):
    """OpenDART 계정과목 목록 어댑터(울타리 제공용)."""

    def __init__(self, *, max_retries: int = 3, backoff: float = 1.5) -> None:
        self._key = os.environ.get("OPENDART_API_KEY")
        self._max_retries = max_retries
        self._backoff = backoff
        self._corp_index: dict[str, str] | None = None  # 정규화된 회사명 → corp_code
        self._validated_corps: set[str] = set()         # 이번 인스턴스에서 '최신 여부'를 이미 확인한 corp


    # ── 공개 API ──────────────────────────────────────────────────────────
    def fetch_account_groups(self, corp: str) -> list[str]:
        """회사명으로 계정과목(계정군) 후보 목록을 반환한다. 없으면 DartNotFound."""
        return _distinct_accounts(self._statement_rows(*self._corp(corp)))

    def fetch_financials(self, corp: str) -> dict:
        """
        규모 분모 후보 {"매출액","자산총계","자기자본"}(원)을 반환한다. 계정 목록과 '같은'
        재무제표 응답에서 뽑아 중복 호출을 막는다. 확보 못 한 항목은 dict 에서 빠진다.
        """
        return _extract_financials(self._statement_rows(*self._corp(corp)))

    def fetch_account_statements(self, corp: str) -> list[dict]:
        """
        계정과목을 DART 원본 재무제표 구분(sj_div)별로 묶어 반환한다(화면 표 구성용).
        같은 재무제표 응답(캐시)을 쓰므로 추가 호출이 없다. 구분·순서를 우리가 임의로
        정하지 않고 DART 원본을 그대로 쓴다(원칙 1). fetch_account_groups(울타리)와 같은
        계정집합이며, 판단이 아니라 표시에만 쓴다.

        루프 추가: 계정 '선택' 화면을 5개 재무제표 전체가 아니라 재무상태표(BS) T계정 하나로
        좁히기 위해, 같은 원시행에서 만든 BS T계정 표를 statements 흐름에 함께 실어 보낸다.
        (파이프라인은 statements 를 불투명하게 통과시키므로 base/pipeline 을 건드리지 않는다.)
        판단의 울타리(fetch_account_groups)와 표 그룹화(_group_by_statement)는 그대로 둔다 —
        BS T계정은 '표시 렌즈'일 뿐이며 판단 재료가 아니다(account-linkage.md, 원칙 1·5).
        """
        rows = self._statement_rows(*self._corp(corp))
        groups = _group_by_statement(rows)
        bs_table = _build_bs_table(rows)
        if bs_table:
            # sj_div=="BS_TTABLE": 합성 마커 그룹. accounts 는 비우고 bs_table(금액 없음)만 싣는다.
            groups.append({"sj_div": "BS_TTABLE", "sj_nm": "재무상태표(T계정)",
                           "accounts": [], "bs_table": bs_table})
        return groups

    def fetch_bs_table(self, corp: str) -> dict | None:
        """
        재무상태표(BS)만 T계정 구조(자산 좌 / 부채 우상 / 자본 우하)로 반환한다(표시용).
        같은 재무제표 응답(캐시)을 쓰므로 추가 호출이 없다. '합계' 계정(자산총계·유동자산·
        부채총계 등)은 고정 이름표가 아니라 '금액 산술구조'로 탐지해 선택 불가로 두고, 말단
        계정만 선택 가능하게 한다(원칙 1). 섹션(자산/부채/자본)은 DART 자체의 IFRS 구간합계
        계정ID(ifrs-full_Assets/Liabilities/Equity/EquityAndLiabilities)를 앵커로 금액
        포함관계로 나눈다 — 우리가 지어낸 분류표가 아니다(sj_div 를 쓰는 것과 같은 성격,
        decision-log D22). 금액은 합계 탐지에만 내부적으로 쓰고 브라우저로는 계정 '이름'만 낸다.
        """
        return _build_bs_table(self._statement_rows(*self._corp(corp)))

    def _corp(self, corp: str) -> tuple[str, str]:
        """키 검사 + 회사명→corp_code 해소. (corp_code, 원래 라벨) 반환."""
        if not self._key:
            raise DartConfigError(
                "OPENDART_API_KEY 환경변수가 없습니다. 임의값으로 대체하지 않고 중단합니다(원칙 4)."
            )
        corp_code = self._resolve_corp_code(corp)
        if corp_code is None:
            raise DartNotFound(f"'{corp}' 를 DART 고유번호 목록에서 찾지 못했습니다.")
        return corp_code, corp

    # ── corp_code 조회 ────────────────────────────────────────────────────
    def _resolve_corp_code(self, corp: str) -> str | None:
        if self._corp_index is None:
            self._corp_index = self._load_corp_index()
        key = _norm(corp)
        # 정확(정규화) 일치 우선, 없으면 계정군 아닌 회사명 수준의 느슨 포함.
        if key in self._corp_index:
            return self._corp_index[key]
        for name, code in self._corp_index.items():
            if key and key in name:
                return code
        return None

    def _load_corp_index(self) -> dict[str, str]:
        """정규화 회사명 → corp_code. 캐시(json)가 있으면 쓰고, 없으면 zip을 받아 만든다."""
        cache = _CACHE_DIR / "corp_index.json"
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))

        raw = self._download_corpcode_zip()
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            xml_bytes = zf.read(zf.namelist()[0])
        index: dict[str, str] = {}
        listed: set[str] = set()  # 상장(stock_code 존재) 우선권 기록
        for el in ET.fromstring(xml_bytes).iter("list"):
            name = (el.findtext("corp_name") or "").strip()
            code = (el.findtext("corp_code") or "").strip()
            stock = (el.findtext("stock_code") or "").strip()
            if not name or not code:
                continue
            nkey = _norm(name)
            # 같은 이름이 여럿이면 상장사(stock_code 존재)를 우선한다.
            if nkey in index and nkey in listed and not stock:
                continue
            index[nkey] = code
            if stock:
                listed.add(nkey)
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        return index

    def _download_corpcode_zip(self) -> bytes:
        cache = _CACHE_DIR / "corpcode.zip"
        if cache.exists():
            return cache.read_bytes()
        raw = self._get_bytes(_CORPCODE_URL, {"crtfc_key": self._key})
        # 키 오류 등은 zip 이 아니라 xml 에러 메시지로 온다 → 조기 발견.
        if not raw[:2] == b"PK":
            msg = raw[:300].decode("utf-8", "replace")
            raise DartConfigError(f"corpCode 응답이 zip 이 아닙니다(키 오류 가능): {msg}")
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(raw)
        return raw

    # ── 재무제표 원시행 조회(계정목록·재무숫자 공용, 캐시 + 최신성 무효화) ──────────
    def _statement_rows(self, corp_code: str, corp_label: str) -> list[dict]:
        """
        전체 재무제표(fnlttSinglAcntAll)의 원시 list 행을 반환한다. 계정 목록과 재무 규모
        숫자(매출액·자산총계·자기자본)를 '같은' 응답(=같은 보고서·같은 기수)에서 뽑기 위해
        raw 행을 캐시한다(.cache/dart/statement_*.json). 최신 연도·CFS 우선.

        캐시 무효화(release-criteria §1 '재무 분모 최신성'): 캐시가 있어도 그 사업연도(bsns_year)가
        지금 시점의 최신 후보 연도보다 낡았으면, 캐시보다 새로운 사업보고서가 공시됐는지 DART 로
        확인해 새로우면(rcept_no 상이) 갱신한다. 낡음 판정·최신 여부는 rows 자체가 지닌 bsns_year·
        rcept_no 로 하므로 캐시 포맷을 바꾸지 않는다(구 캐시 그대로 동작). 최신 확인은 인스턴스당
        corp 1회로 제한해(_validated_corps) 같은 실행에서 3경로(계정·재무·표) 중복 확인을 막는다.
        특정 회사·연도·보고서 하드코딩 없음(원칙 1) — 연도는 _years()가 현재 연도부터 동적 계산.
        """
        cache = _CACHE_DIR / f"statement_{corp_code}.json"
        if cache.exists():
            rows = json.loads(cache.read_text(encoding="utf-8"))
            if rows:
                if corp_code in self._validated_corps:
                    return rows                       # 이번 실행에서 이미 최신 여부 확인함
                latest = _years()[0]
                cyear = str((rows[0].get("bsns_year") or "")).strip()
                if cyear and cyear >= latest:
                    self._validated_corps.add(corp_code)
                    return rows                       # 이미 조회 가능한 최신 연도 = 갱신 불필요
                # 캐시가 낡음(신규 사업보고서 공시 가능) → 캐시보다 '새 연도'만 조회해 확인한다.
                # ★최신성 확인은 '베스트 에포트'다: 키 미설정·쿼터·네트워크로 확인 자체가 막히면
                #   가진 캐시를 그대로 쓴다(크래시 금지 — 낡은 캐시라도 무데이터보다 낫고, 다음 성공
                #   확인 때 갱신된다). 최초 조회(캐시 없음)는 데이터가 없으므로 예외를 그대로 올린다.
                newer = [y for y in _years() if not cyear or y > cyear]
                try:
                    fresh = self._fetch_latest(corp_code, newer)
                except (DartRateLimited, DartConfigError):
                    fresh = None
                self._validated_corps.add(corp_code)
                if fresh:
                    crcept = str((rows[0].get("rcept_no") or "")).strip()
                    frcept = str((fresh[0].get("rcept_no") or "")).strip()
                    if frcept != crcept:              # 캐시보다 새로운 보고서 → 갱신
                        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                        cache.write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
                        return fresh
                return rows                           # 아직 새 사업보고서 공시 전 → 직전 보고서 유지

        fresh = self._fetch_latest(corp_code, _years())
        if fresh:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
            self._validated_corps.add(corp_code)
            return fresh
        raise DartNotFound(
            f"'{corp_label}'(corp_code={corp_code})의 재무제표를 최근 연도에서 조회하지 못했습니다."
        )

    def _fetch_latest(self, corp_code: str, years: list[str]) -> list[dict] | None:
        """주어진 연도들을 신년도 우선 × (연결→별도) 순으로 조회해, 처음 잡히는 연간 사업보고서의
        원시 행(list)을 돌려준다. 어느 연도도 없으면 None(공시 전이거나 대상 아님)."""
        for year in years:
            for fs_div in _FS_DIVS:
                data = self._get_json(_FNLTT_URL, {
                    "crtfc_key": self._key, "corp_code": corp_code,
                    "bsns_year": year, "reprt_code": _REPRT_ANNUAL, "fs_div": fs_div,
                })
                status = data.get("status")
                if status == "000":
                    rows = data.get("list", [])
                    if rows:
                        return rows
                elif status == "013":
                    continue  # 해당 연도/구분 데이터 없음(미공시 등) → 다음 조합 시도
                elif status in ("020", "021"):
                    raise DartRateLimited(f"DART 사용한도 초과(status={status}).")
                elif status in ("011", "012", "010"):
                    raise DartConfigError(f"DART 키/인증 오류(status={status}): {data.get('message')}")
                # 그 외 상태는 다음 조합으로 넘어가 본다.
        return None

    # ── HTTP(재시도 포함) ─────────────────────────────────────────────────
    def _get_bytes(self, url: str, params: dict) -> bytes:
        return self._request(url, params).content

    def _get_json(self, url: str, params: dict) -> dict:
        return self._request(url, params).json()

    def _request(self, url: str, params: dict) -> requests.Response:
        last: Exception | None = None
        for attempt in range(self._max_retries):
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                last = DartRateLimited(f"429 (attempt {attempt + 1})")
                time.sleep(self._backoff * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise DartRateLimited(f"DART 응답 {resp.status_code}: {resp.text[:200]}")
            return resp
        raise last or DartRateLimited("알 수 없는 rate limit 상태")


def _norm(name: str) -> str:
    """회사명 정규화(느슨 매칭용): 공백 제거."""
    return "".join(name.split())


def _distinct_accounts(rows: list[dict]) -> list[str]:
    """fnlttSinglAcntAll 응답에서 계정과목명(account_nm)만 중복 없이 순서 유지로 뽑는다."""
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        nm = (row.get("account_nm") or "").strip()
        if nm and nm not in seen:
            seen.add(nm)
            out.append(nm)
    return out


def _group_by_statement(rows: list[dict]) -> list[dict]:
    """
    계정과목을 재무제표 구분(sj_div/sj_nm)별로 묶는다(예시/폴백 표시용 — 판단에 안 씀).
    구분이 처음 등장한 순서로 표를 놓고, 각 표 안에서는 DART 응답의 '행 순서 그대로'
    (임의 재정렬 금지) 계정을 담는다. ord 필드는 표마다 1부터 시작하지 않거나(예: BS 가 7부터)
    자본변동표처럼 동일값이라 신뢰하지 않고, 응답 배열 순서를 원본 순서로 삼는다. 빈 계정명만
    건너뛴다.

    루프13(계정 증발 방어 — 폴백 경로): 표시명(account_nm)으로 중복 제거하지 않는다. 서로 다른
    계정(다른 account_id·다른 잔액)인데 표시명이 우연히 같은 행(유동/비유동 계약부채·리스부채·
    충당부채 등)을 표시명 dedup 이 조용히 버리던 결함(현대차 -8, CJ -1)을 없앤다 — 원본 행을
    빠짐없이 보존한다(원칙 7, 루프12 주경로 _build_bs_table.collect 과 같은 원칙). 판단 울타리
    (_distinct_accounts)는 여기서 손대지 않는다 — 이름 dedup 이 의도된 설계이며 프롬프트/판단
    캐시의 키다(그 name-set 을 바꾸면 판단 캐시가 전량 무효화된다).
    반환: [{"sj_div","sj_nm","accounts":[이름...]}, ...] (표시용, 원본 행 보존).
    """
    order: list[str] = []                 # sj 키의 첫 등장 순서
    groups: dict[str, dict] = {}
    for row in rows:
        div = (row.get("sj_div") or "").strip()
        nm = (row.get("sj_nm") or "").strip()
        acc = (row.get("account_nm") or "").strip()
        if not acc:                       # 빈 계정명만 건너뛴다(표시명 겹침은 버리지 않는다 — 원칙 7)
            continue
        key = div or nm or "기타"
        if key not in groups:
            order.append(key)
            groups[key] = {"sj_div": div, "sj_nm": nm or "계정과목", "accounts": []}
        groups[key]["accounts"].append(acc)
    return [groups[k] for k in order]


# ── 재무상태표(BS) T계정 표 구성(표시용, 금액 비노출) ──────────────────────────
#
# 목적: 계정 선택 화면을 5개 재무제표 전체가 아니라 '재무상태표(BS)' T계정 하나로 좁힌다.
# '합계' 계정(자산총계·유동자산·부채총계 등)은 고정 이름표가 아니라 '금액 산술구조'로
# 탐지한다(원칙 1): 합계 금액 = 그 자식들 금액의 합(원 단위 정수, 정확 일치).
# 섹션(자산/부채/자본)은 DART 자체의 IFRS 구간합계 계정ID를 앵커로 금액 포함관계로 나눈다
# (우리가 만든 분류표가 아님 — sj_div 를 쓰는 것과 같은 성격, decision-log D22).
# 금액은 이 탐지에만 내부적으로 쓰고 화면으로는 계정 '이름'만 내보낸다(원칙 5).

# 모든 상장사 BS 에 존재하는 DART 표준 IFRS 구간합계 계정ID(이름표가 아니라 ID 앵커).
_BS_ANCHOR_ASSETS = "ifrs-full_Assets"                 # 자산총계
_BS_ANCHOR_LIABILITIES = "ifrs-full_Liabilities"       # 부채총계
_BS_ANCHOR_EQUITY = "ifrs-full_Equity"                 # 자본총계
_BS_ANCHOR_GRAND = "ifrs-full_EquityAndLiabilities"    # 자본과부채총계(총계 — 어느 섹션도 아님)

# DART/IFRS 표준 '구간 합계(소계·총계)' 계정ID 집합(루프11). 이름표(한국어 리터럴)가 아니라
# DART 원본 마커다 — sj_div·앵커ID 를 쓰는 것과 같은 성격(원칙 1 위반 아님, decision-log D22).
# 용도: 금액 산술(_detect_subtotals)이 놓치는 구조적 총계를 보완한다. 예(CJ제일제당) — 부채총계 =
# 유동부채 + 비유동부채 + '매각예정 처분집단 부채'(leaf)라, 중간에 낀 leaf 때문에 '표시된 소계들의
# 합'만 보는 pass2 가 부채총계를 leaf 로 오인해 running 을 오염시켰다. 이 ID 집합에 든 행은 금액
# 탐지 결과와 무관하게 '합계'로 간주해 (a) 누적에서 제외하고 (b) 선택 불가(말단 아님)로 둔다.
_BS_SECTION_SUBTOTAL_IDS = frozenset({
    _BS_ANCHOR_ASSETS, "ifrs-full_CurrentAssets", "ifrs-full_NoncurrentAssets",
    _BS_ANCHOR_LIABILITIES, "ifrs-full_CurrentLiabilities", "ifrs-full_NoncurrentLiabilities",
    _BS_ANCHOR_EQUITY, "ifrs-full_EquityAttributableToOwnersOfParent",
    _BS_ANCHOR_GRAND,
})


def _is_structural_total(account_id: str) -> bool:
    """이 계정ID 가 DART/IFRS 표준 구간합계(소계·총계) 앵커인가(마커 기반, 이름표 아님 — 원칙 1)."""
    return account_id in _BS_SECTION_SUBTOTAL_IDS


def _bs_totals(ids: list[str], amounts: list[int]) -> tuple[list[bool], list[bool]]:
    """
    BS 각 행에 대해 (is_sub[금액산술로 탐지된 합계], is_total[산술합계 OR IFRS 구조앵커])을 돌려준다.
    - is_sub: 금액 구조 탐지(_detect_subtotals) — 회사별 비표준 소계(예 '지배기업 소유주지분')를 잡는다.
    - is_total: is_sub 에 DART/IFRS 표준 앵커ID(_is_structural_total)를 더한 것 — 산술이 놓친
      구조적 총계(예 매각예정 처분집단이 낀 부채총계)를 마커로 보완한다.
    '말단(선택 가능)·누적 대상' 판정은 is_total 로 한다. 소속 계산의 서브트리 경계는 여전히
    금액산술(_pass1_subtotals)을 쓴다. 둘을 분리해 산술 탐지 로직 자체는 건드리지 않는다.
    """
    is_sub = _detect_subtotals(amounts)
    is_total = [is_sub[i] or _is_structural_total(ids[i]) for i in range(len(ids))]
    return is_sub, is_total


def _pass1_subtotals(amounts: list[int]) -> tuple[list[bool], list[int]]:
    """
    전위(pre-order) 재귀로 '부모 == 직계 자식 서브트리들의 합'인 합계(소계·총계)를 탐지하고,
    각 합계가 거느린 '자식 구간의 끝 인덱스'도 함께 돌려준다. 합계 표시(_detect_subtotals)와
    소속 계산(_subtotal_members)이 '같은 산술'을 한 곳에서 공유하도록 여기서만 계산한다
    (원칙 1: 이름표가 아니라 금액 구조). end[i] 는 합계 i 의 자식 반개구간 [i+1, end[i]) 의 끝이고,
    합계가 아니면 i+1(자기 다음)이다.
    consume(i) 는 i 만의 순수 함수(is_sub 를 읽지 않는다)라 메모이즈로 불규칙 배열에서의
    지수 폭발을 막는다 — 결과는 비메모 버전과 동일하다(같은 합계만 표시).
    """
    n = len(amounts)
    is_sub = [False] * n
    end = list(range(1, n + 1))               # 기본: 말단은 자기 다음(i+1)
    memo: dict[int, int] = {}

    def consume(i: int) -> int:
        if i in memo:
            return memo[i]
        target = amounts[i]
        j = i + 1
        s = 0
        while j < n:
            cs = j
            nj = consume(j)
            s += amounts[cs]
            if target != 0 and s == target:
                is_sub[i] = True
                end[i] = nj                   # 합계 i 의 자식 구간 끝
                memo[i] = nj
                return nj
            j = nj
            if target > 0 and s > target:
                break
            if target < 0 and s < target:
                break
        memo[i] = i + 1
        return i + 1

    i = 0
    while i < n:
        i = consume(i)
    return is_sub, end


def _detect_subtotals(amounts: list[int]) -> list[bool]:
    """
    BS 행 금액(원 단위 정수, DART 순서)에서 '합계(소계·총계)' 행을 금액 산술로 탐지한다.
    이름표가 아니라 구조로 판정한다(원칙 1): 합계 금액 = 자식들 금액의 합.
    - pass1: 전위(pre-order) 재귀 — 부모 == 직계 자식 서브트리들의 합(_pass1_subtotals).
    - pass2: 고정점 — 아직 표시 안 된(0 아님) 행의 금액이 이미 표시된 합계 2개 이상의 합과
      같으면 그 행도 (상위)합계다(예: 부채총계=유동부채+비유동부채, 위치가 떨어져 있어도).
    금액 0 행은 말단(선택 가능)으로 둔다 — 합계로 배제하지 않는다.
    """
    n = len(amounts)
    is_sub, _end = _pass1_subtotals(amounts)   # pass1(전위) — 소속 계산과 동일 산술 공유

    from itertools import combinations
    changed = True
    while changed:
        changed = False
        marked = [amounts[k] for k in range(n) if is_sub[k] and amounts[k] != 0]
        for idx in range(n):
            if is_sub[idx] or amounts[idx] == 0:
                continue
            found = False
            for k in range(2, min(len(marked), 6) + 1):
                if any(sum(c) == amounts[idx] for c in combinations(marked, k)):
                    found = True
                    break
            if found:
                is_sub[idx] = True
                changed = True
    return is_sub


def _assign_sections(ids: list[str], amounts: list[int],
                     is_total: list[bool]) -> tuple[list, bool]:
    """
    각 BS 행을 자산/부채/자본 섹션에 배정한다(구조적 방법 — 원칙 1).
    DART 자체 IFRS 구간합계 앵커(자산총계·부채총계·자본총계)의 금액을 '목표'로 두고, DART 원본
    순서로 '말단' 금액을 누적해 목표와 정확히 일치하고 그 구간에 해당 앵커가 실제로 들어있을 때
    그 구간을 해당 섹션으로 닫는다(금액 포함관계 + 앵커 포함 확인 — 오검출 방지). 합계·앵커·총계
    행(is_total)은 누적에서 빼고(자식 말단만 합산) 섹션 라벨만 받는다. 총계(자본과부채총계)는 어느
    섹션에도 넣지 않는다. 섹션 순서를 가정하지 않는다 — CJ제일제당처럼 자본이 부채보다 먼저 오고
    grand 총계가 중간에 껴도(누적에서 is_total 로 제외되므로) 각 섹션이 제자리에 닫힌다(루프11).

    구조가 깨져 끝내 못 닫은 행은 '인접 섹션에 밀어넣지 않는다' — 부채가 자본에 앉는 것보다,
    None(=구분 미확정)으로 남기는 편이 정직하고 덜 위험하다(원칙 4·7). _build_bs_table 이 이들을
    별도 '미확정' 바구니로 노출한다. 반환: (sections[list[str|None]], has_unassigned[bool]).
    """
    n = len(ids)

    def first_amount(anchor_id: str):
        for i, a in enumerate(ids):
            if a == anchor_id:
                return amounts[i]
        return None

    anchor_of = {"자산": _BS_ANCHOR_ASSETS, "부채": _BS_ANCHOR_LIABILITIES,
                 "자본": _BS_ANCHOR_EQUITY}
    targets: dict[str, int] = {}
    for name, aid in anchor_of.items():
        v = first_amount(aid)
        if v is not None:
            targets[name] = v

    sections: list = [None] * n
    block_start = 0
    running = 0
    claimed: set = set()
    for i in range(n):
        if not is_total[i]:                   # 말단 금액만 누적(합계·앵커·grand 총계는 제외)
            running += amounts[i]
        match = None
        for name, tgt in targets.items():
            if name in claimed or running != tgt:
                continue
            # 이 구간(block_start..i)에 해당 섹션 앵커가 실제로 있어야 닫는다(우연한 조기 닫힘 방지).
            if any(ids[k] == anchor_of[name] for k in range(block_start, i + 1)):
                match = name
                break
        if match:
            for k in range(block_start, i + 1):
                if ids[k] == _BS_ANCHOR_GRAND:  # 총계는 어느 섹션도 아님
                    continue
                sections[k] = match
            claimed.add(match)
            block_start = i + 1
            running = 0

    # 미확정(원칙 4·7): 못 닫은 행은 인접 섹션으로 밀어넣지 않고 None 으로 둔다. grand 총계는
    # 원래 어느 섹션도 아니므로 미확정으로 세지 않는다. has_unassigned 는 '구조로 못 놓은 실제
    # 계정 행'이 있는지다(화면에 '구분 미확정' 바구니·확인요망 배지로 정직 노출).
    has_unassigned = any(
        sections[i] is None and ids[i] != _BS_ANCHOR_GRAND for i in range(n)
    )
    return sections, has_unassigned


def _subtotal_members(ids: list[str], nms: list[str], amounts: list[int],
                      is_total: list[bool], sections: list) -> list[list[int]]:
    """
    각 '합계' 행이 거느린 '말단(개별) 계정'의 행 위치(row index)를 돌려준다(합계=그룹 전체 선택
    버튼용). 소속은 새 규칙·이름표가 아니라 루프7의 산술 판별을 '재활용'한다(원칙 1):
      - 섹션 총계(자산총계·부채총계·자본총계 = DART IFRS 앵커): 그 섹션의 말단 전부.
        (_assign_sections 의 앵커 기반 섹션 배정을 재활용 — 총계가 앞/중간 어디 있든 견고.)
      - 중간 합계(유동자산·비유동자산 등): _pass1_subtotals 서브트리 [i+1, end[i]) 안의 '말단만'.
        (★ 중첩: 그 안의 '중간 합계'는 담당 계정이 아니므로 제외하고 말단만 모은다.)
    '말단' 판정은 is_total(산술합계 OR IFRS 구조앵커)로 한다 — 산술이 놓친 구조총계(예 매각예정이
    낀 부채총계)도 말단에서 제외해 그룹선택 오염(루프8 2차 피해: 자본총계 버튼이 부채 말단까지
    선택)을 막는다. 소속이 산술로 깔끔히 안 잡히는 합계(pass1 서브트리 없는 비앵커 등)는 빈 목록
    → 화면이 '그룹선택 불가(잠금)'로 유지(지어내지 않는다 — 원칙 4). 합계가 아니면 [].

    루프12(계정 증발 방어): 소속을 '이름'이 아니라 '행 위치'로 돌려준다. 표시명이 겹치는 서로 다른
    계정(유동/비유동 계약부채 등)도 각 행이 그대로 담기며, 이름 중복 제거를 하지 않는다(원칙 7 —
    동명이라고 조용히 버리지 않는다). 호출부(_build_bs_table)가 행 위치로 안정 키(그룹 토글용)와
    이름(검증 불변식·표시용)을 함께 만든다.
    반환: members[i] = 합계 i 의 말단 계정 '행 위치' 목록(같은 섹션·표 순서 유지, 이름 dedup 없음).
    """
    n = len(ids)
    _p1, end = _pass1_subtotals(amounts)               # 소속용 서브트리(합계 탐지와 동일 산술)
    anchor_of = {_BS_ANCHOR_ASSETS: "자산", _BS_ANCHOR_LIABILITIES: "부채",
                 _BS_ANCHOR_EQUITY: "자본"}

    def leaves_in_section(sec) -> list[int]:
        return [k for k in range(n)
                if sections[k] == sec and not is_total[k] and nms[k]]

    members: list[list[int]] = [[] for _ in range(n)]
    for i in range(n):
        if not is_total[i]:
            continue
        if ids[i] in anchor_of:                        # 섹션 총계 → 그 섹션 말단 전부(행 위치)
            members[i] = leaves_in_section(sections[i])
            continue
        e = end[i]
        if e <= i + 1:                                 # pass1 서브트리 없음 → 소속 불명 → 잠금 유지
            continue
        out: list[int] = []
        for k in range(i + 1, e):
            if is_total[k]:                            # 중간 합계·구조총계 제외 → 말단만
                continue
            if nms[k] and sections[k] == sections[i]:
                out.append(k)
        members[i] = out
    return members


def _enclosing_subtotal_names(nms: list[str], is_total: list[bool],
                              end: list[int]) -> list[str]:
    """
    각 행의 '가장 안쪽 소계' 계정명을 돌려준다(동명 계정 구분 표기용, 루프12).
    소계 포함관계(_pass1_subtotals 의 서브트리 [t+1, end[t]))로 유도한다 — 우리가 지어낸
    라벨표(유동/비유동 고정표)가 아니라, DART 원본이 준 소계 계정(유동부채·비유동부채 등)의
    account_nm 그대로다(원칙 1: sj_div·앵커ID 를 쓰는 것과 같은 성격). 여러 소계에 겹쳐 있으면
    가장 좁은(안쪽) 소계를 고른다. 소속 소계가 없으면 ''(구분 표기 없음).

    용도: 표시명이 우연히 같은 서로 다른 계정(예: 유동 계약부채/비유동 계약부채)이 같은 섹션에
    둘 다 뜰 때, 감사인이 원본 명칭을 훼손하지 않고도 둘을 구분하게 한다(원본 이름은 그대로 두고
    소속 소계명을 곁들일 뿐).
    """
    n = len(nms)
    out = [""] * n
    for i in range(n):
        best_t: int | None = None
        best_span: int | None = None
        for t in range(n):
            if not is_total[t]:
                continue
            if t < i < end[t]:                          # i 를 품는 소계 t
                span = end[t] - t
                if best_span is None or span < best_span:
                    best_span = span
                    best_t = t
        if best_t is not None:
            out[i] = nms[best_t]
    return out


def _build_bs_table(rows: list[dict]) -> dict | None:
    """
    재무상태표(BS) 행만 골라 자산/부채/자본 T계정 구조로 만든다(표시용, 금액 비노출).
    - sj_div=="BS" 만. IS/CIS/CF/SCE 제외.
    - 순서는 DART 원본 순서 그대로(재정렬·재분류 금지 — 원칙 1).
    - 합계 계정은 _detect_subtotals 로 탐지해 selectable=False, 말단만 selectable=True.
    - 합계 계정에는 그 그룹의 '말단 계정 이름들'(members)을 붙인다 — 화면에서 '합계 클릭 =
      그 그룹 말단 전부 선택' 버튼으로 되살리기 위함(소속은 산술 재활용, _subtotal_members).
      소속이 안 잡히는 합계는 members 를 붙이지 않아 화면이 잠금(선택 불가)으로 유지된다.
    - 섹션은 IFRS 앵커 기반 금액 포함관계로 배정(_assign_sections). 총계는 어느 섹션에도 없음.
    - 구조로 끝내 못 놓은 계정은 인접 섹션에 밀어넣지 않고 별도 'unassigned(구분 미확정)'로 낸다
      (원칙 4·7 — 부채가 자본에 앉는 것보다 미확정이 정직). 합계/앵커/grand 판정은 is_total(금액
      산술 OR IFRS 구조앵커 마커)로 해, 산술이 놓친 구조총계도 말단에서 빠진다(루프11 CJ 케이스).
    - 루프12(계정 증발 방어): 각 계정 행은 표시명이 아니라 '행 위치(key)'로 보존한다. 표시명이
      우연히 같은 서로 다른 계정(유동/비유동 계약부채 등)도 각각 하나의 항목으로 실린다(원칙 7 —
      동명이라고 조용히 버리지 않는다). grand 총계와 빈 이름만 의도적으로 제외한다(제외 사유 명시).
      원본 행 수(이름 있는·grand 아닌 BS 행) == 화면 항목 수가 성립한다(불변식 test_screen_vs_raw_
      account_conservation). key 는 DART 원본 응답 순서(행 위치)라 우리가 만든 분류가 아니다(원칙 1).
    반환: {"assets":[{"name","key","account_id","selectable"[,"context"][,"members","member_keys"]}...],
           "liabilities":[...], "equity":[...], "unassigned":[...], "fallback_used": bool}.
           name=회사 보고 명칭(불변), key=안정 식별자(행 위치), context=동명 계정 구분용 소속 소계명
           (겹칠 때만), members=소속 말단 이름(검증·표시용), member_keys=소속 말단 안정 키(그룹 토글용).
           BS 행이나 섹션 앵커가 없으면 None.
    """
    bs = [r for r in rows if (r.get("sj_div") or "").strip() == "BS"]
    if not bs:
        return None
    ids = [(r.get("account_id") or "").strip() for r in bs]
    nms = [(r.get("account_nm") or "").strip() for r in bs]
    amounts = [(_parse_amount(r.get("thstrm_amount", "")) or 0) for r in bs]
    # 섹션 앵커가 하나도 없으면 구조를 신뢰할 수 없다 → 표를 만들지 않는다(원칙 4).
    if not any(a in (_BS_ANCHOR_ASSETS, _BS_ANCHOR_LIABILITIES, _BS_ANCHOR_EQUITY) for a in ids):
        return None
    is_sub, is_total = _bs_totals(ids, amounts)   # is_sub=금액산술, is_total=산술 OR IFRS 구조앵커
    sections, has_unassigned = _assign_sections(ids, amounts, is_total)
    members = _subtotal_members(ids, nms, amounts, is_total, sections)  # 행 위치(안정 키) 기반 소속
    _p1, end = _pass1_subtotals(amounts)
    encl = _enclosing_subtotal_names(nms, is_total, end)  # 동명 계정 구분용 소속 소계명(DART 원본)

    def collect(sec_name) -> list[dict]:
        # 루프12(계정 증발 방어): 표시명(account_nm)이 아니라 '행 위치'로 각 행을 보존한다. 이전엔
        # 이름으로 중복 제거해 유동/비유동처럼 표시명이 겹치는 서로 다른 계정을 조용히 버렸다(원칙 7
        # 위반). 이제 grand 총계와 빈 이름만 제외하고, 나머지 원본 행은 표시명이 겹쳐도 모두 싣는다.
        rows_here = [
            i for i in range(len(bs))
            if sections[i] == sec_name and ids[i] != _BS_ANCHOR_GRAND and nms[i]
        ]
        # 같은 섹션 안에서 표시명이 겹치는 계정에만 '구분 표기(context)'를 붙인다(최소 표기).
        name_count: dict[str, int] = {}
        for i in rows_here:
            name_count[nms[i]] = name_count.get(nms[i], 0) + 1

        out: list[dict] = []
        for i in rows_here:
            nm = nms[i]
            entry = {
                "name": nm,                   # 회사 보고 명칭 그대로(표시명 훼손 금지)
                "key": str(i),                # 안정 식별자 = DART 원본 행 위치(우리가 만든 분류 아님)
                "account_id": ids[i],         # DART 원본 계정ID(비고유일 수 있음 — 투명성용, 키 아님)
                "selectable": not is_total[i],
            }
            if name_count[nm] > 1 and encl[i]:  # 동명 계정 → 소속 소계명으로 구분(원본 이름은 유지)
                entry["context"] = encl[i]
            if is_total[i] and members[i]:      # 합계 + 소속이 산술로 잡힘 → 그룹 전체 선택 버튼
                # members(이름): 검증방 불변식(미확정 비오염)·개발방 테스트가 이름으로 대조 →
                #   그대로 이름 유지(load-bearing). member_keys(행 위치): 프론트 그룹 토글이
                #   동명 계정을 헷갈리지 않고 정확히 그 소속만 켜도록 하는 안정 키(2차 피해 해소).
                entry["members"] = [nms[k] for k in members[i]]
                entry["member_keys"] = [str(k) for k in members[i]]
            out.append(entry)
        return out

    return {
        "assets": collect("자산"),
        "liabilities": collect("부채"),
        "equity": collect("자본"),
        "unassigned": collect(None),          # 구분 미확정(구조로 못 놓음) — 밀어넣지 않고 정직 노출
        "fallback_used": has_unassigned,      # 하위호환 키: 미확정 행이 있으면 True(화면 '확인 요망')
    }


def _parse_amount(s: str) -> int | None:
    """DART 금액 문자열('1,234,567' 또는 '(1,234)')을 정수(원)로. 빈/'-'는 None."""
    s = (s or "").strip()
    if not s or s in ("-", "—"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace(" ", "")
    if not s.lstrip("-").isdigit():
        return None
    val = int(s)
    return -val if neg else val


# 표준 재무 규모 항목의 IFRS 계정ID(어댑터의 데이터 추출용 — 판단 로직의 고정표 아님).
_FIN_TARGETS = {
    "자산총계": ({"ifrs-full_Assets"}, {"자산총계"}),
    "자기자본": ({"ifrs-full_Equity"}, {"자본총계", "자기자본"}),
    "매출액": ({"ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers",
               "dart_OperatingRevenue"}, {"매출액", "수익(매출액)", "영업수익"}),
}


def _extract_financials(rows: list[dict]) -> dict:
    """
    재무제표 원시행에서 규모 분모 후보(매출액·자산총계·자기자본)를 뽑는다.
    표준 IFRS 계정ID 우선, 없으면 계정명 폴백. 당기 금액(thstrm_amount)을 원 단위 정수로.
    (어느 분모로 잴지는 LLM 판단 몫 — 여기서는 세 숫자를 '제공'만 한다.)
    """
    out: dict = {}
    for name, (ids, names) in _FIN_TARGETS.items():
        for row in rows:
            aid = (row.get("account_id") or "").strip()
            anm = (row.get("account_nm") or "").strip()
            if aid in ids or anm in names:
                val = _parse_amount(row.get("thstrm_amount", ""))
                if val is not None and val > 0:
                    out[name] = val
                    break
    return out
