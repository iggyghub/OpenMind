## Parent
#1 — PRD: OpenMind v1

## What to build
Opt-in OS notifications for queue activity and a configurable periodic reminder that fires when items have been sitting in the queue longer than a set interval (default 2 hours).

## Acceptance criteria
- [ ] OS notifications are off by default; toggled from tray settings
- [ ] When enabled, a notification fires when a new item is added to the queue
- [ ] Periodic reminder interval is configurable (minutes, default 120)
- [ ] Reminder fires only if the queue is non-empty at the interval
- [ ] Interval of 0 disables periodic reminders
- [ ] Clicking a notification opens the tray pulldown
- [ ] Settings persist across restarts in system config

## Blocked by
- #8 (tray app queue)
