# Known limitations

- No authentic Hermes session, agent orchestration or hand-off is implemented.
- No Ollama/Qwen inference is performed.
- No Apps Script application is integrated or deployed.
- Authentication is a single-administrator password-based personal-Hub intent gate, not a multi-user identity system.
- Approval expiry and migration checksums are not implemented in this hardening scope.
- Local snapshots are not off-workspace backups; cleanup retains only the ten newest local snapshots.
- Live restore and every external write fail closed.
- App health status is recorded metadata; registry URLs are never probed by the backend.
- Shared GAS theme/AI protocol is a schema only and is not claimed as deployed.
- Codespaces persistence, private forwarding and HTTPS cookies are configured but not verified on this non-Codespaces host.
- Browser CI requires the pinned Chromium binary and Linux dependencies; local browser execution may be blocked by host policy or network availability.
