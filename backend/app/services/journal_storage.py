from __future__ import annotations

import os
from pathlib import Path
from urllib import error, parse, request

_BACKEND_LOCAL = "local"
_BACKEND_SUPABASE = "supabase"
_DEFAULT_JOURNAL_IMAGE_STORAGE_DIR = "storage/journal_images"


def journal_storage_backend() -> str:
    configured = os.getenv("JOURNAL_IMAGE_STORAGE_BACKEND", _BACKEND_LOCAL)
    normalized = configured.strip().lower()
    if normalized in {_BACKEND_LOCAL, _BACKEND_SUPABASE}:
        return normalized
    return _BACKEND_LOCAL


def save_journal_image(*, object_key: str, file_bytes: bytes, mime_type: str) -> None:
    if journal_storage_backend() == _BACKEND_SUPABASE:
        _save_journal_image_supabase(object_key=object_key, file_bytes=file_bytes, mime_type=mime_type)
        return

    path = local_journal_image_path(object_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(file_bytes)


def load_journal_image(*, object_key: str) -> bytes:
    if journal_storage_backend() == _BACKEND_SUPABASE:
        return _load_journal_image_supabase(object_key=object_key)

    path = local_journal_image_path(object_key)
    return path.read_bytes()


def delete_journal_image(*, object_key: str) -> None:
    if journal_storage_backend() == _BACKEND_SUPABASE:
        _delete_journal_image_supabase(object_key=object_key)
        return

    path = local_journal_image_path(object_key)
    path.unlink(missing_ok=True)
    _remove_empty_parent_dirs(path.parent, stop_at=_journal_image_storage_root())


def local_journal_image_path(object_key: str) -> Path:
    return _journal_image_storage_root() / object_key


def _journal_image_storage_root() -> Path:
    configured = os.getenv("JOURNAL_IMAGE_STORAGE_DIR")
    if configured:
        root = Path(configured).expanduser()
        return root.resolve()

    backend_root = Path(__file__).resolve().parents[2]
    return backend_root / _DEFAULT_JOURNAL_IMAGE_STORAGE_DIR


def _save_journal_image_supabase(*, object_key: str, file_bytes: bytes, mime_type: str) -> None:
    url = _supabase_object_url(object_key)
    headers = {
        "Authorization": f"Bearer {_supabase_service_key()}",
        "apikey": _supabase_service_key(),
        "Content-Type": mime_type,
        "x-upsert": "true",
    }
    req = request.Request(url=url, data=file_bytes, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=20):
            return
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"supabase_storage_upload_failed: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"supabase_storage_upload_network_error: {exc.reason}") from exc


def _load_journal_image_supabase(*, object_key: str) -> bytes:
    url = _supabase_object_url(object_key)
    headers = {
        "Authorization": f"Bearer {_supabase_service_key()}",
        "apikey": _supabase_service_key(),
    }
    req = request.Request(url=url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=20) as response:
            return response.read()
    except error.HTTPError as exc:
        if exc.code == 404:
            raise FileNotFoundError(object_key) from exc
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"supabase_storage_download_failed: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"supabase_storage_download_network_error: {exc.reason}") from exc


def _delete_journal_image_supabase(*, object_key: str) -> None:
    url = _supabase_object_url(object_key)
    headers = {
        "Authorization": f"Bearer {_supabase_service_key()}",
        "apikey": _supabase_service_key(),
    }
    req = request.Request(url=url, headers=headers, method="DELETE")
    try:
        with request.urlopen(req, timeout=20):
            return
    except error.HTTPError as exc:
        if exc.code == 404:
            return
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"supabase_storage_delete_failed: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"supabase_storage_delete_network_error: {exc.reason}") from exc


def _supabase_object_url(object_key: str) -> str:
    base_url = _supabase_url()
    bucket = _supabase_bucket()
    encoded_bucket = parse.quote(bucket, safe="")
    encoded_key = parse.quote(object_key, safe="/")
    return f"{base_url}/storage/v1/object/{encoded_bucket}/{encoded_key}"


def _supabase_url() -> str:
    value = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if not value:
        raise RuntimeError("SUPABASE_URL is required when JOURNAL_IMAGE_STORAGE_BACKEND=supabase")
    return value


def _supabase_bucket() -> str:
    value = os.getenv("SUPABASE_STORAGE_BUCKET", "").strip()
    if not value:
        raise RuntimeError("SUPABASE_STORAGE_BUCKET is required when JOURNAL_IMAGE_STORAGE_BACKEND=supabase")
    return value


def _supabase_service_key() -> str:
    value = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not value:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is required when JOURNAL_IMAGE_STORAGE_BACKEND=supabase")
    return value


def _remove_empty_parent_dirs(path: Path, *, stop_at: Path) -> None:
    current = path
    while True:
        if current == stop_at:
            return
        try:
            current.rmdir()
        except OSError:
            return
        parent = current.parent
        if parent == current:
            return
        current = parent
