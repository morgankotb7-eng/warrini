# Changelog

## 0.1.0 — 2026-11-12

First release.

- Local web editor in Arabic (RTL): guides, steps, paste or drag screenshots, focus and cover boxes,
  listen to a step, undo/redo, autosave, conflict detection between tabs.
- Renderer: 1080p or 720p MP4 with smooth sub-pixel camera moves toward the focus box, highlight ring
  and spotlight, opaque covers, title and end cards, step badges, progress bar, Arabic captions and an
  SRT sidecar.
- Voice: local Egyptian voice (VoiceTut-TTS, Tareq) in a worker process that loads the model once;
  optional online voice per guide; clip cache with LRU eviction; loudness normalized to −16 LUFS.
- CLI: `render`, `import-folder`, `doctor`.
- Local server hardened: 127.0.0.1 only, HttpOnly SameSite cookie, token header for writes, Host and
  Origin checks, strict CSP, upload validation, range requests for video.
