"""
Finance MCP plugin — Issue #28 (Finance MCP — Invoice/Receipt OCR — AFK).

Tools:
  - finance_extract_receipt(image_path)
        OCR an image or scanned PDF and return a structured dict with
        per-field confidence scores. **No side effects** — extraction-only
        so the LLM can show the user the result before committing.
  - finance_log_expense(image_path, sheet_target, confirm=False, columns?)
        Extract + (only when confirm=True) append a row to the configured
        Google Sheet or Grist table via the existing google_workspace
        sheets_write_range tool. No autopilot — confirm=False returns the
        extraction and the would-be row but never invokes the workspace
        plugin. The LLM is expected to confirm with the user before re-
        calling with confirm=True.

Delegation chain for the append:
  FinancePlugin
    → google_workspace_plugin.call_tool("sheets_write_range", ...)
      → (n8n → Google Sheets, or Grist fallback transparently
         via plugins/google_workspace_fallback.py)

Same delegation pattern as plugins/zoom.py uses for n8n.

Injection points (so tests never run real OCR, never read PDFs, never hit
n8n):
  - ocr_fn(image_path) -> str
        Defaults to pytesseract.image_to_string(Image.open(image_path)).
  - pdf_to_image_fn(pdf_path) -> list[str]
        Defaults to pdf2image.convert_from_path; only page 1 is OCR'd
        (multi-page receipts are out of scope for v1).
  - google_workspace_plugin
        Defaults to plugins.google_workspace.create(); tests pass a fake
        with a recording call_tool. Mirrors the n8n_plugin injection in
        plugins/zoom.py.

Field extraction (regex over OCR text — no LLM):
  total      keyword anchor → 1.0, bare currency number → 0.5, nothing → 0.0
  currency   $/£/€/¥ → ISO 4217 (USD/GBP/EUR/JPY); literal 3-letter code
             fallback. Default None, confidence 0.0.
  date       ISO and "D MMM YYYY" → 1.0; DD/MM/YYYY vs MM/DD/YYYY is
             locale-ambiguous → 0.5.
  vendor     first non-empty line that doesn't look like an address or
             pure digits. Confidence 0.5 (heuristic).
  line_items best-effort regex; the line that produced total is excluded.

Sheet column schema (configurable via sheet_target.columns):
  Default: [date, vendor, total, currency, items_summary, image_path].
  items_summary joins line_items with "; ". Pass columns=[...] to override;
  missing extracted fields map to "".

Safety:
  - Both tools reject empty image_path and reject paths where
    Path(image_path).is_file() is False — same fail-loud pattern as
    plugins/printer.py. The path is surfaced in the error message.
  - finance_log_expense never auto-writes; confirm=True is required to
    invoke sheets_write_range. Tests assert that confirm=False does not
    call the workspace plugin.
  - Receipt image paths are user-provided — we do not shell out with the
    path. Pure Python OCR + HTTP via the workspace plugin.
"""
import json
import logging
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "finance"

# ADR-0005 / Issue #44 — finance_extract_receipt reads an image/PDF from disk
# (fs_read). finance_log_expense additionally delegates a row append to the
# google_workspace plugin's sheets_write_range (external_data_write).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "fs_read",
    "external_data_write",
})

OcrFn = Callable[[str], str]
PdfToImageFn = Callable[[str], list[str]]

DEFAULT_COLUMNS: list[str] = [
    "date",
    "vendor",
    "total",
    "currency",
    "items_summary",
    "image_path",
]

_CURRENCY_SYMBOLS: dict[str, str] = {
    "$": "USD",
    "£": "GBP",
    "€": "EUR",
    "¥": "JPY",
}

_ISO_CODE_RE = re.compile(r"\b(USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|INR)\b")
_TOTAL_KEYWORD_RE = re.compile(
    r"(?i)(?:grand\s*total|total|amount|balance)\D{0,30}([0-9]+[.,][0-9]{2})"
)
_BARE_AMOUNT_RE = re.compile(r"([0-9]+[.,][0-9]{2})")
_LINE_ITEM_RE = re.compile(r"^(.+?)\s+([0-9]+[.,][0-9]{2})\s*$")
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_DASH_DATE_RE = re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b")
_TEXT_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})\b",
    re.IGNORECASE,
)
_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}
_DIGITS_ONLY_RE = re.compile(r"^[\d\s\W]+$")
_ADDRESSY_RE = re.compile(
    r"\b(?:street|st\.|road|rd\.|avenue|ave\.|suite|floor|"
    r"highway|hwy)\b|\b\d{5}(?:-\d{4})?\b",
    re.IGNORECASE,
)


