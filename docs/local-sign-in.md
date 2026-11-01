# Local sign-in for the connected worker

Local sign-in is an optional second authentication provider for the existing
owner. It does not enable demo/offline mode, change account ownership, create a
cloud user, disable API authentication, or alter worker routing or strategy.
Google remains available. Account data and credentials still use the configured
application database; this feature does not remove a Supabase quota restriction.

## Provisioning

1. On the worker, create a protected directory outside the release tree. On
   Windows, restrict its ACL to Administrators and SYSTEM (full control) and the
   worker service identity (modify). Do not grant ordinary local users access.
2. As an administrator, run the release's Python interpreter with
   `backend/tools/provision_local_auth.py --account ACCOUNT_ID --store ABSOLUTE_DB_PATH --code-file ABSOLUTE_CODE_PATH`.
   Protect the code file's directory for administrators only. The command looks
   up the existing account owner through the application database, refuses to
   overwrite an existing store, and writes a random one-time code to the file.
   No password, token, or setup code is printed.
3. Set `TOPSIGNAL_LOCAL_AUTH_DB` to that absolute SQLite path in the worker's
   protected environment file, preserving `AUTH_REQUIRED=true`, database,
   credential encryption and broker settings. Build the frontend with
   `VITE_LOCAL_AUTH_ENABLED=true` to prefer local login without contacting Google
   on a fresh tab. Deploy/restart normally.
4. Through the authenticated SSH loopback tunnel, open the app, enter the setup
   code, and choose a unique password of at least 14 characters. The code expires
   after 24 hours and is consumed atomically on successful setup.

The login endpoints require a loopback connection and Host plus an allowed
browser Origin. Do not expose them with a public reverse proxy or HTTP listener.
Use the existing authenticated tunnel for remote access.

## Sessions and storage

Passwords use salted scrypt (N=2^17, r=8, p=1), following the
[OWASP password storage baseline](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#scrypt).
Hashing concurrency is bounded. A persisted limit permits ten attempts per five
minutes. The owner UUID and signing key are supplied only by the protected store.

JWTs use a separate key, issuer and audience from Google/Supabase. Each session
must also exist in the local session registry. Sessions last 12 hours and survive
page refreshes in the same browser tab. Logout revokes that session on the worker;
expiry or logout never silently switches back to a cached Google identity.
Closing the tab clears its browser session storage; server expiry still applies.
Signing out of the UI does not stop the independent worker.

The SQLite store contains the password hash, signing key, active sessions and
password-free authentication events. Back it up using SQLite's backup API to an
equally protected destination. It is separate from TopBot's trading audit log;
no cloud history, input candles or trading audit records are pruned. Only expired
authentication session rows are removed. Do not copy the store into Git or a
frontend build.

For forgotten passwords or expired initial setup, an administrator can provision
a **new** protected store for the same account and switch the configured path in
a controlled deployment. Preserve a verified backup of the old store and its
audit events. Switching stores invalidates all old local sessions; Google and
broker credentials remain unchanged. Never reset ownership from browser input.

## Verification

Install `backend/requirements-test.txt` in the development environment and run
`python -m pytest tests/test_local_auth.py tests/test_auth_middleware.py` from
`backend`. Frontend tests cover setup, login, refresh, API token routing, logout,
expiry and rejection handling. Do not place broker orders to test authentication.
