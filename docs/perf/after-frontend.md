# Frontend performance baseline (AFTER)

Generated 2026-11-04T16:48:40.061Z from git `80b1f74` on `main` (82 uncommitted paths under frontend/). Live app http://localhost:15173 (nginx, production build) against the running paper-trading backend; headless Chromium 156.0.8078.4. Re-run with the commands in `perf/README.md` (Frontend section); compare runs with `node perf/fe_compare.mjs`.

Market state while loading pages: `{"KR":{"enabled":true,"open_now":false,"agent_running":false,"engine_status":"RUNNING"},"US":{"enabled":true,"open_now":true,"agent_running":false,"engine_status":"RUNNING"}}`. Times below are medians of 3 cold runs (Fast 3G: 1 run).

## Headline numbers

- **Bundle**: one JS chunk 661.7 KB raw / 229.8 KB gzip, CSS 80.5 KB; route code-splitting: **yes**. Served by nginx **compressed** (— on the wire), no Cache-Control.
- **Page load (content visible, median of 13 routes)**: desktop 94 ms, mobile 4x CPU 328 ms, mobile 4x CPU + Fast 3G 3,910 ms (LCP 3,764 ms). Every route makes ~7 API requests and transfers ~162.0 KB cold.
- **Idle on overview** (desktop, 60 s): 7 API req/min, 69 React commits/min, 97 ms script + 474 ms main-thread tasks per minute, 297 style recalcs/min, 0 chart canvases re-created/min.
- **Idle on dashboard_kr** (desktop, 60 s): 6 API req/min, 66 React commits/min, 96 ms script + 696 ms main-thread tasks per minute, 274 style recalcs/min, 0 chart canvases re-created/min.
- **Idle on room_kr** (desktop, 60 s): 4 API req/min, 66 React commits/min, 77 ms script + 385 ms main-thread tasks per minute, 243 style recalcs/min, 0 chart canvases re-created/min.
- **Component renders while idle on overview**: 170 per minute (top: KstClock 64, Link 33, RelTime2 14, Layout 4).
- **Component renders while idle on dashboard_kr**: 143 per minute (top: KstClock 64, Link 7, IndicatorCard 6, Sparkline 6).
- **Component renders while idle on room_kr**: 162 per minute (top: KstClock 64, RelTime2 40, Layout 4, PaperBadge 4).
- **Market switch** (click -> new content): desktop 국장->미장 83 ms, 미장->전체 43 ms; mobile 4x 105 / 66 ms.
- **Chat scroll** (top and back, 40 px/frame): /room mobile 4x 0 frames > 50 ms (p95 16.8 ms); dashboard chat mobile 4x 0 frames > 50 ms (p95 16.7 ms).
- **Code**: 13,321 app lines + 5,939 CSS lines; 29 useQuery (6 polling), 20 React.memo; eslint 0 errors / 2 warnings; 262 tests (0 failed) in 5.9 s.

## 1. Bundle

`npx vite build --outDir perf/tmp/fe_build --emptyOutDir --sourcemap --manifest` (1.33 s). gzip level 9, brotli q11.

