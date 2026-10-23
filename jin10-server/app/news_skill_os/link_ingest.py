"""Fetch a public news link and extract readable title/time/text.

The user supplies an arbitrary link, so the fetcher rejects non-HTTP schemes and
private/local destinations (including redirects) before any network request.
No cookies, auth headers or raw HTML are stored.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse, urljoin

import httpx
from lxml import html

MAX_BYTES = 2_500_000
MAX_TEXT = 80_000


class LinkIngestError(ValueError):
    pass


def _destination(url: str) -> tuple[str, str]:
    try:
        parsed = urlparse(url.strip())
    except ValueError as exc:
        raise LinkIngestError("链接格式不正确") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise LinkIngestError("只支持 http/https 新闻链接")
    if parsed.username or parsed.password:
        raise LinkIngestError('新闻链接不能包含账号密码')
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise LinkIngestError("该链接不是公网新闻地址")
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme=='https' else 80), type=socket.SOCK_STREAM)})
    except (OSError, ValueError) as exc:
        raise LinkIngestError("无法解析新闻链接域名") from exc
    if not addresses:raise LinkIngestError('新闻链接没有公网地址')
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global or ip.is_multicast:
            raise LinkIngestError("该链接指向内网地址，已拒绝")
    return url.strip(), addresses[0]


def _public_url(url: str) -> str:
    return _destination(url)[0]


class PublicTransport(httpx.HTTPTransport):
    """Connect to the validated IP, keeping the original TLS SNI and Host header.

    DNS is not looked up again by the connection pool, preventing DNS rebinding.
    Every request, including redirects, must pass this boundary.
    """
    def __init__(self):
        # Different public hostnames can share an IP; never reuse a TLS connection
        # validated for a different hostname after replacing the network origin.
        super().__init__(limits=httpx.Limits(max_keepalive_connections=0))

    def handle_request(self, request):
        _, address = _destination(str(request.url))
        pinned = httpx.Request(request.method, request.url.copy_with(host=address),
            headers=request.headers, stream=request.stream,
            extensions={**request.extensions, 'sni_hostname':request.url.host})
        return super().handle_request(pinned)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def fetch_article(url: str) -> dict:
    safe = _public_url(url)
    try:
        with httpx.Client(follow_redirects=False, timeout=25, trust_env=False, transport=PublicTransport(),
                          headers={"User-Agent": "Mozilla/5.0 (compatible; PLUS-NewsSkill/1.0)"}) as client:
          for hop in range(6):
            with client.stream("GET", safe) as response:
                if response.is_redirect:
                    if hop == 5:raise LinkIngestError('新闻链接跳转次数过多')
                    safe = _public_url(urljoin(safe, response.headers.get('location','')))
                    continue
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
                break
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
