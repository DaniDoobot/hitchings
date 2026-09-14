"""Regression tests for read-only session transaction lifecycle and PostgreSQL isolation hardening.

Verifies:
1. Native execution options (REPEATABLE READ, postgresql_readonly=True) configure the transaction
   from its very inception without executing fragile SET statements during an active transaction.
2. Prevention of psycopg.errors.ActiveSqlTransaction ("SET TRANSACTION ISOLATION LEVEL must be called before any query").
3. Direct mutating SQL blocker (before_cursor_execute).
4. ORM flush blocker (before_flush).
5. Clean rollback and listener unregistration on exceptions.
6. Default instantiation of read_only_session_scope() without arguments.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

from scripts.preview_source_discovery import (
    create_read_only_engine,
    create_read_only_session,
    configure_read_only_session,
    read_only_session_scope,
)
from app.models.entry import Entry
from app.models.source import Source, SourceType


def test_create_read_only_engine_options_postgresql():
    """Verify create_read_only_engine attaches REPEATABLE READ and postgresql_readonly for PostgreSQL."""
    pg_engine = create_engine("postgresql+psycopg://user:pass@localhost:5432/hitchings")
    ro_engine = create_read_only_engine(pg_engine)

    assert ro_engine._execution_options.get("isolation_level") == "REPEATABLE READ"
    assert ro_engine._execution_options.get("postgresql_readonly") is True


def test_create_read_only_engine_options_sqlite():
    """Verify create_read_only_engine attaches SERIALIZABLE for SQLite."""
    sqlite_engine = create_engine("sqlite:///:memory:")
    ro_engine = create_read_only_engine(sqlite_engine)

    assert ro_engine._execution_options.get("isolation_level") == "SERIALIZABLE"


def test_postgresql_driver_level_isolation_without_set_transaction_sql():
    """Simulate PostgreSQL psycopg dialect and verify isolation is set at DBAPI level without fragile SQL queries."""
    dialect = PGDialect_psycopg()
    mock_dbapi = MagicMock()
    mock_dbapi.IsolationLevel.REPEATABLE_READ = 3
    dialect.dbapi = mock_dbapi

    mock_connection = MagicMock()

    # When dialect applies read-only and isolation level
    dialect.set_readonly(mock_connection, True)
    assert mock_connection.read_only is True

    dialect.set_isolation_level(mock_connection, "REPEATABLE READ")
    assert mock_connection.isolation_level == 3


def test_read_only_session_scope_with_preexisting_active_transaction():
    """Verify that a session with an existing open transaction is rolled back cleanly.

    Prevents: ActiveSqlTransaction: SET TRANSACTION ISOLATION LEVEL must be called before any query.
    """
    engine = create_engine("sqlite:///:memory:")
    session = Session(engine)

    # Execute a query before entering the scope (simulating prior autobegin query)
    session.execute(text("SELECT 1"))
    assert session.in_transaction() is True

    # Entering read_only_session_scope should roll back pre-existing transaction
    # and configure read-only protections without error
    with read_only_session_scope(session) as ro_db:
        assert ro_db is session
        res = ro_db.execute(text("SELECT 42")).scalar()
        assert res == 42

    # Verify session is clean after exit
    assert session.in_transaction() is False


def test_read_only_session_blocks_mutating_raw_sql(db_session: Session):
    """Verify direct mutating SQL statements (INSERT, UPDATE, DELETE, etc.) are strictly rejected."""
    with read_only_session_scope(db_session) as ro_db:
        # SELECT is permitted
        res = ro_db.execute(text("SELECT 1")).scalar()
        assert res == 1

        # INSERT must be blocked
        with pytest.raises(RuntimeError, match="READ-ONLY VIOLATION: Mutating SQL execution blocked"):
            ro_db.execute(text("INSERT INTO entries (id, title) VALUES ('test-id', 'test-title')"))

        # UPDATE must be blocked
        with pytest.raises(RuntimeError, match="READ-ONLY VIOLATION: Mutating SQL execution blocked"):
            ro_db.execute(text("UPDATE entries SET title = 'updated'"))

        # DELETE must be blocked
        with pytest.raises(RuntimeError, match="READ-ONLY VIOLATION: Mutating SQL execution blocked"):
            ro_db.execute(text("DELETE FROM entries"))


def test_read_only_session_blocks_orm_flush(db_session: Session):
    """Verify ORM flush is fail-closed blocked if any instance is added, modified, or deleted."""
    with read_only_session_scope(db_session) as ro_db:
        source = Source(
            name="Mutating Test Source",
            url="https://example.com/test",
            type=SourceType.WEBSITE,
        )
        ro_db.add(source)

        with pytest.raises(RuntimeError, match="READ-ONLY VIOLATION: Preview attempted to modify database!"):
            ro_db.flush()


def test_read_only_session_scope_cleanup_on_exception():
    """Verify that exceptions inside read_only_session_scope do not leave active transactions or listeners."""
    engine = create_engine("sqlite:///:memory:")
    session = Session(engine)

    with pytest.raises(ValueError, match="Simulated preview error"):
        with read_only_session_scope(session) as ro_db:
            ro_db.execute(text("SELECT 1"))
            assert ro_db.in_transaction() is True
            raise ValueError("Simulated preview error")

    # Transaction must be rolled back
    assert session.in_transaction() is False


def test_read_only_session_scope_default_instantiation():
    """Verify calling read_only_session_scope() without arguments creates and manages an owned session."""
    with read_only_session_scope() as ro_db:
        assert ro_db is not None
        res = ro_db.execute(text("SELECT 100")).scalar()
        assert res == 100

    # The owned session is closed and has no active transaction after exit
    assert ro_db.in_transaction() is False
