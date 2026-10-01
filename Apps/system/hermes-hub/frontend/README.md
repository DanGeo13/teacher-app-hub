# Hermes Hub frontend

React and TypeScript responsive application shell. Its service worker caches the public root shell and declared public assets only; `/api/` is explicitly network-only. The four Playwright browser scenarios live in `e2e/` so `npm run test:e2e` resolves the pinned package from this frontend workspace.

See `docs/setup.md` for supported commands. Browser traces and video are disabled; CI retains only failure PNGs for seven days.
