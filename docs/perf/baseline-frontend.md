# Frontend performance baseline (BEFORE)

Generated 2026-11-04T14:41:55.577Z from git `80b1f74` on `main` (48 uncommitted paths under frontend/). Live app http://localhost:15173 (nginx, production build) against the running paper-trading backend; headless Chromium 156.0.8078.4. Re-run with the commands in `perf/README.md` (Frontend section); compare runs with `node perf/fe_compare.mjs`.

Market state while loading pages: `{"KR":{"enabled":true,"open_now":false,"agent_running":false,"engine_status":"RUNNING"},"US":{"enabled":true,"open_now":false,"agent_running":false,"engine_status":"RUNNING"}}`. Times below are medians of 3 cold runs (Fast 3G: 1 run).

## Headline numbers

- **Bundle**: one JS chunk 596.6 KB raw / 188.6 KB gzip, CSS 62.9 KB; route code-splitting: **no**. Served by nginx **uncompressed** (596.6 KB on the wire), no Cache-Control.
- **Page load (content visible, median of 13 routes)**: desktop 95 ms, mobile 4x CPU 319 ms, mobile 4x CPU + Fast 3G 6,533 ms (LCP 6,168 ms). Every route makes ~15 API requests and transfers ~1025.7 KB cold.
- **Idle on overview** (desktop, 60 s): 27 API req/min, 85 React commits/min, 99 ms script + 409 ms main-thread tasks per minute, 332 style recalcs/min, 0 chart canvases re-created/min.
- **Idle on dashboard_kr** (desktop, 60 s): 22 API req/min, 86 React commits/min, 367 ms script + 1,076 ms main-thread tasks per minute, 686 style recalcs/min, 126 chart canvases re-created/min.
- **Idle on room_kr** (desktop, 60 s): 15 API req/min, 78 React commits/min, 132 ms script + 372 ms main-thread tasks per minute, 240 style recalcs/min, 0 chart canvases re-created/min.
- **Component renders while idle on overview**: 723 per minute (top: Link 269, NavLink 216, KstClock 84, Layout 24).
- **Component renders while idle on dashboard_kr**: 8,354 per minute (top: ChatMessage 4,000, ToolChip 2,480, Link 670, DecisionCard 340).
- **Component renders while idle on room_kr**: 1,032 per minute (top: Link 192, ChatMessage 192, NavLink 162, SessionBlock 96).
- **Market switch** (click -> new content): desktop 국장->미장 80 ms, 미장->전체 180 ms; mobile 4x 98 / 133 ms.
- **Chat scroll** (top and back, 40 px/frame): /room mobile 4x 0 frames > 50 ms (p95 16.7 ms); dashboard chat mobile 4x 0 frames > 50 ms (p95 16.7 ms).
- **Code**: 9,668 app lines + 4,667 CSS lines; 32 useQuery (6 polling), 0 React.memo; eslint 0 errors / 4 warnings; 205 tests (0 failed) in 2.9 s.

## 1. Bundle

`npx vite build --outDir perf/tmp/fe_build --emptyOutDir --sourcemap --manifest` (1.31 s). gzip level 9, brotli q11.

| chunk | type | raw | gzip | brotli | entry | dynamic | initial |
|---|---|---|---|---|---|---|---|
| assets/index-[hash].css | css | 64,396 | 12,953 | 11,570 |  |  | y |
| assets/index-[hash].js | js | 610,947 | 193,131 | 165,275 | y |  | y |

Code splitting: React.lazy calls 0, dynamic import() 0, dynamic chunks 0, pages imported statically in App.tsx 12.

**Bytes by package (minified, attributed through sourcemaps)**

| package | bytes | % |
|---|---|---|
| app (src/) | 182,193 | 29.8 |
| react-dom | 173,784 | 28.4 |
| lightweight-charts | 160,181 | 26.2 |
| @tanstack/query-core | 36,150 | 5.9 |
| react-router | 32,390 | 5.3 |
| fancy-canvas | 8,810 | 1.4 |
| react | 8,333 | 1.4 |
| scheduler | 3,664 | 0.6 |
| [unmapped] | 2,805 | 0.5 |
| @tanstack/react-query | 2,638 | 0.4 |

**Largest app source files in the bundle**

| file | bytes | % of bundle |
|---|---|---|
| src/pages/Settings.tsx | 32,428 | 5.3 |
| src/pages/Memo.tsx | 14,731 | 2.4 |
| src/pages/Costs.tsx | 13,779 | 2.3 |
| src/pages/Agent.tsx | 12,674 | 2.1 |
| src/pages/Overview.tsx | 10,345 | 1.7 |
| src/components/chat/SessionBlock.tsx | 8,351 | 1.4 |
| src/pages/Stock.tsx | 7,688 | 1.3 |
| src/components/Layout.tsx | 7,308 | 1.2 |
| src/pages/ScheduleSettings.tsx | 6,692 | 1.1 |
| src/pages/Portfolio.tsx | 5,773 | 0.9 |

**What the live server sends**