def _default_ocr(image_path: str) -> str:
    """Production OCR — pytesseract over PIL.Image. Lazy-imported so the
    test suite doesn't require Tesseract or Pillow to be installed."""
    import pytesseract
    from PIL import Image

    with Image.open(image_path) as img:
        return pytesseract.image_to_string(img)


def _default_pdf_to_image(pdf_path: str) -> list[str]:
    """Production rasteriser — pdf2image over Poppler. Returns a list of
    paths to PNGs of each page (we only OCR page 1 for v1)."""
    import tempfile

    from pdf2image import convert_from_path

    paths: list[str] = []
    pages = convert_from_path(pdf_path, first_page=1, last_page=1)
    for page in pages:
        with tempfile.NamedTemporaryFile(
            suffix=".png", delete=False
        ) as fh:
            page.save(fh.name, format="PNG")
            paths.append(fh.name)
    return paths


class FinancePlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        google_workspace_plugin=None,
        *,
        ocr_fn: OcrFn | None = None,
        pdf_to_image_fn: PdfToImageFn | None = None,
    ) -> None:
        if google_workspace_plugin is not None:
            self._workspace = google_workspace_plugin
        else:
            from plugins.google_workspace import create as _create_workspace
            self._workspace = _create_workspace()
        self._ocr = ocr_fn or _default_ocr
        self._pdf_to_image = pdf_to_image_fn or _default_pdf_to_image

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="finance_extract_receipt",
                description=(
                    "OCR a receipt image or scanned PDF and return "
                    "{vendor, date, total, currency, line_items, "
                    "confidence}. No side effects — does not write to "
                    "any sheet."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "image_path": {
                            "type": "string",
                            "description": (
                                "Absolute path to a receipt image "
                                "(.png/.jpg/.jpeg) or scanned PDF "
                                "(.pdf — page 1 only)."
                            ),
                        },
                    },
                    "required": ["image_path"],
                },
            ),
            Tool(
                name="finance_log_expense",
                description=(
                    "Extract a receipt and append a row to the "
                    "configured Google Sheet or Grist table. Requires "
                    "confirm=true — by default returns the extraction "
                    "and the would-be row without writing."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "image_path": {
                            "type": "string",
                            "description": (
                                "Absolute path to a receipt image or "
                                "scanned PDF."
                            ),
                        },
                        "sheet_target": {
                            "type": "object",
                            "description": (
                                "Sheet target. For Google Sheets: "
                                "{spreadsheet_id, sheet_name?, columns?}. "
                                "For the Grist fallback: {grist_table, "
                                "grist_doc_id?, columns?}."
                            ),
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": (
                                "If false (default) the row is computed "
                                "but not written. Required to be true to "
                                "actually append."
                            ),
                        },
                    },
                    "required": ["image_path", "sheet_target"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "finance_extract_receipt":
            return await self._extract_receipt(args)
        if tool_name == "finance_log_expense":
            return await self._log_expense(args)
        return ToolResult(
            content=f"Unknown tool: '{tool_name}'", is_error=True
        )

    # ------------------------------------------------------------------
    # finance_extract_receipt
    # ------------------------------------------------------------------

    async def _extract_receipt(self, args: dict) -> ToolResult:
        image_path = args.get("image_path")
        err = self._validate_image_path(image_path)
        if err is not None:
            return err

        extracted, _err = self._extract_from_path(image_path)
        if _err is not None:
            return _err
        return ToolResult(content=json.dumps(extracted))

    # ------------------------------------------------------------------
    # finance_log_expense
    # ------------------------------------------------------------------

    async def _log_expense(self, args: dict) -> ToolResult:
        image_path = args.get("image_path")
        err = self._validate_image_path(image_path)
        if err is not None:
            return err

        sheet_target = args.get("sheet_target")
        if not isinstance(sheet_target, dict) or not sheet_target:
            return ToolResult(
                content="'sheet_target' is required for finance_log_expense",
                is_error=True,
            )
        spreadsheet_id, sheet_name, target_err = self._resolve_sheet_target(
            sheet_target
        )
        if target_err is not None:
            return target_err

        extracted, _err = self._extract_from_path(image_path)
        if _err is not None:
            return _err

        columns = sheet_target.get("columns") or DEFAULT_COLUMNS
        row = self._row_from_extracted(extracted, columns, image_path)

        confirm = bool(args.get("confirm", False))
        if not confirm:
            payload = {
                "extracted": extracted,
                "row": row,
                "columns": columns,
                "would_write_to": {
                    "spreadsheet_id": spreadsheet_id,
                    "sheet_name": sheet_name,
                },
                "written": False,
                "note": (
                    "confirm=false — pass confirm=true to actually "
                    "append this row."
                ),
            }
            return ToolResult(content=json.dumps(payload))

        # confirm=True — delegate the write to the workspace plugin.
        last_col_letter = chr(ord("A") + max(0, len(columns) - 1))
        write_args = {
            "spreadsheet_id": spreadsheet_id,
            "range": f"{sheet_name}!A:{last_col_letter}",
            "data": [row],
        }
        write_result = await self._workspace.call_tool(
            "sheets_write_range", write_args
        )
        if write_result.is_error:
            return write_result

        return ToolResult(content=json.dumps({
            "extracted": extracted,
            "row": row,
            "columns": columns,
            "written": True,
            "spreadsheet_id": spreadsheet_id,
            "sheet_name": sheet_name,
        }))

    # ------------------------------------------------------------------
    # Helpers — input validation
    # ------------------------------------------------------------------

    def _validate_image_path(self, image_path) -> ToolResult | None:
        if not image_path:
            return ToolResult(
                content="'image_path' is required",
                is_error=True,
            )
        if not Path(image_path).is_file():
            return ToolResult(
                content=f"image_path does not exist: {image_path}",
                is_error=True,
            )
        return None

    @staticmethod
    def _resolve_sheet_target(
        sheet_target: dict,
    ) -> tuple[str, str, ToolResult | None]:
        if "spreadsheet_id" in sheet_target:
            spreadsheet_id = sheet_target["spreadsheet_id"]
            sheet_name = sheet_target.get("sheet_name", "Sheet1")
            return spreadsheet_id, sheet_name, None
        if "grist_table" in sheet_target:
            grist_table = sheet_target["grist_table"]
            spreadsheet_id = sheet_target.get(
                "grist_doc_id", grist_table
            )
            return spreadsheet_id, grist_table, None
        return "", "", ToolResult(
            content=(
                "sheet_target must include 'spreadsheet_id' (Google "
                "Sheets) or 'grist_table' (Grist fallback)"
            ),
            is_error=True,
        )

    # ------------------------------------------------------------------
    # Helpers — OCR + parsing
    # ------------------------------------------------------------------

    def _extract_from_path(
        self, image_path: str
    ) -> tuple[dict, ToolResult | None]:
        try:
            text = self._ocr_path(image_path)
        except Exception as exc:
            return {}, ToolResult(
                content=f"OCR failed for '{image_path}': {exc}",
                is_error=True,
            )
        return self._parse_receipt_text(text), None

    def _ocr_path(self, image_path: str) -> str:
        if image_path.lower().endswith(".pdf"):
            pages = self._pdf_to_image(image_path)
            if not pages:
                raise RuntimeError("pdf_to_image_fn returned no pages")
            return self._ocr(pages[0])
        return self._ocr(image_path)

    def _parse_receipt_text(self, text: str) -> dict:
        total, total_conf, total_line = self._extract_total(text)
        currency, currency_conf = self._extract_currency(text)
        date, date_conf = self._extract_date(text)
        vendor, vendor_conf = self._extract_vendor(text)
        line_items = self._extract_line_items(text, exclude_line=total_line)

        return {
            "vendor": vendor,
            "date": date,
            "total": total,
            "currency": currency,
            "line_items": line_items,
            "confidence": {
                "vendor": vendor_conf,
                "date": date_conf,
                "total": total_conf,
                "currency": currency_conf,
            },
        }

    @staticmethod
    def _normalise_amount(raw: str) -> float:
        return float(raw.replace(",", "."))

    def _extract_total(
        self, text: str
    ) -> tuple[float | None, float, str | None]:
        # Keyword-anchored — strongest signal. Use the LAST keyword match
        # so the receipt's "subtotal" earlier in the text doesn't beat
        # the actual "total" near the bottom.
        keyword_matches = list(_TOTAL_KEYWORD_RE.finditer(text))
        if keyword_matches:
            match = keyword_matches[-1]
            anchor_line = self._line_containing(text, match.start())
            return self._normalise_amount(match.group(1)), 1.0, anchor_line
        # Fallback: last bare currency-like number in the text.
        bare_matches = list(_BARE_AMOUNT_RE.finditer(text))
        if bare_matches:
            match = bare_matches[-1]
            anchor_line = self._line_containing(text, match.start())
            return self._normalise_amount(match.group(1)), 0.5, anchor_line
        return None, 0.0, None

    @staticmethod
    def _line_containing(text: str, offset: int) -> str:
        line_start = text.rfind("\n", 0, offset) + 1
        line_end = text.find("\n", offset)
        if line_end == -1:
            line_end = len(text)
        return text[line_start:line_end].strip()

    def _extract_currency(self, text: str) -> tuple[str | None, float]:
        for symbol, code in _CURRENCY_SYMBOLS.items():
            if symbol in text:
                return code, 1.0
        match = _ISO_CODE_RE.search(text)
        if match:
            return match.group(1), 1.0
        return None, 0.0

    def _extract_date(self, text: str) -> tuple[str | None, float]:
        match = _ISO_DATE_RE.search(text)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}", 1.0
        match = _TEXT_DATE_RE.search(text)
        if match:
            day = match.group(1).zfill(2)
            month = _MONTHS[match.group(2)[:3].lower()]
            year = match.group(3)
            return f"{year}-{month}-{day}", 1.0
        # Slash- and dash-separated dates are locale-ambiguous (DD/MM vs
        # MM/DD). We pick DD/MM (international convention) but flag low
        # confidence so the LLM/user can correct it.
        match = _SLASH_DATE_RE.search(text) or _DASH_DATE_RE.search(text)
        if match:
            a, b, year = match.group(1), match.group(2), match.group(3)
            day, month = a, b
            try:
                if int(day) > 12 or int(month) > 12:
                    if int(day) <= 12 and int(month) > 12:
                        day, month = b, a
            except ValueError:
                pass
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}", 0.5
        return None, 0.0

    def _extract_vendor(self, text: str) -> tuple[str | None, float]:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if _DIGITS_ONLY_RE.match(line):
                continue
            if _ADDRESSY_RE.search(line):
                continue
            return line, 0.5
        return None, 0.0

    def _extract_line_items(
        self, text: str, *, exclude_line: str | None
    ) -> list[dict]:
        items: list[dict] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if exclude_line and line == exclude_line:
                continue
            match = _LINE_ITEM_RE.match(line)
            if not match:
                continue
            description = match.group(1).strip()
            # Skip "header"-shaped lines like "Total 19.99" — the keyword
            # would have populated total already.
            if _TOTAL_KEYWORD_RE.search(line):
                continue
            amount = self._normalise_amount(match.group(2))
            items.append({"description": description, "amount": amount})
        return items

    # ------------------------------------------------------------------
    # Helpers — row mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _row_from_extracted(
        extracted: dict, columns: list[str], image_path: str
    ) -> list:
        items_summary = "; ".join(
            f"{item.get('description', '')} {item.get('amount', '')}"
            for item in extracted.get("line_items") or []
        )
        field_map: dict[str, Any] = {
            "date": extracted.get("date") or "",
            "vendor": extracted.get("vendor") or "",
            "total": extracted.get("total") if extracted.get("total") is not None else "",
            "currency": extracted.get("currency") or "",
            "items_summary": items_summary,
            "image_path": image_path,
        }
        return [field_map.get(col, "") for col in columns]


def create(
    google_workspace_plugin=None,
    *,
    ocr_fn: OcrFn | None = None,
    pdf_to_image_fn: PdfToImageFn | None = None,
) -> FinancePlugin:
    return FinancePlugin(
        google_workspace_plugin=google_workspace_plugin,
        ocr_fn=ocr_fn,
        pdf_to_image_fn=pdf_to_image_fn,
    )
