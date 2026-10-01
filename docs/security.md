# Security

## Implemented controls

- Required environment-provided administrator password; no default credential and no short-password bypass.
- Server-side sessions with random tokens, digest-only token storage and expiry; expired sessions are deleted and return 401.
- HttpOnly, SameSite=Strict cookies; Secure by default and `__Host-` naming when Secure.
- Session-bound CSRF token plus an exact, required `HUB_ALLOWED_ORIGINS` allow-list on mutations.
- `Host`, `X-Forwarded-Host` and other forwarded headers are never used to infer Origin trust.
- `Cache-Control: no-store, private` on every API response.
- Pydantic input validation with forbidden extra fields.
- HTTPS required for non-loopback app URLs; embedded credentials, control characters, dangerous schemes and malformed ports rejected.
- Repository-relative managed paths restricted to `Apps/`.
- Runtime endpoint configuration restricted to loopback by default and rejects credentials, query strings, fragments and malformed URLs.
- No arbitrary URL health fetch, shell endpoint or command interpolation.
- Approval action, target and canonical content hash checked and consumed transactionally.
- Restricted operations fail closed because no broker exists.
- CSP, frame denial, referrer and browser permission headers.
- Service worker caches only the public shell and declared public assets; private `/api/` requests are network-only.
- Local snapshots use a 0700 directory, 0600 database/manifest files, temporary files and bounded cleanup. They are not external or disaster backups.
- Conservative high-confidence secret scanner.

## Development exception

`HUB_COOKIE_SECURE=false` is allowed only for direct loopback HTTP development. Codespaces and production access use HTTPS and must keep Secure cookies enabled. `HUB_ALLOWED_ORIGINS` must be the exact browser origin and is required in every startup mode.

## Limitations and intent gate

The bootstrap password boundary is deliberately a single-administrator, personal-Hub intent gate. It is not a multi-user identity system and has no MFA, lockout or external identity lifecycle. The password exists in process environment; use a Codespaces secret and never place it in `.env.example` or tracked files. The scanner is defence in depth, not proof that arbitrary sensitive data is absent.

Approval expiry and migration checksums are intentionally out of the current hardening scope. External-write brokers remain disabled; no approval authorises an operation until a constrained broker exists.

The current process runs under the developer account. A future coding-agent environment still needs credential isolation and a constrained external-write broker before powerful tools can be enabled.
