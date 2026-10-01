# Google Apps Script integration

Status: **BLOCKED**. No Apps Script project, `.clasp.json`, deployment ID, Google identity or deployed test app is available.

The registry supports external links without changing the app. `embedded_legacy` and `hub_aware` are descriptive modes only; selecting either does not alter iframe headers, add theme support or grant AI access.

A future real test must verify Google redirects, nested sandbox frames, sender origin and `event.source`, registered app ID, protocol/request IDs, payload limits and dynamic theme acknowledgement. `ALLOWALL` must not be enabled without a per-app clickjacking and authentication review. External launch remains the fallback.
