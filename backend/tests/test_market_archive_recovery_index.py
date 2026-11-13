import sqlite3

from app.services.market_archive import SCHEMA


QUERY = ('SELECT d.id,d.payload FROM decisions d LEFT JOIN decision_commits c ON c.id=d.id '
         'WHERE c.id IS NULL ORDER BY d.at DESC LIMIT 100')


def test_recovery_index_preserves_pending_decisions_and_avoids_confirmed_payload_reads(tmp_path):
    db = sqlite3.connect(tmp_path/'archive.sqlite3')
    db.executescript(SCHEMA)
    db.execute('DROP INDEX decisions_recovery_time_id')
    for i in range(300):
        db.execute('INSERT INTO decisions VALUES(?,?,?,?)', (str(i), f'{i:06}', 1, b'x'*8192))
        if i % 3:
            db.execute('INSERT INTO decision_commits VALUES(?,?)', (str(i), 'confirmed'))
    expected = db.execute(QUERY).fetchall()
    db.execute('CREATE INDEX decisions_recovery_time_id ON decisions(at,id)')
    assert db.execute(QUERY).fetchall() == expected
    assert len(expected) == 100 and expected[0][0] == '297'
    assert any('decisions_recovery_time_id' in row[-1] for row in db.execute('EXPLAIN QUERY PLAN '+QUERY))
    # SQLite can reject confirmed keys from the two indexes before touching
    # the decisions table. Verify that the table's only Column opcode reads
    # payload, after the confirmation predicate, rather than id for every row.
    program = db.execute('EXPLAIN '+QUERY).fetchall()
    table_root = db.execute("SELECT rootpage FROM sqlite_master WHERE name='decisions'").fetchone()[0]
    cursor = next(row[2] for row in program if row[1]=='OpenRead' and row[3]==table_root)
    table_reads = [row for row in program if row[1]=='Column' and row[2]==cursor]
    assert [row[3] for row in table_reads] == [3]
    assert any(row[1]=='NotNull' and row[0]<table_reads[0][0] for row in program)
    db.close()
