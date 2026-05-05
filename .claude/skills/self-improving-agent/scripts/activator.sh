#!/usr/bin/env bash
# Self-improvement activator — UserPromptSubmit hook
# Outputs a brief reminder injected before each user prompt.
# Keep this short: ~30 tokens overhead is the target.

cat <<'EOF'
<self-improvement>
After this task: if a command failed, the user corrected you, or a better approach was found, log it to .learnings/ (ERRORS.md / LEARNINGS.md / FEATURE_REQUESTS.md). Promote broadly applicable learnings to CLAUDE.md.
</self-improvement>
EOF
