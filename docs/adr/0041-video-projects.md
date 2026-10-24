# ADR-0041: Video projects -- Shotcut as editor, melt as engine, .mlt as ground truth

**Date:** 2026-10-23
**Status:** Accepted (grill session)
**Relates:** ADR-0011 (LibreOffice as editor+engine -- the pattern applied here),
ADR-0037 (absorbed-app contract), ADR-0010 (shell sandbox -- deliberately not used
here), ADR-0017 (video Watching -- its Escalation pattern), ADR-0028 (R1 reach ladder,
R2 third repeat, R5 the singular scheduler). CONTEXT.md: **Video project**, **Reel**,
**Quick trim**, **Vision trim**.

## Context

Felix can watch videos (ADR-0017) and capture game clips (`plugins/game_capture.py`,
Game Bar's last 30/60s into `Videos\Captures`), but cannot edit one. The user named
three jobs, in order: (1) game clips into a highlight reel, (2) shorts (9:16, captions
from the transcript, silence cutting), (3) general "edit this video". Plain ffmpeg
through the shell sandbox (PR #1520) covers one-off trims, but the user wants to fix
Felix's cuts on a real timeline, and a hand-tweakable timeline is the whole reason to
bring in an editor.

## Decision

1. **The `.mlt` timeline is ground truth; the `.mp4` is derived.** Shotcut (GPL) is the
   editor and its bundled `melt` is the headless engine -- one engine for both sides,
   exactly ADR-0011's `.docx`/PDF split. A fix is a timeline edit plus a re-render,
   never an edit to the `.mp4`.
2. **Sources are referenced in place, never copied or modified.** The user points Felix
   at a folder or files ("today's clips", a picked folder). Only the `.mlt` and its
   render are new files, and they land in the **Document library** under
   `Videos/<YYYY>/<MM-DD>/`, keyed on the clips' *capture* date (one **Reel** per day by
   default, `Reel <date>`, ` (2)` for more). Renders leave the library only on request.
3. **A dedicated plugin (`plugins/video_project.py`) runs `melt` directly, not through
   `shell_exec`.** The sandbox can only see its own workdir; staging gigabytes of clips
   per reel, or granting the AppContainer read on the user's video folders, are both
   worse. This is the `plugins/documents.py` posture: a fixed set of operations on
   files the user chose.
4. **The model never writes `.mlt` XML.** It produces an **edit list** (clip, in, out,
   order); the plugin's own code serialises it. That keeps the file `melt` executes
   authored by trusted code even when filenames or the user's comment are hostile, and
   guarantees Shotcut can open what Felix wrote. This is what makes decision 3 safe.
5. **Cut-finding: Quick trim by default, Vision trim by escalation or request.** Quick
   trim keeps each clip's loudest audio stretch (CPU, no model). Clips with no clear
   peak escalate to Vision trim, capped per reel (the ADR-0017 Escalation pattern);
   choosing Vision trim runs it on every clip. Vision shares the one model (R5), so the
   live turn wins.
6. **Invocation: a chat card, not a window.** After the user points at clips, Felix
   replies with a card -- Quick trim / Vision trim / Something else, plus a comment box
   whose text goes to the model with the choice. Skipped when the request already says
   what to do.
7. **Renders are marked out of date, not re-rendered on save.** A changed `.mlt` marks
   its render **out of date**; "render it" re-renders; an export of an out-of-date
   project renders first. Renders run in the background with progress on the card.
8. **Felix edits only timelines it fully understands.** It fingerprints every `.mlt` it
   writes. While the file matches, Felix edits it (snapshot first: 5 most recent + the
   original forever, as ADR-0011). Once the user has changed it in Shotcut, Felix's
   further changes land as a new project beside it, never over it.
9. **v1 is cuts and joins.** Trimmed clips in order, a short audio fade at each cut,
   rendered at the clips' own resolution/frame rate. Transitions, titles and music are
   the user's to add in Shotcut; Felix learns one on the third hand-made repeat (R2).
10. **Shotcut is a declared, detected dependency** -- installed by
    `scripts/setup-shotcut.ps1` or Felix's `pkg_install` (winget `Meltytech.Shotcut`,
    either scope: `melt` runs unsandboxed, so a per-user install under AppData works),
    discovered at runtime;
    absent, video projects say so with the install hint instead of failing silently.