| chunk | type | raw | gzip | brotli | entry | dynamic | initial |
|---|---|---|---|---|---|---|---|
| assets/AccountSection-[hash].js | js | 5,886 | 2,539 | 2,202 |  | y |  |
| assets/Agent-[hash].js | js | 12,979 | 4,744 | 4,178 |  | y |  |
| assets/AlertsSection-[hash].js | js | 7,219 | 3,087 | 2,676 |  | y |  |
| assets/AnimatedNumber-[hash].js | js | 1,095 | 622 | 586 |  |  |  |
| assets/Backtest-[hash].js | js | 4,519 | 2,063 | 1,769 |  | y |  |
| assets/ChatFeed-[hash].css | css | 9,029 | 2,120 | 1,867 |  |  |  |
| assets/ChatFeed-[hash].js | js | 24,967 | 8,861 | 7,865 |  |  |  |
| assets/CommandPalette-[hash].css | css | 2,343 | 919 | 762 |  |  |  |
| assets/CommandPalette-[hash].js | js | 6,392 | 2,995 | 2,618 |  | y |  |
| assets/Costs-[hash].js | js | 15,359 | 5,549 | 4,959 |  | y |  |
| assets/Dashboard-[hash].js | js | 7,555 | 3,078 | 2,679 |  | y |  |
| assets/HoldingsTable-[hash].js | js | 2,125 | 1,011 | 865 |  |  |  |
| assets/LazyCharts-[hash].js | js | 508 | 311 | 258 |  |  |  |
| assets/LlmSection-[hash].js | js | 17,940 | 5,717 | 5,039 |  | y |  |
| assets/Login-[hash].js | js | 1,756 | 997 | 831 |  | y |  |
| assets/Logs-[hash].js | js | 1,885 | 1,025 | 874 |  | y |  |
| assets/ManagerSection-[hash].js | js | 3,968 | 1,819 | 1,531 |  | y |  |
| assets/Memo-[hash].js | js | 15,048 | 5,562 | 4,906 |  | y |  |
| assets/Overview-[hash].css | css | 6,659 | 1,897 | 1,622 |  |  |  |
| assets/Overview-[hash].js | js | 11,274 | 4,413 | 3,902 |  | y |  |
| assets/Portfolio-[hash].js | js | 5,558 | 2,029 | 1,749 |  | y |  |
| assets/RelTime-[hash].js | js | 566 | 388 | 336 |  |  |  |
| assets/RiskSection-[hash].js | js | 3,741 | 1,801 | 1,546 |  | y |  |
| assets/ScheduleSection-[hash].js | js | 7,185 | 2,964 | 2,623 |  | y |  |
| assets/SectionGate-[hash].js | js | 270 | 222 | 182 |  |  |  |
| assets/Settings-[hash].js | js | 5,499 | 2,398 | 2,090 |  | y |  |
| assets/Settings-[hash].css | css | 3,813 | 1,199 | 1,023 |  |  |  |
| assets/Sheet-[hash].css | css | 2,658 | 1,024 | 848 |  |  |  |
| assets/Sheet-[hash].js | js | 2,534 | 1,238 | 1,079 |  | y |  |
| assets/Skeleton-[hash].js | js | 649 | 381 | 347 |  |  |  |
| assets/Stock-[hash].js | js | 8,529 | 3,286 | 2,823 |  | y |  |
| assets/StockSheet-[hash].js | js | 3,829 | 1,834 | 1,613 |  | y |  |
| assets/TradingRoom-[hash].js | js | 3,417 | 1,677 | 1,432 |  | y |  |
| assets/charts-[hash].js | js | 3,572 | 1,686 | 1,523 |  | y |  |
| assets/format-[hash].js | js | 4,324 | 1,797 | 1,606 |  |  |  |
| assets/index-[hash].js | js | 50,515 | 18,712 | 16,645 | y |  | y |
| assets/index-[hash].css | css | 57,938 | 12,387 | 10,980 |  |  | y |
| assets/memo-[hash].js | js | 2,732 | 1,523 | 1,318 |  |  |  |
| assets/meta-[hash].js | js | 1,317 | 736 | 619 |  |  |  |
| assets/pullToRefresh-[hash].js | js | 1,758 | 868 | 775 |  | y |  |
| assets/schedule-[hash].js | js | 2,500 | 1,371 | 1,215 |  |  |  |
| assets/settingsQueries-[hash].js | js | 626 | 375 | 336 |  |  |  |
| assets/useDebounced-[hash].js | js | 259 | 219 | 180 |  |  |  |
| assets/vendor-charts-[hash].js | js | 168,821 | 53,699 | 46,778 |  |  |  |
| assets/vendor-react-[hash].js | js | 258,919 | 81,729 | 71,476 |  |  | y |

Code splitting: React.lazy calls 4, dynamic import() 27, dynamic chunks 23, pages imported statically in App.tsx 0.

**Bytes by package (minified, attributed through sourcemaps)**

| package | bytes | % |
|---|---|---|
| app (src/) | 237,783 | 35.1 |
| react-dom | 173,532 | 25.6 |
| lightweight-charts | 159,691 | 23.6 |
| @tanstack/query-core | 36,130 | 5.3 |
| react-router | 32,773 | 4.8 |
| [unmapped] | 14,095 | 2.1 |
| fancy-canvas | 9,079 | 1.3 |
| react | 8,234 | 1.2 |
| scheduler | 3,678 | 0.5 |
| @tanstack/react-query | 2,639 | 0.4 |

**Largest app source files in the bundle**