| asset | Content-Encoding | Cache-Control | bytes on wire |
|---|---|---|---|
| /assets/index-[hash].js | none | none | 610,904 |
| /assets/index-[hash].css | none | none | 64,396 |

## 2. Page load (cold, fresh browser context per run)

`content` = first animation frame in which the route's data-dependent selector is present (see `ROUTES` in perf/fe_lib.mjs). `idle` = no /api request in flight for 1.5 s. Long tasks / CDP metrics cover navigation -> idle. Bytes = encoded bytes over the wire incl. headers.

### desktop (CPU x1)

| route | path | FCP | LCP | content | DCL | load | idle | CLS | long tasks (ms) | API req | KB | heap MB | DOM nodes | script ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| overview | `/?m=all` | 84 | 112 | 205 | 35 | 35 | 1,749 | 0.096 | 0 (0) | 15 | 1,016 | 3.8 | 528 | 47 |
| dashboard_kr | `/?m=kr` | 88 | 196 | 120 | 33 | 33 | 1,750 | 0.105 | 0 (0) | 17 | 1,062 | 5.7 | 3,322 | 78 |
| room_kr | `/room` | 84 | 384 | 112 | 37 | 37 | 1,646 | 0.021 | 0 (0) | 15 | 1,039 | 3.9 | 927 | 53 |
| portfolio_kr | `/portfolio` | 88 | 88 | 92 | 38 | 38 | 1,748 | 0.57 | 0 (0) | 18 | 1,119 | 4.8 | 2,639 | 68 |
| agent_kr | `/agent` | 80 | 128 | 107 | 37 | 37 | 1,647 | 0.018 | 0 (0) | 15 | 1,022 | 3.6 | 668 | 44 |
| settings_kr | `/settings` | 92 | 92 | 106 | 36 | 36 | 1,645 | 0.295 | 0 (0) | 24 | 1,028 | 4.2 | 4,444 | 55 |
| memo_kr | `/memo` | 80 | 92 | 76 | 37 | 37 | 1,649 | 0.021 | 0 (0) | 15 | 1,015 | 3.5 | 349 | 44 |
| costs | `/costs` | 76 | 92 | 75 | 34 | 34 | 1,646 | 0.023 | 0 (0) | 15 | 1,026 | 3.7 | 877 | 43 |
| stock_kr | `/stock/005930` | 84 | 144 | 94 | 40 | 40 | 2,966 | 0.235 | 0 (0) | 17 | 1,074 | 4.5 | 970 | 76 |
| logs_kr | `/logs` | 76 | 112 | 95 | 35 | 35 | 1,650 | 0.028 | 0 (0) | 14 | 1,014 | 4 | 2,205 | 69 |
| dashboard_us | `/?m=us` | 92 | 176 | 94 | 40 | 40 | 1,666 | 0.112 | 0 (0) | 17 | 1,019 | 4.5 | 427 | 67 |
| room_us | `/room?m=us` | 84 | 128 | 107 | 34 | 34 | 1,645 | 0.021 | 0 (0) | 15 | 1,016 | 3.7 | 398 | 49 |
| stock_us | `/stock/AAPL?m=us` | 80 | 112 | 90 | 37 | 37 | 1,746 | 0.41 | 0 (0) | 17 | 1,033 | 4.2 | 330 | 58 |

### mobile_cpu4x (CPU x4)

| route | path | FCP | LCP | content | DCL | load | idle | CLS | long tasks (ms) | API req | KB | heap MB | DOM nodes | script ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| overview | `/?m=all` | 260 | 420 | 401 | 117 | 119 | 1,942 | 0 | 2 (171) | 15 | 1,016 | 3.7 | 534 | 175 |
| dashboard_kr | `/?m=kr` | 284 | 612 | 406 | 113 | 115 | 2,050 | 0 | 4 (405) | 17 | 1,062 | 5.7 | 3,328 | 299 |
| room_kr | `/room` | 268 | 392 | 375 | 113 | 115 | 1,916 | 0 | 2 (180) | 15 | 1,039 | 3.9 | 933 | 217 |
| portfolio_kr | `/portfolio` | 280 | 532 | 319 | 115 | 117 | 1,946 | 0 | 3 (276) | 18 | 1,119 | 4.6 | 2,645 | 223 |
| agent_kr | `/agent` | 248 | 328 | 311 | 113 | 116 | 1,882 | 0 | 2 (163) | 15 | 1,022 | 3.5 | 674 | 161 |
| settings_kr | `/settings` | 296 | 532 | 325 | 112 | 115 | 1,936 | 0 | 2 (198) | 24 | 1,028 | 4.2 | 4,450 | 201 |
| memo_kr | `/memo` | 240 | 308 | 272 | 112 | 115 | 1,860 | 0 | 2 (154) | 15 | 1,015 | 3.5 | 355 | 166 |
| costs | `/costs` | 236 | 312 | 260 | 112 | 115 | 1,863 | 0 | 2 (154) | 15 | 1,026 | 3.7 | 883 | 168 |
| stock_kr | `/stock/005930` | 252 | 1,016 | 287 | 113 | 116 | 2,512 | 0.032 | 3 (221) | 17 | 1,074 | 4.6 | 976 | 247 |
| logs_kr | `/logs` | 252 | 500 | 314 | 113 | 115 | 1,814 | 0 | 2 (165) | 14 | 1,014 | 4 | 2,211 | 225 |
| dashboard_us | `/?m=us` | 280 | 496 | 415 | 111 | 113 | 1,987 | 0 | 3 (296) | 17 | 1,019 | 4.4 | 433 | 246 |
| room_us | `/room?m=us` | 288 | 628 | 351 | 115 | 117 | 1,911 | 0 | 2 (194) | 15 | 1,016 | 3.6 | 404 | 184 |
| stock_us | `/stock/AAPL?m=us` | 248 | 340 | 290 | 112 | 115 | 1,998 | 0 | 2 (166) | 17 | 1,033 | 4.2 | 336 | 220 |

