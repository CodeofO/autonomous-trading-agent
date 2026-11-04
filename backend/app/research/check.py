"""네이버 뉴스 검색 키 점검: 실제 검색 1회로 키가 맞는지 확인한다 (D-44).

    python -m app.research.check          # 또는 ./run.sh news-check

검색 API 하루 한도는 25,000회다. NAVER API HUB 는 종량제지만 공식 문서상 현재 한시 무료다. 키는 출력하지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.research.naver import LABEL, NaverError, NaverNewsClient


async def check(c: NaverNewsClient, query: str) -> bool:
    try:
        items = await c.search(query, display=3)
    except NaverError as e:
        print(f"  ❌ {e}")
        return False
    finally:
        await c.http.aclose()
    print(f"  ✅ “{query}” 최신 뉴스 {len(items)}건")
    for it in items:
        print(f"     · {it['title'][:60]}")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="네이버 뉴스 검색 키 점검 (실제 검색 1회, 무료)")
    ap.add_argument("query", nargs="?", default="코스피")
    args = ap.parse_args(argv)
    s = get_settings()
    setup_logging("ERROR", s.secret_values())
    if not s.has_naver:
        print(".env 에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 를 채운 뒤 다시 실행하세요.")
        return 2
    assert s.naver_client_id and s.naver_client_secret
    print(f"대상: {LABEL[s.naver_api]} 뉴스 검색 (NAVER_API={s.naver_api})")
    c = NaverNewsClient(
        s.naver_client_id.get_secret_value(), s.naver_client_secret.get_secret_value(), api=s.naver_api
    )
    return 0 if asyncio.run(check(c, args.query)) else 1


if __name__ == "__main__":
    sys.exit(main())
