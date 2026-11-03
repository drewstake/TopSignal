"""Windows DPAPI custody for a separately stored backup encryption key.

User-bound by default. A noninteractive host may explicitly use machine binding
with filesystem access restricted to administrators and its backup service;
off-device custody stays user-bound.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path

from cryptography.fernet import Fernet


def dpapi(data: bytes, *, decrypt=False, machine=False) -> bytes:
    if os.name != 'nt':
        raise RuntimeError('Windows user-bound key custody required')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]
    buffer = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    outgoing = Blob()
    crypto = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypto.CryptUnprotectData if decrypt else crypto.CryptProtectData
    function.restype = wintypes.BOOL
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    flags = 1 | (4 if machine and not decrypt else 0)
    if not function(ctypes.byref(incoming), None, None, None, None, flags, ctypes.byref(outgoing)):
        raise RuntimeError('Windows backup key protection failed')
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(outgoing.data)


def save_key(path: Path, key: bytes, *, machine=False):
    Fernet(key)  # Validate before persisting.
    protected = dpapi(key, machine=machine)
    with path.open('xb') as stream:
        stream.write(protected)
        stream.flush()
        os.fsync(stream.fileno())


def load_key(path: Path):
    key = dpapi(path.read_bytes(), decrypt=True)
    Fernet(key)
    return key
