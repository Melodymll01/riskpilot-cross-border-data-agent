"""Two first-time attachment workers must initialize model metadata atomically."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from infra.evidence.sqlite_index import SqliteEvidenceIndex
from infra.storage._db import SqliteConnectionPool


@pytest.mark.parametrize("different_model", [False, True])
def test_two_initial_writers_share_one_embedding_profile(tmp_path, different_model):
    pool = SqliteConnectionPool(str(tmp_path / "concurrent.sqlite3"))
    SqliteEvidenceIndex(pool, embedding_identity="shared-model")
    both_read = Barrier(2)

    class FirstReadBarrier:
        def __init__(self, connection):
            self.connection = connection
            self.first = True

        def execute(self, query, parameters=()):
            cursor = self.connection.execute(query, parameters)
            if self.first and query.startswith("SELECT identity, dimensions"):
                self.first = False
                both_read.wait(timeout=5)
            return cursor

    def initialize(worker_id):
        connection = pool.get()
        try:
            with connection:
                chosen = SqliteEvidenceIndex.__new__(SqliteEvidenceIndex)
                chosen._embedding_identity = (
                    "different-model" if different_model and worker_id else "shared-model"
                )
                chosen._validate_embedding_identity(FirstReadBarrier(connection), 8, writing=True)
            return "accepted"
        except ValueError as error:
            assert "不一致" in str(error)
            return "rejected"
        finally:
            pool.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(initialize, range(2)))
        row = (
            pool.get()
            .execute("SELECT identity, dimensions FROM evidence_embedding_profile")
            .fetchone()
        )
        assert row[1] == 8
        assert results.count("rejected") == (1 if different_model else 0)
    finally:
        pool.close()
