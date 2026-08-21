# OpenMind — Domain Context

## Glossary

**OpenMind** — the platform. The installable software product as a whole.

**Cerebral** — the local brain. The central Python backend process that runs on the desktop/home server. All devices connect to it. The name is internal/architectural; users don't speak it.

**Felix** — the default wake name. The word a user speaks to activate the assistant. Phonetically distinct, short, reliably detected by Vosk. Customisable per profile. What OpenMind calls itself in conversation.

**Profile** — a user identity container. Stores who someone is (name, voice preference, wake name override, pronunciation guide, **connected accounts**) and their scoped long-term memory. Selected on launch or auto-detected after first use.

**System setting** — a non-identity machine-wide preference. Includes: notifications on/off, reminder interval, camera enabled, visualiser visibility, **active model + per-task model assignments**. Global, shared by every profile, not part of a profile. Lives in the Main window's **Settings** sidebar panel (v1) and persists to `cerebral/data/felix-settings.json`. _Avoid_: calling credentials or connected accounts "settings"; avoid putting profile-scoped state (ACL, memory, connected accounts, wake name) here.

**Connected account** — an external account (e.g. Google) a profile has authorized Felix to act as, plus the stored credential that authorizes it. Belongs to one **Profile** (consent belongs with identity, like memory and ACL), never global. _Avoid_: "linked account", "integration login".

**Wake** — the moment a user speaks Felix's name. Triggers: mic opens, system awaits a command. Does not read the queue aloud. Does not interrupt.

**Passive mode** — the default always-on state. Vosk listens continuously for the wake name and actionable signals. No full transcription, no LLM calls, minimal CPU. The system is observing, not acting. *(v1 scope: Felix acts only when addressed — wake word or typed. Continuous ambient 5W1H capture into the queue is designed-for but deferred post-v1; see ADR-0008.)*

**Active mode** — entered after a wake. faster-whisper transcribes, the LLM processes, tools execute.

**5W1H extraction** — the passive pattern-matching process. When Vosk detects a potentially actionable signal in ambient audio, faster-whisper transcribes the last ~60 seconds and the LLM extracts: Who, What, When, Where, Why, How. Output is a candidate action queued for the user.

**Rolling buffer** — the last ~60 seconds of ambient audio held in RAM. Never written to disk. Discarded continuously. Used only when Vosk triggers a full transcription pass.

**The queue** — the list of **proposals** Felix has raised and the user has not yet decided on. The single "Felix proposes, the user decides" channel: one approve/dismiss surface, one **insight signal** source, one count badge. Lives in the Main window's Conversation route. Acted on only when the user wakes Felix or approves via notification. _Avoid_: defining it as "candidate actions" only — actions are one **proposal kind** among several; avoid "the tray pulldown" (the queue moved into the Main window).

**Proposal** — one queue entry: something Felix suggests and the user approves or dismisses. Three kinds:
- **Candidate action** — a tool call Felix would execute (the original 5W1H-sourced kind).
- **Memory proposal** — a durable fact Felix wants to store; approving writes it to long-term memory.
- **Recipe proposal** — an offer to save a repeated **chain** as a **Recipe**, raised after the same chain runs N times. Keeps "user-approved" true in the **Recipe** definition.

Approving or dismissing a proposal is the moment Felix learns; every decision is a potential **insight signal**.

**Insight signal** — one approve/dismiss decision on a proposal that carries a `tool_name`, i.e. a real action. Notification-class entries (no `tool_name`) are explicitly **not** signals — they would fill the **Insights view** with noise about who messaged the user rather than a model of the user. `PATTERN_THRESHOLD` repeats of the same pattern key mint an **Insight**.

**The harness** — OpenClaw. The master command and communication gateway. All external messaging channels (WhatsApp, Telegram, Slack, Discord, Teams, etc.) flow through it. Also serves as the remote access point for Felix before native mobile clients exist. Felix talks to one thing; OpenClaw talks to the services.

**MCP server** — a Model Context Protocol server. The standard unit of capability in Felix. Each tool (Clock, Browser, Files, Shell, etc.) is an MCP server. The LLM calls tools via MCP regardless of what's underneath. Adding a capability = adding an MCP server.

**Plugin** — an MCP server built for Felix. Lives in `/plugins`. The unit of capability — **every** tool Felix uses is a plugin, regardless of what's underneath. A plugin's implementation may call an external API directly (e.g. `gmail.py` → Google), proxy through n8n (e.g. `google_workspace.py` → n8n → Google), call a local OSS tool (e.g. `notes.py` → SQLite), or wrap an OpenClaw integration. Whether the backed service is open-source or proprietary is **orthogonal** to whether something is a plugin. Generated from natural language description by the builder, or hand-authored. Code is always inspectable and editable. _Avoid_: using "plugin" to mean "proprietary integration" — the OSS plugins are still plugins.

**Watching** (video) — Felix turning one video into understanding: `URL → {transcript, on-screen text, visual summary, source metadata}`, resumable per-video, stored in `openmind.db`. The reusable primitive (`cerebral/video/`, `plugins/video.py`); a channel of videos is just the primitive run in a loop. _Avoid_: conflating the primitive with any one channel's purpose (e.g. "the money-idea analyzer") — that is a caller, not the capability. See ADR-0017.

