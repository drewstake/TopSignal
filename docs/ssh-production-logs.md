# Reading production logs over SSH

The production supervisor already writes rotating logs under
`C:\ProgramData\TopSignal\runtime\logs`. Use the same `-RuntimeRoot` when
installing the scheduled task. File logging is independent of an SSH connection;
closing the reader does not stop the app. Ordinary development launches do not
necessarily enable these handlers.

`scripts/read-production-logs.py` uses only Python's standard library. It does
not import the trading app, connect to the database/broker, or start any worker.
Copy it to a private tools folder on the host, then invoke it through your
existing SSH connection. For example, after connecting to the Windows host:

```powershell
python C:/ProgramData/TopSignal/tools/read-production-logs.py --list
python C:/ProgramData/TopSignal/tools/read-production-logs.py --stream app --tail 100
python C:/ProgramData/TopSignal/tools/read-production-logs.py --stream bot --tail 30 --follow
python C:/ProgramData/TopSignal/tools/read-production-logs.py --stream supervisor --follow
```

Other streams: `access`, `frontend`, `security-out`, `security-error`,
`migration-out`, `migration-error`. Override `--runtime-root` if deployment uses
a different location. `--list` reports file size, last-write time in UTC and
rotation counts; it does not certify application health. Missing files are
reported explicitly. Follow mode waits for a missing file and reopens the
current filename after rotation or truncation. Ctrl+C ends only the reader.
Use `--follow --duration 10` for a bounded live check over SSH.

The initial tail is limited to 1 MiB and 10,000 lines. Live polling is best-effort:
events written into an old file just before rotation can be missed between
polls. For incident analysis, preserve and inspect the rotated files too. The
existing `backend/tools/summarize_bot_logs.py` reads current and rotated bot
diagnostics; see [bot diagnostics](bot-diagnostics.md).

Keep logs local and private. The reader returns the existing file content; it
does not add redaction to arbitrary startup output. Application formatters
already redact conventional credentials, but logs can contain account/trade
details. No log server, public port, or cloud log storage is required.

## peteamdpc preparation, October 21, 2026

The reader and existing summarizer are installed in
`C:\ProgramData\TopSignal\tools`. The empty runtime/log directory is prepared.
The new root inherits no broad user access: SYSTEM and local Administrators have
full control, including the existing SSH administrator. During app deployment,
grant the dedicated service identity the runtime permissions required by the
[Windows operations guide](windows-24x7-operations.md); keep tools read-only to
that identity. Do not run the app as administrator just to obtain log access.

The app, production logging config, dependencies, and startup task still need
deployment. Use the reviewed current release, including the diagnostic modules
and logging configuration; a clone alone can omit uncommitted diagnostic work.
Then verify real `topsignal.log`, `bot-diagnostics.jsonl`, and supervisor output
over SSH. A reader smoke test is not evidence that the application is running.