| file | bytes | % of bundle |
|---|---|---|
| src/pages/settings/LlmSection.tsx | 17,378 | 2.6 |
| src/pages/Memo.tsx | 14,664 | 2.2 |
| src/pages/Costs.tsx | 13,707 | 2 |
| src/pages/Agent.tsx | 12,670 | 1.9 |
| src/pages/Overview.tsx | 10,489 | 1.5 |
| src/components/Layout.tsx | 10,286 | 1.5 |
| src/components/chat/SessionBlock.tsx | 8,350 | 1.2 |
| src/pages/Stock.tsx | 8,186 | 1.2 |
| src/pages/settings/AlertsSection.tsx | 6,703 | 1 |
| src/pages/ScheduleSettings.tsx | 6,480 | 1 |

**What the live server sends**

| asset | Content-Encoding | Cache-Control | bytes on wire |
|---|---|---|---|
| /assets/index-[hash].js | gzip | public, max-age=31536000, immutable | — |
| /assets/vendor-react-[hash].js | gzip | public, max-age=31536000, immutable | — |
| /assets/index-[hash].css | gzip | public, max-age=31536000, immutable | — |

## 2. Page load (cold, fresh browser context per run)

`content` = first animation frame in which the route's data-dependent selector is present (see `ROUTES` in perf/fe_lib.mjs). `idle` = no /api request in flight for 1.5 s. Long tasks / CDP metrics cover navigation -> idle. Bytes = encoded bytes over the wire incl. headers.

### desktop (CPU x1)

| route | path | FCP | LCP | content | DCL | load | idle | CLS | long tasks (ms) | API req | KB | heap MB | DOM nodes | script ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| overview | `/?m=all` | 84 | 112 | 191 | 33 | 38 | 1,745 | 0.052 | 0 (0) | 9 | 154 | 3.3 | 552 | 42 |
| dashboard_kr | `/?m=kr` | 88 | 156 | 113 | 31 | 37 | 1,658 | 0.012 | 0 (0) | 9 | 233 | 5.2 | 3,209 | 64 |
| room_kr | `/room` | 80 | 384 | 98 | 30 | 36 | 1,644 | 0.002 | 0 (0) | 7 | 174 | 3.4 | 982 | 42 |
| portfolio_kr | `/portfolio` | 80 | 232 | 196 | 30 | 35 | 1,757 | 0.002 | 0 (0) | 8 | 162 | 3.9 | 2,678 | 49 |
| agent_kr | `/agent` | 76 | 96 | 75 | 29 | 35 | 1,647 | 0.002 | 0 (0) | 5 | 140 | 3 | 692 | 37 |
| settings_kr | `/settings` | 84 | 112 | 94 | 31 | 37 | 1,643 | 0.002 | 0 (0) | 7 | 159 | 3.3 | 619 | 42 |
| memo_kr | `/memo` | 76 | 88 | 76 | 30 | 36 | 1,650 | 0.001 | 0 (0) | 5 | 136 | 2.9 | 373 | 38 |
| costs | `/costs` | 72 | 88 | 79 | 28 | 33 | 1,646 | 0.002 | 0 (0) | 5 | 135 | 3.2 | 899 | 41 |
| stock_kr | `/stock/005930` | 80 | 108 | 91 | 30 | 36 | 2,052 | 0.01 | 0 (0) | 7 | 205 | 4.4 | 996 | 62 |
| logs_kr | `/logs` | 76 | 104 | 90 | 28 | 34 | 1,644 | 0.002 | 0 (0) | 5 | 164 | 3.4 | 2,225 | 42 |
| dashboard_us | `/?m=us` | 88 | 140 | 104 | 26 | 33 | 1,648 | 0.011 | 0 (0) | 9 | 205 | 4.1 | 489 | 56 |
| room_us | `/room?m=us` | 84 | 112 | 95 | 30 | 36 | 1,646 | 0.002 | 0 (0) | 7 | 144 | 3.1 | 540 | 42 |
| stock_us | `/stock/AAPL?m=us` | 84 | 112 | 90 | 31 | 37 | 1,744 | 0.07 | 0 (0) | 7 | 195 | 4 | 356 | 53 |

### mobile_cpu4x (CPU x4)

