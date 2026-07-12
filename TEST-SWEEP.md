# TEST-SWEEP.md -- Test Sweep campaign driver

Driver for `scripts/run-testsweep.ps1`. Runs the full OpenMind test surface in
per-system batches, one fresh headless session per batch, so a usage-limit
reset never loses more than the batch in flight. Sessions ONLY run tests and
record results here -- no code changes, no PRs, no issues.

---

## Next slice -- start here

Active: **B8**

Model: sonnet
Status: ready

(`Model:`/`Status:` are read directly by `scripts/run-testsweep.ps1`. Allowed
models: haiku | sonnet | opus | fable. `Status: ready` = run the active batch;
`blocked` = needs a human; `done` = sweep finished. Stop gracefully any time
with `scripts/stop-testsweep.ps1`.)

### Batch queue (work top-down; each entry's command is its full spec)

1. [x] B1 -- Core loop & dispatch -- Model: sonnet
2. [x] B2 -- Memory, insights & passive -- Model: sonnet
3. [x] B3 -- Security, consent & permissions -- Model: sonnet
4. [x] B4 -- Channels, harness & browser -- Model: sonnet
5. [x] B5 -- Jobs pipeline -- Model: sonnet
6. [x] B6 -- Plugins A-G -- Model: sonnet
7. [x] B7 -- Plugins H-Z + plugin suites -- Model: sonnet
8. [ ] B8 -- Tray (jest) -- Model: sonnet

### Batch commands (run from repo root, bash shell)

**B1 -- Core loop & dispatch**

```
python -m pytest -q cerebral/tests/test_orchestrator.py cerebral/tests/test_planner.py cerebral/tests/test_router.py cerebral/tests/test_chain_engine.py cerebral/tests/test_extractor.py cerebral/tests/test_recipes.py cerebral/tests/test_queue.py cerebral/tests/test_queue_consent_integration.py cerebral/tests/test_main_dispatcher.py cerebral/tests/test_main_subscriber_lifecycle.py cerebral/tests/test_interrupt_turn.py cerebral/tests/test_model_persistence.py cerebral/tests/test_conversation.py cerebral/tests/test_conversation_store.py cerebral/tests/test_attachments.py
```

**B2 -- Memory, insights & passive**

```
python -m pytest -q cerebral/tests/test_memory.py cerebral/tests/test_memory_injection.py cerebral/tests/test_memory_ipc.py cerebral/tests/test_insights.py cerebral/tests/test_insights_ipc.py cerebral/tests/test_passive_pipeline.py cerebral/tests/test_environment.py
```

**B3 -- Security, consent & permissions**

```
python -m pytest -q cerebral/tests/test_capability_gate.py cerebral/tests/test_call_site_capabilities.py cerebral/tests/test_consent_labels.py cerebral/tests/test_consent_surface.py cerebral/tests/test_credentials.py cerebral/tests/test_credentials_ipc.py cerebral/tests/test_permissions_ipc.py cerebral/tests/test_risky_verbs.py cerebral/tests/test_irreversible_modal.py cerebral/tests/test_profile_acl.py cerebral/tests/test_profile_autodetect.py cerebral/tests/test_voice_consent.py cerebral/tests/test_sandbox_windows.py cerebral/tests/test_tray_ipc_call_tool_gate.py cerebral/tests/test_user_notification.py cerebral/tests/test_settings.py cerebral/tests/test_settings_control.py cerebral/tests/test_set_voice_ipc.py
```

**B4 -- Channels, harness & browser**

```
python -m pytest -q cerebral/tests/test_channel_inbox.py cerebral/tests/test_harness_channels.py cerebral/tests/test_openclaw_channels_plugin.py cerebral/tests/test_discord_allowlist_cli.py cerebral/tests/test_discord_user_plugin.py cerebral/tests/test_discord_user_slice2.py cerebral/tests/test_discord_user_slice3.py cerebral/tests/test_google_oauth.py cerebral/tests/test_n8n_check_credentials.py cerebral/tests/test_rss_poller.py cerebral/tests/test_static_token_factory.py cerebral/tests/test_browser_session.py cerebral/tests/test_browser_session_wiring.py
```

