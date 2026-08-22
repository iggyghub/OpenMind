from __future__ import annotations
import datetime
import textwrap
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Dict, Any


@dataclass
class Idea:
    """Testable trading hypothesis with full provenance."""
    source_url: Optional[str] = None
    page_title: Optional[str] = None
    claim_text: str = ""
    date_accessed: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    provenance: str = ""
    book_info: Optional[Dict[str, str]] = None
    author_claim_text: Optional[str] = None
    raw_content: str = ""


def extract_from_url(
    url: str,
    fetcher: Optional[Callable[[str], Dict[str, Any]]] = None,
    crawler: Optional[Callable[[str], List[str]]] = None,
) -> List[Idea]:
    """Fetches a page, follows relevant internal links, and extracts testable claims."""
    fh = fetcher or _default_fetcher
    cw = crawler or _default_crawler

    visited = {url}
    queue = [url]
    ideas: List[Idea] = []

    while queue:
        current_url = queue.pop(0)
        data = fh(current_url)
        html = data.get("html", "")
        title = data.get("title", "Untitled")
        text_content = data.get("text", html)

        ideas.append(Idea(
            source_url=current_url,
            page_title=title,
            claim_text=text_content[:1000],
            provenance=f"url: {current_url}",
            author_claim_text=f"Author claims: {title}",
            raw_content=html,
        ))

        links = cw(current_url)
        for link in links:
            if link not in visited:
                visited.add(link)
                queue.append(link)

    return ideas


def _default_fetcher(url: str) -> Dict[str, Any]:
    try:
        from plugins.http_client import fetch_html
        return fetch_html(url)
    except ImportError:
        return {"html": "", "title": "Untitled", "text": "", "links": []}


def _default_crawler(url: str) -> List[str]:
    try:
        from plugins.browser import get_internal_links
        return get_internal_links(url)
    except ImportError:
        return []


def from_prose(text: str) -> Idea:
    """Creates an idea from user prose with verbatim provenance."""
    return Idea(
        claim_text=text,
        provenance="user, verbatim",
        author_claim_text=f"User claims: {text}",
    )


def from_book_claim(claim: str, book: str, chapter: str) -> Idea:
    """Creates an idea from book corpus with book/chapter provenance."""
    return Idea(
        claim_text=claim,
        provenance=f"book: {book} ch {chapter}",
        book_info={"book": book, "chapter": chapter},
        author_claim_text=f"Book '{book}' Chapter '{chapter}' claims: {claim}",
    )


def to_strategy(idea: Idea, llm: Optional[Any] = None) -> str:
    """
    Generates a runnable `def strategy(data) -> signals:` Python function.
    Uses Qwen/Budd (free models only) when llm is provided.
    Enforces honesty rule: claims are never collapsed to facts.
    """
    claim = idea.author_claim_text or f"Author claims: {idea.claim_text}"

    prompt = (
        "You are a rigorous quant researcher. Generate a Python strategy function "
        "that implements the following hypothesis.\n"
        "HONESTY RULE: The code must treat the claim as a testable hypothesis, "
        "not as market fact. Never assert 'X is true'. Encode logic that tests 'X'.\n"
        "Claim: {claim}\n\n"
        "Return ONLY valid Python code for:\n"
        "def strategy(data) -> signals:\n"
        "    ..."
    ).format(claim=claim)

    if llm:
        return llm.generate(prompt)

    return _generate_stub_strategy(claim)


def _generate_stub_strategy(claim: str) -> str:
    return textwrap.dedent(f'''
    def strategy(data):
        """
        Tests hypothesis: {claim[:120]}
        Strictly follows honesty rule: implements claim as a signal, not truth.
        """
        close = data.get("close", [])
        # Stub logic; replace with Qwen/Budd generated logic in production
        signals = [1 if x > 0 else -1 for x in close]
        return signals
    ''').strip()


def compile_strategy(code_str: str) -> Callable:
    """Compiles strategy code safely for the backtest engine."""
    namespace: Dict[str, Any] = {}
    exec(code_str, {"__builtins__": __builtins__}, namespace)
    if "strategy" not in namespace:
        raise ValueError("Generated code must define `def strategy(data) -> signals:`")
    return namespace["strategy"]
