"""Book acquisition -- ADR-0025 S1.

Extracts chapters from EPUB (stdlib zipfile + html.parser) and PDF (pypdf).
All I/O is behind injectable seams so tests need no real files.

Each returned chapter dict carries:
  {"title": str, "text": str, "page_start": int, "page_end": int}

page_start/page_end are 1-based page numbers (EPUB chapters use sequential
indexes since EPUBs have no pages; PDF uses actual page numbers from the outline
or heuristic). Required by S4 claim provenance -- cheap to capture now.
"""
from __future__ import annotations

import html
import io
import logging
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── injectable seams ──────────────────────────────────────────────────────────

_read_file_fn: Optional[Callable[[str], bytes]] = None
_parse_pdf_fn: Optional[Callable[[bytes], list[dict]]] = None


def set_read_file_fn(fn: "Callable[[str], bytes] | None") -> None:
    global _read_file_fn
    _read_file_fn = fn


def set_parse_pdf_fn(fn: "Callable[[bytes], list[dict]] | None") -> None:
    global _parse_pdf_fn
    _parse_pdf_fn = fn


def get_read_file_fn() -> Callable[[str], bytes]:
    return _read_file_fn or _prod_read_file


def get_parse_pdf_fn() -> Callable[[bytes], list[dict]]:
    return _parse_pdf_fn or _prod_parse_pdf


def _prod_read_file(path: str) -> bytes:
    return Path(path).read_bytes()


# ── EPUB ──────────────────────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        if tag in ("p", "div", "h1", "h2", "h3", "h4", "li", "br"):
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        return html.unescape("".join(self._parts))


def _xhtml_to_text(xhtml: bytes) -> str:
    p = _TextExtractor()
    p.feed(xhtml.decode("utf-8", errors="replace"))
    return re.sub(r"\n{3,}", "\n\n", p.get_text()).strip()


def _opf_spine_items(opf_xml: str) -> list[str]:
    """Return spine idref list in reading order from OPF XML (no external deps)."""
    # Extract manifest id -> href
    manifest: dict[str, str] = {}
    for m in re.finditer(
        r'<item\b[^>]*\bid=["\']([^"\']+)["\'][^>]*\bhref=["\']([^"\']+)["\']',
        opf_xml, re.I,
    ):
        manifest[m.group(1)] = m.group(2)
    for m in re.finditer(
        r'<item\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*\bid=["\']([^"\']+)["\']',
        opf_xml, re.I,
    ):
        manifest[m.group(2)] = m.group(1)

    # Extract spine idrefs in order
    spine_match = re.search(r'<spine\b[^>]*>(.*?)</spine>', opf_xml, re.I | re.S)
    if not spine_match:
        return list(manifest.values())
    idrefs = re.findall(r'<itemref\b[^>]*\bidref=["\']([^"\']+)["\']', spine_match.group(1), re.I)
    return [manifest[ref] for ref in idrefs if ref in manifest]


