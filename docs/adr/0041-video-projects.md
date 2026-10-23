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
10. **Shotcut is a declared, detected dependency** -- installed machine-wide by
    `scripts/setup-shotcut.ps1` (winget `Meltytech.Shotcut`), discovered at runtime;
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