**Escalation** (video) — the moment Felix reaches for its eyes. Audio transcript is always produced; the expensive visual layers (OCR + vision-model description of scene-change keyframes) fire only when the audio admits it is not enough: a **thin transcript** (empty/short/high no-speech) or a **deictic reference** in the transcript ("look at this", "as you can see"). Capped per batch. _Avoid_: running the visual layers by default — most videos are audio-only.

**Verdict** (video) — Felix's judgment of an extracted idea's validity: `legit / dubious / scam / unverifiable`, with confidence and 1–3 evidence links, from a strong-model + web-search pass. Lives on the **cluster**, not the video: verified once per distinct idea, inherited by every video in the cluster, so every watched video *has* a verdict without paying for it 200×. _Avoid_: treating a verdict as per-video — per-video re-verify is a manual exception.

**Commit** (video) — the gated promotion of a verified idea from the video store into **Memory** as a durable fact (verdict attached). The video store holds all watched videos; Memory receives only the handful the user commits. The "learn/copy" step, and the same "propose, user decides" posture as the queue. _Avoid_: auto-writing extracted ideas into Memory — the store is the reservoir, Memory is the chosen few.

**Direct plugin** — a plugin whose implementation calls the target service directly (no n8n hop). Per-profile credentials, one HTTP hop. Preferred for daily-used services.

**n8n-backed plugin** — a plugin whose implementation posts to a local n8n workflow, which then calls the target. Shared credentials across profiles, two hops, plus n8n daemon as a runtime dependency. Acceptable for occasional-use services or where Cerebral has no first-class client for the target.

**The core loop** — the fundamental operation: intent → the LLM (the *planner*) selects a tool → executes via MCP. It runs in two forms (see ADR-0008):
- **Active loop** — Felix is *addressed* (wake word or typed command). The planner picks a tool via native tool-calling and dispatches it **directly** through the ADR-0005 gate with `passive=False`: silent-class runs friction-free, ask-class prompts via the Conversation consent card, irreversible pops a modal. No queue detour. This is the v1 path.
- **Passive loop** — an ambient 5W1H candidate is queued and executed only after the user approves, with `passive=True` (escalated). v1 narrows this: Felix acts only when addressed; always-listening ambient capture into the queue is deferred post-v1.

If no tool exists, the growth loop begins.

**The growth loop** — when Felix lacks a tool: identify the gap → run /grill-me to design it → build it as an MCP server → register it → Felix has it permanently.

