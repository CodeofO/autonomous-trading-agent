# 토스증권 Open API 조사 노트 (보관용)

> **보관 문서**입니다. 공식 모의투자 환경이 없다는 조사 결과에 따라 사용자 결정(2026-11-02)으로 토스증권 연동은 하지 않습니다(DECISIONS.md D-03).
> 아래에 나오는 코드 경로(`TossHttpClient` 등)는 현재 코드에 없습니다. 현재 데이터 소스는 [../DATA_API_NOTES.md](../DATA_API_NOTES.md) 를 보세요.

> 조사일: 2026-11-02 · 대상 문서 버전: OpenAPI `1.2.28`
> 출처(공식):
> - 개발자센터: <https://developers.tossinvest.com/docs> (`/llms.txt` 에서 원본 문서 위치 안내)
> - 개요: <https://openapi.tossinvest.com/openapi-docs/overview.md>
> - FAQ: <https://openapi.tossinvest.com/openapi-docs/faq.md>
> - OpenAPI 3.0 (정본): <https://openapi.tossinvest.com/openapi-docs/latest/openapi.json>
> - AsyncAPI 3.0 (웹소켓 정본): <https://openapi.tossinvest.com/openapi-docs/latest/asyncapi.json>
>
> GitHub/PyPI 의 비공식 SDK(tossinvest-core, tossinvest-cli 등)는 **참고도 의존성도 하지 않았다.** 아래 내용은 모두 위 공식 문서에서 확인한 것이다.

## 1. 결론 — A/B 분기

| 항목 | 확인 결과 |
|---|---|
| 공식 모의투자(샌드박스) 환경 | **없음.** OpenAPI `servers` 는 `https://openapi.tossinvest.com` 하나뿐이고, 개요·FAQ·OpenAPI·AsyncAPI 어디에도 모의투자/샌드박스/테스트 환경 언급이 없다. 예시 client_id 도 `tsck_live_...` 형태. |
| 분기 | **B.** 시세는 토스 API(조회 전용)로 받고, 주문·체결·잔고는 자체 `PaperBroker`(가상 계좌)로 시뮬레이션한다. **토스 주문/조건주문/계좌 API 는 호출하지 않는다.** |

## 2. 인증

- 방식: OAuth 2.0 **Client Credentials Grant**
- `POST /oauth2/token` (Content-Type `application/x-www-form-urlencoded`)
  - 본문: `grant_type=client_credentials`, `client_id`, `client_secret`
  - 응답(OAuth2 표준, envelope 없음): `access_token`, `token_type`(=`Bearer`), `expires_in`(예시 86400초 = 24시간)
- **refresh token 없음.** 만료 시 같은 엔드포인트로 재발급.
- **클라이언트당 유효 토큰 1개.** 재발급하면 직전 토큰이 즉시 `401 token-revoked`. → 프로세스 간 토큰 공유, 불필요한 재발급 금지.
- 이후 모든 호출: `Authorization: Bearer {access_token}`
- 계좌·자산·주문·조건주문 API 는 추가로 `X-Tossinvest-Account: {accountSeq}` 헤더 필요 (본 시스템은 호출하지 않음).
- 키 발급: 토스증권 WTS 로그인 → 설정 > Open API 에서 `client_id`/`client_secret` 발급. **토스증권 계좌 보유자만 발급 가능.**
- **허용 IP 등록 필수**: 설정 > Open API > 허용 IP 관리. 미등록 IP 는 `403 ip-not-allowed` (토큰 발급은 `access_denied`).

## 3. Base URL

| 용도 | URL |
|---|---|
| REST | `https://openapi.tossinvest.com` |
| WebSocket | `wss://openapi-ws.tossinvest.com/ws/v1` |

모의/실전 구분 URL 은 존재하지 않는다.

## 4. 본 시스템이 사용하는 엔드포인트 (조회 전용 허용목록)

| 메서드 | 경로 | 용도 | Rate group |
|---|---|---|---|
| POST | `/oauth2/token` | 토큰 발급 | AUTH (5/s) |
| GET | `/api/v1/prices?symbols=005930,000660` | 현재가 (최대 200종목, 콤마 구분) | MARKET_DATA (15/s) |
| GET | `/api/v1/candles?symbol=&interval=1m\|1d&count=&before=&adjusted=` | 분봉/일봉 (최대 200개, **최신순 내림차순**, `nextBefore` 로 과거 페이지) | MARKET_DATA_CHART (20/s) |
| GET | `/api/v1/market-calendar/KR?date=YYYY-MM-DD` | 국내 장 운영시간(전일/당일/익일) | MARKET_INFO (3/s) |

### 응답 형식 (공식 예시 기준)

- 성공 응답은 `{"result": ...}` envelope.
- `GET /api/v1/prices` → `result[]`: `symbol`, `timestamp`(ISO8601, +09:00), `lastPrice`(**문자열**), `currency`
- `GET /api/v1/candles` → `result.candles[]`: `timestamp`, `openPrice`, `highPrice`, `lowPrice`, `closePrice`, `volume`(모두 **문자열**), `currency`; `result.nextBefore`(마지막 페이지면 `null`)
  - `1m` 봉의 `timestamp` 는 **봉 종료 시각** (09:01 봉 = 09:00:00~09:00:59.999)
  - `1d` 봉은 해당 거래일 현지 자정을 KST 로 표기
  - `before` 의 `+` 는 `%2B` 로 인코딩
  - 1분봉 과거 데이터: 국내 2022-11-23 이후만 제공
  - 체결 없는 국내 1분봉은 직전가로 채운 거래량 0 봉