| route | path | FCP | LCP | content | DCL | load | idle | CLS | long tasks (ms) | API req | KB | heap MB | DOM nodes | script ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| overview | `/?m=all` | 248 | 348 | 328 | 77 | 99 | 1,871 | 0 | 1 (97) | 9 | 155 | 3.4 | 559 | 156 |
| dashboard_kr | `/?m=kr` | 284 | 600 | 431 | 79 | 104 | 2,020 | 0 | 2 (210) | 9 | 234 | 5.3 | 3,181 | 259 |
| room_kr | `/room` | 264 | 372 | 357 | 79 | 100 | 1,893 | 0 | 1 (105) | 7 | 175 | 3.5 | 982 | 162 |
| portfolio_kr | `/portfolio` | 248 | 480 | 368 | 77 | 100 | 1,895 | 0 | 2 (198) | 8 | 163 | 3.9 | 2,685 | 179 |
| agent_kr | `/agent` | 240 | 272 | 272 | 76 | 99 | 1,754 | 0 | 1 (87) | 5 | 141 | 3 | 699 | 138 |
| settings_kr | `/settings` | 256 | 256 | 276 | 76 | 100 | 1,774 | 0 | 1 (99) | 4 | 156 | 2.9 | 320 | 142 |
| memo_kr | `/memo` | 232 | 312 | 266 | 77 | 100 | 1,754 | 0 | 1 (79) | 5 | 136 | 3 | 380 | 146 |
| costs | `/costs` | 232 | 352 | 332 | 78 | 100 | 1,774 | 0 | 1 (79) | 5 | 135 | 3.3 | 906 | 155 |
| stock_kr | `/stock/005930` | 248 | 684 | 325 | 77 | 100 | 2,222 | 0 | 3 (201) | 7 | 206 | 4.4 | 1,003 | 222 |
| logs_kr | `/logs` | 248 | 248 | 313 | 79 | 102 | 1,873 | 0 | 1 (93) | 5 | 166 | 3.4 | 2,232 | 168 |
| dashboard_us | `/?m=us` | 284 | 496 | 427 | 78 | 103 | 1,996 | 0 | 2 (200) | 9 | 206 | 4.2 | 496 | 209 |
| room_us | `/room?m=us` | 264 | 584 | 328 | 76 | 99 | 1,887 | 0 | 1 (109) | 7 | 145 | 3.1 | 547 | 163 |
| stock_us | `/stock/AAPL?m=us` | 248 | 248 | 295 | 76 | 99 | 1,980 | 0 | 1 (94) | 7 | 196 | 4 | 363 | 205 |

### mobile_cpu4x_fast3g (CPU x4, Fast 3G)

| route | path | FCP | LCP | content | DCL | load | idle | CLS | long tasks (ms) | API req | KB | heap MB | DOM nodes | script ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| overview | `/?m=all` | 2,564 | 3,392 | 3,372 | 1,817 | 2,566 | 5,903 | 0.397 | 1 (114) | 13 | 198 | 3.4 | 567 | 172 |
| dashboard_kr | `/?m=kr` | 3,744 | 5,084 | 4,439 | 1,827 | 2,998 | 8,372 | 0 | 3 (256) | 12 | 275 | 5.4 | 3,188 | 280 |
| room_kr | `/room` | 3,152 | 4,192 | 4,178 | 1,820 | 3,003 | 7,841 | 0.031 | 1 (105) | 10 | 275 | 4 | 993 | 193 |
| portfolio_kr | `/portfolio` | 3,116 | 4,040 | 3,910 | 1,816 | 2,980 | 7,766 | 0 | 2 (204) | 9 | 237 | 4.7 | 2,695 | 199 |
| agent_kr | `/agent` | 3,140 | 3,756 | 3,772 | 1,832 | 3,020 | 5,271 | 0.069 | 1 (90) | 5 | 141 | 3 | 699 | 151 |
| settings_kr | `/settings` | 3,168 | 3,168 | 3,752 | 1,820 | 2,981 | 5,294 | 0 | 1 (125) | 4 | 156 | 3 | 320 | 159 |
| memo_kr | `/memo` | 3,128 | 3,764 | 3,728 | 1,827 | 2,463 | 5,276 | 0.069 | 1 (80) | 5 | 136 | 3 | 380 | 156 |
| costs | `/costs` | 3,100 | 3,756 | 3,717 | 1,826 | 2,472 | 5,219 | 0.146 | 1 (88) | 5 | 135 | 3.2 | 906 | 161 |
| stock_kr | `/stock/005930` | 3,168 | 3,920 | 3,862 | 1,819 | 3,003 | 5,452 | 0.053 | 2 (148) | 7 | 206 | 4.4 | 1,003 | 231 |
| logs_kr | `/logs` | 3,108 | 3,108 | 4,181 | 1,827 | 2,479 | 7,753 | 0.069 | 1 (99) | 7 | 275 | 3.8 | 2,243 | 188 |
| dashboard_us | `/?m=us` | 3,760 | 4,940 | 4,320 | 1,819 | 2,990 | 8,370 | 0 | 2 (201) | 12 | 224 | 4.3 | 503 | 230 |
| room_us | `/room?m=us` | 3,176 | 4,308 | 4,050 | 1,819 | 3,011 | 7,823 | 0.031 | 1 (115) | 10 | 223 | 3.6 | 558 | 184 |
| stock_us | `/stock/AAPL?m=us` | 3,172 | 3,172 | 4,123 | 1,824 | 3,012 | 5,398 | 0.027 | 1 (95) | 7 | 196 | 4.1 | 363 | 218 |