### mobile_cpu4x_fast3g (CPU x4, Fast 3G)

| route | path | FCP | LCP | content | DCL | load | idle | CLS | long tasks (ms) | API req | KB | heap MB | DOM nodes | script ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| overview | `/?m=all` | 5,560 | 6,192 | 6,171 | 4,842 | 4,842 | 9,699 | 0.393 | 2 (171) | 19 | 1,020 | 3.9 | 534 | 210 |
| dashboard_kr | `/?m=kr` | 5,580 | 8,236 | 6,272 | 4,834 | 4,834 | 9,891 | 0.007 | 4 (378) | 17 | 1,062 | 6.4 | 3,328 | 504 |
| room_kr | `/room` | 5,560 | 7,604 | 7,575 | 4,837 | 4,838 | 9,802 | 0.009 | 2 (181) | 15 | 1,039 | 4 | 933 | 256 |
| portfolio_kr | `/portfolio` | 5,564 | 5,808 | 6,850 | 4,842 | 4,842 | 10,265 | 0.112 | 3 (250) | 18 | 1,119 | 4.9 | 2,645 | 264 |
| agent_kr | `/agent` | 5,540 | 6,172 | 6,252 | 4,832 | 4,834 | 9,669 | 0.076 | 2 (161) | 15 | 1,022 | 3.7 | 674 | 182 |
| settings_kr | `/settings` | 5,588 | 5,840 | 6,753 | 4,833 | 4,833 | 10,739 | 0.127 | 2 (199) | 24 | 1,028 | 4.4 | 4,450 | 235 |
| memo_kr | `/memo` | 5,536 | 6,160 | 6,121 | 4,842 | 4,842 | 9,688 | 0.049 | 2 (153) | 15 | 1,015 | 3.6 | 355 | 191 |
| costs | `/costs` | 5,532 | 6,976 | 6,934 | 4,834 | 4,834 | 9,700 | 0.152 | 2 (152) | 15 | 1,026 | 3.8 | 883 | 200 |
| stock_kr | `/stock/005930` | 5,560 | 6,168 | 6,533 | 4,849 | 4,849 | 9,940 | 0.386 | 3 (226) | 17 | 1,074 | 4.7 | 976 | 288 |
| logs_kr | `/logs` | 5,556 | 5,808 | 7,790 | 4,832 | 4,833 | 9,687 | 0.069 | 2 (174) | 14 | 1,014 | 4.1 | 2,211 | 236 |
| dashboard_us | `/?m=us` | 5,580 | 6,876 | 6,155 | 4,839 | 4,839 | 9,686 | 0.007 | 3 (293) | 17 | 1,019 | 5 | 433 | 414 |
| room_us | `/room?m=us` | 5,572 | 6,156 | 6,800 | 4,842 | 4,843 | 9,710 | 0.009 | 2 (181) | 15 | 1,016 | 3.7 | 404 | 221 |
| stock_us | `/stock/AAPL?m=us` | 5,556 | 6,136 | 6,337 | 4,844 | 4,844 | 9,727 | 0.976 | 2 (171) | 17 | 1,033 | 4.4 | 336 | 240 |

API requests made by a cold load of /room (desktop, run 1): `{"/api/agent/progress":2,"/api/agent/runs":1,"/api/auth/status":1,"/api/chat":2,"/api/events":1,"/api/markets":1,"/api/system":1,"/api/us/agent/progress":2,"/api/us/chat":2,"/api/us/events":1,"/api/us/system":1}`

## 3. Steady state (desktop, page left open)

| metric | overview | dashboard_kr | room_kr |
|---|---|---|---|
| API req/min | 27 | 22 | 15 |
| API bytes/min | 32,875 | 31,572 | 17,383 |
| WS frames/min | 4 | 4 | 4 |
| React commits/min | 85 | 86 | 78 |
| chart canvases created/min | 0 | 126 | 0 |
| long tasks/min | 0 | 0 | 0 |
| long task ms/min | 0 | 0 | 0 |
| script ms/min | 99.3 | 366.7 | 131.7 |
| main-thread task ms/min | 408.9 | 1,076.2 | 372.2 |
| layouts/min | 120 | 367 | 60 |
| style recalcs/min | 332 | 686 | 240 |
| heap MB start | 3.7 | 5.6 | 3.9 |
| heap MB end | 4 | 6.6 | 4.1 |
| DOM nodes | 528 | 3,322 | 927 |

