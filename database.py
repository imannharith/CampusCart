"""
Database connection
-------------------
Connects CampusCart to its Turso database, and nothing else.

    get_connection()   a connection to run one query on
    atomic([...])      several statements that succeed or fail together
    check_reachable()  fail fast with a reason if Turso can't be reached

Creating the tables is in tables.py; everything the pages ask the
database for is in shop_functions.py and admin_functions.py.
"""
import os
import urllib.error
import urllib.request

import libsql_client
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

_client = None

def _get_client():
    global _client
    if _client is None:
        url = os.environ["TURSO_DATABASE_URL"]

        url = url.replace("libsql://", "https://", 1)
        auth_token = os.environ["TURSO_AUTH_TOKEN"]
        _client = libsql_client.create_client_sync(url=url, auth_token=auth_token)
    return _client

class _CompatRow:

    __slots__ = ("_row",)

    def __init__(self, row):
        self._row = row

    def __getitem__(self, key):
        return self._row[key]

    def __iter__(self):
        return iter(self._row.astuple())

    def __len__(self):
        return len(self._row)

    def __repr__(self):
        return repr(self._row)

    def keys(self):
        return self._row.asdict().keys()

class _CompatCursor:
    def __init__(self, client):
        self._client = client
        self._result = None

    def execute(self, sql, params=()):
        self._result = self._client.execute(sql, list(params))
        return self

    def executemany(self, sql, seq_of_params):
        for params in seq_of_params:
            self._client.execute(sql, list(params))
        return self

    def fetchone(self):
        if self._result is None or len(self._result.rows) == 0:
            return None
        return _CompatRow(self._result.rows[0])

    def fetchall(self):
        if self._result is None:
            return []
        return [_CompatRow(row) for row in self._result.rows]

    @property
    def lastrowid(self):

        return self._result.last_insert_rowid if self._result is not None else None

    @property
    def rowcount(self):
        return self._result.rows_affected if self._result is not None else -1

class _CompatConnection:
    def __init__(self, client):
        self._client = client

    def cursor(self):
        return _CompatCursor(self._client)

    def commit(self):
        pass

    def close(self):
        pass

def get_connection():
    return _CompatConnection(_get_client())

def check_reachable(timeout=8):
    """The libsql client has no timeout of its own and blocks forever
    if the host can't be reached, so probe it first with something
    that does. Any HTTP response means the host answered; only a
    connection failure or timeout counts as unreachable."""

    url = os.environ["TURSO_DATABASE_URL"].replace("libsql://", "https://", 1)

    try:
        urllib.request.urlopen(url, timeout=timeout)
    except urllib.error.HTTPError:
        pass
    except Exception as error:
        raise ConnectionError(
            f"could not reach {url} within {timeout}s ({error})"
        ) from error


def atomic(statements):
    """libSQL batch executes these statements in one transaction."""
    return _get_client().batch(statements)
