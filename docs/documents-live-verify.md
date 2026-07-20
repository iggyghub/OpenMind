# Documents capability -- live verification checklist

Items here require a real LibreOffice install and cannot be automated in the test suite.
Run these manually on a Windows machine that has (or can get) LibreOffice.

## S2 #453 -- LibreOffice dep: setup script + runtime detection

- [ ] Double-click `scripts/setup-libreoffice.ps1` on a machine without LibreOffice.
      Verify: winget installs LibreOffice, script prints SUCCESS and the detected soffice.exe path.
- [ ] Run `scripts/setup-libreoffice.ps1 -NoPause` from a terminal after install.
      Verify: exits 0, prints SUCCESS and version string, no pause prompt.
- [ ] Start Cerebral (`python -m cerebral.main`) with LibreOffice installed.
      Ask Felix: "doc status". Verify: `available: true` and correct `soffice_path`.
- [ ] Start Cerebral with LibreOffice NOT installed (or SOFFICE_PATH seam unset).
      Ask Felix: "doc status". Verify: `available: false` and the setup-libreoffice.ps1 hint.

## S3 #454 -- document library: store, convert, snapshot versioning

- [ ] Start Cerebral with LibreOffice installed. Ask Felix: "store this file in my document
      library" (attach a .docx). Verify: `doc_store` returns a doc entry with correct kind/name
      and the file appears under `cerebral/data/attachments/<profile>/documents/<id>/`.
- [ ] Ask Felix: "list my documents". Verify: `doc_list` returns the stored doc.
- [ ] Ask Felix: "convert my resume to PDF". Verify: `doc_convert` calls soffice headless,
      the .pdf appears next to the source in the library directory, and a new library entry
      is returned with kind=pdf.
- [ ] Overwrite the stored doc (ask Felix to store an updated version). Then ask Felix to
      revert to the original. Verify: `doc_revert` restores the v0 content and the current
      file is snapshotted before restore.
- [ ] Overwrite the same doc 8 times. Verify: `versions/` contains v0 + exactly 5
      most-recent timestamped snapshots (6 total).

## S4 #455 -- user editing loop: doc_open in Writer + re-ingest on save

- [ ] Start Cerebral. Ask Felix: "open my resume" (or `doc_open` with the resume's doc_id).
      Verify: LibreOffice Writer opens the .docx. Cerebral log shows watcher task started.
- [ ] Edit the document in Writer, save (Ctrl+S). Within ~5 seconds:
      Verify: Cerebral log shows mtime change detected, snapshot created, broadcast sent.
- [ ] Ask Felix: "list my documents". Verify: `updated_at` reflects the edit time.
- [ ] Check `versions/` inside the doc's library dir. Verify: v0 (original before first
      Writer session) + timestamped snapshot(s) for each save detected.
- [ ] Make 6 more saves. Verify: `versions/` contains v0 + exactly 5 most-recent
      timestamped snapshots (pruned by the existing FIFO rule).

## S5 #456 -- Felix editing: doc_edit via headless UNO

- [ ] Store a scratch .docx in the library (e.g. a copy of the resume or any Word file).
      Ask Felix: "change 'old@example.com' to 'new@example.com' in doc <id>".
      Verify: Cerebral calls `doc_edit` with op=find_replace; the file is updated on disk;
      `versions/` contains a v0 (or timestamped) snapshot taken before the edit.
      Inspect the .docx content to confirm the replacement was applied.
- [ ] Ask Felix: "replace the paragraph starting with 'Summary' with '<new paragraph text>'".
      Verify: `doc_edit` with op=replace_paragraph; the targeted paragraph's text is replaced
      while its formatting (bold, font size, etc.) is preserved; snapshot taken before the edit.
- [ ] Verify timeout surface: temporarily lower the timeout or block the LO process.
      Verify: Cerebral returns is_error=True with "timed out" in the message.
- [ ] Ask Felix: "open my resume" then (without closing Writer) ask Felix to edit the same doc.
      Verify: both routes converge -- the watcher detects the UNO-written save and re-ingests.

## S6 #457 -- Documents panel in the Main window sidebar

- [ ] Open the Main window. Click "Documents" in the sidebar nav.
      Verify: the Documents pane opens with the empty-state message when no docs are stored.
- [ ] Store a document (via Felix: "store this file as a document"). Then re-open the Documents
      panel. Verify: the doc card appears with correct name, kind badge, updated date, and
      snapshot count.
- [ ] Click "Open" on a doc card.
      Verify: Cerebral dispatches doc_open, LibreOffice Writer opens the file.
- [ ] Select a format (pdf/docx/txt/rtf) and click "Export" on a doc card.
      Verify: Cerebral dispatches doc_convert, a new library entry is created; the panel
      refreshes (documents_update broadcast) showing the exported file as a new row.
- [ ] Click "Versions (N)" on a card with at least one snapshot.
      Verify: the versions sub-panel expands showing snapshot filenames and Revert buttons.
- [ ] Click "Revert" on a snapshot. Confirm the dialog.
      Verify: Cerebral dispatches doc_revert, the file is restored, panel refreshes.
- [ ] Click "Save a copy..." on a doc card. Enter a destination path (e.g. C:\Users\<you>\Desktop\copy.docx).
      Verify: Cerebral copies the library file to that path; the original library entry is unchanged.
      NOTE: the JS prompt() stand-in for the path input should be replaced by a proper
      Electron save dialog once a preload bridge (contextBridge + ipcRenderer) is added to
      the main window -- track this as a follow-up enhancement.
- [ ] Ask Felix to store two more documents. Verify: the Documents panel shows all three rows,
      the federated search finds documents by name, and the in-pane filter hides non-matching rows.

## S7 #448 -- resume wiring: docx_path, change-hook re-derive, panel resume row

- [ ] Upload a .docx resume file via the chat paperclip and tell Felix "store this as my resume".
      Verify: `jobs_store_resume` stores the docx in the Document library (appears in the Documents
      panel) and sets `docx_path` + `doc_id` in `resume_artifacts`. The Job Search dossier card
      shows "Resume: <filename>".
- [ ] With the resume linked as a library document, ask Felix "open my resume".
      Verify: LibreOffice Writer opens the .docx. Edit and save in Writer.
      Within ~5 seconds: Cerebral log shows mtime change detected → docx converted to PDF →
      dossier re-extracted → `jobs_update` broadcast. The dossier card refreshes automatically.
- [ ] Ask Felix to edit the resume via `doc_edit` (e.g. update the email).
      Verify: the change hook fires: PDF is re-converted, dossier fields update, `jobs_update`
      broadcast reaches the panel. The apply pipeline uploads the new PDF on the next apply run.
- [ ] Click "Open in Writer" in the dossier card (requires doc_id to be set in the resume artifact).
      Verify: dispatches `doc_open` IPC, LibreOffice Writer opens the .docx.
- [ ] Click "Change" in the dossier card.
      Verify: alert guides the user to upload a new file and tell Felix "store this as my resume".
      NOTE: a native Electron file-open dialog requires a contextBridge preload bridge in the
      main window — track this as a follow-up enhancement (currently shows the workflow hint instead).
- [ ] Upload a new PDF resume (no docx). Verify: `pdf_path` is stored; dossier is extracted;
      dossier card shows the PDF filename; "Open in Writer" is absent (no doc_id).
