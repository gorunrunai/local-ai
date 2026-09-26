"""web_fetch and web_search (local SearXNG). The only built-in tools that use the network."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlparse

import httpx

from orchestrator.search import SearchUnavailable, searxng
from orchestrator.tools.base import Tool, ToolContext, ToolResult, render_source

MAX_BYTES = 3 * 1024 * 1024
MAX_TEXT_CHARS = 20_000
USER_AGENT = "Mozilla/5.0 (Macintosh; Apple Silicon) GoRunRunLocalAI/0.1"


class BlockedURL(ValueError):
    pass


async def check_public_url(url: str) -> None:
    """Refuse non-http(s) URLs and any host that resolves to a private/local address.

    This stops a prompt-injected fetch from reaching llama-swap, the backend itself, the
    LAN, the Tailscale network (100.64.0.0/10) or cloud metadata endpoints.
    """
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise BlockedURL("only http(s) URLs with a host are allowed")
    if os.environ.get("WEB_FETCH_ALLOW_PRIVATE_FOR_TESTS") == "1":  # integration tests only
        return
    host = u.hostname
    if host.endswith((".local", ".internal", ".ts.net", ".localhost")) or host == "localhost":
        raise BlockedURL(f"host {host} is local")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, u.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise BlockedURL(f"cannot resolve {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified or ip in ipaddress.ip_network("100.64.0.0/10")):
            raise BlockedURL(f"{host} resolves to a non-public address")


def extract_text(html_text: str, url: str) -> tuple[str, str]:
    import trafilatura

    text = trafilatura.extract(html_text, url=url, include_links=False, include_tables=True,
                               favor_recall=True) or ""
    meta = trafilatura.extract_metadata(html_text)
    title = (meta.title if meta and meta.title else "") or url
    return title, text


class WebFetch(Tool):
    name = "web_fetch"
    description = ("Fetch a public web page and return its main text. Use for URLs the user gives or "
                   "that web_search returned. Cite the page as [n] using the source id in the result.")
    parameters = {"type": "object", "properties": {
        "url": {"type": "string", "description": "Absolute http(s) URL"}}, "required": ["url"]}
    network = True

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        url = args["url"].strip()
        try:
            await check_public_url(url)
        except BlockedURL as e:
            return ToolResult(f"Refused: {e}.", ok=False, data={"url": url})
        async with httpx.AsyncClient(follow_redirects=False, timeout=20,
                                     headers={"User-Agent": USER_AGENT}) as client:
            for _ in range(5):  # follow redirects manually so every hop is checked
                r = await client.get(url)
                if r.is_redirect and r.headers.get("location"):
                    url = str(r.url.join(r.headers["location"]))
                    try:
                        await check_public_url(url)
                    except BlockedURL as e:
                        return ToolResult(f"Refused redirect: {e}.", ok=False, data={"url": url})
                    continue
                break
        if r.status_code >= 400:
            return ToolResult(f"HTTP {r.status_code} fetching {url}", ok=False, data={"url": url})
        body = r.content[:MAX_BYTES]
        ctype = r.headers.get("content-type", "")
        if "html" in ctype or body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
            title, text = await asyncio.to_thread(extract_text, body.decode(r.encoding or "utf-8", "replace"), url)
        elif ctype.startswith("text/") or "json" in ctype:
            title, text = url, body.decode(r.encoding or "utf-8", "replace")
        else:
            return ToolResult(f"Unsupported content type {ctype!r} at {url}", ok=False, data={"url": url})
        truncated = len(text) > MAX_TEXT_CHARS
        text = text[:MAX_TEXT_CHARS] + ("\n… [truncated]" if truncated else "")
        src = ctx.sources.add(title, url=url, snippet=text[:300])
        return ToolResult(render_source(src, text),
                          data={"url": url, "title": title, "chars": len(text), "source": src.index})


class WebSearch(Tool):
    name = "web_search"
    description = ("Search the web through the local SearXNG instance. Returns titles, URLs and snippets; "
                   "use web_fetch to read a page before relying on it. Cite results as [n].")
    parameters = {"type": "object", "properties": {
        "query": {"type": "string"},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5}},
        "required": ["query"]}
    network = True

    def __init__(self, searxng_url: str):
        self.searxng_url = searxng_url.rstrip("/")

    def available(self) -> bool:
        # A local SearXNG must be installed; a remote URL (e.g. your own server) is assumed reachable.
        local = self.searxng_url.startswith(("http://127.0.0.1:", "http://localhost:"))
        return searxng.installed() or not local

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        n = int(args.get("max_results") or 5)
        try:
            await searxng.ensure(self.searxng_url)
        except SearchUnavailable as e:
            return ToolResult(f"Search unavailable: {e}.", ok=False)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(f"{self.searxng_url}/search",
                                     params={"q": args["query"], "format": "json"})
                r.raise_for_status()
                results = r.json().get("results", [])[:n]
        except (httpx.HTTPError, ValueError) as e:
            return ToolResult(f"Search unavailable: {e}. Is SearXNG running at {self.searxng_url}?", ok=False)
        if not results:
            return ToolResult("No results.", data={"query": args["query"], "results": []})
        blocks, items = [], []
        for res in results:
            src = ctx.sources.add(res.get("title") or res.get("url"), url=res.get("url"),
                                  snippet=res.get("content", ""))
            blocks.append(render_source(src, res.get("content", "")))
            items.append({"title": src.title, "url": src.url, "source": src.index})
        return ToolResult("\n".join(blocks), data={"query": args["query"], "results": items})
