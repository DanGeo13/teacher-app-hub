# Security

## Implemented controls

- Required environment-provided administrator password; no default credential.
- Server-side sessions with random tokens, digest-only token storage and expiry.
- HttpOnly, SameSite=Strict cookies; Secure by default.
- Session-bound CSRF token plus exact Origin validation on mutations.
- `Cache-Control: no-store, private` on every API response.
- Pydantic input validation with forbidden extra fields.
- HTTPS required for non-loopback app URLs; embedded credentials and dangerous schemes rejected.
- Repository-relative managed paths restricted to `Apps/`.
- Runtime endpoint configuration restricted to loopback by default.
- No arbitrary URL health fetch, shell endpoint or command interpolation.
- Approval action, target and canonical content hash checked and consumed transactionally.
- Restricted operations fail closed because no broker exists.
- CSP, frame denial, referrer and browser permission headers.
- Conservative high-confidence secret scanner.

## Development exception

`HUB_COOKIE_SECURE=false` is allowed only for direct loopback HTTP development. Codespaces and production access use HTTPS and must keep Secure cookies enabled. `HUB_ALLOWED_ORIGINS` must be the exact browser origin.

## Limitations

The bootstrap password boundary is single-user and has no MFA, lockout or external identity lifecycle. The password exists in process environment; use a Codespaces secret and never place it in `.env.example` or tracked files. The scanner is defence in depth, not proof that arbitrary sensitive data is absent.

The current process runs under the developer account. A future coding-agent environment still needs credential isolation and a constrained external-write broker before powerful tools can be enabled.
