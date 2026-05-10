"""
Finance MCP plugin tests — Issue #28 (Finance MCP — AFK).

Tools:
  - finance_extract_receipt(image_path)
  - finance_log_expense(image_path, sheet_target, confirm=False, columns?)

All side effects are injected:
  - ocr_fn(image_path) -> str          — never invokes Tesseract
  - pdf_to_image_fn(pdf_path) -> [str] — never invokes pdf2image
  - google_workspace_plugin            — fake with recording call_tool;
                                          tests assert call_tool was NOT
                                          invoked when confirm=False
"""
import json
import sys
from pathlib import Path

import pytest

# Ensure plugins/ is importable from anywhere in the tree
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Helpers — fake workspace plugin that records sheets_write_range calls
# ---------------------------------------------------------------------------


class FakeWorkspace:
    """Mirror of plugins.zoom.py's n8n injection in tests — records every
    call_tool invocation and can be primed to return success or error."""

    name = "google_workspace"

    def __init__(self, error: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._error = error

    async def call_tool(self, tool_name: str, args: dict):
        from cerebral.mcp.orchestrator import ToolResult

        self.calls.append((tool_name, args))
        if self._error:
            return ToolResult(
                content=f"workspace error for {tool_name}",
                is_error=True,
            )
        return ToolResult(content=json.dumps({"appended": 1}))


# Sample receipt OCR outputs ------------------------------------------------

_RECEIPT_USD = """\
Acme Coffee Co.
123 Main Street
San Francisco, CA 94110

Latte                 4.50
Bagel                 3.25
Croissant             2.95

Subtotal             10.70
Tax                   0.95
Total                $11.65

2026-04-15 10:42 AM
"""

_RECEIPT_GBP = """\
Tesco Express
14 High Street

Bread                 1.20
Milk                  0.95

Total                £2.15

15/04/2026
"""

_RECEIPT_BARE_AMOUNT = """\
Random Note
A line that is not a price
Another line with no anchor
12.50
"""

_RECEIPT_TEXT_DATE = """\
Books Inc
Novel                12.99
Total               £12.99
3 Apr 2026
"""


def _make_ocr(text: str):
    """Build an injectable ocr_fn that returns a fixed text and records calls."""
    captured: dict = {"calls": 0, "paths": []}

    def ocr_fn(path: str) -> str:
        captured["calls"] += 1
        captured["paths"].append(path)
        return text

    return ocr_fn, captured


def _touch(tmp_path: Path, name: str = "receipt.png", body: bytes = b"png") -> str:
    p = tmp_path / name
    p.write_bytes(body)
    return str(p)


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools / factory
# ---------------------------------------------------------------------------


class TestListTools:
    def test_create_returns_finance_plugin(self):
        from plugins.finance import FinancePlugin, create

        plugin = create(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=lambda _p: "",
            pdf_to_image_fn=lambda _p: [],
        )
        assert isinstance(plugin, FinancePlugin)
        assert plugin.name == "finance"

    def test_list_tools_exposes_two_tools(self):
        from plugins.finance import FinancePlugin

        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=lambda _p: "",
        )
        names = {t.name for t in plugin.list_tools()}
        assert names == {"finance_extract_receipt", "finance_log_expense"}

    def test_tool_names_are_prefixed(self):
        """Flat-global namespace per .learnings/LEARNINGS.md."""
        from plugins.finance import FinancePlugin

        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=lambda _p: "",
        )
        for tool in plugin.list_tools():
            assert tool.name.startswith("finance_")
            assert tool.plugin == "finance"

    def test_extract_receipt_requires_image_path(self):
        from plugins.finance import FinancePlugin

        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=lambda _p: "",
        )
        tool = next(
            t for t in plugin.list_tools() if t.name == "finance_extract_receipt"
        )
        assert "image_path" in tool.schema["required"]

    def test_log_expense_requires_image_path_and_sheet_target(self):
        from plugins.finance import FinancePlugin

        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=lambda _p: "",
        )
        tool = next(
            t for t in plugin.list_tools() if t.name == "finance_log_expense"
        )
        required = set(tool.schema["required"])
        assert "image_path" in required
        assert "sheet_target" in required


# ---------------------------------------------------------------------------
# Cycle 2 — required-arg / file-path validation
# ---------------------------------------------------------------------------


