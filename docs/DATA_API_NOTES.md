# 외부 데이터 API 조사 노트

> 조사일: 2026-11-02. 모두 **공식 문서**에서 확인한 내용만 적었다. 비공식 라이브러리(pykrx, FinanceDataReader 등)는 쓰지 않는다.
> 확인하지 못한 부분은 `TODO(verify)` 로 표시했고, 코드에서는 관대한 파싱(lenient parsing)과 Mock 으로 대체한다.
>
> 시세는 토스증권 Open API(조회 전용)가 기본이다 — [TOSS_API_NOTES.md](TOSS_API_NOTES.md). 이 문서는 나머지 공식 API 를 다룬다.

## 0. 구성 요약

| 용도 | 출처 | 키 | 비고 |
|---|---|---|---|
| 주가(실시간·일봉) · 종목 · 랭킹 · 캘린더 | **토스증권 Open API (조회 전용, 기본)** — [TOSS_API_NOTES.md](TOSS_API_NOTES.md) | `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET` | 허용 IP 등록 필요 |
| 주가(일봉) · 시가총액 (토스 대체) | 공공데이터포털 **금융위원회_주식시세정보** | `DATA_GO_KR_SERVICE_KEY` | **T+1**: 기준일 다음 영업일 13시 이후 공개 |
| 재무제표 · 공시 · 기업코드 | 금융감독원 **OpenDART** | `DART_API_KEY` | 2015년 이후 재무제표 |
| 뉴스 | **네이버 검색 API**(뉴스) | `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | 하루 25,000회 |
| 판단 | 사용자가 지정한 **OpenAI 호환 Chat Completions** 엔드포인트 | `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | tool calling 사용, 미지원 시 JSON 모드 |
| 주문 · 체결 · 잔고 | **자체 PaperBroker**(가상 계좌) | 없음 | 외부 주문 API 없음 |

키가 하나도 없어도 Mock 데이터와 규칙 기반 에이전트로 전체가 동작한다.

## 1. 금융위원회_주식시세정보 (공공데이터포털)

- 상세: <https://www.data.go.kr/data/15094808/openapi.do> (페이지에 포함된 Swagger 2.0 정의로 확인)
- 요청: `GET https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2`
- 공통 파라미터: `serviceKey`(필수), `numOfRows`, `pageNo`, `resultType`(`xml` 기본 / `json`)
- 필터: `basDt`(일치) · `beginBasDt` · `endBasDt` · `likeBasDt`, `likeSrtnCd`, `isinCd`, `likeIsinCd`, `itmsNm`, `likeItmsNm`, `mrktCls`,
  `beginVs/endVs`, `beginFltRt/endFltRt`, `beginTrqu/endTrqu`, `beginTrPrc/endTrPrc`, `beginMrktTotAmt/endMrktTotAmt`, `beginLstgStCnt/endLstgStCnt`
- 응답: `response.header.{resultCode,resultMsg}`, `response.body.{numOfRows,pageNo,totalCount,items.item[]}`
- item 필드:

| 필드 | 의미 |
|---|---|
| `basDt` | 기준일자(YYYYMMDD) |
| `srtnCd` | 단축코드(6자리) |
| `isinCd` | ISIN |
| `itmsNm` | 종목명 |
| `mrktCtg` | 시장 구분 (KOSPI/KOSDAQ/KONEX) |
| `clpr` | 종가 |
| `vs` / `fltRt` | 전일 대비 / 등락률 |
| `mkp` / `hipr` / `lopr` | 시가 / 고가 / 저가 |
| `trqu` / `trPrc` | 거래량 / 거래대금 |
| `lstgStCnt` | 상장주식수 |
| `mrktTotAmt` | 시가총액 (종가 × 상장주식수) |

- **갱신**: 일 1회, 기준일자 다음 영업일 13시 이후. 금요일 데이터는 월요일에 공개.
- **이용조건**: 공공누리 제4유형(출처표시 + 상업적 이용금지 + 변경금지), 제3자 재배포 금지 → 이 시스템은 **개인 로컬용**으로만 쓰고, 데이터를 외부에 배포하지 않는다.
- `TODO(verify)`: Swagger 상 일부 숫자 필드 type 이 string/number 로 섞여 있다. 실제 JSON 은 문자열일 수 있으므로 코드는 둘 다 받아들인다. `items.item` 이 1건일 때 객체로 오는지 배열로 오는지도 확인되지 않아 둘 다 처리한다.
- `TODO(verify)`: 트래픽 한도는 문서에 명시되지 않았다(개발계정 기본값은 포털 마이페이지에서 확인). 클라이언트는 초당 5회 토큰 버킷으로 보수적으로 제한한다.

## 2. OpenDART

- 가이드: <https://opendart.fss.or.kr/guide/main.do>
- 공통: `crtfc_key`(40자리) 쿼리 파라미터, 응답 `status`(`000` 정상), `message`

