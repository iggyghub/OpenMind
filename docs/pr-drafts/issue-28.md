# PR draft — Issue #28

Use this with `/create-pr` (or `gh pr create --body-file docs/pr-drafts/issue-28.md`).
The branch `issue-28-finance-mcp` is one commit ahead of `issue-27-hardware-mcp`,
so see `docs/pr-drafts/issue-27.md` if you fold #27 + #28 into one PR; otherwise
open #28 standalone against `issue-27-hardware-mcp` (or `master` after #27 lands).

---

## Title

`Implement issue #28: Finance MCP — Receipt OCR + Sheets/Grist append`

## Summary

Adds one new MCP plugin — `plugins/finance.py` — that turns a receipt image
or scanned PDF into a structured row in a Google Sheet (or, transparently,
a local Grist table when n8n is unreachable). No autopilot path: the
`finance_log_expense` tool requires `confirm: true` to actually write.

## Step-by-step

Each step is one logical layer of the plugin; tests cover every step in
isolation via injected fakes (no real Tesseract / pdf2image / n8n calls).

1. **Read the input.** `finance_extract_receipt(image_path)` validates the
   path (rejects empty / non-existent — same fail-loud pattern as
   `plugins/printer.py`), then routes to `pdf_to_image_fn` for `.pdf`
   inputs (page 1 only — multi-page is out of scope for v1) or directly
   to `ocr_fn` for images. Both functions are injectable; defaults are
   `pytesseract.image_to_string(Image.open(...))` and
   `pdf2image.convert_from_path`.
2. **Parse the OCR text.** Pure regex, no LLM in the plugin:
   - `total` — keyword anchor (`total|amount|grand total|balance`) →
     confidence 1.0; bare currency-like number → 0.5; nothing → 0.0.
   - `currency` — `$/£/€/¥` map to `USD/GBP/EUR/JPY`; literal ISO 4217
     code fallback (`USD`, `EUR`, `CAD`, …); default `None`.
   - `date` — ISO `YYYY-MM-DD` (1.0); `D MMM YYYY` (1.0); slash/dash
     forms (0.5 — locale-ambiguous, flagged for review).
   - `vendor` — first non-empty line that doesn't look like an address
     or pure digits (heuristic, confidence 0.5).
   - `line_items` — `^(.+?)\s+([0-9]+[.,][0-9]{2})\s*$`; the line that
     produced `total` is excluded.
3. **Map to a sheet row.** `finance_log_expense(image_path, sheet_target,
   confirm?, columns?)` runs step 1 + step 2, then maps the extracted
   dict to the configured column list.
   - Default columns: `[date, vendor, total, currency, items_summary,
     image_path]`.
   - Override per-call via `sheet_target.columns`.
   - The A1 range is narrowed to the column count
     (e.g. `Sheet1!A:F` for 6 cols, `Sheet1!A:B` for 2).
4. **Confirm before writing.** With `confirm` omitted or false (the
   default) the tool returns the extraction + the would-be row but
   **does not** call the workspace plugin. The LLM is expected to
   recite the row back to the user and re-call with `confirm: true`
   only after explicit confirmation. A test asserts that the workspace
   plugin's `call_tool` is never invoked when `confirm=false`.
5. **Delegate the append.** With `confirm: true`, the plugin forwards
   to the existing `google_workspace.sheets_write_range` tool — same
   delegation pattern `plugins/zoom.py` uses for n8n. The Grist
   fallback (`plugins/google_workspace_fallback.py`) detects
   connectivity failures on the primary path and routes the same
   payload to the local Grist instance, so `{grist_table: "Expenses"}`
   and `{spreadsheet_id: "…", sheet_name: "Expenses"}` both go through
   `sheets_write_range` — no special-case Grist code in this plugin.

## Files changed

| File | Why |
|------|-----|
| `plugins/finance.py` | New plugin — `FinancePlugin`, 2 tools, regex parser, factory `create()`. |
| `cerebral/tests/test_plugin_finance.py` | 40 unit tests (TDD vertical slices: arg validation → confidence levels → currency/date coverage → vendor/line items → confirm guard → write payload → Grist routing → PDF input → unknown tool → factory). |
| `cerebral/requirements.txt` | `pytesseract`, `pdf2image`, `Pillow`. |
| `HANDOFF.md` | `### Issue #28 — Finance MCP ✅` retrospective; table tick; "Next issue: #29." |
| `SETUP.md` | `### Finance plugin (#28)` — Tesseract + Poppler install per OS, sheet schema example, confirm-first safety note. |

## Counts

- **Plugins:** 29 → **30**
- **Tools:** 100 → **102** (+2 finance)
- **Python tests:** 710 → **750 passing**, 3 integration skipped
- **JS tests:** 50 passing (unchanged)

## Safety notes

- `finance_log_expense` defaults to `confirm=False`. Tests assert the
  workspace plugin is **not** invoked in that case — protects against an
  LLM accidentally autopiloting an expense write before the user has
  seen the row.
- File-path validation rejects empty / non-existent paths up front and
  surfaces the path in the error message. The path is never shelled out
  with — pure Python OCR + HTTP via the workspace plugin.
- Low-confidence fields (`confidence.<field> < 0.6`, notably locale-
  ambiguous slash dates and bare-number totals) are flagged in the
  returned dict so the LLM can highlight them during the confirmation
  step.

## External installs (from `SETUP.md`)

- **Tesseract OCR binary** on PATH:
  - Linux: `apt install tesseract-ocr`
  - macOS: `brew install tesseract`
  - Windows: UB-Mannheim build, add to PATH.
- **Poppler** (required by `pdf2image` for PDF input):
  - Linux: `apt install poppler-utils`
  - macOS: `brew install poppler`
  - Windows: poppler-windows release on PATH.

## Test plan

- [ ] `cd cerebral && python -m pytest tests/test_plugin_finance.py -v`
      → expect 40 passing.
- [ ] `cd cerebral && python -m pytest tests/` → expect 750 passing,
      3 integration skipped.
- [ ] `cd tray && npm test` → expect 50 passing.
- [ ] (Optional, requires Tesseract on PATH) drop a real receipt JPG
      into a tmp dir and call
      `finance_extract_receipt({"image_path": "<path>"})` from a Python
      REPL to spot-check the parser end-to-end.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
