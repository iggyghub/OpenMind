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


_EXPECTED_TYPES = frozenset({
    "shutdown",
    "health_check",
    "probe_models",
    "create_profile",
    "switch_profile",
    "delete_profile",
    "list_profiles",
    "list_voices",
    "set_voice",
    "switch_model",
    "list_models",
    "refresh_models",
    "set_model_priority",
    "set_model_enabled",
    "set_model_fallback",
    "set_task_model",
    "video_batch_toggle",
    "set_local_only",
    "set_computer_use_full_autonomy",
    "add_custom_model",
    "edit_custom_model",
    "remove_custom_model",
    "discover_models",
    "list_tools",
    "list_plugins",
    "plugins:list",
    "plugins:set_enabled",
    "plugins:test_call",
    "plugins:panels",
    "plugins:panel_spec",
    "get_plugin_settings",
    "discord_allowlist_add",
    "discord_allowlist_remove",
    "list_permissions",
    "set_class_policy",
    "revoke_class_policy",
    "set_tool_override",
    "revoke_session_grant",
    "unlock_shell_exec",
    "clear_new_plugin_flag",
    "list_credentials",
    "set_credential_client",
    "connect_google",
    "disconnect_credential",
    "set_static_token",
    "clear_static_token",
    "set_alpaca_credentials",
    "clear_alpaca_credentials",
    "set_alpaca_paper_credentials",
    "clear_alpaca_paper_credentials",
    "set_discord_user_token",
    "clear_discord_user_token",
    "set_felix_session_login",
    "clear_felix_session_login",
    "run_felix_account_setup",
    "check_felix_provisioning",
    "set_browser_login",
    "clear_browser_login",
    "seed_browser_login",
    "consent_response",
    "irreversible_modal_response",
    "call_tool",
    "computer_use_stop",
    "computer_use_take_over",
    "computer_use_release",
    "heartbeat_ack",
    "result",
    "set_isolated_session_mode",
    "computer_use_handoff_done",
    "user_text_command",
    "attach_files",
    "drop_pending_attachments",
    "list_pending_attachments",
    "list_conversation_turns",
    "list_conversation_threads",
    "new_conversation_thread",
    "switch_conversation_thread",
    "rename_conversation_thread",
    "delete_conversation_thread",
    "search_conversations",
    "list_conversation_projects",
    "create_conversation_project",
    "rename_conversation_project",
    "delete_conversation_project",
    "move_conversation_thread",
    "set_thread_model",
    "list_queue",
    "approve_item",
    "remember",
    "recall",
    "forget",
    "dismiss_item",
    "list_insights",
    "delete_insight",
    "pin_insight",
    "edit_insight",
    "list_memories",
    "edit_memory",
    "delete_memory",
    "move_memory_category",
    "reorder_memory",
    "duplicate_memory",
    "list_recipes",
    "list_job_postings",
    "jobs_fetch_postings",
    "jobs_get_dossier",
    "jobs_score_shortlist",
    "jobs_set_approval",
    "jobs_apply_start",
    "jobs_apply_submit",
    "jobs_approve_all",
    "self_dev_pr_merge",
    "self_dev_pr_state",
    "jobs_clear_postings",
    "open_felix",
    "jobs_answer_fields",
    "jobs_apply_all",
    "jobs_set_auto_submit",
    "jobs_update_dossier_field",
    "list_job_boards",
    "add_job_board",
    "remove_job_board",
    "set_job_board_enabled",
    "save_recipe",
    "rename_recipe",
    "delete_recipe",
    "run_recipe",
    "list_settings",
    "set_setting",
    "set_camera_enabled",
    "get_env_context",
    "request_harness_status",
    "start_openclaw_daemon",
    "stop_openclaw_daemon",
    "restart_openclaw_daemon",
    "set_channel_enabled",
    "set_channel_secret",
    "clear_channel_secret",
    "request_channel_inbox",
    "send_channel_reply",
    "ptt",
    "interrupt_turn",
    "list_documents",
    "list_campaign_drivers",
    "read_campaign_driver",
    "doc_save_to_disk",
    "trading_poll",
    "trading_tickers_poll",
    "activity_poll",
    "strategy_edit",
})


def test_every_message_type_has_a_handler_and_no_chain_remains():
    """S5 froze the IPC message-type set: a typo'd or dropped type fails here
    instead of silently doing nothing."""
    assert set(main._MESSAGE_HANDLERS) == _EXPECTED_TYPES
    assert not _chain_types(), "_handle_message must not compare against message types any more"