- overview API calls/min by endpoint: `{"/api/us/system":12,"/api/system":12,"/api/overview":2,"/api/markets":1}`; WS by channel/min: `{"pong":4}`; market state `{"KR":{"enabled":true,"open_now":false,"agent_running":false,"engine_status":"RUNNING"},"US":{"enabled":true,"open_now":true,"agent_running":false,"engine_status":"RUNNING"}}`
- dashboard_kr API calls/min by endpoint: `{"/api/system":12,"/api/portfolio":6,"/api/us/system":2,"/api/markets":1,"/api/market/indicators":1}`; WS by channel/min: `{"pong":4}`; market state `{"KR":{"enabled":true,"open_now":false,"agent_running":false,"engine_status":"RUNNING"},"US":{"enabled":true,"open_now":true,"agent_running":false,"engine_status":"RUNNING"}}`
- room_kr API calls/min by endpoint: `{"/api/system":12,"/api/us/system":2,"/api/markets":1}`; WS by channel/min: `{"pong":4}`; market state `{"KR":{"enabled":true,"open_now":false,"agent_running":false,"engine_status":"RUNNING"},"US":{"enabled":true,"open_now":true,"agent_running":false,"engine_status":"RUNNING"}}`

## 4. React render counts (unminified prod build via `vite preview`, DevTools-hook fiber walk)

- **overview**: load = 17 commits / 450 component renders; idle = 85 commits/min, 723 component renders/min. Re-renders/min: `{"Link":269,"NavLink":216,"KstClock":84,"Layout":24,"PaperBadge":24,"MarketSwitch":24,"KillSwitch":24,"Page":24,"Outlet":24,"MarketCard":4,"SessionTimeline":3,"Overview":1,"TotalCard":1,"ActivityFeed":1}`
- **dashboard_kr**: load = 19 commits / 1,553 component renders; idle = 86 commits/min, 8,354 component renders/min. Re-renders/min: `{"ChatMessage":4000,"ToolChip":2480,"Link":670,"DecisionCard":340,"NavLink":171,"IndicatorCard":120,"Sparkline":120,"Kpi":80,"SummaryCounts":80,"KstClock":79,"Dashboard":20,"IndicatorStrip":20,"EquityChart":20,"HoldingsTable":20,"ChatFeed":20,"Layout":19,"PaperBadge":19,"MarketSwitch":19,"KillSwitch":19,"Page":19,"Outlet":19}`
- **room_kr**: load = 14 commits / 558 component renders; idle = 78 commits/min, 1,032 component renders/min. Re-renders/min: `{"Link":192,"ChatMessage":192,"NavLink":162,"SessionBlock":96,"RiskNotes":84,"KstClock":78,"FailedBox":48,"CompactDecisions":36,"Layout":18,"PaperBadge":18,"MarketSwitch":18,"KillSwitch":18,"Page":18,"Outlet":18,"TradingRoom":12,"ChatFeed":12,"SummaryCounts":12}`

## 5. Interaction smoothness

**room_scroll** (programmatic scroll 40 px per animation frame to the top and back)

| profile | scroller | scroll px | rows | frames | > 50 ms | > 33 ms | p50 ms | p95 ms | max ms | avg fps | commits | long tasks |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| desktop | chat-scroll | 1,922 | 14 | 69 | 0 | 0 | 16.7 | 16.7 | 16.8 | 58.7 | 4 | 0 |
| mobile_cpu4x | chat-scroll | 2,525 | 14 | 107 | 0 | 0 | 16.7 | 16.7 | 16.8 | 59.2 | 4 | 0 |

**dashboard_chat_scroll** (programmatic scroll 40 px per animation frame to the top and back)

| profile | scroller | scroll px | rows | frames | > 50 ms | > 33 ms | p50 ms | p95 ms | max ms | avg fps | commits | long tasks |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| desktop | chat-scroll | 15,086 | 55 | 739 | 0 | 0 | 16.7 | 16.8 | 33.3 | 59.8 | 22 | 0 |
| mobile_cpu4x | chat-scroll | 23,558 | 55 | 1,157 | 0 | 1 | 16.7 | 16.7 | 33.4 | 59.6 | 35 | 0 |

**MarketSwitch** (start on /?m=kr; click -> aria-pressed (ui) / new content visible / view transition finished; click event duration from Event Timing)

