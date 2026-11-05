# 토스증권 Open API 노트 — 실제 종목 시세(조회 전용)

> **현재 사용 중**입니다(2026-11-02 사용자 결정, DECISIONS D-03·D-23·D-34).
> - 모의투자 모드(기본): 시세·캔들·종목정보·랭킹·장 운영 캘린더만 **조회**합니다. 주문은 자체 모의계좌(PaperBroker)가 토스 실시간 현재가로 체결하고, 주문·계좌 API 는 네트워크 계층에서 차단합니다.
> - 실거래 모드(`TRADING_MODE=live` + 확인 문구): §5 의 주문·계좌 API 중 허용목록에 둔 것만 `TossTradingClient` 가 호출합니다.

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
| 분기 | **B.** 기본은 시세만 토스 API 로 받고, 주문·체결·잔고는 자체 `PaperBroker`(가상 계좌)로 시뮬레이션한다. 사용자가 실거래 모드를 켜면 토스 주문·계좌 API(§5)로 실제 주문한다(D-34). 조건주문·정정은 어느 모드에서도 호출하지 않는다. |

## 2. 인증

- 방식: OAuth 2.0 **Client Credentials Grant**
- `POST /oauth2/token` (Content-Type `application/x-www-form-urlencoded`)
  - 본문: `grant_type=client_credentials`, `client_id`, `client_secret`
  - 응답(OAuth2 표준, envelope 없음): `access_token`, `token_type`(=`Bearer`), `expires_in`(예시 86400초 = 24시간)
- **refresh token 없음.** 만료 시 같은 엔드포인트로 재발급.
- **클라이언트당 유효 토큰 1개.** 재발급하면 직전 토큰이 즉시 `401 token-revoked`. → 프로세스 간 토큰 공유, 불필요한 재발급 금지.
- 이후 모든 호출: `Authorization: Bearer {access_token}`
- 계좌·자산·주문·조건주문 API 는 추가로 `X-Tossinvest-Account: {accountSeq}` 헤더 필요 (int64). `accountSeq` 는 `GET /api/v1/accounts` 에서 얻는다.
- 키 발급: 토스증권 WTS 로그인 → 설정 > Open API 에서 `client_id`/`client_secret` 발급. **토스증권 계좌 보유자만 발급 가능.**
- **허용 IP 등록 필수**: 설정 > Open API > 허용 IP 관리. 미등록 IP 는 `403 ip-not-allowed` (토큰 발급은 `access_denied`).

## 3. Base URL

| 용도 | URL |
|---|---|
| REST | `https://openapi.tossinvest.com` |
| WebSocket | `wss://openapi-ws.tossinvest.com/ws/v1` |

모의/실전 구분 URL 은 존재하지 않는다.

## 4. 본 시스템이 사용하는 엔드포인트 (조회 전용 허용목록, `backend/app/market/toss.py`)

