# 루프 6b 검증 보고 — 넓은 수집이 진짜 작동하는가 (검증방)

- 검증자: 검증방(독립) / 2026-07-17
- 방법: 개발방 보고를 믿지 않고 코드 정독 + 라이브 실행 + 검증방 소유 불변식으로 실측.
- 결론: **검증 1·2·3 전부 통과.** 넓은 수집·깔때기·정합이 말이 아니라 실제로 동작함.

## ■ 설계자 지시 역검증
루프6에서 "존재하지 않던 게이트/dedup"을 루프6b가 실제로 신설했다. docs/ 원칙과 충돌 없음
(수집은 회사명 단일 검색어, 스크리닝은 이해 기반·느슨, 접힘=삭제 아님). 실행 가능 — 역검증 통과.

---

## 검증 1 — 실제 수집량 (핵심): 통과

- **태영건설 실제 넓은 수집 = 1056건** (라이브 실측, LLM 없이 네이버만).
  - meta: raw_fetched=1300 (date 1000 + sim 300), url_dups_removed=244, after_url_dedup=1056,
    dropped_older_than_window=0, hit_naver_ceiling=**true**.
  - 15/30/50 제한값 아님(False), 수백건대 이상(1056). 개발방 주장(1056) 독립 재현됨.
- **수집 건수 드롭다운 제거**: index.html 에 `<option>15/30/50`·`id="display"`·'수집 건수' 라벨
  전부 없음(grep 확인). 수집량은 사용자가 안 고르고 시스템이 넓게 잡는다.
- 코드 경로: `src/sources/naver_news.py::fetch_broad` — 회사명 단일 검색어(부정 키워드 없음),
  최신순 10페이지(≈1000) + 정확도순 3페이지(300), URL-literal 중복만 제거(의미병합 아님·원칙 1),
  1년 필터(파싱실패분 보존), 네이버 1000 상한 표기(원칙 7).

## 검증 2 — 깔때기 실재: 통과

- **깔때기가 코드에 실재하고 순서대로 돈다**: `pipeline.run` → `fetch_broad`(수집) →
  `prescreen`(Haiku: 중복 event_id + 간이 관련성 relevant) → `judge_items`(Opus 7축) →
  `classify_and_rank`. 실행 counts 로 확인.
- **스크리닝에 키워드/고정목록 없음(원칙 1)**: `prescreen.py` 시스템 프롬프트는 "고정된 단어
  목록으로 켜고 끄지 마라. 기사를 이해해서 판단하라" 명시. 코드는 이해 결과(event_id·relevant)를
  묶기만 함. 문자열 유사도 임계값 없음. 방증: '워크아웃 신청' 3건은 같은 사건으로 묶고,
  표현 비슷한 '부산화재/울산화재'는 다른 사건으로 분리 — 이해 기반의 증거.
- **악재 거짓 탈락 없음(원칙 7, 가상 악재 서사 주입 실측)**: 검증방 소유 불변식
  `tests/invariants/test_prescreen.py`(5건) 실행 —
  - 명백 악재 6종(워크아웃·분식·소송·감사의견·횡령·디폴트) **전부 survivors**(Opus 로 감).
  - 다른 사건(부산/울산 화재) **미병합**(둘 다 survivors).
  - 같은 사건 중복(워크아웃 3건) → 대표 1(워크B) + 중복 2 접힘("중복·대표 기사에 통합") = dedup 실재.
  - 명백 무관(봉사·광고) → "간이 스크리닝·재무·감사 무관"으로 접힘(삭제 아님, folded 보존).
  - fail-open: Haiku 가 어떤 항목을 누락하면 relevant=True+고유 event 로 살린다(놓침 방지, 원칙 7).

## 검증 3 — 정합·판단 불변: 통과

- **수집 = 대표 + 접힘 (증발 없음)**: prescreen 불변식 실측 — collected 12 = survivors 8 + folded 4.
  counts={judged 8, after_dedup 10, dup_folded 2, irrelevant_folded 2, truncated 0}.
- **전체 파이프라인 정합(개발방 라이브 재구성)**: 1056 = 표시 34 + 판단후접힘 114 + 입구접힘 908,
  judged(=survivors) 148 = 34 + 114, 148 + 908 = 1056. 항등식 성립(증발 0).
- **7축 판단 로직 미변경**: `engine.py` 는 `on_usage`(순수 비용 텔레메트리, 판단 입출력·로직
  무영향)만 추가. schema/prompt/ranking 불변. 전체 스위트 **46 pass**(루프6 41 + 신규 prescreen 5),
  기존 판단 불변식(direction·paraphrase·magnitude·screening 등) 전부 초록 — 회귀 0.

---

## 실측 수치 (요약)
| 항목 | 값 |
|---|---|
| 태영건설 실제 수집 | **1056건** (raw 1300, URL중복 244 제거, 네이버 1000 상한 도달) |
| 깔때기(검증방 통제셋 12건) | survivors 8 / folded 4 (dup 2 + 무관 2), 증발 0 |
| 명백 악재 6종 거짓 탈락 | 0 (전부 Opus 로 생존) |
| 다른 사건 오병합 | 0 (부산/울산 화재 분리) |
| 전체 테스트 | 46 pass (회귀 0) |

## 다음 처방 (harness/pending/)
- `md-updates/loop6b-naver-ceiling.md` (신규, 낮음): 네이버 start≤1000 하드캡으로 고volume 회사는
  '1년 전체'가 아닌 '최근분+관련도 보강'임을 문구 정직화(이미 표기되나 오해 방지) / 커버리지 개선 선택.
- `new-invariants/loop6-dedup-must-not-merge.md` (발효·완료): dedup 도입에 맞춰 검증방이 가드
  불변식(test_prescreen)을 세움 — 상태 갱신.
- 잔류: fake-precision-breadth(중), loop6-resolution-fold-gap(낮), loop6-screening-premise-mismatch
  (개발방이 깔때기 도입으로 전제 실체화 — 대체로 해소), loop2/loop4 관측(낮).
