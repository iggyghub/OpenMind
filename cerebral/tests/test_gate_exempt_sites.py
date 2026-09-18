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