| 메서드 | 경로 | 용도 | Rate group (공식 → 본 시스템 80%) |
|---|---|---|---|
| POST | `/oauth2/token` | 토큰 발급 | AUTH 5/s → 4/s |
| GET | `/api/v1/prices?symbols=005930,000660` | 실시간 현재가 (최대 200종목) — 실시간 체결·평가·손절익절 | MARKET_DATA 15/s → 12/s |
| GET | `/api/v1/candles?symbol=&interval=1d&count=200&before=` | 일봉 (최신순, `nextBefore` 페이지) — 지표·차트·백테스트 | MARKET_DATA_CHART 20/s → 16/s |
| GET | `/api/v1/stocks?symbols=` | 종목명·시장·발행주식수(시가총액 계산) | STOCK 5/s → 4/s |
| GET | `/api/v1/stocks/all?market=KOSPI\|KOSDAQ&securityType=STOCK` | 유니버스(보통주), 하루 1회 캐시 | STOCK_ALL 1/s → 0.8/s |
| GET | `/api/v1/rankings?type=&marketCountry=KR&duration=` | 스크리닝(거래대금·거래량·상승/하락률 상위 100) | RANKING 5/s → 4/s |
| GET | `/api/v1/market-calendar/KR` | 휴장일·장 시간(전일/당일/익일) | MARKET_INFO 3/s → 2.4/s |
| GET | `/api/v1/orderbook`, `/api/v1/trades`, `/api/v1/price-limits` | 호가·최근 체결·상하한가 (`get_stock_status`) | MARKET_DATA |
| GET | `/api/v1/stocks/{symbol}/warnings` | 매수 유의사항 | STOCK |
| GET | `/api/v1/stocks/{symbol}/investor-trading` · `program-trades` · `short-selling` · `credit-trades` · `securities-lending` | 수급 (`get_supply_demand`, 종목 상세 화면) | STOCK_TRADING_TREND 10/s → 8/s |
| GET | `/api/v1/exchange-rate` | 원/달러 환율 | MARKET_INFO |
| GET | `/api/v1/market-indicators/prices`, `/{KOSPI\|KOSDAQ\|KR_BOND_10Y}/candles`, `/{KOSPI\|KOSDAQ}/investor-trading` | 지수·국채·지수 투자자 매매 (`get_market_overview`, 대시보드 주요 지표 D-40). 지원 심볼은 코스피·코스닥·KR_BOND_* 8종뿐 — 미국 지수는 QQQ·SPY ETF 시세(`/prices`·`/candles`)로 대신 | MARKET_INDICATOR 10/s → 8/s · CHART 5/s → 4/s |
| GET | `/api/v1/sectors`, `/sectors/{id}`, `/sectors/{id}/stocks`, `/sectors/rankings` | 섹터 (`explore_sector`, 시장 개요) | SECTOR · SECTOR_RANKING 5/s → 4/s |
| GET | `/api/v1/rankings/investor-trading` | 외국인·기관·개인 순매수 상위 (시장 개요, 수급 스크리닝) | RANKING |

### 응답 형식 (공식 예시 기준)

- 성공 응답은 `{"result": ...}` envelope.
- `GET /api/v1/prices` → `result[]`: `symbol`, `timestamp`(ISO8601, +09:00), `lastPrice`(**문자열**), `currency`
- `GET /api/v1/candles` → `result.candles[]`: `timestamp`, `openPrice`, `highPrice`, `lowPrice`, `closePrice`, `volume`(모두 **문자열**), `currency`; `result.nextBefore`(마지막 페이지면 `null`)
  - `1m` 봉의 `timestamp` 는 **봉 종료 시각** (09:01 봉 = 09:00:00~09:00:59.999)
  - `1d` 봉은 해당 거래일 현지 자정을 KST 로 표기
  - `before` 의 `+` 는 `%2B` 로 인코딩
  - 1분봉 과거 데이터: 국내 2022-11-23 이후만 제공
  - 체결 없는 국내 1분봉은 직전가로 채운 거래량 0 봉
  - `interval` 은 `1m`·`1d` 두 값뿐 (OpenAPI 1.2.28 enum, 2026-11-05 확인). 5·15·30·60분·주·월 봉은 앱이 모아서 만든다 (D-63)
- `GET /api/v1/market-indicators/{symbol}/candles` → 같은 모양(`candles[]`, `nextBefore`, `before` 지원). timestamp 는 **봉 시작 시각**. `1m` 은 KOSPI·KOSDAQ 만, 국채(`KR_BOND_*`)는 `1d` 만(분봉 요청 시 400). 환율 봉 API 는 없다
- `GET /api/v1/market-calendar/KR` → `result.today|previousBusinessDay|nextBusinessDay` 각 `{date, integrated}`;
  `integrated` 가 `null` 이면 **휴장일**. `integrated.preMarket|regularMarket|afterMarket` 각 `{startTime, singlePriceAuctionStartTime|EndTime, endTime}` (세션 휴장 시 `null`).
  예시: 정규장 09:00~15:30, 종가 단일가 15:20 시작, 프리마켓 08:00~09:00, 애프터마켓 15:30~20:00 (KRX+NXT 통합).

## 5. 주문·계좌 API (실거래 모드에서만 사용)

