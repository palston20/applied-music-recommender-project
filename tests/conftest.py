import pytest

from src.dj_agent import DEFAULT_CATALOG
from src.recommender import load_songs


@pytest.fixture(scope="session")
def songs():
    return load_songs(str(DEFAULT_CATALOG))


@pytest.fixture
def genres(songs):
    return sorted({s["genre"] for s in songs})


@pytest.fixture
def moods(songs):
    return sorted({s["mood"] for s in songs})