- `GET /api/v1/market-calendar/KR` → `result.today|previousBusinessDay|nextBusinessDay` 각 `{date, integrated}`;
  `integrated` 가 `null` 이면 **휴장일**. `integrated.preMarket|regularMarket|afterMarket` 각 `{startTime, singlePriceAuctionStartTime|EndTime, endTime}` (세션 휴장 시 `null`).
  예시: 정규장 09:00~15:30, 종가 단일가 15:20 시작, 프리마켓 08:00~09:00, 애프터마켓 15:30~20:00 (KRX+NXT 통합).

## 5. 확인했지만 **호출하지 않는** 엔드포인트 (어댑터에서 차단)

| 메서드 | 경로 | 비고 |
|---|---|---|
| POST | `/api/v1/orders` | 주문 생성 (지정가·시장가). `clientOrderId` 멱등키(10분 유효), 1억원 이상은 `confirmHighValueOrder` 필요 |
| POST | `/api/v1/orders/{orderId}/modify` | 정정 — 새 orderId 발급될 수 있음 |
| POST | `/api/v1/orders/{orderId}/cancel` | 취소 |
| GET | `/api/v1/orders`, `/api/v1/orders/{orderId}` | 주문 조회 (주문 1건 = 1행, 부분체결은 평균가 합산) |
| GET | `/api/v1/buying-power`, `/sellable-quantity`, `/commissions` | 주문 가능 정보 |
| * | `/api/v1/conditional-orders/**` | 조건주문 (SINGLE/OCO/OTO) |
| GET | `/api/v1/accounts`, `/api/v1/holdings` | 실계좌 정보 |

→ `TossHttpClient` 는 **(메서드, 경로) 허용목록** 방식이라 위 경로는 네트워크로 나가기 전에 `LiveOrderBlockedError` 를 던진다 (`backend/app/brokers/guard.py`, 테스트 `tests/test_guard.py`).

## 6. 실시간 시세 (WebSocket)

- `wss://openapi-ws.tossinvest.com/ws/v1`, 핸드셰이크에 `Authorization: Bearer` 헤더
- 선언형 구독: JSON 배열 1개가 전체 구독(full-replace). 예: `[{"type":"trade:kr","codes":["005930"]}]`
- 채널: `trade:{kr|us}`, `orderbook:{kr|us}`, `personal:order`
- 계정당 동시 연결 2개, 연결당 구독 100건, 선언 5회/초
- 클라이언트 수신 180초 없으면 서버가 종료 → 60초마다 `PING` 권장
- 시세 채널은 LOSSY(최신 우선)
- **본 시스템 결정**: 전략 주기가 1분이므로 REST 폴링으로 충분하다. 토스 WebSocket 클라이언트는 이번 범위에서 구현하지 않는다 (DECISIONS.md D-07).

## 7. Rate Limit

- 클라이언트 × API 그룹 단위 TPS 제한 (위 표 참고). 현재 한도는 응답 헤더 `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` 로 확인.
- 429 응답: `Retry-After` 헤더 만큼 대기 후 재시도, 지수 백오프(1s→2s→4s…) + jitter 권장.
- 비정상 의심 IP 대역은 1회/초로 제한될 수 있음.
- 본 시스템: 그룹별 토큰 버킷(한도의 80%로 보수적 설정) + 429/5xx 지수 백오프 재시도.

## 8. 에러 모델

```json
{"error": {"requestId": "...", "code": "invalid-request", "message": "...", "data": {}}}
```

주요 코드: `invalid-token`, `expired-token`, `token-revoked`(401) · `ip-not-allowed`(403) · `stock-not-found`(404) ·
`rate-limit-exceeded`, `edge-rate-limit-exceeded`(429) · `internal-error`, `maintenance`(500) ·
주문 관련 `order-hours-closed`, `insufficient-buying-power`, `opposite-pending-order-exists` 등(본 시스템은 PaperBroker 에서 유사하게 흉내냄).

본 시스템은 401 `expired-token`/`invalid-token` 수신 시 토큰을 1회 재발급 후 재시도한다 (`token-revoked` 는 다른 프로세스가 재발급했다는 뜻이므로 같은 처리).

## 9. 장 운영시간 제약

- 국내 주문 가능 시간은 매매 시스템과 동일, 불가 시간엔 `422 order-hours-closed`.
- 본 시스템의 PaperBroker/리스크 규칙은 **정규장 연속매매 구간(09:00~15:20 KST)** 에만 주문을 허용한다 (단일가 구간·프리/애프터마켓 제외, D-05).
- 휴장일: 토스 키가 있으면 `market-calendar/KR` 로 판정, 없으면 내장 KRX 휴장일 목록(`app/core/market_calendar.py`) 사용.

## 10. 수수료·세금 (PaperBroker 가정값)

공식 `GET /api/v1/commissions` 는 계좌 헤더가 필요한 주문 그룹이라 호출하지 않는다. 대신 설정값으로 둔다:

- 매매 수수료 `PAPER_COMMISSION_RATE` 기본 0.00015 (0.015%) — FAQ 에서 `commissionRate` 단위가 소수 비율(0.00015=0.015%)임을 확인
- 매도 증권거래세 `PAPER_SELL_TAX_RATE` 기본 0.0020 (0.20%) — **TODO(verify)**: 시장(코스피/코스닥)·연도별 세율은 사용자가 확인 후 조정
- 슬리피지 `PAPER_SLIPPAGE_BPS` 기본 5bp

## 11. TODO(verify)

- `expires_in` 실제 값(예시 86400) — 실제 응답 값 사용, 만료 60초 전 선제 재발급
- `1d` 캔들의 정확한 timestamp 의미는 FAQ 기준으로 해석했으나 실키로 재확인 필요
- 증권거래세율(위 10항)