| 메서드 | 경로 | 사용 | 비고 |
|---|---|---|---|
| GET | `/api/v1/accounts` | ✅ | `[{accountNo, accountSeq, accountType}]`. 현재 `BROKERAGE` 만 반환. `TOSS_ACCOUNT_SEQ` 가 없으면 종합계좌가 하나일 때 자동 선택 |
| GET | `/api/v1/holdings` | ✅ | `items[]`: `symbol, name, marketCountry, quantity, lastPrice, averagePurchasePrice, marketValue, profitLoss, cost{commission,tax}` (숫자는 문자열) |
| GET | `/api/v1/buying-power?currency=KRW` | ✅ | `{currency, cashBuyingPower}` — 현금 주문가능금액(신용 제외) |
| POST | `/api/v1/orders` | ✅ | 아래 본문. 응답 `{orderId, clientOrderId}` |
| GET | `/api/v1/orders/{orderId}` | ✅ | `status`, `execution{filledQuantity, averageFilledPrice, filledAmount, commission, tax, filledAt}` — 주문 1건 = 1행(부분체결 누적) |
| POST | `/api/v1/orders/{orderId}/cancel` | ✅ | 본문 `{}`. 응답 `{orderId}` 는 취소 요청의 **새** 번호. 부분 수량 취소 없음 |
| GET | `/api/v1/sellable-quantity` | 허용만 | `{sellableQuantity}` |
| GET | `/api/v1/orders?status=OPEN\|CLOSED` | ❌ | 목록 조회(응답에 clientOrderId 없음) |
| POST | `/api/v1/orders/{orderId}/modify` | ❌ | 정정 — 호출하지 않음(차단) |
| * | `/api/v1/conditional-orders/**` | ❌ | 조건주문 — 호출하지 않음(차단) |
| GET | `/api/v1/commissions` | ❌ | 호출하지 않음 |

주문 본문(`OrderCreateQuantityBased`, 국내):

| 필드 | 필수 | 이 시스템이 보내는 값 |
|---|---|---|
| `clientOrderId` | 선택 | 내부 주문 UUID(32자, `[a-zA-Z0-9-_]`, ≤36). 멱등키 10분 |
| `symbol` | ✅ | 6자리 종목코드 |
| `side` | ✅ | `BUY` / `SELL` |
| `orderType` | ✅ | `MARKET` / `LIMIT` |
| `timeInForce` | 선택 | `DAY` (`OPG` 장전 동시호가, `CLS` 는 미국 전용 — 쓰지 않음) |
| `quantity` | ✅ | 정수 문자열 |
| `price` | LIMIT 만 | 호가단위에 맞춘 정수 문자열. 시장가에는 보내지 않음(보내면 400) |
| `confirmHighValueOrder` | 선택 | 보내지 않음 → 1억원 이상 주문은 이 시스템이 먼저 막음. 30억원 초과는 토스가 항상 422 |

- 거래소 선택 필드 없음: 스마트 주문 라우팅(SOR)만 지원. 계좌가 SOR 이 아니면 `422 investor-exchange-not-integrated`.
- 같은 종목 반대 방향 미체결이 있으면 `409 opposite-pending-order-exists`(국내).
- 주문 상태: `PENDING, PENDING_CANCEL, PENDING_REPLACE, PARTIAL_FILLED, FILLED, CANCELED, REJECTED, CANCEL_REJECTED, REPLACE_REJECTED, REPLACED`.
  내부 상태로는 대기류 → SUBMITTED(체결분 있으면 PARTIALLY_FILLED), `FILLED` → FILLED, `CANCELED·REPLACED` → CANCELED, `REJECTED` → REJECTED(체결분이 있으면 CANCELED).
- 주요 오류 코드(422): `insufficient-buying-power`, `insufficient-sellable-quantity`, `order-hours-closed`, `stock-restricted`, `price-out-of-range`, `order-type-not-allowed`, `max-order-amount-exceeded`, `order-limit-exceeded`, `idempotency-key-conflict`, `account-restricted`, `prerequisite-required`.
- 레이트 리밋(그룹별 초당): `ACCOUNT 1`, `ASSET 5`, `ORDER 10`(생성·정정·취소 합산), `ORDER_HISTORY 5`, `ORDER_INFO 6`(09:00~09:10 은 3). 이 시스템은 80% 로 맞춘다.
- FAQ: 짧은 시간에 소액 주문을 대량으로 내면 거래가 일시 제한될 수 있다.
- **명세에 없는 것**: 계좌의 시장 구분, 주문의 거래소 지정, 주문 조회 응답의 clientOrderId, 거부 사유 필드, 부분 수량 취소, 샌드박스.

