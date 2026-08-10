"""send_image_to_discord macro tests -- hermetic (no real screen/keys/focus)."""
import json

from plugins.discord_send import DiscordSendPlugin


def _plugin(focus_ok=True, record=None):
    rec = record if record is not None else []
    return DiscordSendPlugin(
        screenshot_fn=lambda: b"PNGBYTES",
        clipboard_fn=lambda b: rec.append(("clipboard", b)),
        focus_fn=lambda title: (rec.append(("focus", title)) or focus_ok),
        press_fn=lambda keys: rec.append(("press", "+".join(keys))),
        type_fn=lambda text: rec.append(("type", text)),
        sleep_fn=lambda s: None,
    ), rec


async def test_full_sequence_in_order():
    plugin, rec = _plugin(focus_ok=True)
    r = await plugin.call_tool("send_image_to_discord", {"contact": "Budd"})
    assert not r.is_error
    assert json.loads(r.content)["ok"] is True
    # Exact order: copy image -> focus -> Ctrl+K -> type Budd -> Enter ->
    # Ctrl+V -> Enter (no message).
    assert rec == [
        ("clipboard", b"PNGBYTES"),
        ("focus", "Discord"),
        ("press", "ctrl+k"),
        ("type", "Budd"),
        ("press", "enter"),
        ("press", "ctrl+v"),
        ("press", "enter"),
    ]


async def test_message_typed_before_send():
    plugin, rec = _plugin(focus_ok=True)
    await plugin.call_tool(
        "send_image_to_discord", {"contact": "Budd", "message": "gg"},
    )
    # The message is typed after the paste, before the final Enter.
    assert ("type", "gg") in rec
    assert rec.index(("type", "gg")) < len(rec) - 1
    assert rec[-1] == ("press", "enter")


async def test_refuses_when_discord_not_focused():
    plugin, rec = _plugin(focus_ok=False)
    r = await plugin.call_tool("send_image_to_discord", {"contact": "Budd"})
    assert r.is_error
    assert "front" in r.content.lower()
    # It copied + tried to focus, but pressed NO keys (never typed/sent).
    assert not any(a[0] in ("press", "type") for a in rec)


async def test_requires_contact():
    plugin, _ = _plugin()
    r = await plugin.call_tool("send_image_to_discord", {"contact": "  "})
    assert r.is_error


def test_tool_is_marked_irreversible():
    tool = DiscordSendPlugin().list_tools()[0]
    assert tool.irreversible is True   # sends a message -> confirm modal
