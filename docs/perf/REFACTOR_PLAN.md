# 리팩토링 계획 (2026-11-04 밤)

목표: 기능은 그대로 두고 **화면 반응 속도·버벅임·DB 부하·코드 구조**를 개선한다. 리팩토링 전 기준 지표(`docs/perf/baseline-*.json`)와 같은 스크립트로 리팩토링 뒤를 다시 재서(`perf/README.md`) 전후 비교 리포트(`docs/perf/REFACTOR_REPORT.md`)를 쓴다.

원칙
- 동작 변경 없음: 기존 테스트(서버 304·화면 205·e2e 6)는 지우거나 건너뛰지 않는다. 바뀐 계약은 테스트로 고정한다.
- 화면·API 형식은 유지한다(필드 추가는 가능, 삭제·이름 변경은 하지 않음).
- 데이터 정리는 보수적으로: 사용자가 보는 기록(주문·체결·세션·결정·채팅 대화)은 지우지 않는다. 지우는 것은 다시 받을 수 있는 캐시·중복 정보뿐이다.
- 단계마다 ruff·mypy·pytest·vitest 통과를 확인한다.

## 서버 (기준: docs/perf/baseline-backend.md)

| # | 문제 (측정값) | 고치는 방법 | 확인 지표 |
|---|---|---|---|
| B1 | 종목 이름을 한 건씩 DB 조회(N+1): `/orders` 300건에 SQL 302회, 판단 세션 SQL 1,163회 중 1,047회 | `PriceStore` 에 종목코드→이름 메모리 맵(종목 목록·일봉 갱신 때 무효화), 목록 API 는 한 번에 조회. `/portfolio` 의 포지션 이중 계산 제거 | 요청당 SQL 수 (/orders·/fills·/portfolio·세션) |
| B2 | 매매 내역(실현손익)을 호출마다 전체 체결로 다시 계산 | 마지막 체결 id 기준 결과 캐시 | 세션·답장 SQL·시간 |
| B3 | 설정(kv)을 읽을 때마다 DB 조회 — `/system`·`/agent/structure` 5초 폴링마다 5~10회 | 쓰기 시 갱신되는 메모리 캐시(프로세스 하나, 시장별 kv 따로) | /system·/agent/structure SQL 수 |
| B4 | API 응답 압축 없음 (`/events` 141KB, `/chat` 105KB) | gzip 응답(작은 응답 제외) | 전송 바이트 |
| B5 | `/llm/costs` 가 사용 기록 × 모델 전부를 파이썬으로 계산(90ms, 기록에 비례) | 마지막 사용 기록 id·날짜·일정 기준 결과 캐시 | /llm/costs 시간 |
| B6 | 장이 닫혀도 1분 점검·10분 갱신이 24시간 돌고 10분마다 자산 스냅샷 기록 | 장 운영 시간 근처에만 실행(장전~장 마감 후), 스냅샷은 장중+마감 1회 | 장외 분당 SQL·토스 호출 수, equity_snapshots 하루 행 수 |
| B7 | 계속 쌓이기만 하는 데이터: 만료된 API 캐시(DART), 모의계좌 JSON 에 모든 주문 이력(주문마다 전체 다시 쓰기) | 만료 캐시 정리(하루 1회), 모의계좌 JSON 은 끝난 주문을 최근 N건만 유지(주문 표에는 전부 남음) | DB 크기, 주문 1건 처리 시 쓰는 바이트 |
| B8 | 필터·정렬에 쓰는데 인덱스가 없는 컬럼 13개, 중복 인덱스 1개 | 마이그레이션 0004: 인덱스 추가·중복 제거 | EXPLAIN 버퍼 수(기억 조회 1,251→) |
| B9 | `/overview` 가 두 시장을 차례로 조회 | 동시에 조회 | /overview 시간 |
| B10 | `routes.py` 1,647줄 한 파일, 복잡도 10 초과 함수 15개(최대 28) | 주제별 라우터 모듈로 나누기(경로·응답 그대로), 복잡한 함수는 표·작은 함수로 분해 | 파일 줄 수, C901 개수·최대값 |

하지 않는 것: 판단 세션 대화 기록을 줄여 LLM 에 보내는 양을 바꾸는 일(판단 품질에 영향), 리스크 규칙, 사용자 기록 삭제.

## 화면 (기준: docs/perf/baseline-frontend.md)

### 1단계 — 속도·버벅임

