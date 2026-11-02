# 아키텍처

## 1. 구성도

```mermaid
flowchart LR
  subgraph UI["프론트엔드 (React + Vite)"]
    Dash[대시보드]
    Room[💬 트레이딩 룸]
    Pages[포트폴리오·종목·설정·백테스트·로그]
  end

  subgraph API["백엔드 (FastAPI)"]
    REST[REST /api/*]
    WS[WebSocket /ws/events]
    Sched[Scheduler<br/>real: cron · sim: 가속 시계]
    Engine[TradingEngine<br/>동기화·체결확정·손절익절·킬스위치]
    Agent[AgentRunner<br/>LLM 도구루프 / JSON / 규칙]
    Tools[ToolBox<br/>포트폴리오·스크리닝·시세지표·재무·공시·뉴스]
    Risk[RiskManager<br/>하드 리밋]
    OM[OrderManager<br/>멱등키·상태머신·감사로그]
    Broker[PaperBroker<br/>가상 계좌]
    Hub[EventHub<br/>감사로그·채팅·상태]
    Store[PriceStore]
    Research[ResearchService]
  end

  subgraph EXT["외부 (조회 전용, 허용목록)"]
    TOSS[(토스증권 Open API<br/>현재가·일봉·종목·랭킹·캘린더)]
    PD[(공공데이터<br/>주식시세 T+1 · 대체)]
    DART[(OpenDART<br/>재무·공시)]
    NAVER[(네이버 뉴스)]
    LLM[(OpenAI 호환 LLM)]
  end

  DB[(SQLite / PostgreSQL)]

  UI -- HTTP --> REST
  UI <-- 실시간 --> WS
  Sched --> Engine
  REST --> Engine
  Engine --> Agent
  Agent --> Tools
  Agent -- 결정 --> OM
  Engine -- 손절/익절 --> OM
  OM --> Risk
  OM --> Broker
  Tools --> Store
  Tools --> Research
  Store --> TOSS
  Store -.토스 키 없을 때.-> PD
  Broker -- 실시간 현재가 --> TOSS
  Tools -- 랭킹 스크리닝 --> TOSS
  Research --> DART
  Research --> NAVER
  Agent --> LLM
  Broker --> Store
  OM --> Hub
  Agent --> Hub
  Hub --> WS
  Hub --> DB
  Broker --> DB
  Store --> DB
```

- 모든 외부 호출은 `SafeHttpClient`(허용목록·토큰버킷·지수 백오프)를 거칩니다. 주문·계좌 경로는 토스 포함 모든 호스트에서 무조건 차단되고, 토스는 조회 7개 경로만 열려 있습니다.
- 키가 없으면 `MockWorld`(가상 종목 20개의 일봉·뉴스·공시·재무)가 공공데이터·DART·네이버를 대신합니다.

## 2. 디렉터리

```
backend/app/
  core/        설정(TRADING_MODE=paper 강제), 마스킹 로깅, 시계, KRX 캘린더, 호가단위, 외부호출 허용목록
  market/      일봉 저장소, 공공데이터 어댑터, MockWorld(가상 시장)
  research/    OpenDART, 네이버 뉴스, ResearchService 퍼사드
  agent/       LLM 클라이언트, 도구, 프롬프트, 세션 실행기, 규칙 기반 대체 에이전트
  brokers/     BrokerInterface, PaperBroker
  risk/        RiskConfig / RiskManager
  strategies/  지표, 규칙 전략(MA 크로스, RSI)
  engine/      이벤트 허브, 주문 상태머신, 주문 관리자, 엔진, 스케줄러
  backtest/    백테스터
  reports/     일일 리포트
  api/         REST·WS 라우터, 인증
  db/          모델(UTCDateTime), 세션, Alembic 마이그레이션
frontend/src/
  components/chat/  트레이딩 룸 (말풍선·도구칩·결정카드·입력중)
  pages/            대시보드·트레이딩룸·포트폴리오·종목·설정·백테스트·로그·로그인
```

## 3. 에이전트 세션 흐름

