# Core Loop (#270) — Human Live-Verify Checklist

The autonomous loop built S1–S3 with mocked tests + one live Ollama smoke test.
This covers what only a human at a running Cerebral can confirm: that Felix
actually picks tools, chains them, gates them, and saves/replays Recipes for real.

**Agent work:** writing this checklist + the automated tests.
**Human work:** executing it in order on the dev box.

Work top-down — each section assumes the one above passed.

---

## 0. Pre-conditions (check these FIRST)

1. **A tool-capable model is installed.** `ollama list` shows a qwen model
   (you have `qwen2.5:7b`). Native tool-calling needs this — Gemma alone is fiddly.
2. **Ollama is running.** `curl http://localhost:11434/api/tags` returns 200.
3. **Start Cerebral:** `python -m cerebral.main` from the repo root. No tracebacks on boot.
4. **The active model is the tool-capable one.** Main window → Settings → Model:
   confirm the active model is `ollama/qwen2.5:7b` (NOT gemma4). If wrong, switch it.
5. **(Cloud only, optional)** OpenClaw running on `:3000` — needed for section 5.

---

## 1. Single-step engine (S1) — the foundation

Do these in order; each is one spoken or typed command to Felix.

1. **Silent-class tool fires friction-free.** Ask a read-only thing —
   *"Felix, what time is it in Tokyo?"* (or any clock/read tool).
   - ✅ Felix calls the tool and speaks the answer. **No consent prompt.**
2. **Chat fallback (no tool).** Ask something with no matching tool —
   *"Felix, what do you think of jazz?"*
   - ✅ Felix answers conversationally. **No tool fired**, no error.
3. **Ask-class tool prompts.** Trigger a write-class action (e.g. *"Felix, create a
   note that says hello"* → an `fs_write`/ask-class tool).
   - ✅ The **Conversation consent card** appears (Once / Session / Persistent / Deny + Why?).
   - ✅ Choosing **Once** runs it; the action completes.
4. **Denial is graceful.** Repeat #3 and click **Deny**.
   - ✅ Felix speaks a "permission declined"-style reply. **No crash, no half-done action.**
5. **Irreversible pops the modal.** Trigger an irreversible tool (e.g. `gmail_send`).
   - ✅ A separate **always-on-top modal** appears (not an inline card). Cancel it.
   - ✅ Nothing is sent.
6. **Ambiguity asks, doesn't guess.** Give a vague command —
   *"Felix, send it to them."*
   - ✅ Felix asks a **clarifying question** instead of firing a random tool.
7. **(Optional) Bad-arg self-correct.** Hard to force by hand; if you see a tool
   call fail once then succeed (or fail gracefully with a spoken "couldn't work
   that out"), that's the one-re-ask path. Skip if not naturally triggered.

---

## 2. Chaining (S2)

1. **A real 2-step chain.** *"Felix, read my latest unread email and reply that
   I'll be there."* (or any 2-tool request you have plugins for).
   - ✅ Step 1 (read/search) runs; step 2 (send) **pops its own gate** (modal for send).
   - ✅ **Each step shows as its own turn** in the Conversation as it happens —
     you can watch it unfold, not just see a final answer.
2. **Mid-chain denial stops cleanly.** Run #1 again; **Deny** the send step.
   - ✅ The chain **stops** — no further steps run, no crash.
3. **(Optional) Step cap.** Hard to hit deliberately. If a confused request loops,
   confirm it **stops after ~8 steps** with a spoken "took more steps than expected"
   rather than running forever.

---

## 3. Recipes (S3)

1. **Save offer appears.** After a successful **2+-step** chain (section 2 #1),
   - ✅ Felix offers in the Conversation: *"Save these steps as a Recipe?"*
   - ✅ A **single**-step action does **not** trigger the offer.
2. **Accept + name.** Accept and name it (e.g. *"morning reply"*).
   - ✅ It appears in the **Recipes panel** (Main window sidebar).
3. **Replay by name.** *"Felix, run my morning reply."*
   - ✅ The saved steps run again.
   - ✅ The irreversible/ask step **prompts AGAIN** on replay (the save stored the
     plan, **not** a standing approval — this is the key security check).
4. **Compose with a normal tool.** *"Felix, run my morning reply, then tell me a joke."*
   - ✅ The Recipe runs **and** the extra step runs — Recipes behave like any tool.
5. **Panel hygiene.**
   - ✅ Each Recipe shows **run count** and **last-run** date.
   - ✅ **Rename** and **delete** work.
   - ✅ (If you can age one) a Recipe unused >30 days shows a **stale** flag;
     two identical Recipes show a **duplicate** flag.
6. **Missing-tool grace.** (Optional) Disable/uninstall a plugin a Recipe uses,
   then replay it.
   - ✅ That step fails with a **spoken notice**, not a crash.
7. **Per-profile isolation.** Switch to a second profile.
   - ✅ Profile A's Recipes do **not** appear for profile B.

---

## 4. Cloud tool path (optional — needs OpenClaw)

1. Start OpenClaw on `:3000`. Main window → Settings → Model → switch to
   `claude/haiku` (or `claude/sonnet`).
2. Re-run section 1 #1 (*"what time is it in Tokyo?"*).
   - ✅ **Best case:** the cloud model picks the tool and it runs (OpenClaw forwards
     tools over `/v1`).
   - ⚠️ **Fail-soft case:** if OpenClaw's `/v1` drops tool calls, Felix should
     **answer as text without crashing** (not silently hang). If you see text-only
     where a tool should have fired, that confirms the known `/v1` limitation —
     point the cloud backend at OpenClaw's native endpoint (see ADR-0008).
3. Also run `python -m pytest cerebral -m integration` with OpenClaw up — the
   `test_claw_complete_with_tools_fail_soft` test should now run (not skip).

---

## What "pass" means

Sections 1–3 all green against the local qwen model = **the core loop works
end-to-end as designed**. Section 4 is the cloud bonus; the local path is the
v1 daily-driver path. Log any failure as a new issue referencing the section
number above.