API requests made by a cold load of /room (desktop, run 1): `{"/api/agent/progress":1,"/api/agent/runs":1,"/api/auth/status":1,"/api/chat":1,"/api/markets":1,"/api/system":1,"/api/us/system":1}`

## 3. Steady state (desktop, page left open)

| metric | overview | dashboard_kr | room_kr |
|---|---|---|---|
| API req/min | 7 | 6 | 4 |
| API bytes/min | 6,939 | 6,601 | 4,334 |
| WS frames/min | 4 | 4 | 5 |
| React commits/min | 69 | 66 | 66 |
| chart canvases created/min | 0 | 0 | 0 |
| long tasks/min | 0 | 0 | 0 |
| long task ms/min | 0 | 0 | 0 |
| script ms/min | 97.3 | 95.9 | 76.7 |
| main-thread task ms/min | 474.4 | 695.8 | 384.9 |
| layouts/min | 114 | 84 | 60 |
| style recalcs/min | 297 | 274 | 243 |
| heap MB start | 3.3 | 5.1 | 3.5 |
| heap MB end | 4.1 | 5.4 | 4.1 |
| DOM nodes | 574 | 3,223 | 1,000 |

- overview API calls/min by endpoint: `{"/api/system":2,"/api/us/system":2,"/api/overview":2,"/api/markets":1}`; WS by channel/min: `{"pong":4}`; market state `{"KR":{"enabled":true,"open_now":false,"agent_running":false,"engine_status":"RUNNING"},"US":{"enabled":true,"open_now":true,"agent_running":false,"engine_status":"RUNNING"}}`
- dashboard_kr API calls/min by endpoint: `{"/api/system":2,"/api/markets":1,"/api/us/system":1,"/api/portfolio":1,"/api/market/indicators":1}`; WS by channel/min: `{"pong":4}`; market state `{"KR":{"enabled":true,"open_now":false,"agent_running":false,"engine_status":"RUNNING"},"US":{"enabled":true,"open_now":true,"agent_running":false,"engine_status":"RUNNING"}}`
- room_kr API calls/min by endpoint: `{"/api/system":2,"/api/markets":1,"/api/us/system":1}`; WS by channel/min: `{"state":1,"pong":4}`; market state `{"KR":{"enabled":true,"open_now":false,"agent_running":false,"engine_status":"RUNNING"},"US":{"enabled":true,"open_now":true,"agent_running":false,"engine_status":"RUNNING"}}`

## 4. React render counts (unminified prod build via `vite preview`, DevTools-hook fiber walk)

- **overview**: load = 14 commits / 228 component renders; idle = 69 commits/min, 170 component renders/min. Re-renders/min: `{"KstClock":64,"Link":33,"RelTime2":14,"Layout":4,"PaperBadge":4,"MarketSwitch":4,"SearchButton":4,"KillSwitch":4,"ConnectionBanner":4,"PullIndicator":4,"Page":4,"RouteErrorBoundary":4,"Outlet":4,"CommandPaletteHost":4,"StockSheetHost":4,"MarketCard":4,"SessionTimeline":3,"Overview":1,"TotalCard":1,"AnimatedNumber2":1,"ActivityFeed":1}`
- **dashboard_kr**: load = 18 commits / 828 component renders; idle = 66 commits/min, 143 component renders/min. Re-renders/min: `{"KstClock":64,"Link":7,"IndicatorCard":6,"Sparkline":6,"Layout":4,"PaperBadge":4,"MarketSwitch":4,"SearchButton":4,"KillSwitch":4,"ConnectionBanner":4,"PullIndicator":4,"Page":4,"RouteErrorBoundary":4,"Outlet":4,"CommandPaletteHost":4,"StockSheetHost":4,"AnimatedNumber2":3,"AgentPanel":2,"DashboardChat":2,"Kpis":1,"EquityCard":1,"EquityChart":1,"HoldingsCard":1,"IndicatorStrip":1}`
- **room_kr**: load = 13 commits / 258 component renders; idle = 66 commits/min, 162 component renders/min. Re-renders/min: `{"KstClock":64,"RelTime2":40,"Layout":4,"PaperBadge":4,"MarketSwitch":4,"Link":4,"SearchButton":4,"KillSwitch":4,"ConnectionBanner":4,"PullIndicator":4,"Page":4,"RouteErrorBoundary":4,"Outlet":4,"CommandPaletteHost":4,"StockSheetHost":4,"TradingRoom":2,"RoomChat":2,"RunList":2}`

