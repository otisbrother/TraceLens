import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from apps.api.db import get_db, make_engine
from apps.api.main import app
from apps.api.models import Base
from data.generator.seed import seed_database


@pytest.fixture
def database(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_database(db)
    yield engine
    engine.dispose()


@pytest.fixture
def client(database):
    def override():
        with Session(database) as db:
            yield db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def login(client, username="investigator"):
    response = client.post("/api/session/login", json={"username": username, "password": "tracelens-local"})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()
