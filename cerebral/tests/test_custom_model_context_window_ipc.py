"""
_parse_context_window (cerebral/main.py, issue #760) -- the input-validation
gate for the add/edit_custom_model IPC's optional per-model context_window.

Pure function, no router/store/network involved -- hermetic by construction.
"""
import cerebral.main as main_mod


def test_valid_positive_integer_string_parses():
    assert main_mod._parse_context_window("131072") == 131072


def test_valid_int_passthrough():
    assert main_mod._parse_context_window(200000) == 200000


def test_blank_string_is_unset():
    assert main_mod._parse_context_window("") is None


def test_none_is_unset():
    assert main_mod._parse_context_window(None) is None


def test_whitespace_only_is_unset():
    assert main_mod._parse_context_window("   ") is None


def test_non_integer_string_rejected():
    assert main_mod._parse_context_window("not-a-number") is None


def test_float_string_rejected():
    assert main_mod._parse_context_window("8192.5") is None


def test_zero_rejected():
    assert main_mod._parse_context_window("0") is None
    assert main_mod._parse_context_window(0) is None


def test_negative_rejected():
    assert main_mod._parse_context_window("-1") is None
    assert main_mod._parse_context_window(-500) is None


def test_ollama_kind_always_unset_even_with_valid_input():
    """OllamaBackend hardcodes num_ctx=8192 regardless of whether it's the
    local ollama/* discovery path or a remote custom/<slug> of kind
    "ollama" -- a declared window bigger than that would silently lie about
    what the endpoint actually keeps, so the kind is hard-blocked."""
    assert main_mod._parse_context_window("131072", kind="ollama") is None
    assert main_mod._parse_context_window(200000, kind="ollama") is None


def test_openai_kind_unaffected_by_the_ollama_block():
    assert main_mod._parse_context_window("131072", kind="openai") == 131072
