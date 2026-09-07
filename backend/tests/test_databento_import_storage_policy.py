"""Cloud imports must fail before opening archives or connecting to a DB."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.services.databento_ingestion import (
    DatabentoIngestionError,
    import_databento_archive,
    import_databento_archives,
)


@pytest.mark.parametrize("bulk", [False, True])
def test_postgres_import_is_rejected_before_any_io(tmp_path, bulk):
    engine = create_engine("postgresql+psycopg://localhost/never_connect")

    @event.listens_for(engine, "do_connect")
    def reject_connection(*_args):
        pytest.fail("Relational history import attempted a PostgreSQL connection")

    archive: Path = tmp_path / "does-not-exist.zip"
    try:
        with Session(engine) as db:
            with pytest.raises(DatabentoIngestionError, match="build_databento_cache.py"):
                if bulk:
                    import_databento_archives(db, [archive], commit_batches=True)
                else:
                    import_databento_archive(db, archive, commit_batches=True)
    finally:
        engine.dispose()
