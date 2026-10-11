"""Acquisition seams for the Webpage and Reddit Library sources.

Same no-API posture as github_source / yt-dlp: plain public GETs, no keys.
The fetch is injectable (set_fetch_fn) so tests need no network.

- Webpage: GET the page, strip chrome (nav/header/footer/aside/script/style),
  prefer <article>/<main>, return {title, text}.
- Reddit: a post + its comment thread, not a page. Read through the public
  Atom feeds (``/.rss``) -- unauthenticated ``.json`` returns 403 "Blocked" and
  old.reddit redirects to the JS app (verified 2026-10-11). A thread URL is ONE
  request (post + up to ~50 comments, flat, no scores); a subreddit URL is ONE
  request for its newest posts (bodies only, no comments). Unauthenticated
  Reddit allows ~1 request/minute, hence never fanning out per-thread.
"""
from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable

_UA = "windows:openmind.felix:v1.0 (personal reader)"

MAX_THREAD_CHARS = 20000
THREAD_COMMENTS = 50
SUBREDDIT_POSTS = 15


def _prod_fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 -- user-supplied public URL
        return r.read().decode("utf-8", errors="replace")


_fetch_fn: Callable[[str], str] = _prod_fetch


def set_fetch_fn(fn: "Callable[[str], str] | None") -> None:
    global _fetch_fn
    _fetch_fn = fn or _prod_fetch


# ── Webpage ──────────────────────────────────────────────────────────────────

_BLOCKS = ["p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote"]


def _soup_text(root) -> str:
    for blk in root.find_all(_BLOCKS):
        blk.insert_after("\n")
    text = re.sub(r"[ \t\r\f\v]+", " ", root.get_text())
    return re.sub(r" *\n[ \n]*", "\n", text).strip()


def html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup  # noqa: PLC0415 -- already a dependency
    return _soup_text(BeautifulSoup(html or "", "html.parser"))


def page_to_text(html: str) -> dict:
    """{title, text} from raw HTML, with page chrome removed."""
    from bs4 import BeautifulSoup  # noqa: PLC0415

    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    return {"title": title, "text": _soup_text(root)}


def fetch_webpage(url: str) -> dict:
    return page_to_text(_fetch_fn(url))


# ── Reddit ───────────────────────────────────────────────────────────────────

_REDDIT = re.compile(r"^https?://(?:[a-z0-9-]+\.)?reddit\.com/r/(?P<sub>[^/?#]+)(?P<rest>/[^?#]*)?", re.I)
_NS = {"a": "http://www.w3.org/2005/Atom"}


def parse_reddit_url(url: str) -> "dict | None":
    """{'kind': 'thread'|'subreddit', 'sub', 'url': canonical} or None if not Reddit."""
    m = _REDDIT.match((url or "").strip())
    if not m:
        return None
    sub, rest = m.group("sub"), (m.group("rest") or "").rstrip("/")
    base = f"https://www.reddit.com/r/{sub}"
    pm = re.match(r"^/comments/([a-z0-9]+)(/[^/]*)?", rest, re.I)
    if pm:
        return {"kind": "thread", "sub": sub, "url": f"{base}/comments/{pm.group(1)}{pm.group(2) or ''}"}
    return {"kind": "subreddit", "sub": sub, "url": base}


def _entries(xml: str) -> list[dict]:
    root = ET.fromstring(xml)
    out = []
    for e in root.findall("a:entry", _NS):
        author = e.find("a:author/a:name", _NS)
        link = e.find("a:link", _NS)
        out.append({
            "id": (e.findtext("a:id", "", _NS) or ""),
            "title": e.findtext("a:title", "", _NS) or "",
            "author": author.text if author is not None else "",
            "link": link.get("href") if link is not None else "",
            "text": html_to_text(e.findtext("a:content", "", _NS) or ""),
        })
    return out


def thread_from_feed(xml: str, sub: str) -> dict:
    """{url, title, text} for one thread: the t3_ post + its t1_ comments."""
    entries = _entries(xml)
    post = next((e for e in entries if e["id"].startswith("t3_")), entries[0] if entries else None)
    if post is None:
        raise ValueError("Empty Reddit feed")
    comments = [f"- {e['author']}: {e['text']}" for e in entries if e["id"].startswith("t1_") and e["text"]]
    parts = [f"r/{sub} -- {post['title']}"]
    if post["text"]:
        parts.append(post["text"])
    if comments:
        parts.append("Comments:\n" + "\n".join(comments))
    return {"url": post["link"], "title": post["title"], "text": "\n\n".join(parts)[:MAX_THREAD_CHARS]}


def fetch_reddit(url: str) -> list[dict]:
    """A thread -> [item]; a subreddit -> one item per recent post. Raises ValueError for non-Reddit."""
    parsed = parse_reddit_url(url)
    if parsed is None:
        raise ValueError("Not a Reddit URL (expected reddit.com/r/<sub> or a /comments/ thread link)")
    if parsed["kind"] == "thread":
        return [thread_from_feed(_fetch_fn(parsed["url"] + "/.rss?limit=" + str(THREAD_COMMENTS)), parsed["sub"])]
    items = []
    for e in _entries(_fetch_fn(parsed["url"] + "/.rss?limit=" + str(SUBREDDIT_POSTS))):
        if e["id"].startswith("t3_") and e["link"]:
            items.append({
                "url": e["link"],
                "title": e["title"],
                "text": (f"r/{parsed['sub']} -- {e['title']}\n\n{e['text']}")[:MAX_THREAD_CHARS],
            })
    return items