class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_extract_missing_image_path_returns_error(self):
        from plugins.finance import FinancePlugin

        ocr, captured = _make_ocr("")
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        result = await plugin.call_tool("finance_extract_receipt", {})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_extract_empty_image_path_returns_error(self):
        from plugins.finance import FinancePlugin

        ocr, captured = _make_ocr("")
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": ""}
        )
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_extract_nonexistent_image_path_returns_error(self):
        """Same fail-loud pattern as plugins/printer.py — surface the path."""
        from plugins.finance import FinancePlugin

        ocr, captured = _make_ocr("")
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        bogus = "/definitely/does/not/exist/receipt.png"
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": bogus}
        )
        assert result.is_error
        assert bogus in result.content
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_log_expense_missing_image_path_returns_error(self):
        from plugins.finance import FinancePlugin

        ws = FakeWorkspace()
        ocr, _ = _make_ocr("")
        plugin = FinancePlugin(google_workspace_plugin=ws, ocr_fn=ocr)
        result = await plugin.call_tool(
            "finance_log_expense",
            {"sheet_target": {"spreadsheet_id": "abc"}},
        )
        assert result.is_error
        assert ws.calls == []

    @pytest.mark.asyncio
    async def test_log_expense_missing_sheet_target_returns_error(
        self, tmp_path
    ):
        from plugins.finance import FinancePlugin

        ws = FakeWorkspace()
        ocr, _ = _make_ocr("")
        plugin = FinancePlugin(google_workspace_plugin=ws, ocr_fn=ocr)
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_log_expense", {"image_path": path}
        )
        assert result.is_error
        assert ws.calls == []

    @pytest.mark.asyncio
    async def test_log_expense_invalid_sheet_target_returns_error(
        self, tmp_path
    ):
        from plugins.finance import FinancePlugin

        ws = FakeWorkspace()
        ocr, _ = _make_ocr("")
        plugin = FinancePlugin(google_workspace_plugin=ws, ocr_fn=ocr)
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_log_expense",
            {"image_path": path, "sheet_target": {"foo": "bar"}},
        )
        assert result.is_error
        assert ws.calls == []


# ---------------------------------------------------------------------------
# Cycle 3 — happy-path extraction returns expected dict shape
# ---------------------------------------------------------------------------


class TestExtractHappyPath:
    @pytest.mark.asyncio
    async def test_extract_receipt_returns_expected_shape(self, tmp_path):
        from plugins.finance import FinancePlugin

        ocr, _ = _make_ocr(_RECEIPT_USD)
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        assert not result.is_error
        data = json.loads(result.content)
        # Top-level keys
        assert set(data.keys()) >= {
            "vendor", "date", "total", "currency", "line_items", "confidence"
        }
        # Confidence is per-field
        assert set(data["confidence"].keys()) >= {
            "vendor", "date", "total", "currency"
        }

    @pytest.mark.asyncio
    async def test_extract_passes_image_path_to_ocr(self, tmp_path):
        from plugins.finance import FinancePlugin

        ocr, captured = _make_ocr(_RECEIPT_USD)
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        assert captured["calls"] == 1
        assert captured["paths"] == [path]


# ---------------------------------------------------------------------------
# Cycle 4 — total: keyword anchor (1.0) vs bare number (0.5) vs nothing (0.0)
# ---------------------------------------------------------------------------


class TestTotalConfidence:
    @pytest.mark.asyncio
    async def test_keyword_anchored_total_has_confidence_1_0(self, tmp_path):
        from plugins.finance import FinancePlugin

        ocr, _ = _make_ocr(_RECEIPT_USD)
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        data = json.loads(result.content)
        assert data["total"] == 11.65
        assert data["confidence"]["total"] == 1.0

    @pytest.mark.asyncio
    async def test_bare_amount_has_confidence_0_5(self, tmp_path):
        from plugins.finance import FinancePlugin

        ocr, _ = _make_ocr(_RECEIPT_BARE_AMOUNT)
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        data = json.loads(result.content)
        assert data["total"] == 12.50
        assert data["confidence"]["total"] == 0.5
        # < 0.6 means low-confidence — flagged for review.
        assert data["confidence"]["total"] < 0.6

    @pytest.mark.asyncio
    async def test_no_amount_yields_confidence_0_0(self, tmp_path):
        from plugins.finance import FinancePlugin

        ocr, _ = _make_ocr("Just a memo with no numbers.")
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        data = json.loads(result.content)
        assert data["total"] is None
        assert data["confidence"]["total"] == 0.0


