"""FELIX-AUDIT S6b (F10): every `_orc.call_tool(` in main.py is an explicit gate decision."""
import ast
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "main.py"


def test_no_bare_orc_call_tool_in_main():
    src = MAIN.read_text(encoding="utf-8")
    lines = src.splitlines()
    bare = []
    for n in ast.walk(ast.parse(src)):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "call_tool"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "_orc"
        ):
            if "capability" not in {k.arg for k in n.keywords}:
                bare.append(n.lineno)
                continue
            # an exemption must say why, in a `# gate-exempt:` comment above the statement
            exempt = any(
                isinstance(k.value, ast.Name) and k.value.id == "GATE_EXEMPT" for k in n.keywords
            )
            if exempt and not any(
                "# gate-exempt:" in lines[i] for i in range(max(n.lineno - 4, 0), n.lineno)
            ):
                bare.append(n.lineno)
    assert not bare, f"_orc.call_tool without an explicit capability/GATE_EXEMPT+reason at main.py lines {bare}"


def test_orc_call_tool_never_passed_as_a_bare_reference():
    """`execute_fn=_orc.call_tool` would run call_tool with capability=None and
    re-gate a call its gate_fn already gated (double prompt): wrap it instead."""
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    bare = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute)
        and n.attr == "call_tool"
        and isinstance(n.value, ast.Name)
        and n.value.id == "_orc"
        and id(n) not in called
    ]
    assert not bare, f"_orc.call_tool used as a bare reference at main.py lines {bare}"


# Operator decision 2026-09-18 (FELIX-GATE G1/G2): these autonomous / UI-reply calls are real gates.
_MUST_BE_GATED = {"self_dev_campaign", "openclaw_messages_send", "rss_check"}


def test_autonomous_and_reply_call_sites_stay_gated():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    seen = set()
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "call_tool"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "_orc"
            and n.args
            and isinstance(n.args[0], ast.Constant)
            and n.args[0].value in _MUST_BE_GATED
        ):
            seen.add(n.args[0].value)
            exempt = any(
                k.arg == "capability" and isinstance(k.value, ast.Name) and k.value.id == "GATE_EXEMPT"
                for k in n.keywords
            )
            assert not exempt, f"{n.args[0].value} call at main.py:{n.lineno} must not be GATE_EXEMPT"
    assert seen == _MUST_BE_GATED, f"call sites not found: {_MUST_BE_GATED - seen}"
