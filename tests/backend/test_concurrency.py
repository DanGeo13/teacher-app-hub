from concurrent.futures import ThreadPoolExecutor

from hermes_hub_backend.database import Database
from hermes_hub_backend.errors import ConflictError
from hermes_hub_backend.models import AppCreate, AppUpdate
from hermes_hub_backend.registry import RegistryService


def test_concurrent_stale_updates_have_one_winner(settings, teaching_app):
    service = RegistryService(Database(settings.database_path))
    created = service.create(AppCreate.model_validate(teaching_app), "admin")
    value = AppUpdate(expected_revision=created.revision, title="Concurrent winner")

    def update():
        try:
            return service.update(created.id, value, "admin")
        except ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _item: update(), range(2)))

    assert sum(result is not None for result in results) == 1
    assert service.get(created.id).title == "Concurrent winner"
