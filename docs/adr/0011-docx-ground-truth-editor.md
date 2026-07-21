# 11. Documents capability: .docx ground truth, LibreOffice as editor+engine, Felix-internal Document library

Date: 2026-07-19
Status: accepted (grill session, issues #452 / #448); narrowed by ADR-0012

> **Narrowed by [ADR-0012](./0012-workspace-and-plugin-contributed-panels.md)
> (2026-07-20).** The "no Felix-built editing UI" consequence below holds for
> `.docx` only. Plain-text and Markdown content is editable in a `<textarea>`
> text widget inside a panel. The reasoning here is unchanged and still governs
> `.docx`: no browser widget reproduces Word's layout engine, so `.docx` editing
> continues to open LibreOffice Writer. CodeMirror and TipTap remain rejected.

## Context

The jobs pipeline stores one résumé artifact per profile (`resume_artifacts.pdf_path`)
and derives the applicant dossier from its text. Nothing is editable inside Felix; the
2026-07-19 apply ramp surfaced a wrong email on the stored résumé with no way to fix it.

The initial plan was to make edited text the source of truth and regenerate documents
with npm libs (`docx`, `pdf-lib`) — zero external binaries. Grilling killed it: the user
requires exported documents to **look exactly like the original Word résumé**, and no
JS library implements Word's layout engine. Regeneration produces a Felix-template
document, not the user's résumé.

## Decision

1. **The `.docx` is ground truth.** `resume_artifacts` gains a `docx_path` (editable
   source) alongside `pdf_path` (derived upload artifact). Text/dossier remain derived.
2. **LibreOffice is both editor and converter — one engine for everything.**
   - *User edits*: LibreOffice **Writer**, opened as its own program on the
     ground-truth `.docx` ("Felix, open my résumé"). True WYSIWYG; Felix re-ingests
     on file change. No Felix-built editing UI.
   - *Felix edits*: **headless UNO scripting** (LibreOffice's bundled Python driven
     from a `/plugins` Python plugin) — find/replace, paragraph rewrite, styling.
     Capability parity with the user through the identical software, no GUI
     automation. Hand-rolled OOXML patching in Node was considered and rejected:
     same ceiling, second engine, more code.
   - *Conversion*: `soffice --headless --convert-to` for `.pdf` (pipeline) and any
     format on demand (rtf/odt/html/txt). Because the same engine edits and renders,
     what Felix edits is exactly what exports.
3. **LibreOffice is a declared, detected dependency**: installed via a setup script
   (winget), discovered at runtime; when absent, the stored artifacts still work and
   editing/conversion degrade with an actionable message.
4. **The apply pipeline is unchanged.** After any edit (either route): re-convert PDF →
   re-derive dossier through the existing `jobs_store_resume` internals → broadcast
   `jobs_update`. Greenhouse continues to receive the PDF it receives today.
5. **Turn-taking, not co-editing.** Felix edits headlessly by itself; the user edits in
   Writer by themselves. Concurrent editing is a non-requirement: last-write-wins on
   the file, no reconciliation layer, no live shared session (kept possible later —
   UNO can attach to a visible Writer instance — but out of scope).
6. **This is a general capability, not jobs machinery.** It lands as a standalone
   `plugins/documents.py` (library CRUD, open-in-Writer, headless edit, convert,
   versions/revert) plus a Documents panel in the Main window sidebar. Documents live
   in the profile-scoped **Document library** (see CONTEXT.md) — inside Felix's own
   data, so they travel with it and are reachable remotely via the harness; loose
   filesystem exports only on explicit user request. `job_search.py` *consumes* the
   capability: the résumé is one library document whose change hook re-derives
   PDF + dossier through the existing `jobs_store_resume` internals.
7. **Versioning: snapshot-before-write.** Any overwrite of a library document first
   copies the current file to `versions/` — keep the 5 most recent plus, always,
   version zero (the original as first stored). Revert is itself a snapshotting
   write. Edit tools stay silent-class under ADR-0005 (reversible by construction);
   outward exposure of an edited résumé is still gated by the apply flow's own
   approvals.

## Consequences

- No npm document stack (`docx`, `pdf-lib`, `jspdf`, CodeMirror, TipTap) enters the
  tree, and no tray editor window is built. The tray's only new surface is an "open
  in Writer" affordance.
- LibreOffice (~700MB) becomes the one external dependency — editor, edit engine,
  and converter in one. Its `.docx` round-trip fidelity is the system's fidelity
  ceiling (acceptable: its renderer produces the exported PDF anyway).
- Felix's document edits run through LibreOffice's bundled Python (UNO bridge), a
  subprocess boundary Cerebral must manage (spawn, timeout, error surface).
- A machine without LibreOffice keeps a working apply pipeline with the stored
  artifacts; it just can't edit or re-derive until LibreOffice is installed.
