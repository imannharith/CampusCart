"""
Database connection
-------------------
Connects CampusCart to its Turso database, and nothing else.

    get_connection()   a connection to run one query on
    atomic([...])      several statements that succeed or fail together
    check_reachable()  fail fast with a reason if Turso can't be reached

Creating the tables is in tables.py; everything the pages ask the
database for is in shop_functions.py and admin_functions.py.

How it talks to Turso: every query is one HTTPS request to Turso's HTTP
API (POST /v2/pipeline) with the SQL and its values, and the answer comes
back as JSON. Plain HTTPS with a time limit, so a slow or dropped
connection can never freeze a page -- it is retried once on a fresh
connection, and otherwise reported as an error.
"""
import base64
import http.client
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# How long one request to Turso may take before we give up on it.
TIMEOUT_SECONDS = 15

# Each web server thread keeps its own open connection to Turso and reuses
# it, which saves the time of a new secure connection on every query.
_local = threading.local()


def _address():
    url = urllib.parse.urlparse(os.environ["TURSO_DATABASE_URL"].replace("libsql://", "https://", 1))
    return url.hostname, url.port or 443


def _send(requests):
    """POST one pipeline of requests to Turso; returns their results.

    Turso closes connections that sit idle, so after a quiet spell the
    kept-open connection may be dead. Then open a fresh one and try once
    more -- the student never notices."""
    body = json.dumps({"requests": requests + [{"type": "close"}]})
    headers = {"Authorization": "Bearer " + os.environ["TURSO_AUTH_TOKEN"],
               "Content-Type": "application/json"}
    for attempt in (1, 2):
        connection = getattr(_local, "connection", None)
        if connection is None:
            connection = _local.connection = http.client.HTTPSConnection(*_address(), timeout=TIMEOUT_SECONDS)
        try:
            connection.request("POST", "/v2/pipeline", body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
        except (OSError, http.client.HTTPException):
            connection.close()
            _local.connection = None
            if attempt == 2:
                raise ConnectionError("Turso did not answer in time.")
            continue
        if response.status != 200:
            raise RuntimeError(f"Turso answered {response.status}: {data[:300]!r}")
        return json.loads(data)["results"][:-1]


# ---------- values going in and coming out ----------

def _to_turso(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    if isinstance(value, (bytes, bytearray)):
        return {"type": "blob", "base64": base64.b64encode(value).decode()}
    return {"type": "text", "value": str(value)}


def _from_turso(value):
    kind = value["type"]
    if kind == "null":
        return None
    if kind == "integer":
        return int(value["value"])
    if kind == "float":
        return float(value["value"])
    if kind == "blob":
        return base64.b64decode(value.get("base64", ""))
    return value["value"]


def _statement(sql, params=()):
    return {"sql": sql, "args": [_to_turso(p) for p in params]}


def _check(result):
    """Turn one pipeline result into its rows, or raise the SQL error."""
    if result["type"] == "error":
        raise RuntimeError(result["error"].get("message", "database error"))
    return result["response"]["result"]


class _Result:
    def __init__(self, result):
        self.columns = [column["name"] for column in result["cols"]]
        self.rows = [Row(self.columns, [_from_turso(v) for v in row]) for row in result["rows"]]
        self.rowcount = result.get("affected_row_count", 0)
        last = result.get("last_insert_rowid")
        self.lastrowid = int(last) if last is not None else None


class Row:
    """One row: row["name"] by column name, row[0] by position, and dict(row)."""

    __slots__ = ("_columns", "_values")

    def __init__(self, columns, values):
        self._columns = columns
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._columns.index(key)]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return repr(dict(zip(self._columns, self._values)))

    def keys(self):
        return list(self._columns)


# ---------- what the rest of the app uses (shaped like sqlite3) ----------

class _Cursor:
    def __init__(self):
        self._result = None

    def execute(self, sql, params=()):
        result = _send([{"type": "execute", "stmt": _statement(sql, params)}])[0]
        self._result = _Result(_check(result))
        return self

    def executemany(self, sql, seq_of_params):
        for params in seq_of_params:
            self.execute(sql, params)
        return self

    def fetchone(self):
        if self._result is None or not self._result.rows:
            return None
        return self._result.rows[0]

    def fetchall(self):
        return self._result.rows if self._result is not None else []

    @property
    def lastrowid(self):
        return self._result.lastrowid if self._result is not None else None

    @property
    def rowcount(self):
        return self._result.rowcount if self._result is not None else -1


class _Connection:
    """Looks like a sqlite3 connection to the rest of the code. Each query
    is sent to Turso straight away, so commit() and close() have nothing to do."""

    def cursor(self):
        return _Cursor()

    def commit(self):
        pass

    def close(self):
        pass


def get_connection():
    return _Connection()


def check_reachable(timeout=8):
    """Fail fast, with a reason, if Turso can't be reached at all.
    Any HTTP response means the host answered; only a connection
    failure or timeout counts as unreachable."""

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
    """Run several statements as one transaction: all of them happen, or none.
    statements is a list of (sql, params) pairs."""
    steps = [{"stmt": {"sql": "BEGIN"}}]
    for sql, params in statements:
        steps.append({"stmt": _statement(sql, params), "condition": {"type": "ok", "step": len(steps) - 1}})
    commit = len(steps)
    steps.append({"stmt": {"sql": "COMMIT"}, "condition": {"type": "ok", "step": commit - 1}})
    steps.append({"stmt": {"sql": "ROLLBACK"}, "condition": {"type": "not", "cond": {"type": "ok", "step": commit}}})

    outcome = _check(_send([{"type": "batch", "batch": {"steps": steps}}])[0])
    for error in outcome["step_errors"]:
        if error is not None:
            raise RuntimeError(error.get("message", "database error"))
    return [_Result(result) for result in outcome["step_results"][1:commit] if result is not None]
