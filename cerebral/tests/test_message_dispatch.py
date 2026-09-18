"""FELIX-AUDIT S5 (F1): _handle_message dispatches through _MESSAGE_HANDLERS."""
import ast
import inspect
import textwrap

from cerebral import main


def _chain_types() -> set[str]:
    """String constants `_handle_message` still compares `t` against."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(main._handle_message)))
    return {
        c.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        for c in node.comparators
        if isinstance(c, ast.Constant) and isinstance(c.value, str)
    }


def test_handlers_are_coroutine_functions():
    for msg_type, fn in main._MESSAGE_HANDLERS.items():
        assert inspect.iscoroutinefunction(fn), msg_type


def test_migrated_type_is_not_also_in_the_chain():
    both = set(main._MESSAGE_HANDLERS) & _chain_types()
    assert not both, f"handled twice (table and if/elif chain): {sorted(both)}"


async def test_unknown_missing_and_nonstring_types_are_noops():
    for msg in ({"type": "no_such_type"}, {}, {"type": ["x"]}, {"type": None}):
        assert await main._handle_message(msg) is None


async def test_registered_handler_receives_the_message(monkeypatch):
    seen = []

    async def h(msg):
        seen.append(msg)

    monkeypatch.setitem(main._MESSAGE_HANDLERS, "__test_only__", h)
    await main._handle_message({"type": "__test_only__", "data": 1})
    assert seen == [{"type": "__test_only__", "data": 1}]
