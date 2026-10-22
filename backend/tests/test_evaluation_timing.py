import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text

from app import evaluation_timing as timing


def test_nested_stages_do_not_double_count_and_errors_reset_context(monkeypatch):
    clock = iter([0., 1., 3., 4.])
    monkeypatch.setattr(timing, 'perf_counter', lambda: next(clock))
    capture, token = timing.begin()
    try:
        with pytest.raises(ValueError):
            with timing.stage('outer'):
                with timing.stage('inner'):
                    raise ValueError('secret must never be recorded')
    finally:
        timing.end(token)
    result = capture.snapshot()
    assert result['outer']['duration_ms'] == 4000
    assert result['outer']['self_ms'] == 2000
    assert result['inner']['self_ms'] == 2000
    assert result['inner']['failures'] == result['outer']['failures'] == 1
    assert timing._current.get() is None
    assert 'secret' not in json.dumps(result)


def test_sql_timings_include_failures_without_parameters_or_cross_thread_leaks():
    def run(label):
        engine = create_engine('sqlite://')
        timing.install_database_timing(engine)
        capture, token = timing.begin()
        try:
            with timing.stage(label), engine.connect() as connection:
                assert connection.execute(text('select :value'), {'value': 'credential-secret'}).scalar() == 'credential-secret'
                with pytest.raises(Exception):
                    connection.execute(text('select * from missing_table'))
                assert connection.execute(text('select 1')).scalar() == 1
        finally:
            timing.end(token)
            engine.dispose()
        return capture.snapshot()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, ['first', 'second']))
    for label, result in zip(['first', 'second'], results):
        assert set(result) == {label}
        assert result[label]['db_queries'] == 3
        assert result[label]['db_connects'] == 1
        assert result[label]['db_query_ms'] >= 0
        assert 'credential-secret' not in json.dumps(result)
    assert timing._current.get() is None
