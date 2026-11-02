"""OpenDART 클라이언트 — 고유번호, 공시검색, 단일회사 전체 재무제표 (docs/DATA_API_NOTES.md §2)."""

from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import UTC, date, datetime, timedelta
from typing import Any
from xml.etree import ElementTree

from sqlalchemy import delete, func, select

from app.core.http import Route, SafeHttpClient
from app.db.models import ApiCache, CorpCode
from app.db.session import Database

log = logging.getLogger(__name__)

HOST = "opendart.fss.or.kr"
BASE = f"https://{HOST}/api"
ROUTES = [Route(HOST, "GET", re.compile(r"/api/(corpCode\.xml|list\.json|fnlttSinglAcntAll\.json)"))]

REPORT_NAMES = {"11013": "1분기", "11012": "반기", "11014": "3분기", "11011": "사업보고서"}

# (키, account_id 후보, account_nm 후보, 재무제표 구분)
_ACCOUNTS: list[tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = [
    ("revenue", ("ifrs-full_Revenue",), ("매출액", "수익(매출액)", "영업수익", "매출"), ("IS", "CIS")),
    ("operating_income", ("dart_OperatingIncomeLoss",), ("영업이익", "영업이익(손실)"), ("IS", "CIS")),
    ("net_income", ("ifrs-full_ProfitLoss",), ("당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익"),
     ("IS", "CIS")),
    ("total_assets", ("ifrs-full_Assets",), ("자산총계",), ("BS",)),
    ("total_liabilities", ("ifrs-full_Liabilities",), ("부채총계",), ("BS",)),
    ("total_equity", ("ifrs-full_Equity",), ("자본총계",), ("BS",)),
    ("operating_cash_flow", ("ifrs-full_CashFlowsFromUsedInOperatingActivities",),
     ("영업활동현금흐름", "영업활동으로 인한 현금흐름"), ("CF",)),
    ("capex", ("ifrs-full_PurchaseOfPropertyPlantAndEquipment",), ("유형자산의 취득",), ("CF",)),
    ("depreciation", ("ifrs-full_AdjustmentsForDepreciationExpense", "ifrs-full_DepreciationExpense"),
     ("감가상각비",), ("CF", "IS", "CIS")),
]


class DartError(RuntimeError):
    pass


def _amount(v: Any) -> float | None:
    if v is None:
        return None
    t = str(v).replace(",", "").strip()
    if t in ("", "-"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def extract_summary(rows: list[dict[str, Any]], field: str = "thstrm_amount") -> dict[str, float | None]:
    """전체 재무제표 행에서 주요 계정을 뽑는다. account_id 우선, 없으면 계정명."""
    out: dict[str, float | None] = {}
    for key, ids, names, divs in _ACCOUNTS:
        val: float | None = None
        cands = [r for r in rows if r.get("sj_div") in divs]
        for r in cands:
            if r.get("account_id") in ids:
                val = _amount(r.get(field))
                if val is not None:
                    break
        if val is None:
            for r in cands:
                nm = str(r.get("account_nm", "")).replace(" ", "")
                if any(nm == n.replace(" ", "") for n in names) or (key == "depreciation" and "감가상각비" in nm):
                    val = _amount(r.get(field))
                    if val is not None:
                        break
        out[key] = val
    return out


class DartClient:
    def __init__(self, api_key: str, db: Database, http: SafeHttpClient | None = None) -> None:
        self._key = api_key.strip()
        self.db = db
        self.http = http or SafeHttpClient(ROUTES, rate_per_sec=5)

    async def _json(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        r = await self.http.request("GET", f"{BASE}/{endpoint}", params={"crtfc_key": self._key, **params})
        if r.status_code != 200:
            raise DartError(f"DART HTTP {r.status_code}")
        data: dict[str, Any] = r.json()
        status = str(data.get("status", ""))
        if status not in ("000", "013"):
            raise DartError(f"DART 오류 {status}: {data.get('message', '')}")
        return data

    # ------------------------------------------------------------ 고유번호
    async def refresh_corp_codes(self, force: bool = False) -> int:
        with self.db.session() as s:
            n = s.scalar(select(func.count()).select_from(CorpCode)) or 0
        if n > 0 and not force:
            return n
        r = await self.http.request("GET", f"{BASE}/corpCode.xml", params={"crtfc_key": self._key})
        if r.status_code != 200 or not r.content.startswith(b"PK"):
            raise DartError("DART 고유번호 ZIP 을 받지 못했습니다(키 확인 필요)")
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            xml_bytes = zf.read(zf.namelist()[0])
        root = ElementTree.fromstring(xml_bytes)
        rows = []
        for el in root.iter("list"):
            stock = (el.findtext("stock_code") or "").strip()
            if stock:
                rows.append(CorpCode(stock_code=stock, corp_code=(el.findtext("corp_code") or "").strip(),
                                     corp_name=(el.findtext("corp_name") or "").strip()))
        with self.db.session() as s:
            s.execute(delete(CorpCode))
            s.add_all(rows)
        log.info("DART 고유번호 %d건 갱신", len(rows))
        return len(rows)

    async def corp_code(self, stock_code: str) -> str | None:
        await self.refresh_corp_codes()
        with self.db.session() as s:
            row = s.get(CorpCode, stock_code)
            return row.corp_code if row else None

    # ------------------------------------------------------------ 공시
    async def disclosures(self, stock_code: str, today: date, days: int = 30) -> list[dict[str, Any]]:
        corp = await self.corp_code(stock_code)
        if corp is None:
            return []
        data = await self._json(
            "list.json",
            {"corp_code": corp, "bgn_de": (today - timedelta(days=days)).strftime("%Y%m%d"),
             "end_de": today.strftime("%Y%m%d"), "page_count": 30},
        )
        return [
            {k: it.get(k) for k in ("report_nm", "rcept_dt", "rcept_no", "flr_nm", "corp_name", "rm")}
            for it in data.get("list", [])
        ]

    # ------------------------------------------------------------ 재무제표
    async def _statement(self, corp: str, year: int, reprt: str) -> tuple[str, list[dict[str, Any]]] | None:
        for fs_div in ("CFS", "OFS"):
            key = f"dart:fs:{corp}:{year}:{reprt}:{fs_div}"
            with self.db.session() as s:
                c = s.get(ApiCache, key)
                fresh = c is not None and (
                    c.payload.get("rows") or datetime.now(UTC) - _aware(c.fetched_at) < timedelta(days=1)
                )
                if c is not None and fresh:
                    rows: list[dict[str, Any]] = c.payload.get("rows", [])
                    if rows:
                        return fs_div, rows
                    continue
            data = await self._json(
                "fnlttSinglAcntAll.json", {"corp_code": corp, "bsns_year": str(year), "reprt_code": reprt,
                                           "fs_div": fs_div},
            )
            rows = data.get("list", []) if data.get("status") == "000" else []
            with self.db.session() as s:
                s.merge(ApiCache(key=key, payload={"rows": rows}, fetched_at=datetime.now(UTC)))
            if rows:
                return fs_div, rows
        return None

    async def financials(self, stock_code: str, today: date) -> dict[str, Any]:
        corp = await self.corp_code(stock_code)
        if corp is None:
            return {"symbol": stock_code, "available": False, "reason": "DART 고유번호 없음"}
        reports: list[dict[str, Any]] = []
        annual_found = False
        candidates: list[tuple[int, str]] = []
        for y in (today.year, today.year - 1, today.year - 2):
            candidates += [(y, "11014"), (y, "11012"), (y, "11013"), (y, "11011")]
        candidates.sort(key=lambda x: (x[0], {"11013": 1, "11012": 2, "11014": 3, "11011": 4}[x[1]]), reverse=True)
        for year, reprt in candidates:
            if len(reports) >= 2 or (reports and annual_found):
                break
            if reports and reprt != "11011":
                continue  # 최신 보고서 하나 + 최근 사업보고서 하나
            got = await self._statement(corp, year, reprt)
            if got is None:
                continue
            fs_div, rows = got
            cur = extract_summary(rows, "thstrm_amount")
            prior = extract_summary(rows, "frmtrm_q_amount" if reprt != "11011" else "frmtrm_amount")
            reports.append({"period": f"{year} {REPORT_NAMES[reprt]}", "type": "A" if reprt == "11011" else "Q",
                            "fs_div": fs_div, **cur, "prior": prior})
            annual_found = annual_found or reprt == "11011"
        if not reports:
            return {"symbol": stock_code, "available": False, "reason": "공시된 재무제표 없음"}
        return {"symbol": stock_code, "available": True, "source": "DART", "reports": reports}


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)
