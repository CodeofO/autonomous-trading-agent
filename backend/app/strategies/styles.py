"""투자 전략 카탈로그 (D-62): 매니저가 판단할 때 따르는 투자 철학.

- 시장(국장·미장)마다 **주 계좌 전략** 하나를 고른다. 주 계좌는 실거래로 돌릴 수 있는 유일한 계좌다.
- 나머지 전략은 '전략 실험실'에서 시나리오로 켜면 각자 가상(모의) 계좌로 같은 시각에 판단한다 — 실거래는 막혀 있다.
- 전략은 프롬프트의 '판단 원칙(투자 정책)' 자리에 들어간다. 리스크 한도(종목 비중·주문 금액·손절/익절·일일 손실)는
  전략과 관계없이 시스템이 코드로 강제한다.

새 전략은 아래 STYLES 에 한 항목을 더하면 된다 (id 는 영문 소문자, 시나리오 DB 파일 이름에 쓰인다).
글은 각 투자자가 책·주주서한에서 공개적으로 밝힌 원칙을 요약한 것이며, 숫자 기준은 매니저가 판단할 때 쓰는 목표치다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_ID = "default"

_KR_DATA = (
    "- 자료: 재무는 get_financials(DART), 공시는 get_disclosures, 후보 찾기는 screen_stocks·explore_sector, "
    "가격 추세는 get_price_history, 뉴스는 search_news 로 확인한다. 확인하지 못한 숫자는 지어내지 않는다."
)
_US_DATA = (
    "- 자료: 미장에는 재무제표 도구가 없다. 실적·밸류에이션은 search_news 기사와 get_price_history·screen_stocks 로 "
    "확인하고, 확인하지 못한 숫자는 지어내지 않는다 (모르면 모른다고 rationale 에 쓴다)."
)


@dataclass(frozen=True)
class Style:
    id: str
    name: str  # 화면 이름
    emoji: str
    persona: str  # 시나리오 매니저 이름
    summary: str  # 한 줄 설명
    principles: str  # 판단 원칙 (시장 공통)
    tags: tuple[str, ...] = ()

    def policy(self, us: bool) -> str:
        market = "미국 주식·ETF 모의계좌(달러)" if us else "국내 주식 계좌"
        return (
            f"- 전략: {self.name} — {self.summary} ({market})\n"
            f"{self.principles.rstrip()}\n{_US_DATA if us else _KR_DATA}\n"
        )

    def as_dict(self, us: bool = False) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "emoji": self.emoji,
            "persona": self.persona,
            "summary": self.summary,
            "tags": list(self.tags),
            "policy": None if self.id == DEFAULT_ID else self.policy(us),
        }


STYLES: dict[str, Style] = {
    s.id: s
    for s in (
        Style(
            id=DEFAULT_ID,
            name="기본 매니저",
            emoji="🦉",
            persona="알파",
            summary="펀더멘털·뉴스·추세를 함께 보는 중기 균형 운용 (설정 > 매니저의 투자 정책 문구)",
            principles="",
            tags=("균형",),
        ),
        Style(
            id="lynch",
            name="피터 린치",
            emoji="🧺",
            persona="린치",
            summary="이해하기 쉬운 사업 중 이익 성장에 비해 아직 싼 종목(GARP)",
            tags=("성장주", "가치"),
            principles="""\
- 종목을 6가지 유형(저성장·대형 우량·고성장·경기순환·회생·자산주)으로 나누고, 유형마다 기대 수익과 매도 기준을 다르게 둔다.
- 가격 대비 성장: PEG(PER ÷ 연 이익 성장률)가 1 이하인 종목을 선호하고 2 이상은 피한다. 이익이 연 15~25% 꾸준히 크는
  회사를 특히 찾는다 (지나치게 빠른 성장은 오래가지 못할 수 있어 의심한다).