| API | 요청 | 주요 파라미터 | 비고 |
|---|---|---|---|
| 고유번호 | `GET https://opendart.fss.or.kr/api/corpCode.xml` | `crtfc_key` | **ZIP(바이너리)** 안에 XML. 필드 `corp_code`(8), `corp_name`, `corp_eng_name`, `stock_code`(6, 상장사만), `modify_date` |
| 공시검색 | `GET https://opendart.fss.or.kr/api/list.json` | `corp_code`, `bgn_de`, `end_de`, `pblntf_ty`(A~J), `page_no`, `page_count`(≤100) | 응답 list: `corp_name`, `stock_code`, `report_nm`, `rcept_no`, `flr_nm`, `rcept_dt`, `rm` |
| 단일회사 전체 재무제표 | `GET https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json` | `corp_code`, `bsns_year`(2015~), `reprt_code`(`11013` 1분기 · `11012` 반기 · `11014` 3분기 · `11011` 사업보고서), `fs_div`(`CFS` 연결 · `OFS` 별도) | list: `sj_div`(BS/IS/CIS/CF/SCE), `account_id`, `account_nm`, `thstrm_amount`, `frmtrm_amount`, `thstrm_add_amount`, `currency` 등. 분기·반기 IS 의 `thstrm_amount` 는 **3개월치** |

- 상태코드: `000` 정상, `010` 미등록 키, `011` 사용 정지 키, `012` 허용되지 않은 IP, `013` 데이터 없음, `020` 요청 제한 초과(일반적으로 20,000건 이상), `100` 필드 오류, `800` 점검, `900` 정의되지 않은 오류
- 재무 지표 추출: `account_id` 우선(IFRS 표준 ID), 없으면 `account_nm` 키워드로 찾는다(매출액/수익(매출액), 영업이익, 당기순이익, 자산총계, 부채총계, 자본총계, 영업활동현금흐름, 유형자산의 취득, 감가상각비).
  - `TODO(verify)`: 회사마다 계정명이 달라 감가상각비 같은 일부 항목은 비어 있을 수 있다. 비어 있으면 LLM 에게 "N/A" 로 전달한다.

## 3. 네이버 검색 API — 뉴스

- 문서: <https://developers.naver.com/docs/serviceapi/search/news/news.md>
- 요청: `GET https://openapi.naver.com/v1/search/news.json`
- 헤더: `X-Naver-Client-Id`, `X-Naver-Client-Secret`
- 파라미터: `query`(필수, UTF-8), `display`(기본 10, 최대 100), `start`(기본 1, 최대 1000), `sort`(`sim` 정확도 기본 · `date` 최신순)
- 응답: `lastBuildDate`, `total`, `start`, `display`, `items[]`: `title`, `originallink`, `link`, `description`, `pubDate`
  - `title`/`description` 의 검색어 일치 부분은 `<b>` 태그로 감싸져 있어 제거해야 한다.
- 한도: 검색 API **하루 25,000회**(클라이언트 아이디별 합산)
- 오류: `SE01`(잘못된 쿼리) · `SE02`(display) · `SE03`(start) · `SE04`(sort) 등 400 계열

## 4. LLM (OpenAI 호환 Chat Completions)

사용자는 `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` 만 지정한다. 이 세 값만으로 붙일 수 있는 사실상의 표준은 OpenAI 호환 API 다.

- 요청: `POST {LLM_BASE_URL}/chat/completions`, 헤더 `Authorization: Bearer {LLM_API_KEY}`
- 본문: `model`, `messages`, `tools`(function), `tool_choice`, `temperature`, `max_tokens`
- 응답: `choices[0].message.{content, tool_calls[].{id, function.{name, arguments}}}`, `usage`
- 제공자가 tool calling 을 지원하지 않으면 `LLM_TOOL_MODE=json` 으로 바꾼다. `auto`(기본)는 tool 호출 요청이 400 으로 거부되면 자동으로 JSON 모드로 전환한다.
- `TODO(verify)`: 제공자마다 `max_tokens`/`max_completion_tokens` 지원 여부가 다르다. 기본은 `max_tokens` 를 보내고 `LLM_MAX_TOKENS=0` 이면 생략한다.

## 5. 모의 체결 규칙 (데이터 제약에서 도출)

공식 시세가 T+1 일봉뿐이라 장중 실시간 가격이 없다. 그래서 PaperBroker 는 **"다음 공식 가격"** 으로 체결한다.

| 주문 접수 시각 (거래일 D, KST) | 체결 가격 | 확정 시점 |
|---|---|---|
| 08:30 ~ 09:00 (장 시작 전) | D 시가(`mkp`) | D+1 13시 이후 D 시세 공개 시 |
| 09:00 ~ 15:20 (장중 연속매매) | D 종가(`clpr`) | 동일 |
| 그 외 시간 / 휴장일 | **접수 거부** (장 운영시간 외 주문 금지) | — |

- 지정가: 시가 체결이면 `시가 ≤ 지정가`(매수)일 때 시가로, 아니면 `저가 ≤ 지정가`일 때 지정가로 체결한다. 종가 체결이면 종가가 지정가 조건을 만족할 때만 체결한다. 미체결분은 당일 종료 시 취소한다.
- 유동성 상한: 한 주문의 체결 수량은 그날 거래량의 `PAPER_MAX_VOLUME_PARTICIPATION`(기본 5%) 이하다. 초과분은 부분체결(PARTIALLY_FILLED) 후 취소(CANCELED)된다.
- 수수료 0.015%, 매도 거래세 0.20%(`TODO(verify)`: 시장·연도별 세율 확인 필요), 슬리피지 5bp 는 설정값이다.
