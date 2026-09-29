from __future__ import annotations

import logging
import re


SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"\b(?:sk|ak|atria)[-_][A-Za-z0-9_-]{8,}\b"),
)


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}…{value[-4:]}"


def redact(text: str) -> str:
    result = text
    for pattern in SECRET_PATTERNS:
        if pattern.groups:
            result = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", result)
        else:
            result = pattern.sub("[REDACTED]", result)
    return result


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def configure_logging(verbose: bool = False) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(SecretRedactionFilter())
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[handler],
        force=True,
    )
