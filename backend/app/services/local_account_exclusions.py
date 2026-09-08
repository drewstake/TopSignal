"""Keep explicitly deleted local accounts out of subsequent broker imports."""

import json
from pathlib import Path

from sqlalchemy.orm import Session


def local_account_exclusions_path(db: Session) -> Path | None:
    bind = db.get_bind()
    if bind.dialect.name != "sqlite":
        return None
    database = bind.engine.url.database
    if not database or database == ":memory:":
        return None
    return Path(database).resolve().with_suffix(".deleted-projectx-accounts.json")


def excluded_local_projectx_accounts(db: Session, *, user_id: str) -> set[str]:
    path = local_account_exclusions_path(db)
    if path is None or not path.exists():
        return set()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("version") != 1 or not isinstance(document.get("users"), dict):
            raise ValueError
        excluded = document["users"].get(user_id, [])
        if not isinstance(excluded, list) or any(
            not isinstance(value, str) or not value.isascii() or not value.isdigit() or int(value) <= 0
            for value in excluded
        ):
            raise ValueError
        return {str(int(value)) for value in excluded}
    except (ValueError, AttributeError, TypeError) as exc:
        # Do not silently recreate deleted accounts if their exclusion file is
        # damaged. The file belongs in the backup with the local SQLite database.
        raise ValueError("Invalid local ProjectX account exclusions file") from exc