# ---------------------------------------------------------------------------
# Cycle 5 — currency: symbol → ISO 4217
# ---------------------------------------------------------------------------


class TestCurrencyMapping:
    @pytest.mark.parametrize(
        "symbol,iso",
        [("$", "USD"), ("£", "GBP"), ("€", "EUR"), ("¥", "JPY")],
    )
    @pytest.mark.asyncio
    async def test_symbol_maps_to_iso(self, tmp_path, symbol, iso):
        from plugins.finance import FinancePlugin

        ocr, _ = _make_ocr(f"Coffee\nTotal {symbol}5.00\n")
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        data = json.loads(result.content)
        assert data["currency"] == iso

    @pytest.mark.asyncio
    async def test_no_currency_default_none(self, tmp_path):
        from plugins.finance import FinancePlugin

        ocr, _ = _make_ocr("Total 5.00")
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        data = json.loads(result.content)
        assert data["currency"] is None
        assert data["confidence"]["currency"] == 0.0

    @pytest.mark.asyncio
    async def test_iso_code_fallback(self, tmp_path):
        from plugins.finance import FinancePlugin

        ocr, _ = _make_ocr("Vendor\nTotal 12.34 CAD")
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        data = json.loads(result.content)
        assert data["currency"] == "CAD"


# ---------------------------------------------------------------------------
# Cycle 6 — date format coverage (ISO / DD-MM / MM-DD / D MMM)
# ---------------------------------------------------------------------------


class TestDateExtraction:
    @pytest.mark.asyncio
    async def test_iso_date_high_confidence(self, tmp_path):
        from plugins.finance import FinancePlugin

        ocr, _ = _make_ocr(_RECEIPT_USD)
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        data = json.loads(result.content)
        assert data["date"] == "2026-04-15"
        assert data["confidence"]["date"] == 1.0

    @pytest.mark.asyncio
    async def test_text_date_high_confidence(self, tmp_path):
        from plugins.finance import FinancePlugin

        ocr, _ = _make_ocr(_RECEIPT_TEXT_DATE)
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        data = json.loads(result.content)
        assert data["date"] == "2026-04-03"
        assert data["confidence"]["date"] == 1.0

    @pytest.mark.asyncio
    async def test_slash_date_low_confidence_due_to_locale(self, tmp_path):
        from plugins.finance import FinancePlugin

        ocr, _ = _make_ocr(_RECEIPT_GBP)
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        data = json.loads(result.content)
        assert data["date"] == "2026-04-15"
        # Locale ambiguity → flagged for review.
        assert data["confidence"]["date"] == 0.5

    @pytest.mark.asyncio
    async def test_no_date_zero_confidence(self, tmp_path):
        from plugins.finance import FinancePlugin

        ocr, _ = _make_ocr("Vendor\nTotal $5.00\n")
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        data = json.loads(result.content)
        assert data["date"] is None
        assert data["confidence"]["date"] == 0.0


# ---------------------------------------------------------------------------
# Cycle 7 — vendor heuristic (first non-empty non-address line)
# ---------------------------------------------------------------------------


class TestVendorExtraction:
    @pytest.mark.asyncio
    async def test_first_non_empty_line_is_vendor(self, tmp_path):
        from plugins.finance import FinancePlugin

        ocr, _ = _make_ocr(_RECEIPT_USD)
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        data = json.loads(result.content)
        assert data["vendor"] == "Acme Coffee Co."
        # Heuristic — confidence is 0.5 even when found.
        assert data["confidence"]["vendor"] == 0.5

    @pytest.mark.asyncio
    async def test_skips_addressy_first_line(self, tmp_path):
        from plugins.finance import FinancePlugin

        text = "123 Main Street\nReal Vendor Inc\nTotal $5.00\n"
        ocr, _ = _make_ocr(text)
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        data = json.loads(result.content)
        assert data["vendor"] == "Real Vendor Inc"

    @pytest.mark.asyncio
    async def test_skips_digits_only_first_line(self, tmp_path):
        from plugins.finance import FinancePlugin

        text = "12345\nVendor Co\nTotal $5.00\n"
        ocr, _ = _make_ocr(text)
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        data = json.loads(result.content)
        assert data["vendor"] == "Vendor Co"


# ---------------------------------------------------------------------------
# Cycle 8 — line items
# ---------------------------------------------------------------------------