| profile | step | ui ms | content ms | transition done ms | click event ms | commits | API req |
|---|---|---|---|---|---|---|---|
| desktop | cold_kr_to_us | 65.4 | 80.1 | 330.8 | 80 | 5 | 2 |
| desktop | cold_us_to_all | 29.6 | 179.8 | 293 | 40 | 3 | 1 |
| desktop | warm_all_to_kr | 49.2 | 49.2 | 318.3 | 40 | 5 | 3 |
| desktop | warm_kr_to_us | 31.2 | 31.2 | 295.1 | 32 | 6 | 3 |
| desktop | warm_us_to_all | 32.8 | 32.8 | 296 | 32 | 2 | 1 |
| mobile_cpu4x | cold_kr_to_us | 97 | 97.8 | 361.8 | 64 | 5 | 2 |
| mobile_cpu4x | cold_us_to_all | 64.4 | 133 | 314.5 | 24 | 3 | 1 |
| mobile_cpu4x | warm_all_to_kr | 99.7 | 100.1 | 382.3 | 16 | 5 | 3 |
| mobile_cpu4x | warm_kr_to_us | 48.8 | 48.8 | 330.7 | 24 | 5 | 3 |
| mobile_cpu4x | warm_us_to_all | 49.1 | 49.1 | 299.1 | 24 | 2 | 1 |

## 6. Code metrics

app 41 files / 9,668 lines, tests 21 files / 4,958 lines, CSS 4 files / 4,667 lines, e2e specs 2.

| file | kind | lines |
|---|---|---|
| src/styles.css | css | 3,560 |
| src/pages/Settings.tsx | app | 1,385 |
| src/pages/Memo.tsx | app | 782 |
| src/pages/Costs.tsx | app | 685 |
| src/api/types.ts | app | 642 |
| src/pages/overview.css | css | 483 |
| src/components/chat/chat-session.css | css | 479 |
| src/pages/Overview.tsx | app | 470 |
| src/__tests__/llmProfile.test.tsx | test | 468 |
| src/pages/Agent.tsx | app | 452 |
| src/__tests__/memo.test.tsx | test | 444 |
| src/__tests__/market.test.tsx | test | 388 |
| src/components/chat/SessionBlock.tsx | app | 364 |
| src/components/Layout.tsx | app | 349 |
| src/pages/Stock.tsx | app | 330 |
| src/__tests__/session.test.tsx | test | 326 |
| src/pages/ScheduleSettings.tsx | app | 315 |
| src/__tests__/costs.test.tsx | test | 304 |
| src/components/chat/ChatMessage.tsx | app | 300 |
| src/__tests__/openrouterModels.test.tsx | test | 292 |

**Largest components** (90 components, 9 over 150 lines)

| component | at | lines |
|---|---|---|
| AgentPage | src/pages/Agent.tsx:28 | 269 |
| ScheduleEditor | src/pages/ScheduleSettings.tsx:83 | 233 |
| TestEndpointSettings | src/pages/Settings.tsx:376 | 232 |
| PushSettings | src/pages/Settings.tsx:870 | 225 |
| PaidModelPicker | src/pages/Settings.tsx:645 | 208 |
| MemoCardItem | src/pages/Memo.tsx:533 | 198 |
| Backtest | src/pages/Backtest.tsx:11 | 167 |
| Stock | src/pages/Stock.tsx:170 | 161 |
| ChatFeed | src/components/chat/ChatFeed.tsx:31 | 156 |
| ModelPanel | src/pages/Costs.tsx:367 | 149 |
| Layout | src/components/Layout.tsx:72 | 147 |
| StockPicker | src/pages/Memo.tsx:343 | 139 |

**Hooks**: `{"useQuery":32,"useQueries":0,"useMutation":33,"useEffect":18,"useLayoutEffect":2,"useState":70,"useMemo":12,"useCallback":4,"react_memo":0,"react_lazy":0,"invalidateQueries":15,"invalidateQueries_all":9,"setInterval":4,"websocket":1}`

**React Query**: QueryClient defaults `{ queries: { retry: 1, staleTime: 2000 } }`; 32 useQuery calls, 6 with refetchInterval:

| at | queryKey | endpoint | refetchInterval | staleTime |
|---|---|---|---|---|
| src/auth.tsx:26 | `["auth-status"]` | /api/auth/status |  | Infinity |
| src/hooks.ts:10 | `api.key("system")` | /api/system | opts.refetchInterval ?? 5000 | (default 2000) |
| src/hooks.ts:20 | `api.key("portfolio")` | /api/portfolio | 10000 | (default 2000) |
| src/hooks.ts:29 | `["market-indicators"]` | /api/market/indicators | 60_000 | 55_000 |
| src/hooks.ts:40 | `["llm-costs", months]` | /api/llm/costs |  | 60_000 |
| src/market.ts:62 | `MARKETS_KEY` | /api/markets | 60_000 | 20_000 |
| src/pages/Agent.tsx:32 | `api.key("agent-structure")` | /api/agent/structure | 5000 | (default 2000) |
| src/pages/Memo.tsx:361 | `[m.market, "market-search", term]` |  |  | 60_000 |
| src/pages/Overview.tsx:31 | `OVERVIEW_KEY` | /api/overview | 30_000 | (default 2000) |

**Type escapes (app code)**: `{"as_casts":25,"as_const":5,"as_unknown_as":1,"any":0,"non_null_assertions":3,"ts_ignore":0,"eslint_disable":0}`; tests: `{"as_casts":34,"as_const":7,"as_unknown_as":3,"any":0,"non_null_assertions":8,"ts_ignore":0,"eslint_disable":0}`

