# DOCUMENTS.md -- Documents capability campaign driver

Design: `docs/adr/0011-docx-ground-truth-editor.md` + CONTEXT.md ("Document library",
"Resume artifact"). Grilled 2026-07-19. One engine (LibreOffice) is editor, edit
engine, and converter; documents live inside Felix's Document library; the resume
becomes one library document with derived PDF + dossier.

## Status: ready

## Next slice -- start here

- **Active:** S7 -- #448
- **Model:** opus

## Queue

- [x] S1 -- #452 -- jobs_update_dossier_field + inline dossier email edit (Tier 1)
- [x] S2 -- #453 -- LibreOffice dep: setup-libreoffice.ps1 + find_soffice/doc_status
- [x] S3 -- #454 -- documents.py core: library store, doc_convert, snapshot versioning
- [x] S4 -- #455 -- user editing loop: doc_open in Writer + re-ingest on save
- [x] S5 -- #456 -- Felix editing: doc_edit via headless UNO (Model: opus)
- [x] S6 -- #457 -- Documents panel in the Main window sidebar
- [ ] S7 -- #448 -- resume wiring: docx_path, change-hook re-derive, panel resume row (Model: opus)

Per-slice model: sonnet unless the queue entry says otherwise. When ticking a slice,
set the next entry's model on the `Model:` line above.

## Landed PRs

- PR #460 — S1 #452 — jobs_update_dossier_field + inline dossier edit (6500c38)
- PR #461 — S2 #453 — LibreOffice dep: setup-libreoffice.ps1 + find_soffice/doc_status (b076e83)
- PR #462 — S3 #454 — documents.py core: library store, doc_convert, snapshot versioning (d91d105)
- PR #463 — S4 #455 — user editing loop: doc_open in Writer + re-ingest on save (979956c)
- PR #464 — S5 #456 — Felix editing: doc_edit via headless UNO (f05a608)
- PR #466 — S6 #457 — Documents panel in the Main window sidebar (3981b95)

## SAFETY

- NEVER install software in a loop session: no winget, no downloads. The LibreOffice
  install happens only when the USER runs scripts/setup-libreoffice.ps1 themselves.
- NEVER invoke a real soffice.exe / LibreOffice / UNO bridge in tests or smoke runs.
  All conversion/edit/launch calls go through injectable seams and are stubbed.
  Behaviour only checkable with real LibreOffice -> APPEND a checklist item to
  docs/documents-live-verify.md instead of performing it.
- NEVER touch files on the user's Desktop. A pending resume correction
  (adam_poder_resume_AdamPoder8.docx) lives there and is owned by another session.
- NEVER fetch a live job board, drive a real ATS, or submit anything -- same rules
  as the jobs campaigns.
- Seam rule (#153/#385): no `from plugins.<x> import ...` inside cerebral/ -- wire
  through _wire_plugin_seams against _orc.get_plugin_module. Keep
  cerebral/tests/test_jobs_seam_wiring.py passing.
- Tray renderer is no-nodeIntegration: new panel logic uses the UMD-ish dual-mode
  wrapper in tray/lib/*.js (PR #203 pattern).
- Operator .ps1 scripts: ASCII-only bodies, pause-on-exit + -NoPause switch
  (CLAUDE.md rules).