## Considered and rejected

- **Plain ffmpeg, no timeline.** Cheapest, but a wrong cut means a full redo by
  conversation. Kept for one-offs via the sandbox (PR #1520).
- **Parsing user-edited `.mlt` back into an edit list** (decision 8's alternative). Most
  capable, but the parser must survive anything Shotcut writes, and a bug there
  silently corrupts the user's work. Revisit if forked reels become common.
- **Auto re-render on save.** Burns minutes of CPU on every intermediate save.
- **Kdenlive** (same MLT format, weaker Windows build), **OpenShot** (Python API,
  stability reputation), **LosslessCut** (no timeline).

## Consequences

- One external dependency (Shotcut, ~200 MB) brings editor + engine together.
- `melt` is a second unsandboxed subprocess boundary next to LibreOffice; its safety
  rests on decision 4 and must not be relaxed by letting the model emit raw XML.
- Shorts (job 2) reuse Video project, out-of-date renders and the fingerprint rule, and
  get their own short grill for 9:16, captions and silence cutting.

## Amendment (2026-10-23) -- Vision trim uncapped and never silently skipped; card and library-button mechanics

**Context** -- Building VIDEO-EDIT V5-V7 surfaced four calls decision 5 and decision 6
left open or settled differently from what the user wants. The user answered them
before V5 shipped.

**Decision**
1. **No escalation cap** (changes decision 5's "capped per reel"). Every clip Quick trim
   cannot place -- no audio track, or no clear audio peak -- goes to Vision trim. There
   is no per-reel limit. R5 still holds: vision runs inside the live tool call, one
   request at a time on the one model.
2. **Fail the reel when vision is needed and no vision-capable model answers.** Whether
   Vision trim was chosen or escalated, an unreachable model (no VL backend, a raised
   error, or an empty reply) stops the call: the user is told which clip needed it and
   no project is made. There is no fallback to keeping the clip whole. A model that
   answers but names no frame keeps that one clip whole, flagged `vision_unclear`.
3. **The card's buttons send a message to Felix** (decision 6's mechanics). Quick trim /
   Vision trim send the choice, the clips and the comment-box text as an ordinary typed
   message (`user_text_command`); "Something else" sends just the comment. The model reads
   it and calls the tools -- a button never calls a trim tool directly. The card is a
   `system_event` turn (`video_trim_offer`), the same structured-card mechanism as the
   self-dev pending-review card and `recipe_offer`.
4. **Opening a reel: by asking, and from the library.** "Felix, open today's reel" calls
   `video_open`, and the reel's entry in Library > Documents shows **Open in Shotcut**
   (the Documents panel's existing Open action, routed to `video_open` for `.mlt`). The
   user asked for this button explicitly, which is what CLAUDE.md rule 7 requires before
   a new UI action; it adds no menu item, route or hotkey. `doc_open` refuses `.mlt`
   files so a reel never opens in LibreOffice.

**Considered and rejected**
- **A per-reel cap (3).** Bounds model time on a bad audio day, but leaves the clips
  past the cap uncut -- the user would rather wait.
- **Falling back to the whole clip when vision is unreachable.** Makes something, but a
  reel of uncut 60 s clips looks like Felix did the job; failing loudly is honest.
- **Buttons calling `video_quick_trim` directly.** Skips a model turn, but drops the
  comment box's meaning (it would need parsing) and is a second path to the tools.
- **A `.ps-action` panel-spec button.** Library > Documents is rendered by
  `tray/lib/documents-panel.js`, not a panel spec; routing its existing Open button is
  the smaller change on the surface the user actually sees.

**Consequences**
- Without Budd or another VL model, a Quick trim that meets a silent or flat clip fails
  instead of producing a reel; clips with a clear audio peak still never touch the model.
- Vision trim's cost now scales with the number of peakless clips, unbounded.
- The 16-class capability vocabulary is unchanged; `video_project` still declares only
  `fs_read` and `fs_write`.