## 5. Interaction smoothness

**room_scroll** (programmatic scroll 40 px per animation frame to the top and back)

| profile | scroller | scroll px | rows | frames | > 50 ms | > 33 ms | p50 ms | p95 ms | max ms | avg fps | commits | long tasks |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| desktop | chat-scroll | 2,018 | 14 | 73 | 0 | 0 | 16.7 | 16.7 | 16.8 | 58.6 | 4 | 0 |
| mobile_cpu4x | chat-scroll | 2,229 | 14 | 115 | 0 | 0 | 16.7 | 16.8 | 16.8 | 59 | 5 | 0 |

**dashboard_chat_scroll** (programmatic scroll 40 px per animation frame to the top and back)

| profile | scroller | scroll px | rows | frames | > 50 ms | > 33 ms | p50 ms | p95 ms | max ms | avg fps | commits | long tasks |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| desktop | chat-scroll | 3,377 | 55 | 743 | 0 | 0 | 16.7 | 16.8 | 16.8 | 59.9 | 15 | 0 |
| mobile_cpu4x | chat-scroll | 3,479 | 55 | 1,157 | 0 | 0 | 16.7 | 16.7 | 16.8 | 59.9 | 22 | 0 |

**MarketSwitch** (start on /?m=kr; click -> aria-pressed (ui) / new content visible / view transition finished; click event duration from Event Timing)

| profile | step | ui ms | content ms | transition done ms | click event ms | commits | API req |
|---|---|---|---|---|---|---|---|
| desktop | cold_kr_to_us | 65.7 | 82.7 | 332.2 | 80 | 7 | 5 |
| desktop | cold_us_to_all | 42.7 | 42.7 | 310 | 40 | 3 | 0 |
| desktop | warm_all_to_kr | 46.7 | 46.8 | 315.7 | 32 | 2 | 0 |
| desktop | warm_kr_to_us | 31.5 | 31.5 | 280.4 | 32 | 1 | 0 |
| desktop | warm_us_to_all | 31.6 | 31.6 | 294 | 32 | 3 | 0 |
| mobile_cpu4x | cold_kr_to_us | 81.9 | 105 | 346.3 | 64 | 5 | 5 |
| mobile_cpu4x | cold_us_to_all | 65.5 | 65.5 | 315.1 | 24 | 3 | 0 |
| mobile_cpu4x | warm_all_to_kr | 65.6 | 66 | 352 | 24 | 2 | 0 |
| mobile_cpu4x | warm_kr_to_us | 48.3 | 48.3 | 313.7 | 24 | 1 | 0 |
| mobile_cpu4x | warm_us_to_all | 49 | 49 | 314.7 | 24 | 3 | 0 |

## 6. Code metrics

app 76 files / 13,321 lines, tests 25 files / 6,327 lines, CSS 7 files / 5,939 lines, e2e specs 2.

| file | kind | lines |
|---|---|---|
| src/styles.css | css | 4,215 |
| src/pages/settings/LlmSection.tsx | app | 809 |
| src/pages/Memo.tsx | app | 778 |
| src/pages/Costs.tsx | app | 685 |
| src/api/types.ts | app | 642 |
| src/components/Layout.tsx | app | 570 |
| src/__tests__/appFeel.test.tsx | test | 556 |
| src/pages/Overview.tsx | app | 498 |
| src/pages/overview.css | css | 483 |
| src/components/chat/chat-session.css | css | 479 |
| src/__tests__/llmProfile.test.tsx | test | 468 |
| src/pages/Agent.tsx | app | 455 |
| src/__tests__/memo.test.tsx | test | 444 |
| src/__tests__/market.test.tsx | test | 423 |
| src/components/chat/SessionBlock.tsx | app | 375 |
| src/pages/Stock.tsx | app | 357 |
| src/live/store.ts | app | 353 |
| src/__tests__/live.test.tsx | test | 349 |
| src/__tests__/session.test.tsx | test | 326 |
| src/components/chat/ChatFeed.tsx | app | 316 |

