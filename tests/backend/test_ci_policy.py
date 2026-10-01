from pathlib import Path


def test_ci_runs_browser_validation_with_least_privilege():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/ci.yml").read_text()
    playwright = (root / "Apps/system/hermes-hub/frontend/playwright.config.ts").read_text()
    setup = (root / "docs/setup.md").read_text()

    assert "contents: read" in workflow
    assert "playwright install --with-deps chromium" in workflow
    assert "run test:e2e" in workflow
    assert "retention-days: 7" in workflow
    assert "--proxy-headers" not in workflow
    assert "trace: 'off'" in playwright
    assert "video: 'off'" in playwright
    assert "screenshot: 'only-on-failure'" in playwright
    assert "--no-proxy-headers" in playwright
    assert "external-write broker" in setup
    assert "Do not rebuild, migrate or alter an existing Codespace automatically" in setup


def test_supplied_start_commands_do_not_trust_forwarded_headers():
    root = Path(__file__).resolve().parents[2]
    run_local = (root / "scripts/run-local.sh").read_text()
    setup = (root / "docs/setup.md").read_text()
    assert "--no-proxy-headers" in run_local
    assert "--no-proxy-headers" in setup
    assert "--proxy-headers" not in run_local
