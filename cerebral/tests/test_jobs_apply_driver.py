"""#423 — the apply driver maps dossier values onto REAL DOM selectors.

Live failure 2026-07-18: the LLM invented CSS selectors from page text; the
first miss burned a 30s Page.fill timeout and failed the whole application.
"""
from __future__ import annotations

import json
import types

import cerebral.main as main


class _Router:
    def __init__(self, response: str):
        self.response = response

    async def complete(self, prompt, task_type="chat"):
        self.prompt = prompt
        return self.response


class _Session:
    def __init__(self, dom_fields, fail_selectors=()):
        self.dom_fields = dom_fields
        self.fail_selectors = set(fail_selectors)
        self.filled: list[tuple[str, str]] = []
        self.uploaded: list[tuple[str, str]] = []
        self.selected: list[tuple[str, str]] = []
        self.clicked: list[str] = []

    async def read_page(self, url):
        return types.SimpleNamespace(url=url, title="", text="form")

    async def list_form_fields(self):
        return self.dom_fields

    async def fill_fields(self, pairs):
        for sel, val in pairs:
            if sel in self.fail_selectors:
                raise RuntimeError(f"Page.fill: Timeout 5000ms exceeded ({sel})")
            self.filled.append((sel, val))

    async def select_option(self, selector, label):
        self.selected.append((selector, label))

    async def click(self, selector):
        self.clicked.append(selector)
        return "https://ats/x"

    async def upload_file(self, selector, path):
        self.uploaded.append((selector, path))


def _wire(monkeypatch, session, llm_fields):
    monkeypatch.setattr(main, "_router", _Router(json.dumps(llm_fields)))
    monkeypatch.setattr(main, "_get_open_browser_session", lambda: session)


async def test_invented_selectors_are_dropped(monkeypatch):
    session = _Session([
        {"selector": "#first_name", "label": "First name", "type": "text", "required": True},
    ])
    _wire(monkeypatch, session, [
        {"selector": "#first_name", "label": "First name", "value": "Iggy", "required": False},
        {"selector": "input[name=made_up]", "label": "Ghost", "value": "x", "required": True},
    ])

    draft = await main._jobs_apply_driver("https://ats/x", {}, "")

    sels = [f["selector"] for f in draft["fields"]]
    assert sels == ["#first_name"]          # invented selector dropped
    assert draft["fields"][0]["required"] is True  # DOM required wins
    assert session.filled == [("#first_name", "Iggy")]


async def test_single_fill_failure_clears_field_not_apply(monkeypatch):
    session = _Session(
        [
            {"selector": "#a", "label": "A", "type": "text", "required": True},
            {"selector": "#b", "label": "B", "type": "text", "required": False},
        ],
        fail_selectors={"#a"},
    )
    _wire(monkeypatch, session, [
        {"selector": "#a", "label": "A", "value": "va", "required": True},
        {"selector": "#b", "label": "B", "value": "vb", "required": False},
    ])

    draft = await main._jobs_apply_driver("https://ats/x", {}, "")

    by_sel = {f["selector"]: f for f in draft["fields"]}
    assert by_sel["#a"]["value"] == ""      # cleared -> awaiting-input upstream
    assert by_sel["#a"]["is_known"] is False
    assert session.filled == [("#b", "vb")]  # the rest still filled


async def test_resume_input_found_from_dom_when_llm_misses_it(monkeypatch):
    session = _Session([
        {"selector": "#resume", "label": "Resume/CV", "type": "file", "required": True},
    ])
    _wire(monkeypatch, session, [])  # LLM returns nothing

    draft = await main._jobs_apply_driver("https://ats/x", {}, "cv.pdf")

    assert session.uploaded == [("#resume", "cv.pdf")]
    assert any(f.get("is_file_upload") for f in draft["fields"])


async def test_unmapped_required_dom_fields_are_carried(monkeypatch):
    """#425 — required DOM fields the LLM omits must reach the field list
    with empty values, so _apply_start escalates to awaiting-input instead
    of declaring an unfilled form ready_to_submit (the live Nourish bug)."""
    session = _Session([
        {"selector": "#first_name", "label": "First name", "type": "text", "required": True},
        {"selector": "#nickname", "label": "Nickname", "type": "text", "required": False},
    ])
    _wire(monkeypatch, session, [])  # LLM maps nothing

    draft = await main._jobs_apply_driver("https://ats/x", {}, "")

    by_sel = {f["selector"]: f for f in draft["fields"]}
    assert by_sel["#first_name"]["value"] == ""
    assert by_sel["#first_name"]["required"] is True
    assert by_sel["#first_name"]["is_known"] is False
    assert "#nickname" not in by_sel  # optional unmapped fields stay out
    assert session.filled == []


async def test_select_and_radio_fill_dispatch(monkeypatch):
    """#429 — selects go through select_option; radio groups click the
    matching option's own selector; a value with no matching option clears
    the field instead of failing the apply."""
    session = _Session([
        {"selector": "#country", "label": "Country", "type": "select",
         "required": True, "options": ["United States", "Canada"]},
        {"selector": '[data-felix-field="7"]', "label": "Years of experience",
         "type": "radio", "required": True,
         "options": [{"label": "0-2", "selector": '[data-felix-field="7"]'},
                     {"label": "2-4", "selector": '[data-felix-field="8"]'}]},
        {"selector": "#pronoun", "label": "Pronouns", "type": "radio",
         "required": False,
         "options": [{"label": "they/them", "selector": "#p1"}]},
    ])
    _wire(monkeypatch, session, [
        {"selector": "#country", "label": "Country", "value": "United States"},
        {"selector": '[data-felix-field="7"]', "label": "Years of experience", "value": "2-4"},
        {"selector": "#pronoun", "label": "Pronouns", "value": "ze/zir"},  # no such option
    ])

    draft = await main._jobs_apply_driver("https://ats/x", {}, "")

    assert session.selected == [("#country", "United States")]
    assert session.clicked == ['[data-felix-field="8"]']
    by_sel = {f["selector"]: f for f in draft["fields"]}
    assert by_sel["#pronoun"]["value"] == ""      # cleared, not fatal
    assert by_sel["#pronoun"]["is_known"] is False
    assert by_sel["#country"]["options"] == ["United States", "Canada"]  # carried for the panel


async def test_empty_dom_enumeration_returns_no_fields(monkeypatch):
    session = _Session([])
    _wire(monkeypatch, session, [
        {"selector": "#anything", "label": "X", "value": "v"},
    ])

    draft = await main._jobs_apply_driver("https://ats/x", {}, "cv.pdf")

    assert draft["fields"] == []
    assert session.filled == [] and session.uploaded == []