**The self-dev loop** — how Felix changes its *own core* (Cerebral, the tray/UI, the plugins it ships), as opposed to adding a peripheral tool. Distinct from the **growth loop** (which builds a *new* **Plugin** — inspectable text that can't touch the running brain): a core edit modifies the running brain and could disable its own gate, so it carries a fundamentally larger blast radius and runs through a stricter path. The path: Felix **clones** its own repo into the **shell sandbox** workdir (`cerebral/data/sandbox/self_dev/<run-id>/`, ADR-0010), a **selectable model** (`task_type="self_dev"` in the router — local, cloud, or a connected server) edits on a branch, the existing test suites run *inside the sandbox* as the gate, and a **PR** is opened. Crossing into the live tree is a separate gated step — a `github` PR merge plus a **restart** — never an in-place file write into the running source. Shipped as `plugins/self_dev.py`. Cloud/local both work (model choice is orthogonal to safety); unavailable where no sandbox backend exists (fail-closed, same posture as `shell_exec`). See ADR-0015. _Avoid_: folding it into the growth loop (different blast radius); calling the working clone a "worktree" in the literal git sense (it is a full independent clone, so the live `.git` is untouchable from inside the sandbox).

**Blast-radius gate** — the rule governing whether a **self-dev loop** change auto-merges or escalates to a human PR review. Auto-merge on green tests is allowed only when the diff stays in *safe zones* (`plugins/`, `skills/`, `docs/`, tests, non-security tray views); any diff touching the **guardrails** — `cerebral/security/` (the ADR-0005 gate), the ADR-0010 sandbox, the credential/keyring store, `cerebral/main.py` core, or the self-dev loop's own code — **always** escalates to human review, regardless of test colour. Felix may *propose* changes to its own guardrails but never *self-approve* them. The code analog of the job pipeline's **zero-guessed rule**. _Avoid_: treating auto-merge as "unsafe" — the boot self-check + rollback still runs on every load.

**Boot self-check + rollback** — the reversal guarantee for the **self-dev loop**, owned by the launcher/Electron layer (where restart lives), *not* by Cerebral's Python (the thing that might be broken). Before a restart-to-load, Cerebral's current `master` SHA is pinned as `last_known_good` and the structured state (`openmind.db` + `felix-settings.json`) is snapshotted (5 rolling). On relaunch the new code runs a self-check (imports OK + ADR-0005 gate present + IPC up on 7766); **pass** promotes the new SHA to `last_known_good`, **fail** resets to `last_known_good`, restores the matching state snapshot, relaunches the old code, and notifies. Git history *is* the code backup — no folder copies. _Avoid_: putting the health-check inside Cerebral (a broken brain can't rescue itself); snapshotting `chroma/`, `browser/`, or documents (rebuildable, disposable, or not corruptible by a code change).

**Shell sandbox** — the OS containment a `shell_exec` command always runs inside (ADR-0010). A Windows AppContainer child wrapped in a Job Object: writes confined to a per-profile workdir by a kernel ACL (not a path denylist), no network capability, a scrubbed minimal environment (no secrets), and resource caps (1 GB / 32 procs / 120 s wall). Execution is never un-sandboxed. `shell_exec` stays deny-by-default per ADR-0005; the sandbox makes the one-time deny→ask opt-in *safe*, not automatic, and is only offered where a sandbox backend exists — other platforms keep `shell_exec` denied (fail-closed). This is the "future subprocess sandbox" ADR-0005's gate-location note reserved a slot for. _Avoid_: calling it a VM (it is an in-OS boundary, not a virtual machine); calling the file boundary a "denylist" (it is a kernel ACL allowlisting one workdir).

**Computer use** — the capability for Felix to operate the desktop the way a person does: *see* the screen and drive the *mouse and keyboard* itself, for apps with no API, plugin, or channel. **Hybrid by modality** (ADR-0016): Felix reaches first for a *structured* handle — the OS **accessibility tree** (Windows UI Automation), acting on named *elements* (fast, runs on the local text model, reliable) — and falls back to *raw pixel vision + coordinate clicks* only where the tree is absent or too thin (general but slow; the only path covering games, canvases, and tree-less apps). Pixel-vision grounding routes through the model-priority chain (local → custom server → cloud) honoring `local_only`, and needs a vision-capable backend, so the `Backend` protocol gains a multimodal image seam. Rides two existing ADR-0005 classes — `screen_capture` (seeing) and `device_control` (acting) — so it is *execution mechanics on the existing vocabulary*, not a new class (the ADR-0010 pattern). **Windows-only in v1** (UIA is Windows-specific); fail-closed elsewhere like `shell_exec`.

**Actuation axis (background vs foreground).** Distinct from the *perception* axis (structured/UIA read vs pixel/vision). **Background actuation** acts on a control through its UIA **control pattern** (`Invoke`, `SetValue`, `Toggle`, `Select`, `ExpandCollapse`) — it never moves the cursor or injects keystrokes, so the user keeps using the mouse and keyboard while Felix drives (concurrent use). **Foreground actuation** is the `pyautogui` synthetic-input path — it steals the physical cursor/keyboard. Background is tried first on the structured path and falls back to foreground when a control exposes no usable pattern; the pixel path is always foreground (a raw coordinate has no control handle, so `pixel + background` is an impossible combination). _Avoid_: calling background actuation a third sibling of "structured/pixel" — it is a property of the structured path, orthogonal to how the target was perceived.

**The browser is just another app.** Computer use drives a *normal, user-launched* browser window via OS-level input + its UIA tree — deliberately **not** through Playwright/CDP, because CDP carries automation fingerprints (`navigator.webdriver`, timing) that serious bot-detection (Cloudflare, Discord) reads, whereas OS-level input on a real browser looks human. This is the **stealth-sensitive** path (logged-in accounts, ban-risky sites). The **Browser** plugin (Playwright DOM) coexists as the **fast** path for benign/anonymous use (the logged-out Job board, public reads/extraction); the planner picks per site — speed when detection doesn't matter, stealth when it does. Distinct also from **Apps** (launch/switch/close). _Avoid_: treating raw pixel-clicking as the default (it is the slow fallback); routing detection-sensitive web through Playwright (that is the fingerprinted path); calling it a "sandbox" (a real mouse/keyboard cannot be OS-contained the way `shell_exec` is — containment is the gate + bounded region + kill switch, not a kernel jail).

**Chain** — the active loop run as a loop: the planner picks a tool, sees the result, picks the next, and repeats until it returns final text or hits the step cap (default 8). A single tool call is a chain of length one. Chaining is the real target; the single-step engine is its stepping stone.

**Sub-agent** — a **chain** run inside its own **context boundary**: a bounded subtask with a *fresh* transcript (only the task the caller hands it, not the parent's history or memory), a scoped tool subset, and an optional model pin, that runs to completion and returns *one compact result* to its caller. The point is **token isolation**, not parallelism — on the single 8 GB GPU, parallel local inference just serializes, so a sub-agent is deliberately a sequential blocking call, never a parallel worker. Each nested step re-fires the same ADR-0005 gate, so a sub-agent can never exceed the caller's permissions. _Avoid_: "swarm" / "parallel agents" — that throughput frame is explicitly rejected (see ADR-0020); a sub-agent is a context boundary Felix spends deliberately, optionally on a cloud model.

**Delegation** — the act of a caller handing a subtask to a **sub-agent**. v1 is *caller-invoked only* (specific heavy internal flows call the primitive); the planner does **not** autonomously delegate until that behavior is measured behind the eval harness. _Avoid_: treating delegation as automatic decomposition — in v1 it is an explicit call, not a planner reflex.

**Recipe** — a saved, named, user-approved **chain** (a frozen sequence of tool calls) a user re-runs on command, scoped per profile. Replaying a Recipe re-fires every per-step ADR-0005 gate — a Recipe saves the *plan*, never a *grant*. Distinct from the growth loop, which builds a *new* tool: a Recipe just remembers a sequence of tools that already exist.

**Skill** — an installable, named package of *instructions* (a procedure) — not code, not a frozen chain — that Felix loads into the planner's context to change how it approaches a class of task (e.g. a grill-style design interview, a test-first build loop, breaking a plan into issues). A skill may bundle resource files (templates, reference data, helper scripts); a bundled script runs only when the procedure calls an existing gated tool (e.g. `shell_exec` in the ADR-0010 sandbox), so **a skill adds know-how, never capability** — every tool it triggers still hits the ADR-0005 gate exactly as a direct user request would. Sourced by install: hand-written locally or fetched from an online source, always inspectable and editable. Front-matter reserves a `kind` field (`procedure` in v1; `agent` — run the skill as a scoped sub-planner in its own context with a tool allowlist that is a subset of the profile's grants — is a reserved-but-deferred post-v1 kind). Distinct from a **Plugin** (adds a tool), a **Recipe** (replays a fixed chain of tools that already exist), and the **growth loop** (builds a new plugin). _Avoid_: calling a skill a plugin or a recipe; treating a bundled script as a plugin (it registers no tool and runs only through the gate); using "skill" for a one-off prompt (a skill is installed and reusable).

**Insights view** — the UI panel showing Felix's learned model of a user. Displays detected preferences, patterns, and behavioural adjustments per profile. Every entry is editable, deletable, or pinnable. Full transparency into what Felix has inferred.

**Visualiser** — the floating on-screen representation of Felix. A 200x200 transparent, click-through, always-on-top window that mirrors Felix's voice/system state (idle / listening / thinking / speaking / switching model). Architecturally a separate window from the **Main window** so it survives the Main window being closed and so a future **body** can move around the screen (which a window-embedded visualiser could not). Runs independently of the Main window's own in-header state pill (which signals the same state to a user already inside the chat).

**Visualiser theme** — what the visualiser renders. v1 default: the **orb** (dark, animated abstract form, waveform-style). Future themes: 2D avatars, 3D models, animal characters, abstract theme packs — user-selectable per profile. The v1 visualiser's renderer is theme-pluggable in shape so the orb is one option among many, not a single hard-coded form. Post-v1: a fully embodied **body** that walks around the screen as a theme.

**State pill** — a one-line indicator in the Main window's chat header ("Felix is listening…" / "thinking…" / "speaking…"). Covers state-signalling *inside* the chat so the user doesn't have to glance at the floating Visualiser. Same state machine as the Visualiser, different render surface, different attention context.

**Plugins panel** — the Main window sidebar item that lists every registered plugin with: name / status (loaded / error / disabled) / declared capabilities / tool count. Click a row → plugin-detail view: per-tool list (read-only) and per-plugin settings (where applicable — e.g. the **Discord allowlist editor** for `discord_user.py` lives here). The Plugins panel hosts plugin-specific configuration; the **Permissions panel** keeps its ADR-0005 two-tab shape (Capabilities / Tools) for class+tool ACL only. _Avoid_: putting per-plugin settings inside Permissions, or putting class-level ACL inside Plugins.

**Main window** — the primary Felix UI surface. A chat/interaction canvas where the user converses with Felix by voice (the fast lane — wake + speak) or by typing (the slow-but-silent lane). The transcript renders both lanes interleaved. Layout: a collapsible left **sidebar nav** of four sections — **Conversation**, **Harness**, **Library**, **Settings** (#473 collapsed the original 16 routes; profile switching moved to a header control). Everything else is a sub-view reached inside one of the four. The right side is the **workspace**. The Queue earns a count badge in the chat header (the only time-sensitive surface) so the user sees pending items without leaving the conversation. Distinct from the **Visualiser** (ambient overlay) and from the **tray** (always-on launcher + quick-actions). Lifecycle: the Main window does **not** autostart with Cerebral — the user opens it on demand from the tray. Closing the window **hides** it; it does not quit Felix. Quit is reachable only from the tray. _Avoid_: calling it "the chat window" — it is the chat *and* the control surface.

**Workspace** — the Main window's right-hand area: a **primary slot** that permanently holds the **Conversation**, and a **secondary slot** beside it holding zero or more **panels** (tab strip when several are open), separated by a drag **splitter**. Closing the secondary slot returns the Conversation to full width. A panel can be **detached** into its own OS window. Layout state (sidebar collapsed, splitter position, open panels) is machine-global and persisted in renderer `localStorage` — layout is ergonomics, not identity, so it is neither a **System setting** nor **Profile**-scoped. _Avoid_: calling it a dockable workspace in the free-form sense — there is no arbitrary split tree, only primary + secondary.

**Panel** — a dockable view that opens in the workspace's secondary slot, contributed by a **plugin**. Distinct from the four sidebar sections, which are navigation, not panels.

**Panel spec** — the JSON a plugin returns to describe its **panel**: a declarative tree of widgets drawn from a fixed vocabulary (list, table, form, detail, text). A plugin never ships HTML or JavaScript into the Main window renderer — the renderer owns the drawing, the plugin owns the data (ADR-0012). This is what keeps an LLM-generated plugin from executing code beside the Credentials UI. Extends the schema-driven form rendering already proven in `schemaToFormHtml`. _Avoid_: "plugin UI code" — plugins contribute *data*, never code, to the renderer.

**Text widget** — the one editable widget in the **panel spec** vocabulary, backed by a plain `<textarea>`. Covers plain-text and Markdown content (notes, dossier fields, `.md` documents). `.docx` editing is **not** in scope for it and keeps ADR-0011's LibreOffice Writer path — no browser widget reproduces Word's layout engine. _Avoid_: treating it as a code editor; there is deliberately no syntax highlighting, because CodeMirror would require introducing a bundler.

**Tray (post-Main-window)** — the always-on launcher and escape hatch. After the Main window ships, the tray menu collapses from its previous fragmented-control role (~14 items) to four jobs: a status line ("Felix — Running" / "ACTIVE — listening"), `Open Felix` (focus or open Main window), `Switch profile` submenu (fast multi-profile action that doesn't justify drilling into Profiles), and `Quit`. All other controls (model picker, notifications, camera, reminder interval, visualiser toggle, Queue/Insights/Memory/Permissions/Credentials/Plugins/Profiles open-window items) move into the Main window's sidebar. Single source of truth per setting; no tray⇄Main sync.

### Flagged ambiguities

- **"notes" vs Memory** — resolved: these are unrelated. **Memory** is the profile-scoped ChromaDB long-term tier surfaced in the Library's Memory tab. The `notes` SQLite table belongs to the `notes` plugin and indexes Markdown files in `cerebral/data/notes/`. An empty `notes` table says nothing about Memory. _Avoid_: reading `notes` as "the Memory table".
- **"editor"** — resolved: two different things. Editing plain text or Markdown happens in a **text widget** inside a **panel**. Editing a `.docx` happens in LibreOffice Writer as its own program (ADR-0011). Neither is "the Felix editor".
- **"dockable"** — resolved: the **workspace** is primary + secondary + detach, not a free-form split tree. Asking for "docking" does not imply arbitrary panel arrangement.
- **Nav prominence vs dockability** — resolved: orthogonal. The four-section sidebar governs *discovery* (#473 deliberately reduced it); the **workspace** governs *composition*. Making a view dockable does not restore it to the nav.

### Deployment topology (post-Main-window)

Two processes, one binary on each side. Both stay local.

- **Cerebral (Python).** AI pipeline, memory, MCP execution, WebSocket IPC server on `ws://localhost:7766`. v1 floor: user starts manually (`python -m cerebral.main`). Post-v1: registered as an OS service (Windows service / macOS launchd / Linux systemd) and autostarts at boot. The v1 architecture must not preclude the service shape.
- **Felix (Electron).** Hosts the tray, the Main window, the Visualiser, and the irreversible-modal popup. Started on demand from a desktop shortcut. Connects to Cerebral over WebSocket. The renderer code (HTML/CSS/JS in `tray/windows/`) is **stack-agnostic** — no `require('electron')` from renderers, all backend calls go through the existing WebSocket IPC — so a future PWA mirror is a v2 deepening, not a v1 design choice.

PWA serving from Cerebral (a local HTTP server + service-worker shell) is **out of v1 scope**. Thin clients (phone, glasses) continue to ride OpenClaw bridges per CONTEXT.md "Deployment topology" until native clients ship.

**Conversation** — a turn-by-turn record of what Felix heard, said, was typed at, and called. Lives in the Main window's chat canvas. Per-profile. The canonical transcript surface — replaces the tray's previous fragmented "what just happened?" surfaces (queue results, model-switch notifications, tool-call logs).

**Conversation store** — the SQLite-backed persistent transcript. A new structured-memory tier alongside profiles / queue / ACL / credentials. Schema: `conversation_turns(id, profile_id, ts, kind, content_json)` where `kind ∈ {user_voice, user_text, felix_speech, tool_call, tool_result, system_event}`. Per-profile (consent belongs with identity). Stored unencrypted in the user's local SQLite (disk encryption is the user's OS responsibility, same posture as profiles + queue + memories). Retention is infinite in v1 — purge UX is a deepening, not a blocker. The rolling RAM buffer's raw audio stays unwritten per the existing memory-model rule; only the post-Whisper text of voice turns is persisted. Dropped 5W1H candidates stay in the queue table; the Conversation store records only acted-upon turns and system events.

**Distinct from Long-term memory.** The Conversation store keeps raw turns. ChromaDB keeps *extracted facts* learned from those turns. Conflating the two pollutes the semantic store with verbatim noise — Felix recalls "you live in Berlin" from the extraction pipeline, not by re-reading last Tuesday's transcript.

**Initial Main-window load.** On open, the Conversation pane shows the most recent ~50 turns of the active profile, scrolled to bottom, with a "load older" affordance at the top.

### Job-application pipeline

**Job board** — the aggregator Felix reads for openings. v1 target: Rat Race Rebellion (`ratracerebellion.com/job-postings`), a curated work-from-home feed that is fully readable **logged-out**, so Felix reads it anonymously (the `browser` plugin's `navigate` / Readability path — no login, no bot-wall). It only *links out*; applications are never submitted on the Job board itself. _Avoid_: treating the Job board as the place applications happen.

**Job posting** — one opening in the Job board feed (title, company, pay, snapshot, date, and an outbound link to an employer **ATS**). The unit Felix ranks, dedups, and applies against. Dedup key is the outbound URL.

**ATS (Applicant Tracking System)** — the employer's own application system a Job posting links out to (Greenhouse, Lever, Workday, or a bespoke career site). Heterogeneous — Felix drives them with a **generic form-filler** (LLM maps live DOM fields to known values), never per-ATS code. An ATS that requires login is a **Connected account** provider like any other. Felix bails and notifies on any ATS it cannot reliably drive (Workday is the expected hard case) rather than submitting a half-filled form.

**Résumé artifact** — the user's résumé as Felix keeps it: a ground-truth `.docx` (the editable source) plus the PDF derived from it, which is what ATS file fields receive. Both live in the **Document library**. Editable by the user (LibreOffice Writer) and by Felix (headless LibreOffice scripting) — one engine, turn-taking, snapshot-versioned (5 rolling + the original forever). Felix never rewrites it *per job* — edits change the one canonical résumé, not per-application copies. See ADR-0011. _Avoid_: treating the PDF as the source — it is derived output.

**Document library** — the profile-scoped store of documents Felix keeps *in itself* (files in the attachments dir + a SQLite index), browsable in the Felix UI the way credentials are. Exports and conversions Felix produces land here by default — never loose on the filesystem unless the user names a destination ("export it to my Desktop"). Because documents live inside Felix's own data, they travel with it across machines and are reachable remotely through the harness. The **Résumé artifact** is one entry in it. _Avoid_: "exports folder" — the library is a store with UI, not a directory convention.

**Applicant dossier** — the structured, form-fillable facts about the user (name, contact, work history, education, links) that Felix extracts from the **Résumé artifact** and fills discrete ATS fields from. Per-profile (SQLite). Distinct from **Profile** (Felix-identity container) and from the Résumé artifact (the PDF). _Avoid_: calling it a "profile" — Profile is the user-identity term.

**Answer bank** — the growing store of answers to ATS questions Felix has learned (work-authorization, years-with-X, salary expectation, …). Source of truth in SQLite; indexed in ChromaDB so a reworded question on a different ATS still matches a known answer — the `recall()` pattern from Long-term memory, pointed at application answers. Together with the Applicant dossier it forms the set of **known values**.

**Known value** — a field value that came from the Applicant dossier or the Answer bank (never inferred). The **zero-guessed rule** governs autonomy: Felix auto-submits an **Application** only when every filled field is a Known value. A required field with no Known value → Felix stops, notifies the user, stores the answer (making it Known next time). Eligibility/knockout questions (work authorization, sponsorship, relocation, start date, salary) always escalate on first encounter regardless. See ADR-0009.

**Application** — Felix's attempt to apply to one Job posting: its status (`shortlisted` / `awaiting-input` / `submitted` / `failed` / `skipped`), the ATS, and the Known values used. Logged in SQLite, deduped on the posting's outbound URL — the same posting is never applied to twice, but different roles at one employer are separate Applications. Rendered date-foldered in the **Job Search panel**.

**Shortlist** — the ranked subset of new Job postings Felix proposes for a run, scored against the Applicant dossier and the user's targeting (AI/tech + IT). The user approves entries before Felix applies (v1); the user's approval is the fit/pay filter. Post-v1 this converges toward threshold auto-selection.

**Job Search panel** — the Main-window sidebar panel for the pipeline: the date-foldered Application list, a "Check for new jobs" action (on-demand cadence in v1), and a link across to the **Credentials** panel, where the ATS and jobs-email Connected accounts live (not duplicated here). _Avoid_: "Job Search tab" — Main-window surfaces are panels.

### Book knowledge corpus

**Book library** — the profile-scoped store of books Felix has ingested (PDF/EPUB), browsable the way the **Document library** is. `book_ingest(path)` reads a book, chunks it by chapter, and files each chapter through the same clusters/collections spine that **Video ingest** and **GitHub ingest** already use (`source_type="book"` on the shared `videos` table). See ADR-0025. _Avoid_: conflating it with the Document library — books are a read-only ingested corpus, not user-editable documents.

**Source tier** — a book's evidentiary standing (1 Primary .. 4 Opinion/Anecdotal), stored on the book and carried through retrieval as context. Never used to rank or auto-resolve a **contradiction** — it labels a source, it does not decide who is right.

**Concept** (book corpus) — a named idea extracted from a book chapter (canonical name, aliases, definition, related concepts), linked back to its source passage. Distinct from a **Known value** — a concept is something a book *teaches*, not a fact Felix holds about the user.

**Claim** (book corpus) — a substantive assertion extracted from a passage, tagged with a `claim_type` (factual/empirical/theoretical/causal/predictive/methodological/normative/opinion/anecdotal/historical/definitional) and an `evidence_type`, always linked back to its source passage. Presented at answer time as "author X claims Y" — never merged with fact or with Felix's own inference. _Avoid_: treating an extracted claim as something Felix believes; it is something a source said.

**Claim graph** — typed relations (`supports`/`contradicts`/`depends_on`/`supported_by`/`derived_from`) between claims, evidence, and assumptions across the corpus. When two claims contradict, Felix surfaces both with sources — it does not pick a winner. _Avoid_: any "most authoritative author wins" resolution logic; ADR-0025 rules it out explicitly.

---

## Architecture

### Stack

| Layer | Technology |
|-------|-----------|
| Backend brain | Python |
| Frontend / tray | Node.js + web (HTML/CSS/JS) |
| Local LLM | Ollama (default tool-calling model: Qwen 3.6; Gemma 4 supported) |
| Cloud LLM | Claude (Anthropic) |
| Model router | OpenClaw |
| Always-on STT | Vosk |
| Full STT | faster-whisper |
| TTS | Kokoro (local, changeable voices) |
| Short-term memory | RAM (rolling buffer, never persisted) |
| Long-term memory | ChromaDB or Qdrant (local vector DB) |
| Structured memory | SQLite (profiles, preferences, queue) |
| Tool protocol | MCP (Model Context Protocol) |
| Messaging harness | OpenClaw |
| Workflow automation | n8n (self-hosted) |
| Primary integrations | Google (Gmail, Sheets, Drive, Calendar) |
| Offline fallbacks | Grist (sheets), IMAP/SMTP (mail) |

### Deployment topology

```
[Desktop — Cerebral]
  ├── Python backend (AI pipeline, memory, action execution)
  ├── Node.js frontend (system tray, dark UI, visualiser)
  ├── Ollama (local LLM)
  ├── OpenClaw harness (messaging + remote access)
  ├── MCP servers / plugins
  ├── ChromaDB / SQLite (local storage)
  └── n8n (workflow automation)

[Other devices — thin clients]
  └── Connect to Cerebral over local network
      (phone/glasses via OpenClaw until native clients ship)
```

### Audio pipeline

```
Ambient audio
  → Vosk (always-on, lightweight keyword + signal detection)
      → [no signal]: discard, loop
      → [signal detected]: last ~60s from rolling buffer
          → faster-whisper (full transcription)
              → LLM (5W1H extraction → candidate action)
                  → queue
```

### Action execution pipeline

```
User wakes Felix ("Felix, ...")
  → faster-whisper transcribes command
  → LLM decomposes into tasks
  → selects MCP tools
  → executes
  → result spoken via Kokoro + shown in UI
```

---

## Design principles

1. **Open source throughout.** Every component must have an accessible, modifiable codebase. No black boxes.

2. **Integration is the product.** Getting the components talking correctly is the hard work and the value. A feature that doesn't integrate cleanly doesn't ship.

3. **Local first, cloud fallback.** Felix works fully offline. Cloud services (Claude, Google APIs) enhance when available; local alternatives (Ollama, Grist, IMAP) cover when they don't.

4. **The growth loop over the bloat loop.** Felix does not ship every possible feature. It ships the core loop plus the ability to grow. Missing tool → design it → build it → done.

5. **Passive by default, active on wake.** Felix never interrupts. It observes, queues, and waits. The user controls when it speaks.

6. **Transparent intelligence.** Felix shows its work. The Insights view, the queue, the plugin directory — everything Felix knows and does is visible and editable by the user.

7. **Profile = identity, not configuration.** System settings are global. Profiles store who you are, what you remember, and how Felix sounds when talking to you.

---

## Integration registry

### Already covered by OpenClaw (do not duplicate)

| | What |
|--|------|
| ✅ Model providers | Anthropic, Ollama, OpenAI, Google, Groq, Mistral, DeepSeek, LM Studio, HuggingFace, Qwen, and 20+ more |
| ✅ Browser automation | Playwright (bundled) |
| ✅ Web extraction | Mozilla Readability (bundled) |
| ✅ PDF reading | PDF.js (bundled) |
| ✅ Messaging channels | WhatsApp, Telegram, Discord, Slack, Teams, and more |
| ✅ Image generation | ComfyUI (bundled) |
| ✅ Vector SQLite | sqlite-vec (bundled) |

### Starter tools (ships with Felix core)

| MCP Server | Capabilities |
|-----------|-------------|
| Clock | Timers, alarms, reminders, world time |
| Scheduler | Calendar events, recurring tasks |
| Browser | Web search, open URLs, page summarisation |
| Files | Create, open, move, search files and folders |
| Apps | Launch, switch, close applications |
| Clipboard | Read, write, monitor clipboard |
| Notes | Quick capture, searchable local notes |
| System | Volume, brightness, WiFi, screenshots, power |
| Shell | Run terminal commands and scripts |
| OpenClaw | All messaging channels + remote access harness |

### Google Workspace (online, with local OSS fallbacks)

| MCP Server | Fallback | Capabilities |
|-----------|---------|-------------|
| Gmail | IMAP/SMTP | Read, write, send, search, label, thread |
| Google Calendar | Local scheduler | Events, reminders, availability, invites |
| Google Drive | Nextcloud | Upload, download, search, organise |
| Google Docs | LibreOffice Writer | Read, write, create, export |
| Google Sheets | Grist | Read, write, formulas, create |
| Google Slides | LibreOffice Impress | Read, create, export |
| Google Contacts | Local SQLite | Read, search, create |
| Google Maps | OpenStreetMap | Directions, places, travel time |
| Google Tasks | Local scheduler | Create, complete, list |

### Day 1 integrations

| MCP Server | Category | Capabilities |
|-----------|---------|-------------|
| Git | Dev | Status, commit, push, pull, diff, log, branch |
| GitHub / GitLab | Dev | Issues, PRs, repos, notifications |
| Docker | Dev | List, start, stop, build containers |
| Package Managers | Dev | npm, pip, winget — install, update, search |
| SSH | Dev | Remote machines, run remote commands |
| HTTP Client | Dev | API requests, webhooks, test endpoints |
| Wikipedia | Information | Search, lookup, summarise articles |
| Weather | Information | Forecast, alerts, hourly (Open-Meteo OSS) |
| News | Information | Headlines, topic monitoring, sources |
| Stocks / Crypto | Information | Price lookup, watchlist, read-only market data |
| Bitwarden | Security | Read-only local vault access |
| VPN | Security | Connect, disconnect, check status |
| Network Scanner | Security | Devices, ports, ping, diagnostics |
| Printer / Scanner | Hardware | Print jobs, scan to file, check status |
| Game Launcher | Hardware | Steam — launch, library, running status |
| Invoice / Receipt | Finance | OCR extract → Google Sheets / Grist |
| Zoom / Google Meet | Communication | Join, schedule, manage video calls |
| Phone Calls | Communication | Via OpenClaw channels |

### Second wave (growth loop — add when needed)

| MCP Server | Category |
|-----------|---------|
| Notion | Productivity |
| Obsidian | Productivity |
| Todoist / Tasks | Productivity |
| Time Tracker | Productivity |
| YouTube | Social / Content |
| Reddit | Social / Content |
| Twitter / X | Social / Content |
| RSS Monitor | Social / Content |
| Sports Scores | Social / Content |
| GIMP / Darktable | Creative |
| Blender | Creative |
| Figma | Creative |
| FFmpeg | Creative |
| Home Assistant | Smart Home |
| Dropbox / OneDrive | Cloud Storage (low priority) |

### Later

| MCP Server | Category |
|-----------|---------|
| Health (Fitbit / Garmin / Google Fit) | Health |

---

## Memory model

| Tier | Store | Scope | Retention |
|------|-------|-------|-----------|
| Short-term | RAM rolling buffer | System-wide | ~60 seconds, never persisted |
| Environmental | RAM | Per-session | Camera/GPS context (location, travel state, building) |
| Long-term | ChromaDB (vector) | Per-profile | Indefinite, semantically searchable |
| Structured | SQLite | Per-profile | Profiles, queue, preferences, learned patterns |

---

## v1 ship criteria

OpenMind ships v1 when **both** are true:

1. **Feature complete against PRD #1.** Every one of PRD #1's 45 user stories has its full implementation delivered. Stand-in implementations (e.g. n8n-bridge wrappers used as placeholders for first-class OAuth plugins) do not count as delivered unless n8n was the deliberate target architecture for that story.
2. **Daily-driver stable for the author.** The author uses Cerebral every day as their primary assistant. The core wake → queue → approve loop does not break for daily-use stretches. Crashes, regressions, and broken plugins on the daily path are bugs that block v1.

Installable-on-a-friend's-machine, public release, PyPI/installer artefacts, marketing surface, and a v1.0.0 tag are **explicitly post-v1**. They become the v2 ship criteria.

---

## Discord user-account integration

OpenMind has **two** Discord integration paths, intentionally:

1. **Bot-API via OpenClaw** (the harness path, the default). A
   registered Discord bot is the messaging account; messages flow
   through `plugins/openclaw_channels.py` (#168 / PR #171). This is
   the same path Telegram / WhatsApp / Slack take. Currently
   deferred per the user's "add bots at a later date" decision
   ([#164](https://github.com/iggyghub/OpenMind/issues/164)).
2. **User-account direct** (the self-bot path,
   `plugins/discord_user.py` -- Issue
   [#175](https://github.com/iggyghub/OpenMind/issues/175), ADR-0006).
   Felix reads incoming DMs from real humans on the *user's
   personal* Discord account and replies as the user. This path
   bypasses OpenClaw entirely because OpenClaw 2026.4.29's Discord
   channel is bot-API only -- there is no user-account login flow
   to consume.

**Why both can coexist.** They bind two *different* Discord
identities (a bot user vs. the human user), so they don't race on
incoming events.

### ToS risk on the self-bot path

Discord's Developer Terms forbid automating personal user accounts.
Discord actively detects self-bots; detection results in **permanent
ban** of the human account (DMs, friend list, server ownership,
Nitro, purchase history -- all lost, no recovery). The user filing
#175 has explicitly accepted this risk. Slice-2 mitigations (human-
shaped reply delays sampled from a log-normal distribution, typing
indicators, per-sender allowlist, sleep-hours, per-channel rate
limits, per-channel serialisation) reduce detection probability but
do not eliminate it.

**No contributor should run `plugins/discord_user.py` against a
Discord account they are not prepared to lose.** See ADR-0006 for
the full posture; SETUP.md's "Discord (user account) -- experimental,
high risk" subsection covers the setup steps.

### Token storage

The user-account token is stored via the #160 keyring +
`DISCORD_USER_TOKEN` env-var chain, NEVER in a plain-JSON config and
NEVER logged. The provider is *deliberately not* surfaced in the
tray's "API keys" UI (friction-as-safety -- setting it requires the
user to do a slightly more deliberate thing than pasting into a
form).

### Slice sequencing

- **Slice 1** (PR #176, shipped): plugin skeleton + outbound
  `discord_send_message` + draft-only inbound via
  `cerebral/main.py:_surface_discord_draft`. Every inbound DM became
  a queue draft for manual approval; no auto-reply.
- **Slice 2** (PR #179, shipped): per-sender auto-reply allowlist +
  detection-mitigation gauntlet (`cerebral/discord_auto_reply.py`).
  Empty allowlist preserves slice-1 byte-identical behaviour. The
  `scripts/discord_user_allowlist.py` CLI manages senders + settings
  (rate limit, delay distribution, typing indicator, sleep-hours
  window).
- **Slice 3** (Issue #178, PR #219, shipped): `discord_react` /
  `discord_edit` / `discord_delete` outbound tools (all
  `irreversible=True`, `confirm`-gated, same `_scrub` discipline).
  `DiscordPresenceController` (`cerebral/discord_presence.py`) drives
  dynamic auto-idle / auto-online transitions based on LLM Discord
  activity; the slice-2 sleep-hours window wins over all
  auto-transitions; manual `discord_set_presence` calls override until
  the next LLM Discord action.

---

## Not in scope (yet)

- Security model and per-profile permissions
- Native mobile client (OpenClaw bridges this for now)
- Smartglasses client
- 2D / 3D character themes (visualiser ships first)
- Multi-context / multi-user profiles in a shared household
- Visual plugin builder (natural language builder ships first)
- **Computer use** beyond v1 (ADR-0016): isolated / virtual-desktop session (v1 drives the live desktop); macOS AX / Linux AT-SPI backends (Windows UIA only in v1); continuous autonomous screen-watching (Felix drives only when addressed, like the wake-gated core loop); screenshot persistence or visual training data (raw frames are never written to disk)
