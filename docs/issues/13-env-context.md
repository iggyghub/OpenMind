## Parent
#1 — PRD: OpenMind v1

## What to build
Environmental context module: camera (webcam) and GPS/IP geolocation feed into short-term memory for situational awareness. No frames or coordinates are written to disk — RAM only. Camera access is opt-in.

## Acceptance criteria
- [ ] IP geolocation runs at startup and on network change, storing rough location in short-term memory
- [ ] Camera capture (if enabled) runs on a low-frequency interval, frames passed to a local vision model for scene inference
- [ ] Environmental context is attached to 5W1H extractions as metadata
- [ ] Camera access is opt-in, off by default, toggleable from tray settings
- [ ] No camera frames or GPS data written to disk
- [ ] Context updates emitted over IPC so the visualiser can reflect environment state

## Blocked by
- #3 (audio pipeline)
- #12 (memory manager)
