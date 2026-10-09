/* Felix's Help encyclopedia -- the content shown in the Help tab's Guide sub-tab.
 *
 * HOW TO UPDATE THIS FILE (instructions for whichever AI is asked to):
 *  - This is the ONLY file that holds Guide content. Add a topic by appending a
 *    topic object to `topics`; the Help pane's section list is derived from the
 *    `section` field in declaration order, so placement in the array is the
 *    placement in the UI.
 *  - Write for two readers at once: a human who has never used Felix, and an AI
 *    agent trying to work out what Felix can do. Prefer concrete mechanism over
 *    marketing. Name the real subsystem when it helps ("Cerebral", "the planner",
 *    "MCP tools") and explain it rather than assuming it.
 *  - Do NOT list individual tool names here. The Capabilities sub-tab renders the
 *    live tool index straight from the running orchestrator; a hand-written tool
 *    list here would go stale the first time a tool is renamed.
 *  - Keep `see_also` ids pointing at real topic ids. A dangling id renders as
 *    nothing (help-panel.js skips it), so it fails quietly -- check by hand.
 *  - Source of truth for anything factual: CONTEXT.md (glossary + architecture)
 *    and docs/adr/*.md. If this file and an ADR disagree, the ADR is right and
 *    this file is stale.
 *  - After editing, run `cd tray; npm test`.
 *  - Fuller guidance: docs/agents/help-tab.md.
 */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else if (typeof window !== 'undefined') {
    window.HelpContent = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  return {
    version: '1',
    topics: [
      {
        id: 'what-is-felix',
        section: 'Orientation',
        title: 'What Felix is',
        summary: 'A personal AI agent that runs on your own computer: you talk to it, it plans, and it acts through tools.',
        body: [
          'Felix is the name you speak to. Saying "Felix" wakes the assistant; the name can be changed per profile.',
          'The thinking happens in a background program called Cerebral, which runs on your desktop. The Felix window, the tray icon and any other device you connect all talk to Cerebral; it is the one place where memory, planning and actions live.',
          'Local-first means your profiles, memories, documents and history are stored on this machine. What leaves it:',
          '- requests sent to a model server that is not on this computer (today usually Budd, a remote server; Claude if you have chosen it);',
          '- the outside services a tool talks to on your behalf, such as Gmail, a web search or a broker.',
          'Everything Felix does goes through tools, and every tool call passes the same permission check. See "From a sentence to an action".'
        ],
        see_also: ['how-a-request-becomes-an-action', 'models-and-routing']
      },
      {
        id: 'how-a-request-becomes-an-action',
        section: 'Orientation',
        title: 'From a sentence to an action',
        summary: 'Your words become a plan, the plan becomes tool calls, and each call is permission-checked before it runs.',
        body: [
          'Every request follows the same path:',
          '- Speech is transcribed (faster-whisper) or typed text is taken as-is.',
          '- A language model reads the request with your recent conversation and relevant memories, and decides which tools to use.',
          '- Each tool call is checked against your permission settings: it runs silently, asks you first, or is refused.',
          '- Results go back to the model, which may call more tools, until it has an answer.',
          '- The reply is shown in the window and, when voice is on, spoken by the local voice (Kokoro).',
          'Tools are provided by plugins, using a standard called MCP. Felix cannot do anything that no tool does; when a capability is missing it can build a new plugin or say so.'
        ],
        see_also: ['memory', 'conversation-and-compaction']
      },
      {
        id: 'talking-to-felix',
        section: 'Orientation',
        title: 'Ways to reach Felix',
        summary: 'Say its name, type in the Main window, use the tray, or message it from another app.',
        body: [
          '- Voice: a lightweight listener (Vosk) waits for the wake word, then the full transcriber takes the request. Voice needs the speech model installed; without it, typing still works.',
          '- The Main window: a chat with your conversation history, plus every other panel in the sidebar.',
          '- The tray icon: restart Felix, open the logs, quit.',
          '- Other apps: messaging channels connected through OpenClaw can relay messages to Felix.',
          'However you reach it, the request goes through the same pipeline and the same permission checks.'
        ],
        see_also: ['the-main-window', 'how-a-request-becomes-an-action']
      },
      {
        id: 'the-main-window',
        section: 'Orientation',
        title: 'A tour of the window',
        summary: 'What each section in the sidebar is for.',
        body: [
          'Conversation: talk to Felix and see each turn, including the tools it called.',
          'Harness: the plugins that give Felix its tools (with their status and switches) and the installed Skills.',
          'Library: what Felix keeps for you. Sub-tabs cover Memory, Insights, Recipes, Documents, Job search, Videos, Books and GitHub, plus Thinking, which shows what Felix has been doing behind the scenes.',
          'Trading: paper trading only. Overview, Strategies, Tickers, Trades and Replay show the strategies, their trades and their backtests.',
          'Log: a running activity record of what Felix did and when.',
          'Help: this guide, and a live list of every tool Felix currently has.',
          'Settings: General preferences, AI models (which models answer and in what order), Sign-in (connected accounts) and Permissions.'
        ],
        see_also: ['talking-to-felix', 'models-and-routing']
      },
      {
        id: 'models-and-routing',
        section: 'How Felix thinks',
        title: 'Which brain answers',
        summary: 'You choose the models and their order; if one is unavailable Felix moves to the next.',
        body: [
          'Felix can use three kinds of model:',
          '- local models on this computer, run by Ollama (free per request, limited by the graphics card);',
          '- remote model servers you add by address, such as Budd (any OpenAI-compatible server);',
          '- cloud models from Anthropic (Claude), which cost per request.',
          'In Settings > AI models you set a priority order. A request goes to the first model in the order; if it fails or is unreachable, Felix falls back to the next one.',
          'Coding work can use a separate model: mark a model "Use for coding" and code tasks go there instead.',
          'Only one model request runs at a time, and your live conversation takes priority over background work.'
        ],
        see_also: ['what-is-felix', 'conversation-and-compaction']
      },
      {
        id: 'memory',
        section: 'How Felix thinks',
        title: 'What Felix remembers',
        summary: 'A few seconds of audio, the current surroundings, long-term memories you approved, and structured data.',
        body: [
          'Felix keeps four kinds of memory:',
          '- Short-term: about the last 60 seconds of audio, held in RAM only and never saved.',
          '- Environmental: the current session\'s context, such as location, also in RAM only.',
          '- Long-term: memories stored by meaning (a vector database), so Felix can recall them later by topic. One set per profile.',
          '- Structured: profiles, preferences, the proposal queue and learned patterns, in a local database.',
          'Felix does not save memories silently. When you state something durable, it raises a memory proposal; the memory is written only when you approve it. Saved memories are in Library > Memory.'
        ],
        see_also: ['recipes-and-insights', 'conversation-and-compaction']
      },
      {
        id: 'conversation-and-compaction',
        section: 'How Felix thinks',
        title: 'Long conversations',
        summary: 'Every turn is logged; when a conversation gets too long for the model, older turns are summarised.',
        body: [
          'Every turn (what you said, what Felix said, each tool call and its result) is written to the conversation log. That log is the record: anything the model sees can be rebuilt from it.',
          'A model can only read so much at once. When the conversation passes about 70% of the active model\'s limit, Felix summarises the oldest turns into one summary turn and continues with that plus the recent turns.',
          'The summary is itself saved as a turn in the log, so nothing disappears from the record; only what the model is shown gets shorter.'
        ],
        see_also: ['memory', 'models-and-routing']
      },
      {
        id: 'skills',
        section: 'How Felix thinks',
        title: 'Skills: procedures Felix can install',
        summary: 'Installed instructions that change how Felix approaches a kind of task. They add know-how, never new powers.',
        body: [
          'A skill is a named set of instructions, such as how to run a design interview or how to break a plan into issues. When a request matches, Felix loads it into the planner.',
          'Skills are written locally or fetched from an online source, and can always be read and edited. Manage them in Harness > Skills.',
          'A skill adds know-how, never capability. It cannot add a tool, and every tool it leads Felix to use still goes through the normal permission check.',
          'Not to be confused with:',
          '- a plugin, which adds a tool;',
          '- a recipe, which replays a fixed sequence of tool calls.'
        ],
        see_also: ['recipes-and-insights', 'how-a-request-becomes-an-action']
      },
      {
        id: 'recipes-and-insights',
        section: 'How Felix thinks',
        title: 'Recipes and insights',
        summary: 'Felix proposes saving sequences you repeat, and learns from what you approve and dismiss. Nothing is applied silently.',
        body: [
          'A recipe is a saved, named sequence of tool calls that you can run again on command.',
          'When the same sequence runs several times, Felix proposes saving it as a recipe. It is saved only if you approve, and running it later re-checks every step\'s permissions: a recipe saves the plan, never a permission.',
          'Proposals (actions, memories, recipes) arrive in one queue that you approve or dismiss.',
          'Each decision on a proposal that would run a real tool counts as a signal. When the same kind of decision repeats enough times, Felix records an insight about how you like things done. Insights are shown in Library > Insights.'
        ],
        see_also: ['memory', 'skills']
      },
      {
        id: 'tools-and-plugins',
        section: 'What Felix can do',
        title: 'Tools and plugins',
        summary: 'Every capability is an MCP tool from a plugin; how plugins register, trust, and handle conflicts.',
        body: [
          'Every capability Felix has is a tool supplied by a plugin, running over the Model Context Protocol (MCP). Tools come from plain Python files in the plugins/ folder, and a plugin must declare which capability classes it needs.',
          '- To register, a plugin must pass a static safety scan. Plugins in plugins/_trusted/ skip the scan but show a permanent red "trusted, unverified" badge and still pass every permission check.',
          '- Every plugin needs a matching test file or it is refused at startup.',
          '- If two plugins offer the same tool name, the later one takes over and the takeover is logged.',
          '- Felix can build new plugins itself as part of the growth loop; a new plugin loads on the next plugin scan without a restart.',
          '- The live list of tools is rendered from the running system in Help > Capabilities.'
        ],
        see_also: ['how-a-request-becomes-an-action', 'permissions-and-consent', 'skills']
      },
      {
        id: 'computer-use',
        section: 'What Felix can do',
        title: 'Driving the computer',
        summary: 'Felix can read the screen and operate Windows applications, preferring background actions over taking your mouse.',
        body: [
          'Windows only. It requires screen capture and device control capabilities.',
          '- Felix reads the screen accessibility tree first, identifying named buttons and fields. It falls back to reading pixels and clicking coordinates only when an application has no usable tree, such as games or canvases.',
          '- It drives a normal browser window like any other application, rather than using a remote-controlled automation browser.',
          '- Where possible, it acts in the background through accessibility actions instead of taking over your mouse.'
        ],
        see_also: ['permissions-and-consent', 'how-a-request-becomes-an-action']
      },
      {
        id: 'browsing-and-search',
        section: 'What Felix can do',
        title: 'The web',
        summary: 'Web search and page navigation run through the OpenClaw harness; saved logins let Felix reach sites that need an account.',
        body: [
          '- Web search and page fetching go through the OpenClaw harness command-line tools. Search currently uses DuckDuckGo.',
          '- OpenClaw is also the gateway for messaging channels, which are other applications that can relay messages to Felix.',
          '- Some sites require a signed-in browser session. Felix keeps saved logins for those and will ask you to step in when a site shows a human verification check.'
        ],
        see_also: ['how-a-request-becomes-an-action', 'memory']
      },
      {
        id: 'documents',
        section: 'What Felix can do',
        title: 'Documents',
        summary: 'Your .docx file is the source of truth; LibreOffice is the editor, Felix\'s editing engine and the converter.',
        body: [
          '- A document\'s editable .docx file is the source of truth. PDFs and plain text are derived from it.',
          '- LibreOffice handles everything: you edit in LibreOffice Writer, Felix edits the same file headlessly through LibreOffice\'s scripting, and LibreOffice converts the file to PDF and other formats.',
          '- Saved documents appear in Library > Documents.'
        ],
        see_also: ['memory', 'skills']
      },
      {
        id: 'trading',
        section: 'What Felix can do',
        title: 'Trading',
        summary: 'Simulated (paper) trading only: strategies, how ideas are validated, and the trend basket.',
        body: [
          '- This is paper trading only, using simulated orders, not real money.',
          '- A strategy is a ticker symbol plus a small piece of code that turns price bars into buy or flat signals. Ideas come from web discovery, books and other sources, and they pass through one validation pipeline called the Gauntlet: judge the idea, generate the code, and backtest it in a sandbox.',
          '- Strategies start as paper trading and can graduate to live status after passing validation. Hand-written exceptions skip the per-symbol backtest, such as the IPO play and the trend basket.',
          '- As of October 2026, the trend basket is the only active strategy. It buys up to 10 strong S&P 500 stocks when market breadth exceeds 55 percent, and exits on a 12 percent trailing stop or after 20 trading days.',
          '- Every fill is recorded. The Trading panel Overview, Strategies, Tickers, Trades and Replay tabs show all strategies, trades and backtests.'
        ],
        see_also: ['how-a-request-becomes-an-action', 'permissions-and-consent']
      },
      {
        id: 'jobs',
        section: 'What Felix can do',
        title: 'Job applications',
        summary: 'Finding postings, mapping them onto an application form, and where a human decision is still required.',
        body: [
          '- Felix finds job postings, scores them, and fills application forms using your applicant dossier and an answer bank.',
          '- Submitting an application is irreversible, so Felix asks you to confirm in a pop-up. The first several applications per profile always require this confirmation.',
          '- Auto-submit is opt-in per profile and only triggers when every field is filled from known answers with nothing guessed and no new eligibility question appears. Otherwise, Felix falls back to asking you.',
          '- Job searches and applications are shown in Library > Job search.'
        ],
        see_also: ['permissions-and-consent', 'memory']
      },
      {
        id: 'video-and-books',
        section: 'What Felix can do',
        title: 'Watching and reading',
        summary: 'Watching videos to extract and cluster ideas, and the book knowledge corpus Felix draws on.',
        body: [
          '- Give Felix a video URL and it transcribes the audio, reads on-screen text when needed, and stores a summary.',
          '- Books are split by chapter and processed the same way, keeping page or paragraph references.',
          '- Ideas extracted from videos, books and GitHub repositories are grouped into clusters you can browse in Library > Videos, Books and GitHub.'
        ],
        see_also: ['memory', 'skills']
      },
      {
        id: 'permissions-and-consent',
        section: 'Safety and self-modification',
        title: 'What Felix is allowed to do',
        summary: 'Every tool needs a permission class; each class runs silently, asks first, or is denied. Irreversible actions always confirm.',
        body: [
          '- Every tool declares which of 16 fixed capability classes it needs, including reading files, writing files, deleting files, the shell, cloud network access, screen capture, device control, and secrets. Tools cannot invent new classes.',
          '- Each class is set to one of three levels: run silently, ask first, or deny. By default, reading files, the clipboard, network access, reading outside data and device control run silently. Writing or deleting files, reading secrets, installing code, screen capture and writing outside data ask first. The shell is denied until you turn it on in settings, at which point it asks.',
          '- When asked, you can allow once, for the session, or permanently.',
          '- Actions that cannot be undone, such as sending, submitting, or deleting, always show a confirmation pop-up, even if you granted the class permanently.',
          '- Requests that come from things Felix overheard, rather than from you waking it, are one step stricter: silent becomes ask, ask becomes deny.',
          '- Shell commands always run inside a Windows sandbox. They can only touch one sandbox folder, have no network access, run in a clean environment with no API keys, and are capped at 1 GB of memory and 120 seconds.',
          '- Change these settings in Settings > Permissions.'
        ],
        see_also: ['how-a-request-becomes-an-action', 'tools-and-plugins', 'self-dev']
      },
      {
        id: 'self-dev',
        section: 'Safety and self-modification',
        title: 'Felix changing its own code',
        summary: 'Felix can change its own code in a sandboxed copy; changes merge only if tests pass, and a failed boot rolls back.',
        body: [
          '- Felix can change its own code. A self-dev run clones the repository into the sandbox folder, never touching the running copy, makes one bounded change, and runs the full test suites there.',
          '- The result is a pull request. It is merged only if the tests pass, after which Felix pulls the change and restarts.',
          '- After a self-dev restart the launcher runs a boot self-check. If Felix fails to come up healthy, it rolls back to the previous version automatically and keeps a copy of any uncommitted work.',
          '- The model that writes the change is whichever you pick for self-dev in Settings > AI models. The safety checks do not depend on which model wrote it.',
          '- Adding a plugin is a different, lighter path, which needs no restart.'
        ],
        see_also: ['tools-and-plugins', 'permissions-and-consent', 'updating-this-guide']
      },
      {
        id: 'updating-this-guide',
        section: 'Maintaining this guide',
        title: 'Keeping this guide current',
        summary: 'For an AI (or person) asked to update this guide: where it lives, its format, and how to check it.',
        body: [
          '- This guide content lives entirely in tray/lib/help-content.js. A topic is one object appended to topics, and array order is the order in the UI. Sections appear in the order of their first use.',
          '- Topic fields are id, section, title, summary, body (an array of strings), and an optional see_also array of topic ids.',
          '- Body format: a string starting with "- " is a bullet. Consecutive bullets form one list. Anything else is a paragraph. No other markdown syntax.',
          '- A see_also id that matches no topic silently renders nothing, so always verify them. Never hand-list tool names here; Help > Capabilities is generated from the running system.',
          '- The factual source of truth is CONTEXT.md plus docs/adr. If those disagree with a topic, the topic is the stale one.',
          '- Any change that adds or alters a feature should update its topic in the same pull request.',
          '- Check your work with `cd tray && npm test`.'
        ],
        see_also: ['tools-and-plugins']
      }
    ]
  };
});