| # | 문제 (측정값) | 고치는 방법 | 확인 지표 |
|---|---|---|---|
| F1 | nginx 가 JS 를 압축 없이(611KB) 보내고 캐시 헤더 없음 → Fast 3G 첫 화면 6.5초 | gzip(정적 파일·JSON), `/assets/` 는 파일명 해시라 1년 `immutable` 캐시, index.html·sw.js 는 no-cache 유지 | 전송 바이트, Fast 3G FCP/LCP |
| F2 | 코드 분할 없음: 12개 화면 + 차트 라이브러리가 한 파일(gzip 193KB) | 화면별 지연 로딩(React.lazy), 차트 모듈 분리, vendor 청크 | 첫 화면 JS gzip |
| F3 | 화면마다 채팅·진행 상황을 시장별로 두 번, 이벤트 로그를 두 시장 모두 받음(~360KB) | 한 번만 받고, 채팅·로그는 보이는 화면에서만 | 첫 로딩 API 바이트·요청 수 |
| F4 | 가만히 있어도 대시보드 분당 8,354회 다시 그림(채팅 메시지 4,000) | 메시지·카드 컴포넌트 memo, 폴링 위젯 분리 | 분당 렌더 수 |
| F5 | 차트가 새 배열 때문에 분당 126회 새로 만들어짐 | 차트는 한 번 만들고 데이터만 갱신 | 분당 캔버스 생성 수 |
| F6 | 이벤트 값 하나를 모두가 구독 → 어느 시장의 메시지든 머리말·메뉴·화면 전체가 다시 그려짐 | 컨텍스트 분리/선택적 구독 | 분당 렌더 수 |
| F7 | 웹소켓이 있는데도 5초 폴링, 탭 전환마다 다시 받음 | 연결돼 있으면 느린 폴링(30~60초), 끊기면 빠르게 | 분당 API 요청 수(대기 27→) |
| F8 | 이벤트 하나에 모든 쿼리를 다시 받음(`invalidateQueries()` 9곳) | 이벤트 종류 → 필요한 쿼리 키만 | 이벤트당 요청 수 |
| F9 | 데이터 도착 때 화면 밀림(CLS: 포트폴리오 0.57, 종목 0.41, 설정 0.30, 모든 화면 머리말 0.10) | 자리 미리 잡기·스켈레톤 | CLS |
| F10 | 긴 목록(로그·채팅)을 전부 그리고 메시지 올 때마다 최대 800개 다시 정렬 | 정렬된 상태 유지(삽입), `content-visibility` 또는 창 렌더링 | 메시지당 처리 시간 |

### 2단계 — 앱 같은 화면·설정 분리
- 설정을 주제별로 분리(LLM / 판단 일정 / 리스크·주문 / 매니저·메모 / 알림 / 계좌·데이터): 데스크톱은 왼쪽 목록 + 오른쪽 내용, 휴대폰은 목록 → 눌러 들어가는 화면.
- 로딩 스켈레톤, 화면 전환 효과, 숫자 바뀔 때 부드러운 변화·오르내림 깜빡임, 토스트 알림, 연결 끊김 띠, 휴대폰 당겨서 새로고침·아래에서 올라오는 시트, 데스크톱 ⌘K 빠른 이동, 시장 전환 애니메이션 짧게.
- 같은 기능·같은 API, 기존 테스트 유지.

## 서버 구현 메모 (2026-11-05)

측정: `docs/perf/raw/after/{inprocess,code}.json` (in-process 벤치·코드 지표, 같은 시드·시각). 실서버 API·DB 측정은 배포 뒤에 한다.

