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


def to_strategy(idea: Idea, llm: Optional[Any] = None, router=None) -> str:
    """
    Generates a runnable `def strategy(data) -> signals:` Python function.
    Uses Qwen/Budd (free models only) when llm or router is provided.
    Enforces honesty rule: claims are never collapsed to facts.
    """
    claim = idea.author_claim_text or f"Author claims: {idea.claim_text}"

    prompt = (
        "You are a rigorous quant researcher. Generate a Python strategy function "
        "that implements the following hypothesis.\n"
        "HONESTY RULE: The code must treat the claim as a testable hypothesis, "
        "not as market fact. Never assert 'X is true'. Encode logic that tests 'X'.\n"
        "Claim: {claim}\n\n"
        # The live dispatcher's contract, spelled out -- see
        # cerebral/trading/live_tick.py. Left vague, generated code read
        # data.get('close') (lowercase) and produced no signals at all.
        "CONTRACT:\n"
        "- `data` is a pandas DataFrame with an ascending DatetimeIndex and "
        "capitalised columns Open, High, Low, Close, Volume.\n"
        "- Return a list of target positions, one per bar, each 1 (hold long), "
        "0 (hold nothing) or -1 (hold short). The LAST element is the position "
        "to hold right now.\n"
        "- No imports: only Python builtins and the DataFrame itself are in scope.\n\n"
        "Return ONLY valid Python code for:\n"
        "def strategy(data) -> signals:\n"
        "    ..."
    ).format(claim=claim)

    if llm:
        return llm.generate(prompt)
    
    if router:
        try:
            result = router.complete(prompt, task_type="coding")
            return result
        except Exception:
            pass

    return _generate_stub_strategy(claim)


def _generate_stub_strategy(claim: str) -> str:
    """Fallback when no LLM is available.

    Reads data["Close"] (capitalised), not data.get("close", []): the old
    lowercase .get() returned the [] default against every DataFrame
    fetch_ohlcv produces, so the stub emitted no signals at all. And it
    returns target positions in {{1, 0, -1}} per live_tick.py's contract --
    the old 1/-1 encoding had no way to say "hold nothing".
    """
    return textwrap.dedent(f'''
    def strategy(data):
        """
        Tests hypothesis: {claim[:120]}
        Strictly follows honesty rule: implements claim as a signal, not truth.
        """
        close = list(data["Close"])
        # Stub logic: long while price is above its own running mean, else
        # flat. Replace with Qwen/Budd generated logic in production.
        signals = []
        running_total = 0.0
        for i, price in enumerate(close):
            running_total += price
            signals.append(1 if price > running_total / (i + 1) else 0)
        return signals
    ''').strip()


# A strategy function only ever needs to consume `data` and return
# `signals` -- no file/network/process access belongs in that contract.
# This is a real but partial mitigation (a determined adversary can still
# find an escape via pure-Python tricks like `().__class__.__bases__`),
# not equivalent to the ADR-0010 sandbox self_dev already uses for
# untrusted code. TRADING.md's SAFETY section flags routing this through
# that sandbox for real before S5+ runs strategy code against a broker.
_SAFE_BUILTIN_NAMES = (
    "abs", "all", "any", "bool", "dict", "enumerate", "float", "int",
    "len", "list", "max", "min", "range", "round", "sorted", "sum",
    "tuple", "zip", "True", "False", "None",
)
_ALL_BUILTINS = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
_SAFE_BUILTINS = {name: _ALL_BUILTINS[name] for name in _SAFE_BUILTIN_NAMES if name in _ALL_BUILTINS}


def _compile_strategy(code_str: str) -> Callable:
    """TEST-ONLY helper. Compiles strategy code for backtests.

    This is NOT real sandboxing. It exec()s code ultimately derived from
    scraped web content. Production code must route through
    cerebral.trading.sandboxed_eval.evaluate_signals instead.
    """
    from cerebral.security import scan_source

    issue = scan_source(code_str)
    if issue is not None:
        raise ValueError(f"Generated strategy code rejected: {issue.detail}")

    namespace: Dict[str, Any] = {}
    exec(code_str, {"__builtins__": _SAFE_BUILTINS}, namespace)
    if "strategy" not in namespace:
        raise ValueError("Generated code must define `def strategy(data) -> signals:`")
    return namespace["strategy"]
