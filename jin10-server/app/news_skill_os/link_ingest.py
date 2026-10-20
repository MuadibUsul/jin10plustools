"""Fetch a public news link and extract readable title/time/text.

The user supplies an arbitrary link, so the fetcher rejects non-HTTP schemes and
private/local destinations (including redirects) before any network request.
No cookies, auth headers or raw HTML are stored.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx
from lxml import html

MAX_BYTES = 2_500_000
MAX_TEXT = 80_000


class LinkIngestError(ValueError):
    pass


def _public_url(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
    except ValueError as exc:
        raise LinkIngestError("链接格式不正确") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise LinkIngestError("只支持 http/https 新闻链接")
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise LinkIngestError("该链接不是公网新闻地址")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise LinkIngestError("无法解析新闻链接域名") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise LinkIngestError("该链接指向内网地址，已拒绝")
    return url.strip()


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def fetch_article(url: str) -> dict:
    safe = _public_url(url)
    try:
        with httpx.Client(follow_redirects=True, timeout=25,
                          headers={"User-Agent": "Mozilla/5.0 (compatible; PLUS-NewsSkill/1.0)"}) as client:
            with client.stream("GET", safe) as response:
                response.raise_for_status()
                final = str(response.url)
                _public_url(final)
                content_type = response.headers.get("content-type", "").lower()
                if "html" not in content_type and "text" not in content_type:
                    raise LinkIngestError("该链接不是新闻网页")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise LinkIngestError("新闻页面过大，无法分析")
                    chunks.append(chunk)
                raw = b"".join(chunks)
    except LinkIngestError:
        raise
    except httpx.HTTPError as exc:
        raise LinkIngestError("新闻链接抓取失败，请检查链接是否公开可访问") from exc
    try:
        document = html.fromstring(raw)
    except Exception as exc:
        raise LinkIngestError("新闻页面无法解析") from exc
    for node in document.xpath("//script|//style|//noscript|//svg|//nav|//footer|//form"):
        node.drop_tree()
    title = ""
    for xpath in ("//meta[@property='og:title']/@content", "//meta[@name='twitter:title']/@content",
                  "//title/text()", "//h1//text()"):
        values = document.xpath(xpath)
        if values:
            title = _clean_text(" ".join(values))
            if title:
                break
    site = ""
    for xpath in ("//meta[@property='og:site_name']/@content", "//meta[@name='application-name']/@content"):
        values = document.xpath(xpath)
        if values:
            site = _clean_text(values[0])
            if site:
                break
    published = ""
    for xpath in ("//meta[@property='article:published_time']/@content",
                  "//meta[@name='publishdate']/@content", "//meta[@name='pubdate']/@content",
                  "//time/@datetime"):
        values = document.xpath(xpath)
        if values:
            published = _clean_text(values[0])
            if published:
                break
    paragraphs = []
    for node in document.xpath("//p|//li|//h2|//h3"):
        text = _clean_text(" ".join(node.itertext()))
        if len(text) >= 20:
            paragraphs.append(text)
    text = "\n".join(dict.fromkeys(paragraphs))
    if len(text) < 120:
        text = _clean_text(" ".join(document.xpath("//body//text()")))
    if len(text) < 80:
        raise LinkIngestError("新闻页面没有可分析的有效正文")
    if not title:
        title = _clean_text(text[:80])
    return {"url": final, "title": title[:300], "site": site[:200],
            "published_at": published[:100], "text": text[:MAX_TEXT]}
