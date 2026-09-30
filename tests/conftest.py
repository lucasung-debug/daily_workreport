import pytest

from workreport.db import Database


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """모든 테스트에서 설정·데이터 경로를 임시 폴더로 격리한다."""
    home = tmp_path / "home"
    monkeypatch.setenv("WORKREPORT_HOME", str(home))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    return home


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()
