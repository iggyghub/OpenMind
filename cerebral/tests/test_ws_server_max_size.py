"""Regression test for the IPC server's WebSocket max_size (2026-08-26).

Background: `websockets.serve()` defaults `max_size` to 1 MiB and silently
closes the connection on anything bigger -- no error surfaced to the tray.
upload_book's base64-encoded file payload blows past that for any real
book (the feature's own live-verify used a one-paragraph test file, well
under 1 MiB, so "upload a book" appeared broken with zero feedback: the
user saw nothing happen after picking a real file). Source-level check
(no real socket) since spinning up the actual server/tray round trip
isn't worth it for a one-line config constant.
"""
import inspect
import cerebral.main as main


def test_ipc_server_max_size_is_raised_above_the_websockets_default():
    source = inspect.getsource(main.main)
    assert "serve(_ws_handler, HOST, PORT, max_size=" in source, (
        "serve() must override the default 1 MiB max_size -- otherwise "
        "any upload (e.g. upload_book) over ~750KB silently drops the "
        "connection with no error shown to the user"
    )