class TestLineItems:
    @pytest.mark.asyncio
    async def test_extracts_line_items_excluding_total(self, tmp_path):
        from plugins.finance import FinancePlugin

        ocr, _ = _make_ocr(_RECEIPT_USD)
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        data = json.loads(result.content)
        descs = {item["description"] for item in data["line_items"]}
        # Real items appear
        assert "Latte" in descs
        assert "Bagel" in descs
        assert "Croissant" in descs
        # Total/Subtotal/Tax are keyword-anchored; the explicit "Total"
        # line is excluded.
        for item in data["line_items"]:
            assert "Total" not in item["description"]

    @pytest.mark.asyncio
    async def test_no_line_items_returns_empty_list(self, tmp_path):
        from plugins.finance import FinancePlugin

        ocr, _ = _make_ocr("Vendor Co\nTotal $5.00\n2026-04-15\n")
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
        )
        path = _touch(tmp_path)
        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        data = json.loads(result.content)
        assert data["line_items"] == []


# ---------------------------------------------------------------------------
# Cycle 9 — confirm=False does NOT call the workspace plugin
# ---------------------------------------------------------------------------


class TestConfirmGuard:
    @pytest.mark.asyncio
    async def test_default_confirm_false_does_not_write(self, tmp_path):
        from plugins.finance import FinancePlugin

        ws = FakeWorkspace()
        ocr, _ = _make_ocr(_RECEIPT_USD)
        plugin = FinancePlugin(google_workspace_plugin=ws, ocr_fn=ocr)
        path = _touch(tmp_path)

        result = await plugin.call_tool(
            "finance_log_expense",
            {
                "image_path": path,
                "sheet_target": {
                    "spreadsheet_id": "abc-123",
                    "sheet_name": "Expenses",
                },
            },
        )
        assert not result.is_error
        # CRITICAL: workspace plugin must NOT have been called.
        assert ws.calls == []
        data = json.loads(result.content)
        assert data["written"] is False
        # The would-be row is returned so the LLM can show the user.
        assert "row" in data

    @pytest.mark.asyncio
    async def test_confirm_false_explicit_does_not_write(self, tmp_path):
        from plugins.finance import FinancePlugin

        ws = FakeWorkspace()
        ocr, _ = _make_ocr(_RECEIPT_USD)
        plugin = FinancePlugin(google_workspace_plugin=ws, ocr_fn=ocr)
        path = _touch(tmp_path)

        await plugin.call_tool(
            "finance_log_expense",
            {
                "image_path": path,
                "sheet_target": {"spreadsheet_id": "abc"},
                "confirm": False,
            },
        )
        assert ws.calls == []


# ---------------------------------------------------------------------------
# Cycle 10 — confirm=True calls sheets_write_range with the right payload
# ---------------------------------------------------------------------------


