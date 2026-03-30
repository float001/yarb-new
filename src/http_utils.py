"""HTTP 错误信息格式化。"""

from __future__ import annotations

import json
from typing import Optional, List, Any

import requests


def format_requests_detail(
    exc: Optional[BaseException] = None,
    response: Optional[requests.Response] = None,
    url: str = "",
    body_max: int = 1200,
) -> str:
    parts: List[str] = []
    if url:
        parts.append(f"url={url}")
    if exc is not None:
        parts.append(f"{type(exc).__name__}: {exc}")
    if response is not None:
        parts.append(f"status={response.status_code}")
        u = getattr(response, "url", None) or url
        if u and f"url={u}" not in " | ".join(parts):
            parts.append(f"final_url={u}")
        body = ""
        try:
            ct = response.headers.get("Content-Type") or ""
            if "json" in ct.lower():
                body = json.dumps(response.json(), ensure_ascii=False)
            else:
                body = response.text or ""
        except Exception:
            body = (response.text or "")[:body_max]
        body = (body or "")[:body_max].replace("\n", " ").replace("\r", " ")
        if body:
            parts.append(f"body={body}")
    return " | ".join(parts)
