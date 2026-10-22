"""No app/database/broker imports: exercise actual file replacement and decoding."""
import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "ssh_log_reader", Path(__file__).resolve().parents[2] / "scripts" / "read-production-logs.py"
)
reader_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader_module)


def test_tail_append_and_rotation_to_larger_file(tmp_path):
    path = tmp_path / "topsignal.log"
    path.write_bytes(b"old\nrecent\n")
    reader = reader_module.LogReader(path, 1)
    assert reader.read()[0] == "recent\n"
    with path.open("ab") as stream:
        stream.write(b"appended\n")
    assert reader.read()[0] == "appended\n"
    # A larger replacement must not be mistaken for an append; no open handle
    # is left behind to prevent this rename on Windows.
    path.rename(tmp_path / "topsignal.log.1")
    path.write_text("replacement with more bytes than the previous file\n", encoding="utf-8")
    output, notice = reader.read()
    assert output.startswith("replacement")
    assert notice
    assert reader.read()[0] == ""


def test_truncation_and_split_utf8(tmp_path):
    path = tmp_path / "topsignal.log"
    path.write_bytes(b"old data\n")
    reader = reader_module.LogReader(path, 0)
    assert reader.read()[0] == ""
    path.write_bytes(b"new\n")
    assert reader.read()[0] == "new\n"
    encoded = "\u20ac".encode("utf-8")
    with path.open("ab") as stream:
        stream.write(encoded[:1])
    assert reader.read()[0] == ""
    with path.open("ab") as stream:
        stream.write(encoded[1:] + b"\n")
    assert reader.read()[0] == "\u20ac\n"


def test_reader_does_not_create_missing_log(tmp_path):
    import pytest
    path = tmp_path / "missing.log"
    reader = reader_module.LogReader(path, 100)
    with pytest.raises(FileNotFoundError):
        reader.read()
    assert not path.exists()