class TestConfirmedWrite:
    @pytest.mark.asyncio
    async def test_confirm_true_calls_sheets_write_range(self, tmp_path):
        from plugins.finance import FinancePlugin

        ws = FakeWorkspace()
        ocr, _ = _make_ocr(_RECEIPT_USD)
        plugin = FinancePlugin(google_workspace_plugin=ws, ocr_fn=ocr)
        path = _touch(tmp_path)

        result = await plugin.call_tool(
            "finance_log_expense",
            {
                "image_path": path,
                "sheet_target": {
                    "spreadsheet_id": "abc-123",
                    "sheet_name": "Expenses",
                },
                "confirm": True,
            },
        )
        assert not result.is_error
        assert len(ws.calls) == 1
        tool_name, write_args = ws.calls[0]
        assert tool_name == "sheets_write_range"
        assert write_args["spreadsheet_id"] == "abc-123"
        assert write_args["range"].startswith("Expenses!A:")
        # data is a 2D array (one row)
        assert isinstance(write_args["data"], list)
        assert len(write_args["data"]) == 1
        row = write_args["data"][0]
        # Default columns: [date, vendor, total, currency, items_summary, image_path]
        assert row[0] == "2026-04-15"
        assert row[1] == "Acme Coffee Co."
        assert row[2] == 11.65
        assert row[3] == "USD"
        # items_summary is a joined string
        assert isinstance(row[4], str)
        assert row[5] == path

    @pytest.mark.asyncio
    async def test_custom_columns_override_default(self, tmp_path):
        from plugins.finance import FinancePlugin

        ws = FakeWorkspace()
        ocr, _ = _make_ocr(_RECEIPT_USD)
        plugin = FinancePlugin(google_workspace_plugin=ws, ocr_fn=ocr)
        path = _touch(tmp_path)

        await plugin.call_tool(
            "finance_log_expense",
            {
                "image_path": path,
                "sheet_target": {
                    "spreadsheet_id": "abc",
                    "sheet_name": "Sheet1",
                    "columns": ["vendor", "total"],
                },
                "confirm": True,
            },
        )
        write_args = ws.calls[0][1]
        # Range narrows to A:B for two columns
        assert write_args["range"] == "Sheet1!A:B"
        row = write_args["data"][0]
        assert row == ["Acme Coffee Co.", 11.65]

    @pytest.mark.asyncio
    async def test_confirm_true_propagates_workspace_error(self, tmp_path):
        from plugins.finance import FinancePlugin

        ws = FakeWorkspace(error=True)
        ocr, _ = _make_ocr(_RECEIPT_USD)
        plugin = FinancePlugin(google_workspace_plugin=ws, ocr_fn=ocr)
        path = _touch(tmp_path)

        result = await plugin.call_tool(
            "finance_log_expense",
            {
                "image_path": path,
                "sheet_target": {"spreadsheet_id": "abc"},
                "confirm": True,
            },
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_grist_target_routes_via_sheets_write_range(self, tmp_path):
        """The Grist fallback is transparent — same delegation path, the
        fallback wrapper picks it up when n8n is unreachable."""
        from plugins.finance import FinancePlugin

        ws = FakeWorkspace()
        ocr, _ = _make_ocr(_RECEIPT_USD)
        plugin = FinancePlugin(google_workspace_plugin=ws, ocr_fn=ocr)
        path = _touch(tmp_path)

        await plugin.call_tool(
            "finance_log_expense",
            {
                "image_path": path,
                "sheet_target": {"grist_table": "Expenses"},
                "confirm": True,
            },
        )
        assert len(ws.calls) == 1
        tool_name, args = ws.calls[0]
        assert tool_name == "sheets_write_range"
        # Grist fallback parses the table_id from the range, so the range
        # must start with the table name.
        assert args["range"].startswith("Expenses!")


# ---------------------------------------------------------------------------
# Cycle 11 — PDF input path uses pdf_to_image_fn before ocr_fn
# ---------------------------------------------------------------------------


class TestPdfInput:
    @pytest.mark.asyncio
    async def test_pdf_path_calls_pdf_to_image_then_ocr(self, tmp_path):
        from plugins.finance import FinancePlugin

        # The PDF "rasteriser" returns the path to a fake PNG that the OCR
        # then reads.
        rasterised = _touch(tmp_path, name="page1.png")
        pdf_calls: list[str] = []

        def fake_pdf_to_image(pdf_path: str) -> list[str]:
            pdf_calls.append(pdf_path)
            return [rasterised]

        ocr, ocr_captured = _make_ocr(_RECEIPT_USD)
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
            pdf_to_image_fn=fake_pdf_to_image,
        )
        pdf_path = _touch(tmp_path, name="receipt.pdf", body=b"%PDF-1.4")

        result = await plugin.call_tool(
            "finance_extract_receipt", {"image_path": pdf_path}
        )
        assert not result.is_error
        # PDF rasteriser was invoked with the PDF path
        assert pdf_calls == [pdf_path]
        # OCR was invoked on the rasterised PNG, not the PDF itself
        assert ocr_captured["paths"] == [rasterised]

    @pytest.mark.asyncio
    async def test_image_path_skips_pdf_to_image(self, tmp_path):
        from plugins.finance import FinancePlugin

        pdf_calls: list[str] = []

        def fake_pdf_to_image(pdf_path: str) -> list[str]:
            pdf_calls.append(pdf_path)
            return []

        ocr, _ = _make_ocr(_RECEIPT_USD)
        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=ocr,
            pdf_to_image_fn=fake_pdf_to_image,
        )
        path = _touch(tmp_path, name="receipt.png")

        await plugin.call_tool(
            "finance_extract_receipt", {"image_path": path}
        )
        assert pdf_calls == []


# ---------------------------------------------------------------------------
# Cycle 12 — unknown tool returns is_error
# ---------------------------------------------------------------------------


class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.finance import FinancePlugin

        plugin = FinancePlugin(
            google_workspace_plugin=FakeWorkspace(),
            ocr_fn=lambda _p: "",
        )
        result = await plugin.call_tool("not_a_real_tool", {})
        assert result.is_error
