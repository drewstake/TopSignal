"""Request-local, bounded timings; never retain SQL, parameters or HTTP payloads."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter


_current = ContextVar("evaluation_timing", default=None)


class EvaluationTiming:
    def __init__(self):
        self.stages = {}
        self.stack = []

    def row(self, name):
        return self.stages.setdefault(name, dict(duration_ms=0., self_ms=0., calls=0,
            failures=0, db_queries=0, db_query_ms=0., db_connects=0, db_connect_ms=0.))

    @contextmanager
    def stage(self, name):
        row = self.row(name)
        frame = [name, perf_counter(), 0.]
        self.stack.append(frame)
        row['calls'] += 1
        try:
            yield
        except BaseException:
            row['failures'] += 1
            raise
        finally:
            elapsed = (perf_counter() - frame[1]) * 1000
            self.stack.pop()
            row['duration_ms'] += elapsed
            row['self_ms'] += max(0., elapsed - frame[2])
            if self.stack:
                self.stack[-1][2] += elapsed

    def snapshot(self):
        return {name: {key: round(value, 3) if isinstance(value, float) else value
                       for key, value in row.items()} for name, row in self.stages.items()}


def begin():
    timing = EvaluationTiming()
    return timing, _current.set(timing)


def end(token):
    _current.reset(token)


@contextmanager
def stage(name):
    timing = _current.get()
    if timing is None:
        yield
    else:
        with timing.stage(name):
            yield


def timed_stage(name):
    def decorate(function):
        @wraps(function)
        def measured(*args, **kwargs):
            with stage(name):
                return function(*args, **kwargs)
        return measured
    return decorate


def _db_start():
    timing = _current.get()
    if timing is None:
        return None
    name = timing.stack[-1][0] if timing.stack else 'evaluation_overhead'
    return timing.row(name), perf_counter()


def _db_finish(sample, kind):
    if sample is not None:
        row, started = sample
        row['db_queries' if kind == 'query' else 'db_connects'] += 1
        row['db_query_ms' if kind == 'query' else 'db_connect_ms'] += (perf_counter()-started)*1000


def install_database_timing(engine):
    """Install once on the application engine; event state belongs to each execution."""
    from sqlalchemy import event

    @event.listens_for(engine, 'do_connect')
    def connect(dialect, connection_record, args, kwargs):
        sample = _db_start()
        try:
            return dialect.connect(*args, **kwargs)
        finally:
            _db_finish(sample, 'connect')

    @event.listens_for(engine, 'before_cursor_execute')
    def before(connection, cursor, statement, parameters, context, executemany):
        context._topsignal_timing = _db_start()

    def finish(context):
        sample = getattr(context, '_topsignal_timing', None)
        if sample is not None:
            context._topsignal_timing = None
            _db_finish(sample, 'query')

    @event.listens_for(engine, 'after_cursor_execute')
    def after(connection, cursor, statement, parameters, context, executemany):
        finish(context)

    @event.listens_for(engine, 'handle_error')
    def failed(exception_context):
        finish(exception_context.execution_context)
