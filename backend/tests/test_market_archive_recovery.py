from threading import Event

from app.services import market_archive_recovery as recovery


def test_slow_recovery_is_nonblocking_single_flight_and_throttled(monkeypatch):
    monkeypatch.setenv('TOPSIGNAL_MARKET_ARCHIVE_ENABLED', 'true')
    entered, release, finished = Event(), Event(), Event()
    calls = []
    factory = object()

    def recover(actual_factory, owner):
        calls.append((actual_factory, owner))
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(recovery, 'recover_decision_commits', recover)
    worker = recovery.DecisionCommitRecovery(factory)
    original_run = worker._run

    def run(owners):
        try:
            original_run(owners)
        finally:
            finished.set()

    monkeypatch.setattr(worker, '_run', run)
    try:
        assert worker.request(['owner', 'owner'])
        assert entered.wait(5)
        # The trading caller has returned while filesystem work is still held.
        assert not finished.is_set()
        assert not worker.request(['owner'])
        assert calls == [(factory, 'owner')]
    finally:
        release.set()
        assert finished.wait(5)
    assert not worker.request(['owner'])


def test_failure_is_reported_and_allows_later_retry(monkeypatch):
    errors = []
    monkeypatch.setattr(recovery, 'recover_decision_commits',
                        lambda *_: (_ for _ in ()).throw(OSError('disk unavailable')))
    monkeypatch.setattr(recovery, 'failure', lambda *args: errors.append(args))
    worker = recovery.DecisionCommitRecovery(object())
    worker._busy = True
    worker._run(('owner',))
    assert not worker._busy
    assert errors[0][:2] == ('owner', 'decision_commit_recovery')


def test_disabled_recovery_never_opens_a_session(monkeypatch):
    monkeypatch.delenv('TOPSIGNAL_MARKET_ARCHIVE_ENABLED', raising=False)
    worker = recovery.DecisionCommitRecovery(lambda: (_ for _ in ()).throw(AssertionError()))
    assert not worker.request(['owner'])