**Largest components** (141 components, 11 over 150 lines)

| component | at | lines |
|---|---|---|
| AgentPage | src/pages/Agent.tsx:29 | 271 |
| CommandPalette | src/components/CommandPalette.tsx:62 | 239 |
| ScheduleEditor | src/pages/ScheduleSettings.tsx:76 | 234 |
| PushSettings | src/pages/settings/AlertsSection.tsx:50 | 233 |
| TestEndpointSettings | src/pages/settings/LlmSection.tsx:337 | 230 |
| PaidModelPicker | src/pages/settings/LlmSection.tsx:604 | 206 |
| MemoCardItem | src/pages/Memo.tsx:527 | 198 |
| Stock | src/pages/Stock.tsx:173 | 185 |
| ChatFeed | src/components/chat/ChatFeed.tsx:42 | 169 |
| Backtest | src/pages/Backtest.tsx:11 | 167 |
| Layout | src/components/Layout.tsx:91 | 163 |
| ModelPanel | src/pages/Costs.tsx:367 | 149 |

**Hooks**: `{"useQuery":43,"useQueries":0,"useMutation":33,"useEffect":37,"useLayoutEffect":7,"useState":78,"useMemo":17,"useCallback":10,"react_memo":20,"react_lazy":4,"invalidateQueries":5,"invalidateQueries_all":0,"setInterval":5,"websocket":1}`

**React Query**: QueryClient defaults `{ queries: { retry: 1, staleTime: 2000 } }`; 29 useQuery calls, 6 with refetchInterval:

| at | queryKey | endpoint | refetchInterval | staleTime |
|---|---|---|---|---|
| src/auth.tsx:26 | `["auth-status"]` | /api/auth/status |  | Infinity |
| src/components/CommandPalette.tsx:74 | `api.key("market-search", term)` | /api/market/search |  | 60_000 |
| src/components/StockSheet.tsx:25 | `api.key("candles", symbol)` | /api/market/ |  | 60_000 |
| src/hooks.ts:15 | `api.key("system")` | /api/system | every | (default 2000) |
| src/hooks.ts:27 | `api.key("portfolio")` | /api/portfolio | every | (default 2000) |
| src/hooks.ts:36 | `["market-indicators"]` | /api/market/indicators | 60_000 | 55_000 |
| src/hooks.ts:47 | `["llm-costs", months]` | /api/llm/costs |  | 60_000 |
| src/market.ts:62 | `MARKETS_KEY` | /api/markets | 60_000 | 20_000 |
| src/pages/Agent.tsx:35 | `api.key("agent-structure")` | /api/agent/structure | every | (default 2000) |
| src/pages/Memo.tsx:355 | `[m.market, "market-search", term]` |  |  | 60_000 |
| src/pages/Overview.tsx:38 | `OVERVIEW_KEY` | /api/overview | 30_000 | (default 2000) |

**Type escapes (app code)**: `{"as_casts":37,"as_const":7,"as_unknown_as":1,"any":0,"non_null_assertions":2,"ts_ignore":0,"eslint_disable":0}`; tests: `{"as_casts":36,"as_const":12,"as_unknown_as":3,"any":0,"non_null_assertions":11,"ts_ignore":0,"eslint_disable":0}`

**Inline style props**: 16 (`{"src/components/Layout.tsx":{"object_literal":1,"expression":1},"src/components/LazyCharts.tsx":{"object_literal":1,"expression":0},"src/components/MarketSwitch.tsx":{"object_literal":1,"expression":0},"src/components/Sheet.tsx":{"object_literal":0,"expression":1},"src/components/Skeleton.tsx":{"object_literal":1,"expression":0},"src/components/charts.tsx":{"object_literal":1,"expression":0},"src/components/chat/ChatFeed.tsx":{"object_literal":0,"expression":1},"src/components/chat/ChatMessage.tsx":{"object_literal":1,"expression":0},"src/pages/Costs.tsx":{"object_literal":2,"expression":0},"src/pages/Overview.tsx":{"object_literal":5,"expression":0}}`); index keys: 13.

