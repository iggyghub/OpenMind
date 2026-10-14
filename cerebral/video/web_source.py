"""Acquisition seams for the Webpage and Reddit Library sources.

Same no-API posture as github_source / yt-dlp: plain public GETs, no keys.
The fetch is injectable (set_fetch_fn) so tests need no network.

- Webpage: GET the page, strip chrome (nav/header/footer/aside/script/style),
  prefer <article>/<main>, return {title, text}.
- Reddit: a post + its comment thread, not a page. Read through the public
  Atom feeds (``/.rss``) -- unauthenticated ``.json`` returns 403 "Blocked" and
  old.reddit redirects to the JS app (verified 2026-10-11). A thread is ONE
  request (post + its top 25 comments, flat, no scores). A subreddit or a
  keyword search is one listing request, then one request per thread.
  Unauthenticated Reddit allows ~1 request/minute, so the caller paces
  (plugins/reddit_ingest.py crawls in the background).
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable

_UA = "windows:openmind.felix:v1.0 (personal reader)"

MAX_THREAD_CHARS = 60000  # safety cap; post + top comments is ~15k, Budd has ~64k tokens
# Top-voted comments only -- deeper threads are mostly jokes and side-chatter
# (user call 2026-10-11 after the first r/AI_Agents read).
THREAD_COMMENTS = 25


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


def reddit_target(text: str) -> "dict | None":
    """What the Reddit field holds: a thread link, a subreddit link, or a search.

    Search = plain keywords, optionally prefixed with ``r/<sub>`` to stay in one
    subreddit (``r/algotrading breadth thrust``). Returns None for a non-Reddit URL.
    """
    text = (text or "").strip()
    if re.match(r"^https?://", text, re.I):
        return parse_reddit_url(text)
    m = re.match(r"^/?r/([A-Za-z0-9_]+)\s*(.*)$", text)
    sub, query = (m.group(1), m.group(2).strip()) if m else (None, text)
    if sub and not query:
        return {"kind": "subreddit", "sub": sub, "url": f"https://www.reddit.com/r/{sub}"}
    if not query:
        return None
    return {"kind": "search", "sub": sub, "query": query}


def listing_url(target: dict, limit: int) -> str:
    """The ONE feed request that lists a subreddit's or a search's threads."""
    if target["kind"] == "subreddit":
        # Top of all time: the community's most-upvoted (most durable) knowledge,
        # not whatever happens to be on the front page today.
        return f"{target['url']}/top/.rss?t=all&limit={limit}"
    q = urllib.parse.quote_plus(target["query"])
    if target["sub"]:
        return (f"https://www.reddit.com/r/{target['sub']}/search.rss"
                f"?q={q}&restrict_sr=1&sort=relevance&t=all&limit={limit}")
    return f"https://www.reddit.com/search.rss?q={q}&sort=relevance&t=all&limit={limit}"


def threads_in_listing(xml: str) -> list[dict]:
    """[{url, title, sub}] for every post (t3_) in a subreddit/search feed."""
    out = []
    for e in _entries(xml):
        parsed = parse_reddit_url(e["link"]) if e["id"].startswith("t3_") else None
        if parsed and parsed["kind"] == "thread":
            out.append({"url": parsed["url"], "title": e["title"], "sub": parsed["sub"]})
    return out


def thread_feed_url(thread_url: str) -> str:
    return thread_url.rstrip("/") + f"/.rss?limit={THREAD_COMMENTS}&sort=top"