| # | 바꾼 것 | 파일 | 전 → 후 (in-process) |
|---|---|---|---|
| B1 | `PriceStore` 종목 이름 메모리 캐시(`names()`/`name_of()`, `upsert` 때 그 종목만·기록 정리 때 전부 무효화, 일봉 없는 종목은 캐시 안 함) + 마지막 일봉 일괄 조회(`last_bars()`). `/orders`·`/fills`·`/watchlist` 는 이름·일봉을 한 번에. `BrokerInterface.account()` 로 잔고·보유 종목을 한 번에 계산(`/portfolio`·리스크 확인의 이중 계산 제거, 실거래는 토스 잔고 조회 1번) | market/store.py, brokers/{base,paper,toss_live}.py, engine/engine.py, api/routes/{orders,risk}.py | `/orders` SQL 302→1, `/fills` 202→1, `/portfolio` 21→3, `/overview` 52→8, `/watchlist` 21→2 |
| B2 | 매매 원장 계산 캐시: DB·기준 시각별, 키 = 체결 (건수, 마지막 id). 뒤에 붙은 체결은 이어서 계산, 삭제·앞선 시각 체결이면 처음부터. 이름은 결과 만들 때 붙임(같은 합산 순서) | engine/trades.py | 판단 세션 SQL 1,163→123 (이름 N+1 1,047건 제거 포함) |
| B3 | kv(`SettingsStore`) write-through 캐시: DB(Database 인스턴스)마다 하나 — 국장·미장 따로, 같은 DB 의 저장소끼리 공유. 처음 한 번 표 전체를 읽고, `set()` 은 커밋 뒤 캐시 갱신, 읽기는 깊은 복사 | engine/settings_store.py | `/system` 6→1, `/agent/structure` 16→4, `/llm/profile` 5→0, `/markets` 4→0, 스케줄 확인 2→0 |
| B4 | `GZipMiddleware(minimum_size=1024, compresslevel=4)` — http 만(WebSocket 영향 없음, event-stream 제외) | main.py | 전송 바이트 18~51% 로 (예: events 377KB→105KB, chat 164KB→46KB, reports 754KB→207KB) |
| B5 | `/llm/costs` 결과 캐시: 키 = (오늘, 기간, 하루 세션 수, 남은 거래일, 사용 기록 건수·마지막 id). 행마다 필드 dict 를 한 번만 만들고 계산 순서는 그대로 | agent/costs.py | 91.6ms→1.3ms (캐시 적중), SQL 3→1 |
| B6 | 1분 감시는 장중 감시 구간(시작 1분 전~마감 5분 뒤) 밖이면 토스 연결 확인도 하지 않음. 국장 10분 갱신도 미장처럼 장 시작 1시간 전~마감 30분 뒤에만. 자산 스냅샷: 장중(1분 감시가 10분마다·체결 직후, 갱신·판단 뒤에는 최근 10분 안에 기록이 없을 때만) + 마감 뒤 하루 한 번 | engine/engine.py, engine/scheduler.py | 장외 분당 SQL·토스 호출 0 / 0 (하루 시뮬레이션), 국장 자산 스냅샷 하루 47행 (기존 실서버 193~212) |
| B7 | 하루 한 번 정리(국장 04:40, 미장 12:40 KST) + 기동 때 한 번: api_cache 는 수명이 지난 것만(DART 빈 결과 1일 경과, 다시 읽히지 않는 연도(올해−3년 미만)의 재무제표 키). 모의계좌 JSON 은 미체결 + 최근 끝난 주문 200건만(백테스트 브로커는 정리 안 함). 정리 실패는 기동·스케줄을 막지 않음 | engine/maintenance.py, brokers/paper.py, container.py, main.py | 모의계좌 JSON 366KB→73KB (주문 1,000건 기준) |
| B8 | 마이그레이션 0004: 인덱스 12개 추가(agent_runs started_at·(status,id), orders created_at·run_id·broker_order_id, fills filled_at·symbol, agent_decisions created_at·order_id, chat_messages (role,id), memory_cards (symbol,kind), api_cache fetched_at), `ix_daily_prices_symbol` 삭제. 모델 선언도 같게(autogenerate 차이 0) | db/migrations/versions/0004_perf_indexes.py, db/models.py | SQLite 계획: 기억 조회가 새 인덱스 사용. Postgres 는 배포 뒤 EXPLAIN 으로 확인 |
| B9 | `/overview` 환율·시장별 요약을 `asyncio.gather` 로 동시에 | api/routes/markets.py | 7.5ms→2.4ms (SQL 감소 포함) |
| B10 | `routes.py` → `app/api/routes/` 패키지 14개 모듈(경로·메서드·상태 코드·이름·응답 모델 표 비교 동일). `activity.result_line`/`preview` 는 도구별 표, `costs.summary` 는 작은 함수, `create_app`·`PaperBroker.place_order`·`events_ws` 분해 | api/routes/*, agent/activity.py, agent/costs.py, main.py, brokers/paper.py, api/ws.py | 가장 큰 라우트 모듈 275줄, C901>10: 15→9, 최대 28→16 |

- 응답 JSON 은 바이트 단위로 같다(in-process 벤치 bytes 동일. `us_equity` 만 기동 시 스냅샷 규칙(B6) 때문에 최근 1,000점 구간이 1점 밀림).
- 판단 세션의 LLM 입력 크기·토스 호출 수는 그대로다(하지 않는 것).
- 남은 C901: risk.rules.check(16, 리스크 규칙은 손대지 않음), run_backtest(15), toss_live.place_order(14, 실거래 주문 경로), runner(13·12), llm(11·11), rule_agent(11), tools._us_market_overview(11).

## 화면 1단계 구현 메모 (2026-11-05)

측정: `docs/perf/raw/after/{fe_bundle,fe_code}.json` (오프라인 부분만 — 번들·코드 지표). 브라우저 측정(첫 화면·대기·렌더 수·CLS)은 배포 뒤에 한다. `fe_bundle.json` 의 `served` 는 아직 배포 전 nginx(옛 이미지)를 잰 값이라 압축·캐시 헤더가 없다고 나온다 — 새 설정은 임시 nginx:1.27-alpine 컨테이너로 확인했다(아래 F1).

| # | 바꾼 것 | 파일 | 전 → 후 |
|---|---|---|---|
| F1 | gzip(JS·CSS·JSON·SVG·매니페스트, 1KB 이상, `gzip_proxied any`, `Vary`). `/assets/` 는 `public, max-age=31536000, immutable` + 없는 파일은 index.html 대신 404(옛 화면이 지난 배포의 조각을 찾을 때 HTML 이 1년짜리로 캐시되지 않게). index.html·`/`·sw.js·매니페스트는 `no-cache`. /api·/ws 프록시 그대로 | frontend/nginx.conf | vendor JS 전송 610,904 B(무압축) → 82,512 B(gzip). 백엔드가 이미 압축한 /api 응답은 그대로 통과(이중 압축 없음: /api/chat 23,945 B 직접 = nginx 경유) |
| F2 | 화면 12개 React.lazy(`lazyWithPreload`: 미리 받기 + 지난 배포 조각 404 면 한 번 새로고침, 그래도 실패면 `RouteErrorBoundary` 안내). Suspense 는 본문 자리에 두어 라우터 전환(startTransition) 중에는 지금 화면을 유지. 지금 주소의 화면 조각은 main.tsx 에서 바로 받기 시작, 메뉴·시장 전환에 손이 닿으면 미리 받기, 첫 화면 뒤 쉬는 동안 나머지 받기(데이터 절약·2G 제외). 차트(lightweight-charts)는 대시보드·종목·백테스트에서만(`LazyCharts`, 같은 크기 빈 자리). `manualChunks`: vendor-react · vendor-charts | App.tsx, routes.ts, lazyPage.ts, main.tsx, components/{LazyCharts.tsx,chartsLoader.ts,RouteErrorBoundary.tsx,Layout.tsx,MarketSwitch.tsx}, vite.config.ts | 첫 화면 JS gzip 193,131 → 97,715 B (raw 610,947 → 300,702)(+ 화면 조각 1~4KB), JS 조각 1 → 23개(화면 13개는 동적), 전체 JS gzip 193,131 → 212,909 B, 첫 화면 CSS gzip 12,953 → 10,725 B |
| F3·F6 | 실시간 저장소(`live/store.ts`): 시장마다 웹소켓 하나(그대로). 채팅 기록·진행 상태는 그것을 보여 주는 화면(대시보드·룸·한눈에 보기)이 있을 때만, 웹소켓이 열린 뒤 연결마다 한 번(300ms 안에 안 열리면 먼저 받고 열린 뒤 한 번 더). 감사 로그는 로그 화면에서만. 끊겼다 다시 열리면 보여 주는 묶음만 다시 맞춤. 화면은 `useSyncExternalStore` 로 조각만 구독(`useConnected`·`useChat`·`useChatMessages`·`useProgress`·`useAudit`) — 머리말·메뉴는 연결 상태만 | events.tsx, live/{store,hooks}.ts, pages/{Dashboard,TradingRoom,Overview,Logs}.tsx, components/Layout.tsx | 채팅·진행 GET: 시장마다 2번×두 시장(모든 화면) → 보여 주는 시장만 1번. 감사 로그 GET: 두 시장(모든 화면) → /logs 에서 지금 시장 1번. 격리 백엔드에서 본 첫 로딩 API 수: /room 7, /portfolio 8, /settings 14, 종목 7, 한눈에 보기 9, 대시보드 9 (기준선은 실서버에서 14~24) |
| F7 | 웹소켓이 연결돼 있으면 폴링을 늦춘다(`slowWhenLive`: 5초→30초, 10초→60초, 30초→60초): /api/system(보는 시장 30초·안 보는 시장 60초), /api/portfolio 30초, /api/agent/structure 30초. 끊기면 원래 주기. 연결된 시장의 캐시는 기본 staleTime 30초(직접 정한 staleTime 은 그대로) — 화면을 옮길 때마다 다시 받지 않는다 | hooks.ts, live/hooks.ts, events.tsx, pages/Agent.tsx | 대기 중 한눈에 보기 /api/system 두 시장 24회/분 → 4회/분(예상) |
| F8 | 이벤트 → 캐시 이름 표(`live/invalidation.ts`): 체결·주문 접수·결정·세션 정리·마감 리포트·세션 시작/실패·그 밖의 시스템 안내·내 메시지·상태(agent_running·engine_status·equity) 별로 그 시장 캐시만(+ 한눈에 보기·시장 목록). 매니저 생각·도구·리스크 안내·진행·입력 중·감사 로그는 다시 받지 않는다. 600ms 묶음 그대로. 화면의 변경(킬 스위치·재개·판단 실행·일시정지·주문 취소·매니저/리스크/전략 저장)도 바뀐 것만 | live/invalidation.ts, components/KillSwitch.tsx, pages/{Dashboard,TradingRoom,Portfolio,Settings}.tsx | `invalidateQueries()` 전체 무효화 9곳 → 0곳 |
| F4·F10 | memo: ChatMessage(메시지·매니저 이름이 같으면)·ToolChip·DecisionCard·SessionBlock·TypingIndicator·ChatFeed·Kpi·HoldingsTable·표의 줄(주문·체결·결정·로그). 세션 묶음·요약은 내용이 같으면 지난 객체 재사용(`stabilizeGroups`·`SessionViewCache`), 진행 상태는 도는 세션 블록에만. 대시보드는 지표·KPI·차트·매니저·보유·채팅을 각자 구독하는 작은 컴포넌트로. 채팅 목록은 id 정렬을 유지하며 끼워 넣기(이진 탐색, 메시지마다 정렬 없음), 받아 온 기록과 합칠 때 있던 객체 유지. 채팅의 맨 아래 12줄을 뺀 지난 줄은 `content-visibility: auto` | components/chat/{ChatFeed,ChatMessage,SessionBlock}.tsx, components/chat/sessions.ts, live/merge.ts, pages/{Dashboard,Portfolio,Logs,TradingRoom}.tsx, components/HoldingsTable.tsx, styles.css | React.memo 0 → 17 |
| F5 | 차트는 처음(통화가 바뀔 때만 다시) 한 번 만들고, 데이터가 실제로 바뀔 때만 `setData`(새 배열이어도 내용이 같으면 그대로). 대시보드는 자산 추이 점 배열을 `useMemo` | components/charts.tsx, pages/Dashboard.tsx | 대기 중 차트 새로 만들기(기준선 126회/분) → 0 예상 |
| F9 | 상단 바: 상태를 받기 전에도 칩·엔진 상태·킬 스위치 자리(보이지 않음)를 지난번 너비로(보기별 `topbar.widths`, 글자는 저장하지 않음) 잡는다. 포트폴리오: 표 다섯 개를 다 받을 때까지 자리 표시 한 번(‘없어요’ 깜빡임 없음), 대시보드 보유 종목도 받는 중 자리 표시. 종목: 차트 자리·수급 카드 자리. 설정: 위쪽 카드(LLM·알림·판단 주기)를 다 받을 때까지 자리 표시 한 번(같은 쿼리 정의 공유: `settingsQueries.ts`) | components/{Layout,Skeleton,HoldingsTable}.tsx, pages/{Portfolio,Stock,Settings,ScheduleSettings,settingsQueries}.ts(x), styles.css | 격리 백엔드·1440×900 에서 CLS 0(대시보드·포트폴리오·설정·종목·한눈에 보기). 실서버 CLS 는 배포 뒤 측정 |

- 테스트: 화면 205 → 229(새 파일 `live.test.tsx` 14개 — 합치기·이벤트→캐시 표·필요할 때만 한 번 받기·재접속·폴링 주기·staleTime, `render.test.tsx` 10개 — 메모된 줄·세션 블록·차트 재사용·화면 조각·지난 배포 조각 안내·상단 바/포트폴리오/종목 자리). e2e 6개 그대로 통과. 바꾼 기존 테스트 1개: `market.test.tsx` "미장이 켜져 있으면 두 시장 웹소켓을 함께 열고 각자 대화를 받는다" — 대화를 보여 주는 화면 없이도 두 시장 채팅을 받는다는 것을 확인하던 부분을, 두 시장 대화를 구독하는 화면을 두고 확인하도록(F3 로 바뀐 내부 동작) + 미장 웹소켓 메시지가 미장에만 들어가는지 확인 추가.
- 코드: 앱 9,668 → 11,032줄(실시간 저장소·화면 조각·자리 표시 모듈 추가), eslint 경고 4 → 3, `invalidateQueries` 15 → 6.
- 서비스 워커(public/sw.js)는 푸시 전용으로 fetch 를 가로채지 않아(캐시 없음) 새 조각 이름과 충돌하지 않는다. 열려 있던 옛 화면은 지난 배포 조각이 404 이면 한 번 새로고침한다(`vite:preloadError` 포함).
- 하지 않은 것(2단계로): 설정 화면 나누기, 스켈레톤 체계·화면 전환 효과·토스트·연결 끊김 띠·당겨서 새로고침·바텀 시트·⌘K. 진행 이벤트의 프레임 단위 묶기, 긴 목록 창 렌더링(새 의존성 없이 content-visibility·memo 로 대신), 로그 표의 content-visibility(표 줄에는 적용되지 않음), 한눈에 보기 30초 폴링(이미 범위 안), 첫 페이지 크기 줄이기(`limit=50` + 더 보기 — 화면 동작이 바뀜).

## 화면 2단계 구현 메모 (2026-11-05)

측정: `docs/perf/raw/after/{fe_bundle,fe_code}.json` 을 2단계까지 넣어 다시 쟀다(오프라인 — 번들·코드 지표). 브라우저 측정은 배포 뒤에 한다. 1단계 성질(화면별 지연 로딩, 실시간 저장소 조각 구독, memo 된 줄, 이벤트별 캐시 무효화, 연결돼 있으면 느린 폴링, CLS 0 자리 잡기, nginx 캐시)은 그대로 두었다. 새 의존성 없음.

| # | 바꾼 것 | 파일 | 전 → 후 |
|---|---|---|---|
| S1 | 설정을 주제 6개로 나눔: LLM(무료/유료·유료 모델·OpenRouter·테스트용 LLM·미장 매니저 LLM) · 알림 · 판단 일정 · 리스크·주문 · 매니저(이름·운용 방침·메모 카드 링크·규칙 전략) · 계좌·데이터(모의계좌·데이터 연결 상태·관심 종목·LLM 비용 링크). 주소 `/settings/:section?` — `/settings` 는 목록(넓은 화면은 목록 + LLM 모델, 예전 링크 그대로), 모르는 주제는 `/settings` 로. 넓은 화면은 왼쪽 목록(위에 붙음) + 오른쪽 내용, 휴대폰은 목록(지금 값 한 줄씩: LLM 칩·판단 주기·매니저 이름·시세 연결) → 밀려 들어오는 주제 화면 + 위에 붙는 '‹ 설정'(목록에서 왔으면 브라우저 뒤로, 주소로 바로 왔으면 목록으로). 시장마다 다른 주제는 '🇺🇸 미장에만 적용돼요' 표시. 주제마다 따로 받는 조각(메뉴에 손이 닿으면·쉬는 동안 미리), 주제마다 다 받을 때까지 자리 표시 한 번. 상단 바 LLM 칩 → `/settings/llm` | pages/Settings.tsx, pages/settings/{lazy,meta}.ts, pages/settings/{Llm,Alerts,Schedule,Risk,Manager,Account}Section.tsx, SectionGate.tsx, settings.css, utils/risk.ts, routes.ts, App.tsx, Layout.tsx | Settings 조각 gzip 12,040 B 한 덩어리 → 목록 2,398 B + 주제 1.8~5.7 KB. 격리 백엔드에서 본 넓은 화면 `/settings` 첫 로딩 API 14 → 7개(인증·시장 상태 2·시장 목록 + LLM 주제 3) |
| S2 | 자리 표시 체계: 받는 동안 '없어요'·'—' 대신 모양 자리. 채팅(트레이딩 룸·대시보드)은 기록을 받기 전(웹소켓이 열리기 전 300ms 포함) 말풍선 자리 — '아직 조용하네요'가 잠깐 보이던 것 없앰(`useChat().ready`). 대시보드 KPI 숫자·매니저 상태, 세션 목록, 종목 화면 재무·결정 이력, 이벤트 로그(감사 로그 `auditReady`), `<Loading>` 은 넓은 화면에서도 카드 모양 | live/{store,hooks}.ts, components/{chat/ChatFeed,Skeleton,Loading}.tsx, pages/{Dashboard,TradingRoom,Stock,Logs}.tsx, styles.css | 채팅 빈 안내 깜빡임 0 |
| S3 | 토스트(`ui/toast.ts` + `<Toaster>`, 앱에 하나): 저장·전환·정지·재개·메모·일정·관심 종목·주문 취소·판단 실행·알림 설정의 '됐어요'를 아래에 잠깐(3.8초, 같은 글은 쌓지 않음, 3개까지, 누르면 닫힘). 성공·안내는 `aria-live=polite`, 실패는 `assertive`. 예전 그 자리 성공 글(✓)은 토스트로 옮기고, 오류 자세한 내용은 그 자리에 그대로. 그 자리에 오류를 보일 곳이 없던 버튼(재개·일시정지·주문 취소·관심 종목 추가/삭제)의 실패만 오류 토스트 | ui/toast.ts, components/{Toaster,KillSwitch}.tsx, pages/settings/*, pages/{ScheduleSettings,Memo,Dashboard,TradingRoom,Portfolio,Stock}.tsx | — |
| S4 | 실시간 연결 끊김 띠: 보고 있는 시장(전체 보기면 켜진 시장 모두)의 웹소켓이 1.5초 넘게 끊겨 있으면 상단 바 아래에 겹쳐(본문을 밀지 않게) "실시간 연결이 끊겼어요 · 다시 연결 중…", 일부 시장만이면 '🇺🇸 미장 …'. 다시 연결되면 바로 사라짐 | components/ConnectionBanner.tsx, Layout.tsx | — |
| S5 | 살아 있는 숫자(`AnimatedNumber`): 총자산·KPI·한눈에 보기 총자산·시장 총자산·보유 종목 현재가/평가금액/수익률·주요 지표·빠른 보기 가격이 바뀌면 0.42초 굴러가고 오르면 빨강·내리면 파랑으로 깜빡(한국 시장 관례 — 등락 글자색과 같다). 처음 값·같은 값은 굴리지 않음, 중간 글자는 같은 글자 노드에 직접 써서 React 다시 그리기 0, '동작 줄이기'면 바로 바꾸고 깜빡이지 않음. 숫자는 `tabular-nums`(KST 시계 칩 포함 — 매초 폭이 흔들리지 않음) | components/AnimatedNumber.tsx, pages/{Dashboard,Overview}.tsx, components/{HoldingsTable,IndicatorStrip}.tsx, utils/format.ts(`MONEY_FMT`·`PRICE_FMT`·`pctFmt`) | — |
| S6 | 휴대폰 앱 느낌: 당겨서 새로고침(대시보드·한눈에 보기·룸·자산·구조·로그·종목 — 보이는 쿼리 `refetchQueries({type:"active"})` + 실시간 저장소 채팅/감사 로그 `resync()`, 안쪽 목록이 위로 스크롤돼 있으면 시작 안 함, 문턱을 넘으면 안드로이드는 짧은 진동). 아래 시트: 확인 창(킬 스위치·유료 전환·유료 모델·메모 삭제 등 `Modal` 전부)이 휴대폰에서는 아래 시트(끌어내려 닫기·바깥·Esc, 초점 가둠·되돌림, 버튼이 엄지 쪽), 보유·관심 종목·한눈에 보기 보유 줄을 누르면 종목 빠른 보기(가격·전일 대비·최근 60거래일 SVG 흐름·내 보유·종목 상세 링크, 종목 화면과 같은 일봉 캐시). safe-area(시트·토스트·빠른 이동), 눌림 효과, 시장 전환 애니메이션 0.22s → 0.18s(본문 0.16 → 0.14s), 넓은 화면 화면 전환은 투명도만 0.18s(첫 화면은 효과 없음 — 첫 표시를 늦추지 않게) | ui/{usePullToRefresh,pullToRefresh,overlays}.ts, components/{Sheet,Modal,StockSheet,Overlays,HoldingsTable,Layout}.tsx, Sheet.css, pages/Overview.tsx, styles.css | 손가락 처리(0.9 KB)·시트(1.2 KB + CSS 1.0 KB)·빠른 보기(1.8 KB)는 처음 쓸 때 받는 조각 |
| S7 | ⌘K / Ctrl+K 빠른 이동(넓은 화면, 휴대폰은 상단 바 🔍): 화면·설정 주제로 이동, 시장 바꾸기(시장 전환 버튼과 같은 규칙), 보유 종목(이미 받은 계좌에서), 종목 검색 `/api/market/search?q=`(지금 시장 — api client 의 시장 주소 바꾸기로 미장은 `/api/us/…`, 메모 카드 종목 고르기와 같은 캐시 키). ↑/↓·Home/End·Enter·Esc, 한글 조합 중 키 무시, combobox/listbox ARIA. 처음 열 때 받는 조각 | components/{CommandPalette,Overlays}.tsx, CommandPalette.css, ui/{overlays,useDebounced}.ts | 3.0 KB + CSS 0.9 KB (첫 화면에는 단축키·열기 버튼만) |
| S8 | 다크 모드(새 부품 모두 색 변수, 토스트·시트 그림자 다크 값), 키보드 초점 테두리(`:focus-visible` — 마우스·손가락에는 안 그림), '동작 줄이기'(깜빡임·굴림·시트·토스트·화면 전환·자리 표시 반짝임 끔) | styles.css, *.css | — |
| S9 | 그 밖: 상대 시각 "3분 전"(마우스를 올리면 정확한 KST — 화면 전체가 30초 시계 하나를 같이 씀: 세션 목록·한눈에 보기 소식·알림 기기 마지막 전송), 비어 있을 때 다음 할 일(보유 없음 → 트레이딩 룸, 관심 종목 없음 → 🔍 종목 찾기, 로그 유형 필터 결과 없음 → 모든 유형 보기), 이벤트 로그 표 머리줄 고정(넓은 화면), 종목 화면 '⭐ 관심종목 추가'에 결과 알림(예전에는 아무 표시 없음), 계좌·데이터의 연결 상태 목록(토스 시세·시세 원천·실시간 이벤트·재무·뉴스·LLM) | components/RelTime.tsx, utils/format.ts(`fmtAgo`·`fmtFull`), pages/{TradingRoom,Overview,Logs,Stock}.tsx, pages/settings/{Alerts,Account}Section.tsx | — |

- 번들: 첫 화면 JS gzip 97,715 → 100,441 B(+2,726: 토스트 저장소·그리기, 연결 끊김 띠, ⌘K 단축키·호스트, 당겨서 새로고침 훅, 설정 주제 조각 목록, 시트 지연 로딩 껍데기), 첫 화면 CSS gzip 10,725 → 12,387 B(시트·빠른 이동·설정 CSS 는 각자 조각으로). 전체 JS gzip 212,909 → 235,326 B, JS 조각 23 → 39개.
- 테스트: 화면 229 → 262(새 파일 `settingsHub.test.tsx` 8개 — 주소·미리 받기·넓은 화면 목록/내용·모르는 주제·시장 표시·휴대폰 목록 → 주제 → 뒤로, `appFeel.test.tsx` 25개 — 받는 동안 자리 표시(채팅·로그), 토스트(성공·실패·닫기·저절로), 연결 끊김 띠, ⌘K(키보드로 열기·고르기·Esc, 국장/미장 종목 검색, 시장 바꾸기), 아래 시트(확인 창·빠른 보기 열고 닫기, 넓은 화면은 그대로), 당겨서 새로고침이 다시 받기, 숫자 굴림·동작 줄이기, 상대 시각). e2e 6개 그대로 통과. 바꾼 기존 테스트: 6개 파일의 import 경로만(설정 분리 — `pages/settings/*Section`, `utils/risk`), `llmProfile.test.tsx` LLM 칩 주소 `/settings` → `/settings/llm`(의도한 변경), 공용 도구 `__tests__/utils.tsx` 가 앱처럼 `<Toaster />` 를 함께 그림(성공 글이 토스트로 옮겨도 기존 확인 그대로 통과)·`test/setup.ts` 가 테스트 사이 토스트 비움. 지우거나 건너뛴 테스트 없음.
- 코드: 앱 11,032 → 13,321줄(76개 파일), eslint 경고 3 → 2(`validateRisk` 를 utils 로), React.memo 17 → 20.
- 측정 도구: `perf/fe_lib.mjs` 의 `settings_kr` '보임' 기준을 바꿈 — 예전에는 한 화면의 리스크 폼이 채워지면, 이제 넓은 화면은 LLM 모델 내용, 휴대폰은 목록(시세 연결 한 줄)이 보이면. 키는 그대로지만 같은 화면을 재는 것이 아니므로 비교할 때 유의.
- 하지 않은 것: 로그 화면 필터 칩·검색(지금 `<select>` 유지), 낙관적 갱신 확대(관심 종목·전략 토글), 룸 세션 목록·메모 카드 편집을 시트로, 첫 페이지 크기 줄이기, 테마 수동 전환. 휴대폰 확인 창은 시트를 처음 열 때 조각을 받는다(킬 스위치에 손가락이 닿을 때·첫 화면 뒤 쉬는 동안 미리 받음).