**Duplicates**: 27 duplicated 6-line windows; same name defined in several files: `CommandPalette` (src/components/CommandPalette.tsx:62, src/components/Overlays.tsx:7); `W` (src/components/IndicatorStrip.tsx:83, src/components/StockSheet.tsx:133); `H` (src/components/IndicatorStrip.tsx:84, src/components/StockSheet.tsx:134); `STATUS_KO` (src/components/Layout.tsx:65, src/utils/format.ts:198); `EquityChart` (src/components/LazyCharts.tsx:10, src/components/charts.tsx:184); `CandleChart` (src/components/LazyCharts.tsx:18, src/components/charts.tsx:88); `Sheet` (src/components/Modal.tsx:5, src/components/Sheet.tsx:31); `StockSheet` (src/components/Overlays.tsx:8, src/components/StockSheet.tsx:19); `listeners` (src/components/RelTime.tsx:10, src/ui/toast.ts:24); `subscribe` (src/components/RelTime.tsx:12, src/ui/toast.ts:69); `won` (src/components/charts.tsx:29, src/utils/format.ts:1); `LEAD_EMOJI` (src/components/chat/ChatFeed.tsx:228, src/components/chat/SessionBlock.tsx:22); `fmtTokens` (src/components/chat/ChatFeed.tsx:230, src/utils/format.ts:180); `is404` (src/market.ts:58, src/pages/ScheduleSettings.tsx:22, src/pages/settingsQueries.ts:8); `fmtUsd` (src/utils/format.ts:154, src/utils/llm.ts:80); `pad` (src/utils/memo.ts:90, src/utils/schedule.ts:21). Format helpers outside src/utils: `formats` src/components/IndicatorStrip.tsx:9, `formatFor` src/components/IndicatorStrip.tsx:10, `won` src/components/charts.tsx:29, `priceFormatter` src/components/charts.tsx:33, `pricePrecision` src/components/charts.tsx:35, `fmtTokens` src/components/chat/ChatFeed.tsx:230, `fmtElapsed` src/components/chat/ChatFeed.tsx:234, `fmtMs` src/components/chat/ChatMessage.tsx:148, `fmtDuration` src/components/chat/sessions.ts:235, `pct` src/pages/Agent.tsx:26, `wonRounded` src/pages/Overview.tsx:30.

**CSS**

| file | bytes | lines | rules | selectors | declarations | @media | @keyframes | infinite anims | !important | custom props |
|---|---|---|---|---|---|---|---|---|---|---|
| src/components/CommandPalette.css | 3,082 | 156 | 22 | 24 | 91 | 2 | 2 | 0 | 0 | 0 |
| src/components/Sheet.css | 3,517 | 193 | 32 | 35 | 98 | 1 | 4 | 0 | 0 | 0 |
| src/components/chat/chat-activity.css | 3,096 | 146 | 28 | 28 | 76 | 2 | 0 | 0 | 0 | 5 |
| src/components/chat/chat-session.css | 8,948 | 480 | 84 | 87 | 275 | 3 | 0 | 0 | 0 | 6 |
| src/pages/overview.css | 8,764 | 484 | 86 | 98 | 273 | 3 | 0 | 1 | 0 | 1 |
| src/pages/settings/settings.css | 5,202 | 271 | 48 | 51 | 140 | 2 | 3 | 0 | 0 | 0 |
| src/styles.css | 81,946 | 4,216 | 729 | 876 | 2,249 | 22 | 18 | 14 | 0 | 34 |

**eslint**: 0 errors, 2 warnings over 107 files (1.38 s) `{"react-refresh/only-export-components":2}`; prettier unformatted files: 0; tsc errors: 0 (2.54 s).

**vitest**: 23 files, 262 tests, 0 failed, wall 5.86 s. Slowest files: src/__tests__/appFeel.test.tsx 4410 ms, src/__tests__/live.test.tsx 1932 ms, src/__tests__/memo.test.tsx 1228 ms, src/__tests__/schedule.test.tsx 790 ms, src/__tests__/activity.test.tsx 719 ms.

<!-- MANUAL:BEGIN -->
## 7. Hotspots and observations (hand-written, kept across rebuilds)

_(fill in)_
<!-- MANUAL:END -->
