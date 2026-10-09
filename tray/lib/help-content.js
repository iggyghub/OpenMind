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
        summary: 'A local-first personal AI agent that runs on the user\'s own machine.',
        body: [
          'Felix is a local-first personal AI agent that runs on your own machine.',
          '- The wake name is Felix, but the backend process that does the heavy lifting is called Cerebral.',
          'Local-first means your data stays on your device, avoiding per-request costs for local work and keeping your private context offline.',
          '- Some capabilities, like complex reasoning or web retrieval, still route to cloud models for better performance.'
        ],
        see_also: ['talking-to-felix', 'models-and-routing']
      },
      {
        id: 'how-a-request-becomes-an-action',
        section: 'Orientation',
        title: 'From a sentence to an action',
        summary: 'The pipeline that turns your words into tool calls, permissions, and spoken replies.',
        body: [
          'Your speech or typed message enters the input pipeline and is transcribed or parsed.',
          '- An LLM planner breaks the intent down into discrete steps.',
          'Each step becomes an MCP tool call, which passes through a permission gate.',
          '- Approved actions execute, and their results feed back into the loop.',
          'The final response is synthesized by the local TTS engine and spoken aloud.'
        ],
        see_also: ['memory', 'conversation-and-compaction']
      },
      {
        id: 'talking-to-felix',
        section: 'Orientation',
        title: 'Ways to reach Felix',
        summary: 'Always-on wake-word listening, the Main window chat, the tray, and remote channels.',
        body: [
          'You can interact with Felix through the always-on wake-word listening mode.',
          '- The Main window provides a persistent chat interface for longer conversations.',
          'The system tray offers quick commands and status monitoring.',
          '- Remote messaging is supported via the OpenClaw harness, letting you message Felix from external applications.'
        ],
        see_also: ['the-main-window']
      },
      {
        id: 'the-main-window',
        section: 'Orientation',
        title: 'A tour of the window',
        summary: 'One short paragraph per sidebar section: Conversation, Harness, Library, Trading, Log, Help, Settings.',
        body: [
          'Conversation holds your active chat threads and interaction history.',
          '- Harness displays the current OpenClaw session state and connected external services.',
          'Library stores your saved notes, references, and manually curated knowledge.',
          '- Trading shows real-time market data, portfolio metrics, and execution logs.',
          'Log provides a detailed audit trail of all background processes and tool invocations.',
          '- Help opens this encyclopedia and provides quick-start guides.',
          'Settings controls model selection, permissions, audio options, and system preferences.'
        ],
        see_also: ['talking-to-felix']
      },
      {
        id: 'models-and-routing',
        section: 'How Felix thinks',
        title: 'Which brain answers',
        summary: 'Local Ollama models, cloud Claude, and custom servers, prioritized by the user.',
        body: [
          'Cerebral supports multiple model providers to balance speed, cost, and capability.',
          '- Local models run through Ollama, keeping sensitive data on-device and eliminating cloud latency.',
          'Complex reasoning tasks are routed to Claude via the OpenClaw integration for superior accuracy.',
          '- Custom OpenAI-compatible servers can be added for private enterprise instances.',
          'The priority order is set by the user, and coding work automatically routes to the specialized code model route.'
        ],
        see_also: ['what-is-felix', 'memory']
      },
      {
        id: 'memory',
        section: 'How Felix thinks',
        title: 'What Felix remembers',
        summary: 'Three layers of memory: short-term buffer, vector store, and structured facts.',
        body: [
          'Short-term memory holds the immediate conversation context for the current session.',
          '- A vector store indexes past interactions by meaning, enabling semantic recall across sessions.',
          'A structured database maintains verified facts and user preferences.',
          '- The system writes to short-term and vector memory automatically to keep context fresh.',
          'Important facts and structured entries wait for explicit user approval before permanent storage.'
        ],
        see_also: ['conversation-and-compaction', 'recipes-and-insights']
      },
      {
        id: 'conversation-and-compaction',
        section: 'How Felix thinks',
        title: 'Long conversations',
        summary: 'The session log is the source of truth; long threads are compacted rather than truncated.',
        body: [
          'Every interaction is recorded in the session log, which serves as the single source of truth.',
          '- When a conversation grows too large for the model\'s context window, compaction triggers automatically.',
          'The system summarizes older turns and merges them into concise historical summaries.',
          '- This preserves continuity without losing key details or hitting token limits.',
          'Truncation is never used; the conversation history always remains intact in the compacted format.'
        ],
        see_also: ['memory']
      },
      {
        id: 'skills',
        section: 'How Felix thinks',
        title: 'Skills: procedures Felix can install',
        summary: 'Reusable step-by-step procedures that load when a task matches.',
        body: [
          'Skills are reusable, step-by-step procedures that extend Felix\'s capabilities on demand.',
          '- They are installed from local files or community repositories.',
          'When a task matches a skill\'s trigger conditions, Cerebral loads the procedure into the planner.',
          '- Skills guide the agent through multi-step workflows without needing manual instruction each time.',
          'They keep complex operations consistent and reproducible across different sessions.'
        ],
        see_also: ['recipes-and-insights']
      },
      {
        id: 'recipes-and-insights',
        section: 'How Felix thinks',
        title: 'Recipes and insights',
        summary: 'Repeatable procedures and passive observations surfaced as user proposals.',
        body: [
          'Recipes are repeatable procedures that Felix learns from your actual workflows.',
          '- Insights are passive observations about patterns in your usage or environment.',
          'Both are surfaced to you as actionable proposals rather than applied silently.',
          '- You review, accept, or reject each proposal before it becomes part of your system.',
          'This keeps automation transparent and ensures you maintain full control over what gets executed.'
        ],
        see_also: ['memory', 'skills']
      }
    ]
  };
});
