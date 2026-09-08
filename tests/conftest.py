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

    Fail-closed structural validation:
    1. For SQLite: in-memory ('', ':memory:', 'file::memory:') or file containing 'test'.
    2. For external databases (PostgreSQL, MySQL, etc.): the DATABASE NAME component
       must unequivocally be a test database (e.g. starts with 'test_' or ends with '_test' or '_tests').
       Having 'test' in username, password, host, or query parameters is strictly REJECTED.

    Raises:
        RuntimeError: if the URL does not match safe test patterns.
    """
    from sqlalchemy.engine import make_url

    try:
        parsed = make_url(url_str)
    except Exception as exc:
        raise RuntimeError(f"SECURITY / LEDGER VIOLATION: Invalid database URL format: {exc}") from exc

    backend = parsed.get_backend_name()
    database = (parsed.database or "").strip().lower()

    is_safe = False
    if backend == "sqlite":
        if database in ("", ":memory:", "file::memory:") or not database:
            is_safe = True
        elif "test" in database:
            is_safe = True
    else:
        if database and (
            database.startswith("test_")
            or database.endswith("_test")
            or database.endswith("_tests")
            or database in ("test", "tests")
        ):
            is_safe = True

    if not is_safe:
        safe_repr = f"backend='{backend}', database='{database}'"
        raise RuntimeError(
            f"SECURITY / LEDGER VIOLATION: Database target ({safe_repr}) does not match safe test patterns. "
            f"Tests must strictly run against an isolated test database (e.g. SQLite in-memory or a database name ending with '_test'). "
            f"Aborting test execution to prevent contamination of development or production database."
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
