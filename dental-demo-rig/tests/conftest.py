from __future__ import annotations

import pytest

from clone.profile import PracticeProfile
from clone.settings import PROSPECTS_DIR
from webhooks.store import MemoryStore, generate_week, set_store


@pytest.fixture
def profile() -> PracticeProfile:
    path = PROSPECTS_DIR / "_showcase.yaml"
    return PracticeProfile.from_yaml(path.read_text(encoding="utf-8"))


@pytest.fixture
def store(profile: PracticeProfile) -> MemoryStore:
    memory = MemoryStore()
    memory.seed(
        profile.prospect_id,
        generate_week(
            profile.prospect_id,
            profile.hours.model_dump(),
            [p.model_dump() for p in profile.providers],
            profile.timezone,
        ),
    )
    set_store(memory)
    yield memory
    set_store(None)
