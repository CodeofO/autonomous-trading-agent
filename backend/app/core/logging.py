"""마스킹 로깅.

- 등록된 비밀값(키/시크릿/토큰)은 앞 4자리만 남기고 `abcd****` 로 치환한다.
- Bearer 토큰, client_secret=..., access_token 필드 패턴도 값과 무관하게 마스킹한다.
"""

from __future__ import annotations

import logging
import re
import threading

_lock = threading.Lock()
_secrets: set[str] = set()

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(Bearer\s+)([A-Za-z0-9._\-~+/=]{4,})", re.IGNORECASE),
    re.compile(r"([?&](?:token|serviceKey|crtfc_key)=)([^&\s\"']+)", re.IGNORECASE),
    re.compile(
        r"((?:client_secret|client_id|access_token|password)[\"']?\s*[=:]\s*[\"']?)([^\s&\"',}]{1,})",
        re.IGNORECASE,
    ),
]


def mask(value: str) -> str:
    if not value:
        return value
    return value[:4] + "****" if len(value) > 4 else "****"


def register_secret(value: str | None) -> None:
    if value and len(value) >= 4:
        with _lock:
            _secrets.add(value)


def redact(text: str) -> str:
    with _lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for s in secrets:
        if s in text:
            text = text.replace(s, mask(s))
    for p in _PATTERNS:
        text = p.sub(
            lambda m: m.group(1) + mask(m.group(2)) if "****" not in m.group(2) else m.group(0), text
        )
    return text


class RedactingFilter(logging.Filter):
    """메시지 템플릿과 인자를 각각 마스킹한다(인자 구조는 유지 — uvicorn 접근 로그 포매터 호환)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: str = "INFO", secrets: list[str] | None = None) -> None:
    for s in secrets or []:
        register_secret(s)
    root = logging.getLogger()
    root.setLevel(level.upper())
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    handler.addFilter(RedactingFilter())
    root.handlers = [handler]
    # 다른 라이브러리 로거가 자체 핸들러를 쓰더라도 마스킹되도록 필터 부착
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "apscheduler"):
        lg = logging.getLogger(name)
        for h in lg.handlers:
            h.addFilter(RedactingFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)  # 시뮬레이션 1초 틱 로그 억제