def _extract_epub(data: bytes) -> list[dict]:
    """Parse EPUB, return one chapter per spine item."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Not a valid EPUB (bad zip)")

    names = zf.namelist()

    # Find content.opf (may be in a subdir)
    opf_path = next((n for n in names if n.lower().endswith(".opf")), None)
    if opf_path is None:
        raise ValueError("No OPF manifest found in EPUB")

    opf_dir = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
    opf_xml = zf.read(opf_path).decode("utf-8", errors="replace")
    spine_hrefs = _opf_spine_items(opf_xml)

    chapters: list[dict] = []
    page_idx = 1
    for href in spine_hrefs:
        # href may be relative to OPF dir
        full = opf_dir + href if opf_dir and not href.startswith("/") else href
        # normalise any ../
        parts = []
        for seg in full.split("/"):
            if seg == "..":
                if parts:
                    parts.pop()
            elif seg and seg != ".":
                parts.append(seg)
        candidate = "/".join(parts)
        entry = candidate if candidate in names else next(
            (n for n in names if n.endswith("/" + href) or n == href), None
        )
        if entry is None:
            continue
        raw = zf.read(entry)
        text = _xhtml_to_text(raw)
        if not text.strip():
            continue
        title = _title_from_text(text) or href
        chapters.append({"title": title, "text": text, "page_start": page_idx, "page_end": page_idx})
        page_idx += 1

    return chapters or [{"title": "Book", "text": "", "page_start": 1, "page_end": 1}]


# ── PDF ───────────────────────────────────────────────────────────────────────

_HEADING_RE = re.compile(
    r"^(Chapter\s+\d+|CHAPTER\s+\d+|Part\s+\d+|PART\s+[IVX]+|[A-Z][A-Z\s]{3,40})$",
    re.M,
)


def _title_from_text(text: str) -> str:
    """Best-effort title: first non-empty line, truncated."""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:80]
    return ""


def _prod_parse_pdf(data: bytes) -> list[dict]:
    """Parse PDF via pypdf, returning chapters with page provenance."""
    import pypdf  # noqa: PLC0415 -- optional dep, guarded at call site

    reader = pypdf.PdfReader(io.BytesIO(data))
    total = len(reader.pages)

    def _page_text(n: int) -> str:
        try:
            return reader.pages[n].extract_text() or ""
        except Exception:  # noqa: BLE001
            return ""

    # Try outline/bookmarks for real chapter boundaries
    outline = reader.outline if hasattr(reader, "outline") else []
    boundaries: list[tuple[str, int]] = []  # (title, 0-based start page)
    try:
        for item in outline:
            if hasattr(item, "title") and hasattr(item, "page"):
                page_ref = item.page
                if page_ref is not None:
                    try:
                        pg = reader.get_page_number(page_ref)
                        boundaries.append((item.title, pg))
                    except Exception:  # noqa: BLE001
                        pass
    except Exception:  # noqa: BLE001
        pass

    if len(boundaries) > 1:
        chapters = []
        for i, (title, start) in enumerate(boundaries):
            end = boundaries[i + 1][1] - 1 if i + 1 < len(boundaries) else total - 1
            text = "\n".join(_page_text(p) for p in range(start, end + 1))
            chapters.append({
                "title": title,
                "text": text.strip(),
                "page_start": start + 1,
                "page_end": end + 1,
            })
        return chapters

    # Heuristic: split on heading-pattern lines across all pages
    all_pages = [_page_text(p) for p in range(total)]
    full_text = "\n".join(all_pages)

    splits: list[tuple[str, int]] = []  # (heading, char offset in full_text)
    for m in _HEADING_RE.finditer(full_text):
        splits.append((m.group(0).strip(), m.start()))

    if len(splits) > 1:
        chapters = []
        for i, (title, start_char) in enumerate(splits):
            end_char = splits[i + 1][1] if i + 1 < len(splits) else len(full_text)
            text = full_text[start_char:end_char].strip()
            # Approximate page numbers from char offset
            frac_start = start_char / max(len(full_text), 1)
            frac_end = end_char / max(len(full_text), 1)
            chapters.append({
                "title": title,
                "text": text,
                "page_start": max(1, int(frac_start * total) + 1),
                "page_end": max(1, min(total, int(frac_end * total) + 1)),
            })
        return chapters

    # Fallback: whole PDF as one chapter
    return [{"title": "Book", "text": full_text.strip(), "page_start": 1, "page_end": total}]


# ── public API ────────────────────────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {".epub", ".pdf", ".txt", ".md"}


def extract_chapters(path: str) -> list[dict]:
    """Extract chapters from a local book file.

    Returns a list of chapter dicts:
      {"title": str, "text": str, "page_start": int, "page_end": int}

    Raises ValueError for unsupported formats. Never fetches from the network.
    """
    suffix = Path(path).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported format '{suffix}'. Supported: "
            + ", ".join(sorted(SUPPORTED_EXTENSIONS))
        )

    data = get_read_file_fn()(path)

    if suffix == ".epub":
        return _extract_epub(data)

    if suffix == ".pdf":
        return get_parse_pdf_fn()(data)

    # .txt / .md: whole file as one chapter (same path as github_ingest docs)
    text = data.decode("utf-8", errors="replace").strip()
    title = _title_from_text(text) or Path(path).stem
    return [{"title": title, "text": text, "page_start": 1, "page_end": 1}]