**Inline style props**: 12 (`{"src/components/Layout.tsx":{"object_literal":1,"expression":0},"src/components/MarketSwitch.tsx":{"object_literal":1,"expression":0},"src/components/charts.tsx":{"object_literal":1,"expression":0},"src/components/chat/ChatFeed.tsx":{"object_literal":0,"expression":1},"src/components/chat/ChatMessage.tsx":{"object_literal":1,"expression":0},"src/pages/Costs.tsx":{"object_literal":2,"expression":0},"src/pages/Overview.tsx":{"object_literal":5,"expression":0}}`); index keys: 10.

**Duplicates**: 25 duplicated 6-line windows; same name defined in several files: `STATUS_KO` (src/components/Layout.tsx:54, src/utils/format.ts:188); `won` (src/components/charts.tsx:27, src/utils/format.ts:1); `LEAD_EMOJI` (src/components/chat/ChatFeed.tsx:190, src/components/chat/SessionBlock.tsx:20); `fmtTokens` (src/components/chat/ChatFeed.tsx:192, src/utils/format.ts:170); `is404` (src/market.ts:58, src/pages/ScheduleSettings.tsx:21); `fmtUsd` (src/utils/format.ts:144, src/utils/llm.ts:80); `pad` (src/utils/memo.ts:90, src/utils/schedule.ts:21). Format helpers outside src/utils: `won` src/components/charts.tsx:27, `priceFormatter` src/components/charts.tsx:31, `pricePrecision` src/components/charts.tsx:33, `fmtTokens` src/components/chat/ChatFeed.tsx:192, `fmtElapsed` src/components/chat/ChatFeed.tsx:196, `fmtMs` src/components/chat/ChatMessage.tsx:135, `fmtDuration` src/components/chat/sessions.ts:217, `pct` src/pages/Agent.tsx:25.

**CSS**

| file | bytes | lines | rules | selectors | declarations | @media | @keyframes | infinite anims | !important | custom props |
|---|---|---|---|---|---|---|---|---|---|---|
| src/components/chat/chat-activity.css | 3,096 | 146 | 28 | 28 | 76 | 2 | 0 | 0 | 0 | 5 |
| src/components/chat/chat-session.css | 8,948 | 480 | 84 | 87 | 275 | 3 | 0 | 0 | 0 | 6 |
| src/pages/overview.css | 8,764 | 484 | 86 | 98 | 273 | 3 | 0 | 1 | 0 | 1 |
| src/styles.css | 67,500 | 3,561 | 632 | 719 | 1,950 | 17 | 12 | 9 | 0 | 30 |

**eslint**: 0 errors, 4 warnings over 68 files (1.23 s) `{"react-refresh/only-export-components":4}`; prettier unformatted files: 0; tsc errors: 0 (2.33 s).

**vitest**: 19 files, 205 tests, 0 failed, wall 2.94 s. Slowest files: src/__tests__/memo.test.tsx 1544 ms, src/__tests__/schedule.test.tsx 1256 ms, src/__tests__/costs.test.tsx 861 ms, src/__tests__/llmProfile.test.tsx 657 ms, src/__tests__/openrouterModels.test.tsx 403 ms.

<!-- MANUAL:BEGIN -->
## 7. Hotspots and observations (hand-written, kept across rebuilds)