미국 주식 주문 (D-55, `US_TRADING_MODE=live` 일 때만):
- 같은 `POST /api/v1/orders`. `symbol` = 영문 티커. `price` = 달러 소수 문자열 — $1 이상 소수 둘째, 미만 넷째 자리(그 아래는 토스가 절삭).
- 수량은 기본 정수. 소수점 수량은 미국 시장가 매도만, 금액 주문(`orderAmount`, 달러)은 미국 시장가 전용이고 정규장 시작~종료 1시간 전만 받는다. **이 시스템은 정수 수량만 쓴다.**
- `timeInForce`: `DAY`(기본), `CLS`(미국 지정가 종가 주문). 이 시스템은 `DAY` 만.
- 잔고 `items[]` 에 `marketCountry`(KR/US)·`currency`(KRW/USD). 가격은 거래 통화 기준. 주문가능금액 `buying-power?currency=USD`.
- `confirmHighValueOrder`: 1억원 이상 주문에 필요. 미국 주문도 원화 환산으로 보고 1억원 이상은 보내지 않는다.

장 운영 캘린더의 장외 세션 (D-57):
- KR `integrated.{preMarket, regularMarket, afterMarket}` (KRX+NXT 통합, 장전/장후 시간외종가 제외). preMarket `{startTime, singlePriceAuctionStartTime, endTime}`, afterMarket `{startTime, singlePriceAuctionEndTime, endTime}`.
- US `{dayMarket, preMarket, regularMarket, afterMarket}` 각각 `{startTime, endTime}` (KST, 휴장이면 모두 null).
- `/prices` 결과의 `timestamp`(데이터 시각, 체결이 없으면 null)로 장외 체결 여부를 판단한다.

→ 모의투자 모드에서는 `core/http.check_outbound` 가 주문·계좌 경로를 **어떤 호스트든 허용목록보다 먼저** 차단한다(`LiveOrderBlockedError`). 실거래 모드의 `TossTradingClient`(`allow_trading=True`)만 위 표의 ✅·허용 경로를 정확히 호출할 수 있고, 정정·조건주문·다른 호스트는 여전히 차단된다. 주문 POST 는 자동 재시도하지 않는다(`tests/test_guard.py`, `tests/test_live_broker.py`).

## 6. 실시간 시세 (WebSocket)

- `wss://openapi-ws.tossinvest.com/ws/v1`, 핸드셰이크에 `Authorization: Bearer` 헤더
- 선언형 구독: JSON 배열 1개가 전체 구독(full-replace). 예: `[{"type":"trade:kr","codes":["005930"]}]`
- 채널: `trade:{kr|us}`, `orderbook:{kr|us}`, `personal:order`
- 계정당 동시 연결 2개, 연결당 구독 100건, 선언 5회/초
- 클라이언트 수신 180초 없으면 서버가 종료 → 60초마다 `PING` 권장
- 시세 채널은 LOSSY(최신 우선)
- **본 시스템 결정**: 판단은 하루 2회, 장중 감시는 1분 주기라 REST 폴링(현재가 5초 캐시)으로 충분하다. 토스 WebSocket 은 쓰지 않는다(웹소켓 호스트도 차단).

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
- 본 시스템: 08:30~09:00 접수 → 09:00 이후 첫 현재가로 체결, 09:00~15:20 접수 → 현재가로 즉시 체결(지정가는 도달 시), 15:20 이후 미체결분 취소. 그 외 시간 접수 거부 (D-24).
- 휴장일: `market-calendar/KR` 결과가 내장 KRX 휴장일 목록보다 우선한다(기동 시·매일 장전 갱신).

## 10. 수수료·세금 (PaperBroker 가정값)

공식 `GET /api/v1/commissions` 는 계좌 헤더가 필요한 주문 그룹이라 호출하지 않는다. 대신 설정값으로 둔다:

- 매매 수수료 `PAPER_COMMISSION_RATE` 기본 0.00015 (0.015%) — FAQ 에서 `commissionRate` 단위가 소수 비율(0.00015=0.015%)임을 확인
- 매도 증권거래세 `PAPER_SELL_TAX_RATE` 기본 0.0020 (0.20%) — **TODO(verify)**: 시장(코스피/코스닥)·연도별 세율은 사용자가 확인 후 조정
- 슬리피지 `PAPER_SLIPPAGE_BPS` 기본 5bp

## 11. TODO(verify)

- `expires_in` 실제 값(예시 86400) — 실제 응답 값 사용, 만료 60초 전 선제 재발급
- `1d` 캔들의 정확한 timestamp 의미는 FAQ 기준으로 해석했으나 실키로 재확인 필요
- 증권거래세율(위 10항)
