import asyncio
import inspect
from cerebral.main import _handle_message, _MESSAGE_HANDLERS, _message_handler


def test_all_registered_handlers_are_coroutines():
    for type_str, handler in _MESSAGE_HANDLERS.items():
        assert isinstance(handler, _message_handler.__code__.co_consts[0] if hasattr(_message_handler, '__code__') else None) or inspect.iscoroutinefunction(handler), (
            f"Handler for '{type_str}' is not a coroutine function."
        )
        assert inspect.iscoroutinefunction(handler), f"Handler for '{type_str}' is not a coroutine function."


def test_no_duplicated_branches_in_handle_message():
    import ast
    source = inspect.getsource(_handle_message)
    tree = ast.parse(source)
    
    # Find all string comparisons `msg.get("type") == "x"` or `t == "x"` in the AST
    found_types_in_chain = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for op, comparator in zip(node.ops, node.comparators):
                if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    found_types_in_chain.add(comparator.value)
    
    migrated_types = set(_MESSAGE_HANDLERS.keys())
    unexpected_types = found_types_in_chain & migrated_types
    assert not unexpected_types, (
        f"Branches for types {unexpected_types} are present in both _MESSAGE_HANDLERS and "
        "the if/elif chain inside _handle_message. This would cause double-dispatch or "
        "conflicting behavior."
    )


def test_unknown_and_invalid_types_are_noops():
    """Unknown type, missing type, and non-string type must return None silently."""
    result_no_such = asyncio.run(_handle_message({"type": "no_such_type"}))
    assert result_no_such is None
    
    result_empty = asyncio.run(_handle_message({}))
    assert result_empty is None
    
    result_list_type = asyncio.run(_handle_message({"type": ["x"]}))
    assert result_list_type is None