Measured 2026-11-04 ~23:15-23:45 KST: KR closed, US opened at 23:30, **no agent session ran** (the only WS
traffic was pongs). So the steady-state numbers are the *idle* floor; during a live session `progress`/`chat`
frames add more renders and invalidations on top (see #4/#5). Headless Chromium's frame pacing is optimistic:
the scroll tests showed no dropped frames even at 4x CPU, so the jank found here is load / re-render work, not
scrolling.

### Top 10 hotspots (expected impact is an estimate, to be confirmed by the AFTER run)

| # | where | what was measured | proposed fix | expected impact |
|---|---|---|---|---|
| 1 | `frontend/nginx.conf` (no `gzip`, no cache headers for `/assets/`) | JS sent **uncompressed**: 610,904 B on the wire (gzip 193,131 B, brotli 165,275 B); no `Cache-Control`. Fast 3G + 4x CPU: DCL 4.8 s, FCP 5.5 s, content median 6.5 s (max /logs 7.8 s), dashboard LCP 8.2 s. API JSON is also uncompressed (`/api/chat?limit=200` 104 KB, `/api/events?limit=200` 142 KB) | `gzip on; gzip_types application/javascript text/css application/json image/svg+xml;` (or `gzip_static` with pre-compressed assets); `location /assets/ { add_header Cache-Control "public, max-age=31536000, immutable"; }`; keep `index.html` `no-cache` | Fast 3G content/LCP about -2.5 to -3 s; ~-420 KB JS and ~-280 KB API per cold load; warm loads skip JS entirely |
| 2 | `frontend/src/App.tsx:9-20` (all 12 pages imported statically) | 1 JS chunk for every route; `lightweight-charts` + `fancy-canvas` = 169 KB (27.6 %) although only Dashboard/Stock/Backtest draw charts; Settings 32 KB, Memo 15 KB, Costs 14 KB, Agent 13 KB of app code | `React.lazy` per route + `<Suspense>` skeleton; lazy-load `components/charts.tsx`; `manualChunks` for react/react-dom/router (long-lived vendor cache); prefetch the next tab's chunk on hover/idle | initial JS ~611 -> ~330 KB raw (~193 -> ~105 KB gzip); mobile 4x long tasks at load (median 180 ms, dashboard 405 ms) about -40 % |
| 3 | `frontend/src/events.tsx:101-110,136,164` + `:76-86` | Every cold load of **any** route fetches `/api/chat?limit=200` and `/api/agent/progress` **twice per market** (`sync()` runs before `connect()` and again in `onopen`) plus `/api/events?limit=200` for both markets: ~350 KB of the 360-470 KB API bytes per load, also on /memo, /costs, /settings | sync once (on open only); load chat only where a chat is shown, audit only on /logs; smaller first page (`limit=50`) + "load older"; a tiny "latest decisions/fills" endpoint for the Overview feed | API bytes per cold load 360 KB -> <60 KB; API requests 15 -> ~8 per route |
| 4 | `frontend/src/pages/Dashboard.tsx:17-19,129-135` -> `components/chat/ChatFeed.tsx:116-148`, `ChatMessage.tsx:35,197,254` | Dashboard re-renders on every `/api/system` (5 s) and `/api/portfolio` (10 s) poll and every WS event, and re-renders all 200 chat rows each time (0 `React.memo` in the codebase): **8,354 component renders/min while idle** (ChatMessage 4,000, ToolChip 2,480, DecisionCard 340), 367 ms script/min vs 99-132 on the other pages | `React.memo` for ChatMessage/ToolChip/DecisionCard/SessionBlock (stable `persona` via `useMemo`); move polled widgets (KPIs, agent panel) into their own components so the page shell does not re-render; `select` in `useQuery` to subscribe to the fields used | idle renders 8.3k -> <300/min; dashboard script/min ~-60 % |
| 5 | `frontend/src/components/charts.tsx:117-142` + `pages/Dashboard.tsx:64` | `points={(equity.data ?? []).map(...)}` is a new array every render, so the effect destroys and re-creates the whole lightweight-charts instance on every Dashboard render: **126 canvases created/min idle**, 367 layouts/min (vs 60-120 elsewhere), main-thread task time 1,076 ms/min (vs ~400) | create the chart once (`useEffect([], ...)`), keep `series` in a ref and call `series.setData` only when `equity.data` changes (`useMemo` on the query data); `applyOptions` for currency/theme; same pattern for `CandleChart` | canvases re-created 126 -> 0/min; dashboard idle task time ~-50 % |
| 6 | `frontend/src/events.tsx:56` (+ `components/Layout.tsx:94-95`) | one context value `{KR, US}` that changes on **any** WS frame of either market; Layout subscribes to both markets only for `connected`, so a `progress`/`typing` frame re-renders the header, nav (9 NavLink + Links) and every `useEvents` page. Idle already 723 renders/min on Overview (Link 269, NavLink 216) from polls alone | split into separate contexts or an external store with selectors (`useSyncExternalStore`): connection status, messages, progress; batch progress updates per animation frame | renders per WS frame -> only the consumers of that slice; big win during live sessions (not visible in this idle baseline) |
| 7 | `frontend/src/components/Layout.tsx:77-78`, `hooks.ts:13,23`, `pages/Agent.tsx:35`, `App.tsx:64` | Polling although the WS already pushes `state` events that invalidate queries: idle **27 API req/min on Overview** (`/api/system` + `/api/us/system` every 5 s), 22 on Dashboard, 15 on Room; `/api/agent/structure` every 5 s; global `staleTime: 2000` makes every tab switch refetch everything | rely on WS invalidation; when WS is connected poll `/api/system` every 30-60 s (5 s only while disconnected or `agent_running`); poll the non-viewed market at 60 s; raise default `staleTime` to 30 s for config-like queries | idle API calls ~27 -> ~3/min; fewer commits (KstClock aside) |
| 8 | `frontend/src/events.tsx:115-127` + 9x `qc.invalidateQueries()` with no filter (`Dashboard.tsx:27,31`, `TradingRoom.tsx:25`, `Portfolio.tsx:74`, `KillSwitch.tsx:27,32`, `Settings.tsx:1110,1225,1285`) | every `chat` (non-analyst, fill, decision, summary) or `state` frame invalidates **all** queries of that market + overview/markets + all global ones, i.e. refetches equity (1000 points), orders (300), decisions (100), ... that are mounted | map event -> keys (fill -> portfolio/orders/fills/equity; state -> system/markets; decision -> decisions/runs); mutations invalidate only what they changed | removes refetch bursts during sessions; measure with a `steady` run while `agent_running` is true |
| 9 | Layout shift: `components/Layout.tsx:165,180,186` (topbar chips appear when `/api/system` arrives), `pages/Portfolio.tsx:85-134` (empty states shown while loading), `pages/Stock.tsx:223,229` (chart / SupplyPanel inserted on arrival), `pages/Settings.tsx:146-152` + sections | desktop CLS: **/portfolio 0.57**, /stock/AAPL 0.41, /settings 0.30, /stock/005930 0.24, ~0.10 on every page from the topbar (main content moves 22.5 px); Fast 3G /stock/AAPL 0.98, /overview 0.39 | reserve topbar chip/kill-switch width before data; skeleton rows instead of "아직 체결이 없어요"/"없음" while `isPending`; fixed-height chart container rendered before data; per-section skeletons on Settings | CLS < 0.05 on all routes; no false "empty" flashes |
| 10 | `frontend/src/pages/Logs.tsx:44-55`, `ChatFeed.tsx:116-148`, `events.tsx:181-185` | unvirtualized long lists: dashboard chat 200 messages = 3,322 DOM nodes and 23.5k px scroll height (mobile); Logs 200-300 rows rebuilt and re-formatted (`fmtDateTime` per row) on every audit frame; `merge()` re-sorts up to 800 messages per incoming chat frame | windowing (e.g. `@tanstack/react-virtual`) or at least `content-visibility: auto; contain-intrinsic-size` on rows; memoized rows; append instead of merge+sort when ids are increasing | DOM nodes on dashboard 3.3k -> <1k; less work per WS frame during sessions |

Smaller items: `.paper-badge .dot` pulses forever on every page (`styles.css:242-249`; ~240 style recalcs/min
idle, ~0 cost alone, but 10 infinite animations in CSS keep the main thread busy whenever anything else runs a
rAF loop); `KstClock` re-renders every second (`Layout.tsx:328-348`, fine but isolate it); `Portfolio.tsx:79`
`closed = orders.filter(o => !open.includes(o))` is O(n*m); `Settings.tsx:676-681` fires
`POST /api/llm/openrouter/models/refresh` by itself on mount when the OpenRouter list is stale (did not fire in this
run; the perf guard blocks it) - a write on navigation is surprising for a settings page; duplicated helpers
(`fmtTokens`, `won`, `STATUS_KO`, `LEAD_EMOJI`, `is404`, `fmtUsd`, `pad`, 25 duplicated 6-line windows) and
format helpers living outside `src/utils/format.ts` (charts.tsx, ChatFeed.tsx, ChatMessage.tsx, sessions.ts,
Agent.tsx); `styles.css` is a single 3,560-line / 632-rule file loaded on every route (per-page CSS would split with
the lazy chunks).

### UX / app-feel opportunities (not implemented)

- **Skeletons instead of blank/false-empty states**: Dashboard KPIs show "—" until `/api/portfolio` arrives;
  Portfolio shows "보유 종목이 없어요 / 아직 체결이 없어요 / 없음" while loading (`Portfolio.tsx:21,101,181`); Stock shows
  no chart area until candles arrive; Agent/Memo show a plain `<Loading/>` text. Overview already has `.ov-skel` -
  reuse it.
- **Settings is one 1,385-line page with 9 sections** (LLM, push, agent, schedule, risk, strategies, watchlist +
  links; 4,444 DOM nodes, 24 API calls on open): split into tabs or sub-routes (`/settings/llm`, `/settings/alerts`,
  `/settings/agent`, `/settings/risk`, ...) with a sticky section nav on desktop and a list -> detail push on mobile;
  sticky save bar per form.
- **Route transitions on desktop**: mobile has View Transitions for tabs; desktop swaps instantly with layout shift.
  With lazy routes, keep the old page until the next chunk + first query are ready (`useTransition` / router
  `startTransition`) and prefetch on hover.
- **Pull-to-refresh** on mobile Dashboard / Portfolio / Room instead of relying on 5-10 s polling; show "updated
  12 s ago" next to KPIs.
- **Bottom sheets on mobile** for the /room session list (currently an aside under the chat), the kill-switch
  confirm, memo-card editing (`Memo.tsx` MemoCardItem 198 lines), and a stock quick view when tapping a holding.
- **Command palette (Cmd/Ctrl-K)**: jump to a stock by name/symbol (the `market-search` query used in Memo exists),
  switch 전체/국장/미장, open a settings section, run read-only navigation.
- **Connection state**: WS loss is only a small dot (`conn-dot`); show a toast/banner with "reconnecting..." and stop
  showing stale numbers as live.
- **Optimistic updates** for watchlist add/remove, strategy toggles and memo cards (today every mutation
  invalidates everything, see #8).
- **Logs**: filter chips + search instead of a `<select>`, sticky header, relative times; the table is not usable
  on 375 px.
- **Market switch**: content is on screen in 30-180 ms but the View Transition keeps animating to ~300-380 ms;
  a shorter (150-200 ms) cross-fade would feel snappier.
<!-- MANUAL:END -->
