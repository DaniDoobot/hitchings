"""Pytest test configuration and fixtures."""

import pytest
from collections.abc import Generator
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.main import app
from app.db.base import Base
from app.db.session import get_db

from sqlalchemy.pool import StaticPool

# Enable SQLite compilation for PostgreSQL-specific types during tests
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"

@compiles(UUID, "sqlite")
def compile_uuid_sqlite(type_, compiler, **kw):
    return "CHAR(36)"


def verify_test_db_url_is_safe(url_str: str) -> None:
    """Validate that a database URL strictly targets an isolated test environment.

    Fail-closed:
    Rejects any URL that does not explicitly target an in-memory SQLite database
    or a dedicated database containing 'test' in its name/path.

    Raises:
        RuntimeError: if the URL does not match safe test patterns.
    """
    low = url_str.lower()
    is_safe = (
        low.startswith("sqlite:///:memory:")
        or ":memory:" in low
        or "test" in low
    )
    if not is_safe:
        raise RuntimeError(
            "SECURITY / LEDGER VIOLATION: Test database URL does not match safe test patterns. "
            "Tests must strictly run against an isolated test database (e.g. SQLite in-memory or a database containing 'test'). "
            "Aborting test execution to prevent contamination of development or production database."
        )


# In-memory test engine with StaticPool to share connection state
test_engine = create_engine(
    "sqlite:///:memory:",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="session", autouse=True)
def guard_test_database_isolation():
    """Fail-closed guard: ensure test engine strictly points to an isolated test DB."""
    verify_test_db_url_is_safe(str(test_engine.url))


@pytest.fixture(scope="session", autouse=True)
def setup_test_db(guard_test_database_isolation):
    """Create all database tables for the test session."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide a clean transactional database session for each test."""
    connection = test_engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    session.close()
    if transaction.is_active:
        transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """Provide a TestClient with the test database session overridden."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
