from pathlib import Path


def test_service_worker_excludes_private_apis():
    root = Path(__file__).resolve().parents[2]
    source = (root / "Apps/system/hermes-hub/frontend/public/sw.js").read_text()
    guard = source.index("url.pathname.startsWith('/api/')")
    cache = source.index("caches.match(request)")
    assert guard < cache
    offline_assets = source[source.index("OFFLINE_ASSETS") : source.index("self.addEventListener")]
    assert "/api/" not in offline_assets
