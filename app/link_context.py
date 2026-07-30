from __future__ import annotations

import ipaddress
import logging
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>'\"]+", re.IGNORECASE)
TRAILING_URL_PUNCTUATION = ".,!?;:)]}"
MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 32_768
MAX_TITLE_CHARS = 160
MAX_SUMMARY_CHARS = 700
ALLOWED_CONTENT_TYPES = {
    "application/json",
    "text/html",
    "text/plain",
}


@dataclass(frozen=True)
class LinkPreview:
    url: str
    title: str
    summary: str


Resolver = Callable[[str, int], list[str]]


def _public_addresses(hostname: str, port: int) -> list[str]:
    addresses = {
        str(result[4][0])
        for result in socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    }
    return sorted(addresses)


def _first_url(text: str) -> str | None:
    match = URL_RE.search(text)
    if match is None:
        return None
    url = match.group(0).rstrip(TRAILING_URL_PUNCTUATION)
    if url.lower().startswith("www."):
        return f"https://{url}"
    return url


def _compact_text(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


class LinkInspector:
    def __init__(
        self,
        http: requests.Session | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self._http = http or requests.Session()
        self._resolver = resolver or _public_addresses

    def inspect_text(self, text: str) -> LinkPreview | None:
        url = _first_url(text)
        if url is None:
            return None
        try:
            return self._fetch(url)
        except (OSError, ValueError, requests.RequestException) as exc:
            logger.info("link preview unavailable url=%s reason=%s", url, exc)
            return None

    def _validated_url(self, raw_url: str) -> str:
        parsed = urlsplit(raw_url)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ValueError("unsupported URL scheme")
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("URL must have a public hostname without credentials")

        try:
            port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        except ValueError as exc:
            raise ValueError("invalid URL port") from exc
        if port not in {80, 443}:
            raise ValueError("nonstandard URL ports are not allowed")

        addresses = self._resolver(parsed.hostname, port)
        if not addresses:
            raise ValueError("URL hostname did not resolve")
        if any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("URL resolves to a non-public address")

        return urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc,
                parsed.path or "/",
                parsed.query,
                "",
            )
        )

    def _fetch(self, initial_url: str) -> LinkPreview:
        current_url = initial_url
        for redirect_count in range(MAX_REDIRECTS + 1):
            current_url = self._validated_url(current_url)
            response = self._http.get(
                current_url,
                allow_redirects=False,
                headers={
                    "User-Agent": "ScratchAIAgent-LinkPreview/1.0",
                    "Accept": "text/html,text/plain,application/json",
                },
                stream=True,
                timeout=(3, 6),
            )
            try:
                if 300 <= response.status_code < 400:
                    location = response.headers.get("Location", "").strip()
                    if not location:
                        raise ValueError("redirect response has no location")
                    if redirect_count == MAX_REDIRECTS:
                        raise ValueError("too many redirects")
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                content_type = (
                    response.headers.get("Content-Type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise ValueError(f"unsupported content type: {content_type or 'unknown'}")

                content_length = response.headers.get("Content-Length", "").strip()
                if content_length and int(content_length) > MAX_RESPONSE_BYTES:
                    raise ValueError("linked page is too large")

                body = bytearray()
                for chunk in response.iter_content(chunk_size=4096):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise ValueError("linked page is too large")

                encoding = response.encoding or "utf-8"
                text = bytes(body).decode(encoding, errors="replace")
                return self._preview(current_url, content_type, text)
            finally:
                response.close()

        raise ValueError("too many redirects")

    @staticmethod
    def _preview(url: str, content_type: str, text: str) -> LinkPreview:
        if content_type == "text/html":
            soup = BeautifulSoup(text, "html.parser")
            title = _compact_text(
                soup.title.get_text(" ", strip=True) if soup.title else "",
                MAX_TITLE_CHARS,
            )
            description_tag = soup.find(
                "meta",
                attrs={"name": re.compile(r"^description$", re.IGNORECASE)},
            )
            description = ""
            if description_tag is not None:
                description = str(description_tag.get("content") or "")
            if not description:
                for removable in soup(["script", "style", "noscript"]):
                    removable.decompose()
                description = soup.get_text(" ", strip=True)
        else:
            title = ""
            description = text

        return LinkPreview(
            url=url,
            title=title,
            summary=_compact_text(description, MAX_SUMMARY_CHARS),
        )