- 부채가 적고 현금이 넉넉한 회사, 재고가 매출보다 빨리 늘지 않는 회사를 고른다. 유행 업종의 인기주는 피한다.
- '2분 이야기': 왜 사는지를 두 문장으로 설명할 수 없으면 사지 않는다 — rationale 에 그 이야기를 쓴다.
- 이야기가 유지되는 동안은 주가가 흔들려도 팔지 않는다. 성장이 꺾이거나 PEG 가 지나치게 높아지거나 이야기가 깨지면 판다.
- 분산: 유형을 섞어 5~10종목. 확신이 낮으면 HOLD.""",
        ),
        Style(
            id="buffett",
            name="워런 버핏",
            emoji="🏰",
            persona="버핏",
            summary="경제적 해자가 있는 훌륭한 회사를 적정 가격에 사서 오래 보유",
            tags=("가치주", "장기"),
            principles="""\
- 이해할 수 있는 사업만 산다. 오래가는 경쟁 우위(경제적 해자: 브랜드·네트워크·전환 비용·원가 우위)가 있는지 먼저 본다.
- 수익성과 재무: 자기자본이익률(ROE)이 여러 해 15% 안팎 이상으로 꾸준하고, 이익률이 높고, 부채가 적고,
  잉여현금흐름이 꾸준한 회사.
- 가격: 훌륭한 회사도 내재가치보다 충분히 싸게(안전마진) 살 수 있을 때만 산다. 비싸면 현금을 들고 기다리는 것도 결정이다.
- 집중과 장기 보유: 확신이 높은 3~8종목에 크게 담고 오래 갖고 간다. 단기 뉴스·주가 등락으로 사고팔지 않는다(회전율 최소).
- 매도: 해자가 무너졌을 때, 경영진을 믿을 수 없게 됐을 때, 터무니없이 비싸졌을 때만.
- 남들이 겁낼 때 좋은 회사를 사고, 남들이 욕심낼 때 조심한다.""",
        ),
        Style(
            id="graham",
            name="벤저민 그레이엄",
            emoji="📏",
            persona="그레이엄",
            summary="숫자로 확인되는 깊은 저평가 종목을 넓게 분산 (안전마진)",
            tags=("가치주", "분산"),
            principles="""\
- 이야기보다 숫자. 방어적 투자자 기준: PER 15 이하, PBR 1.5 이하(또는 PER×PBR 22.5 이하), 여러 해 흑자,
  배당을 꾸준히 준 이력, 유동비율이 높고 부채가 적은 회사.
- 안전마진: 보수적으로 잡은 내재가치보다 확실히 쌀 때만 산다. 인기주·고평가 성장주는 사지 않는다.
- 넓은 분산: 10종목 이상, 한 종목 비중은 작게 — 개별 종목의 실수를 분산으로 흡수한다.
- 매도: 주가가 내재가치에 이르렀거나(대략 50% 안팎 상승), 오래 지나도 저평가가 풀리지 않고 근거가 약해졌을 때.
- 시장 분위기(공포·탐욕)에 휩쓸리지 않는다. '미스터 마켓'이 싸게 팔 때만 산다.""",
        ),
        Style(
            id="momentum",
            name="모멘텀·추세",
            emoji="🚀",
            persona="모멘텀",
            summary="시장이 오를 때 실적이 가속하는 주도주를 사고, 손실은 짧게 자른다",
            tags=("추세", "성장주"),
            principles="""\
- 시장 방향이 먼저: 지수(국장 코스피·코스닥, 미장 QQQ·SPY)가 상승 추세일 때만 새로 산다. 하락 추세면 현금 비중을 늘린다.
- 이익·매출 증가가 빨라지는 업종 주도주를 고른다. 52주 신고가 근처에서 거래량이 늘며 오르는 종목을 선호한다.
- 손실은 짧게: 매수가 대비 7~8% 떨어지면 이유를 따지지 않고 판다 (리스크 엔진 손절과 별개로 판단에 반영).
- 강한 종목에 집중하고 약한 종목은 빨리 정리한다. 물타기(떨어진 종목 추가 매수)는 하지 않는다.
- 추세가 꺾이거나(주요 이동평균 이탈) 실적 가속이 멈추면 판다.""",
        ),
    )
}


def get(style_id: str | None) -> Style | None:
    return STYLES.get(str(style_id or ""))


def catalog(us: bool = False) -> list[dict[str, Any]]:
    return [s.as_dict(us) for s in STYLES.values()]
