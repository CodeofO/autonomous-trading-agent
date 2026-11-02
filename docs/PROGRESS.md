# 진행 현황

최종 갱신: 2026-11-02

## 마일스톤

| # | 내용 | 상태 | 커밋 |
|---|---|---|---|
| M0 | API 조사(토스 → 공공데이터·DART·네이버), 골격, docker compose, make | ✅ | `feat(M0)` |
| M1 | BrokerInterface + PaperBroker + MockWorld, 실거래 차단 가드 | ✅ | `feat(M1)` |
| M2 | 시세 저장소, 공공데이터·DART·네이버 어댑터, respx 테스트 | ✅ | `feat(M2)` |
| M3 | 규칙 전략 2종, 지표, 리스크 모듈(경계값 테스트) | ✅ | `feat(M3)` |
| M4 | LLM 매니저 에이전트, 주문 상태 머신, 엔진, 스케줄러, 킬 스위치, Mock 이틀치 시뮬레이션 E2E | ✅ | `feat(M4)` |
| M5 | REST/WS API, 인증 | ✅ | `feat(M5)` |
| M6 | 프론트 전 화면 + 트레이딩 룸 채팅, Vitest, Playwright | ✅ | `feat(M6)` |
| M7 | 백테스터, 일일 리포트, 고정 데이터 지표 재현 테스트 | ✅ | `feat(M7)` |
| M8 | 실연동 점검(Docker·로컬 기동), 문서화 | ✅ (실키 연동 제외 — 아래) | `docs(M8)` |

## 테스트 결과 요약

| 영역 | 결과 |
|---|---|
| 백엔드 pytest | **121 passed**, 라인 커버리지 **93%** (`app/`) |
| 프론트 Vitest | **13 passed** (트레이딩 룸 메시지·결정 카드·도구 칩·입력중, 킬 스위치 모달, PAPER 배지, 리스크 검증, 로그인) |
| Playwright E2E | **2 passed** (계정 생성 → 대시보드 → 킬 스위치 → 재개 / 트레이딩 룸 세션 → 결정 카드) |
| 린트 | ruff · mypy(51 파일 무오류) · eslint(오류 0, 경고 4: react-refresh 안내) · prettier · tsc 통과 |
| 기동 확인 | `./run.sh local` → 로그인·대시보드·트레이딩 룸·종목 상세 화면 확인. `docker compose up` → PostgreSQL 위에서 로그인·세션·주문·채팅·WS(nginx 프록시) 확인 후 `down -v` 로 정리 |

주요 검증 항목:
- 실거래 차단: 증권사 호스트 9곳 × 메서드 3종, 주문형 경로, 허용목록 밖 호스트, 브로커 플래그 변조 → 모두 차단. 차단 요청은 네트워크로 나가지 않음
- 리스크 경계값: 1회 주문금액, 종목 비중 정확히 20%, 일일 손실 정확히 −3%, 손절 −5%/익절 +10% 경계, 08:29:59/08:30/15:19:59/15:20, 최소 주문 간격 59/60분
- 백테스트: MA2/3 고정 데이터 → 최종 965,000원(−3.5%), MDD −4.1708%, 승률 0%, 거래 2건이 손계산과 일치
- 비밀정보: `/api/system`·OpenAPI 응답에 키 미노출, 로그 마스킹(키·Bearer·`token=`·`serviceKey=`), `.env` git 제외

## 남은 일 / 사람이 해야 할 일

1. **LLM 연결**: `.env` 에 `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` 를 채우세요. 테스트는 가짜 LLM(respx)으로만 했고, 실제 모델로는 아직 돌려 보지 않았습니다.
2. **실데이터 키**(선택): 공공데이터포털 주식시세 활용신청, OpenDART 인증키, 네이버 검색 API 애플리케이션 등록.
3. 실키 확보 후 실제 응답으로 픽스처를 다시 녹화하세요(현재 픽스처는 공식 문서 스키마 기반, `backend/tests/fixtures/README.md`).

## 알려진 문제 · 제약

| 항목 | 내용 | 대응 |
|---|---|---|
| 시세 지연 | 공공데이터는 T+1 일봉뿐 → 장중 가격 반응 불가, 체결 확정이 다음 날 | 설계로 수용(D-05). 증권사 계좌가 생기면 시세 어댑터 추가 검토 |
| 실키 미검증 | 공공데이터 숫자 필드 타입, `items.item` 단건 형태, `endBasDt` 경계, DART 계정명 편차 | 관대한 파싱으로 처리했지만 실응답 확인 필요(`TODO(verify)`) |
| 휴장일 | 내장 목록은 best-effort | 실제와 다르면 `core/market_calendar.py` 수정 |
| 매도세율 | 0.20% 가정 | `.env` `PAPER_SELL_TAX_RATE` 로 조정 |
| LLM 비용 | 세션당 최대 24단계 tool 호출 | `LLM_MAX_STEPS` 로 상한 조정 |
| 규칙 에이전트 | 단순 점수 합산 예시 — 성과 기대 금지 | LLM 연결 권장 |
| 인증 | 로컬 단일 사용자, HTTP | 외부 공개 배포 금지(범위 밖) |
| 컨테이너 로그 시각 | slim 이미지에 tzdata 가 없어 로그 시각이 UTC 로 보일 수 있음 | 기능 영향 없음 |
| eslint 경고 4건 | react-refresh 의 개발용 핫리로드 안내 | 기능 영향 없음 |

## 재현 방법 (문제 발생 시)

```bash
./run.sh test
```

```bash
cd frontend && npx playwright test
```

```bash
cd backend && uv run pytest tests/test_engine_agent.py -q
```

E2E 는 별도 포트(8001/5174)와 `frontend/test-results/e2e-data` 임시 DB 를 씁니다. 마지막 명령은 Mock 시장 이틀치 시뮬레이션입니다.
