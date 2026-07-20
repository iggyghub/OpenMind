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