**B5 -- Jobs pipeline**

```
python -m pytest -q cerebral/tests/test_jobs_boards.py cerebral/tests/test_jobs_quality_routing.py cerebral/tests/test_jobs_seam_wiring.py cerebral/tests/test_plugin_job_search.py
```

**B6 -- Plugins A-G**

```
python -m pytest -q cerebral/tests/test_plugin_apps.py cerebral/tests/test_plugin_bitwarden.py cerebral/tests/test_plugin_browser_session.py cerebral/tests/test_plugin_builder.py cerebral/tests/test_plugin_calendar.py cerebral/tests/test_plugin_clockify.py cerebral/tests/test_plugin_docker.py cerebral/tests/test_plugin_finance.py cerebral/tests/test_plugin_git.py cerebral/tests/test_plugin_github.py cerebral/tests/test_plugin_gmail.py cerebral/tests/test_plugin_google_contacts.py cerebral/tests/test_plugin_google_docs.py cerebral/tests/test_plugin_google_drive.py cerebral/tests/test_plugin_google_maps.py cerebral/tests/test_plugin_google_sheets.py cerebral/tests/test_plugin_google_tasks.py cerebral/tests/test_plugin_google_workspace_fallback.py
```

**B7 -- Plugins H-Z + plugin suites**

```
python -m pytest -q cerebral/tests/test_plugin_homeassistant.py cerebral/tests/test_plugin_http_client.py cerebral/tests/test_plugin_inspectability.py cerebral/tests/test_plugin_markets.py cerebral/tests/test_plugin_meet.py cerebral/tests/test_plugin_memory.py cerebral/tests/test_plugin_n8n.py cerebral/tests/test_plugin_network_scanner.py cerebral/tests/test_plugin_news.py cerebral/tests/test_plugin_notion.py cerebral/tests/test_plugin_obsidian.py cerebral/tests/test_plugin_package_manager.py cerebral/tests/test_plugin_phone.py cerebral/tests/test_plugin_printer.py cerebral/tests/test_plugin_reddit.py cerebral/tests/test_plugin_rss_monitor.py cerebral/tests/test_plugin_settings_ipc.py cerebral/tests/test_plugin_shell.py cerebral/tests/test_plugin_sports.py cerebral/tests/test_plugin_ssh.py cerebral/tests/test_plugin_steam.py cerebral/tests/test_plugin_todoist.py cerebral/tests/test_plugin_toggl.py cerebral/tests/test_plugin_vpn.py cerebral/tests/test_plugin_weather.py cerebral/tests/test_plugin_wikipedia.py cerebral/tests/test_plugin_youtube.py cerebral/tests/test_plugin_zoom.py cerebral/tests/test_plugins_browser.py cerebral/tests/test_plugins_os.py cerebral/tests/test_plugins_time_notes.py
```

**B8 -- Tray (jest)**

```
cd tray && npx jest --ci
```

## Results

(One entry per finished batch. Format:
`B<n> -- PASS (<n> passed, <m> skipped, <t>s)` or
`B<n> -- FAIL (<n> passed, <f> failed)` followed by an indented line per
failing test id with its one-line error. Never truncate the failure list.)

B1 -- PASS (482 passed, 5 skipped, 48.60s)

B2 -- PASS (148 passed, 0 skipped, 91.10s)

B3 -- PASS (695 passed, 0 skipped, 58.13s)

B4 -- PASS (310 passed, 0 skipped, 31.49s)

B5 -- PASS (235 passed, 0 skipped, 28.55s)

B6 -- PASS (762 passed, 0 skipped, 7.97s)

B7 -- PASS (1032 passed, 1 skipped, 45.39s)

## SAFETY

- Sessions run tests and edit THIS file only. No code changes, no PRs, no
  issues, no fixing failures -- failures are triaged by a human afterwards.
- NEVER launch Cerebral (`python -m cerebral.main`) or any live service; the
  suites run against fakes/fixtures by design. Integration-marked tests that
  need live services (Ollama, OpenClaw) skip by default -- leave them skipped.
- No live network fetches, no real credentials, no real submissions.
- TEST-SWEEP.md is the ONLY file committed (directly to master).