```mermaid
sequenceDiagram
  autonumber
  participant S as Scheduler / 사용자
  participant E as TradingEngine
  participant A as AgentRunner
  participant L as LLM
  participant T as ToolBox
  participant O as OrderManager
  participant R as RiskManager
  participant B as PaperBroker
  participant H as EventHub → UI

  S->>E: morning / afternoon / manual
  E->>E: sync_data (공개된 일봉) → settle → 손절/익절
  E->>A: run(trigger)
  A->>H: 🔔 세션 시작
  loop 최대 LLM_MAX_STEPS
    A->>L: messages + tools
    L-->>A: "가온전자 재무 볼게요 🧾" + tool_calls
    A->>H: 💬 생각 / 🧾 도구 칩
    A->>T: get_financials / search_news / ...
    T-->>A: 결과(JSON, 데이터로만 취급)
  end
  L-->>A: submit_decisions(BUY/SELL/HOLD, 확신도, 근거)
  A->>A: 보유종목 누락 → HOLD 보정, 매도 먼저 정렬
  loop 결정마다
    A->>H: 🟢/🔴/⚪ 결정 카드
    A->>O: OrderIntent
    O->>R: check()
    alt 차단
      R-->>O: 사유
      O->>H: 🛡️ "잠깐! 막을게요"
    else 허용(필요시 수량 축소)
      O->>B: place_order(client_order_id)
      B-->>O: SUBMITTED (시가/종가 체결 예정)
      O->>H: 💸 "접수 완료!"
    end
  end
  A->>H: 🏁 세션 정리
```

도구 호출을 지원하지 않는 LLM 이면 자료를 미리 모아 한 번에 전달하고 JSON 결정을 받습니다(`LLM_TOOL_MODE=auto` → json 자동 전환).
LLM 이 없으면 같은 도구로 규칙 기반 결정을 냅니다.

## 4. 주문 상태 머신

```mermaid
stateDiagram-v2
  [*] --> PENDING: OrderManager 생성 (리스크 통과 후)
  PENDING --> SUBMITTED: 브로커 접수
  PENDING --> REJECTED: 브로커 거부 (시간외·현금부족·수량부족)
  SUBMITTED --> FILLED: 공식 일봉 공개 → 전량 체결
  SUBMITTED --> PARTIALLY_FILLED: 거래량 상한
  PARTIALLY_FILLED --> CANCELED: 당일 미체결분 취소
  PARTIALLY_FILLED --> FILLED
  SUBMITTED --> CANCELED: 사용자 취소 · 킬 스위치 · 지정가 미도달
  FILLED --> [*]
  CANCELED --> [*]
  REJECTED --> [*]
```

허용되지 않은 전이는 `InvalidTransitionError` 입니다(`engine/state_machine.py`). 같은 `client_order_id` 로 다시 접수하면 기존 결과를 돌려줍니다(멱등).

## 5. 데이터 시점 규칙 (미래 정보 차단)

```mermaid
gantt
  dateFormat  HH:mm
  axisFormat  %H:%M
  title 거래일 D (KST)
  section 데이터
  D-1 일봉 공개 (공공데이터)     :milestone, 13:00, 0m
  section 매니저
  오전 판단 (D-2 일봉까지 사용) → 시가 체결 :08:35, 25m
  오후 판단 (D-1 일봉까지 사용) → 종가 체결 :14:00, 30m
  section 엔진
  동기화·체결 확정 (D-1 주문)    :13:20, 10m
  일일 리포트                    :15:45, 10m
```

- `MarketCalendar.latest_published_day(now)` 가 '지금 볼 수 있는 마지막 일봉'을 정합니다. MockWorld·백테스터·PaperBroker 모두 이 함수를 따릅니다.
- 백테스터는 시점마다 공개된 일봉만 저장소에 넣습니다. 손계산 재현 테스트가 미래 정보 누설을 잡아냈고, 그 수정이 반영돼 있습니다(`tests/test_backtest.py`).

## 6. 실시간 이벤트 (WebSocket)

`/ws/events?token=…` 채널:

| channel | 내용 |
|---|---|
| `chat` | 트레이딩 룸 메시지 `{role: analyst\|risk\|broker\|system\|user, kind: text\|tool\|decision\|fill\|block\|summary}` |
| `typing` | 입력 중 표시 `{role, active, label}` |
| `audit` | 감사 로그 (신호·주문·체결·리스크 차단·킬 스위치 …) |
| `state` | 엔진 상태·시뮬레이션 시각·자산 변경 → UI 가 쿼리를 다시 불러옴 |
