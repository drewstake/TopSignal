"""Isolated off-device PostgreSQL and local sign-in restore acceptance.

Run on the recovery computer, using a disposable local PostgreSQL administrator.
Preserves the restored audit database/auth file and never enables authority.
No tokens, passwords or restored environment values are printed.
"""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4

from cryptography.fernet import Fernet
from dotenv import dotenv_values

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.backup_execution_database import restore
from tools.storage_backup_crypto import load_key


def verify_auth(directory, key):
    auth_path = directory/('isolated-auth-'+uuid4().hex+'.sqlite3')
    with auth_path.open('xb') as stream:
        stream.write(Fernet(key).decrypt((directory/'local-auth.sqlite3.fernet').read_bytes()))
    for name in list(os.environ):
        if name.startswith('SUPABASE_'):
            os.environ.pop(name)
    os.environ.update(TOPSIGNAL_LOCAL_AUTH_DB=str(auth_path),TOPSIGNAL_LOCAL_AUTH_MODE='trusted_device',
                      AUTH_REQUIRED='true',PYTHON_DOTENV_DISABLED='1')
    from app import local_auth
    from app.auth import authenticate_request_token, AuthError
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(local_auth.make_router(lambda origin: origin=='http://127.0.0.1:4173'))
    headers = {'Origin':'http://127.0.0.1:4173','X-TopSignal-Local-Access':'device'}
    with patch('socket.create_connection',side_effect=AssertionError('Network access forbidden during restored authentication')):
        with TestClient(app,base_url='http://127.0.0.1:8000',client=('127.0.0.1',1234)) as client:
            response = client.post('/auth/local/device',json={},headers=headers)
            if response.status_code != 200:
                raise RuntimeError('Restored local sign-in issuance failed')
            token = response.json()['access_token']
            owner = authenticate_request_token(token).user_id
            importlib.reload(local_auth)
            if authenticate_request_token(token).user_id != owner:
                raise RuntimeError('Restored local session failed restart verification')
            response = client.post('/auth/local/logout',json={},headers=headers|{'Authorization':'Bearer '+token})
            if response.status_code != 200:
                raise RuntimeError('Restored local sign-out failed')
            try:
                authenticate_request_token(token)
            except AuthError:
                pass
            else:
                raise RuntimeError('Restored local session was not revoked')
    return dict(auth_issuance=True,auth_restart=True,auth_revocation=True,auth_network_calls=0,
                isolated_auth=str(auth_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('env-file','pg-bin','directory','key-file','receipt'):
        parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--database',required=True)
    args = parser.parse_args()
    try:
        if args.receipt.exists() or args.receipt.parent.resolve() != args.directory.resolve():
            raise ValueError('New receipt inside the off-device backup directory required')
        key = load_key(args.key_file)
        values = dotenv_values(args.env_file,interpolate=False)
        result = restore(url=values['DATABASE_URL'],pg_bin=args.pg_bin,directory=args.directory,
                         key=key,database=args.database)
        result.update(verify_auth(args.directory,key), encrypted_files={
            p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in args.directory.glob('*.fernet')})
        with args.receipt.open('x',encoding='utf-8') as stream:
            json.dump(result,stream,indent=2)
        print(json.dumps(result))
    except Exception as error:
        print(json.dumps({'verified':False,'error_type':type(error).__name__}),file=sys.stderr)
        raise SystemExit(1) from None


if __name__=='__main__':
    main()
