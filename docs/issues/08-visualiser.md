## Parent
#1 — PRD: OpenMind v1

## What to build
On-screen visualiser: a dark animated abstract orb/waveform in a frameless always-on-top web overlay window. Reacts to Felix states — passive (slow pulse), active (brighter, faster), speaking (waveform synced to audio), thinking (processing animation). Toggled from the tray menu.

## Acceptance criteria
- [ ] Frameless, always-on-top, click-through window (does not capture mouse events)
- [ ] Dark aesthetic with animated orb or waveform in a contrasting colour
- [ ] Four distinct visual states: passive, active, speaking, thinking
- [ ] State transitions are smooth, not instant cuts
- [ ] Responds to state events over the IPC bridge from Cerebral
- [ ] Toggling from tray shows/hides without restarting
- [ ] Window position is saved between sessions

## Blocked by
- #3 (audio pipeline)
- #8 (tray app queue)
