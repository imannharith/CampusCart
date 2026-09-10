import os

import libsql_client
from dotenv import load_dotenv

load_dotenv()


# -------------------------
# CONNECTION HELPER
# -------------------------
# Every admin function in admin_functions.py calls get_connection()
# and then uses it exactly like a sqlite3 connection (cursor(),
# execute(), fetchone(), fetchall(), commit(), close()). The actual
# database now lives on Turso (shared by everyone, instead of each
# laptop having its own local database.db), so this wraps a single
# shared libsql client in that same sqlite3-shaped interface -- the
# rest of the codebase doesn't need to know anything changed.
#
# The client itself is created once and reused (it opens a
# background thread), so close() here is a no-op rather than
# actually tearing down the connection.

_client = None


def _get_client():
    global _client
    if _client is None:
        url = os.environ["TURSO_DATABASE_URL"]
        # `turso db show --url` prints a libsql:// (websocket) URL, but
        # the websocket handshake fails on some networks -- HTTP works
        # the same either way, so always use it under the hood.
        url = url.replace("libsql://", "https://", 1)
        auth_token = os.environ["TURSO_AUTH_TOKEN"]
        _client = libsql_client.create_client_sync(url=url, auth_token=auth_token)
    return _client


class _CompatRow:
    # libsql's Row already supports row["col"] and row[0], but unlike
    # sqlite3.Row it has no keys() method -- which dict(row) relies on
    # (admin_functions.py does this a lot). Adding keys() is enough to
    # make dict(row) work again.
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
        # NOTE: check "is not None", not truthiness -- a ResultSet for
        # an INSERT/UPDATE/DELETE has zero *returned* rows, which makes
        # it falsy (via __len__) even though it's a perfectly valid
        # result with real last_insert_rowid/rows_affected data on it.
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


# -------------------------
# CREATE ALL TABLES
# -------------------------

def init_db():

    connection = get_connection()
    cursor = connection.cursor()

    # SHOPPING CART TABLE
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cart (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 1
    )
    """)

    # ORDERS TABLE
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        subtotal REAL NOT NULL,
        discount REAL NOT NULL DEFAULT 0,
        total REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'Pending',
        order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # PRODUCTS INSIDE AN ORDER
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        price REAL NOT NULL
    )
    """)

    # DISCOUNT CODES TABLE
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS discount_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        discount_percent REAL NOT NULL,
        active INTEGER NOT NULL DEFAULT 1
    )
    """)

    # PRODUCTS TABLE
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        seller TEXT NOT NULL,
        category TEXT NOT NULL,
        price REAL NOT NULL,
        stock INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'Pending'
    )
    """)

    # REVIEWS TABLE
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        reviewer TEXT NOT NULL,
        rating INTEGER NOT NULL,
        comment TEXT,
        review_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # USERS TABLE (student / seller accounts, not admins -- those are
    # a separate hard-coded allowlist in app.py)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fullname TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'student',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    connection.commit()
    connection.close()


# -------------------------
# USER ACCOUNTS
# -------------------------

def create_user(fullname, email, password_hash, role):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("""
        INSERT INTO users (fullname, email, password_hash, role)
        VALUES (?, ?, ?, ?)
    """, (fullname, email, password_hash, role))
    connection.commit()
    connection.close()


def get_user_by_email(email):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    connection.close()
    return user


def get_all_users(search=None):
    """Registered accounts, newest first, optionally filtered by a
    case-insensitive match on name or email."""

    connection = get_connection()
    cursor = connection.cursor()

    query = "SELECT id, fullname, email, role, created_at FROM users WHERE 1=1"
    params = []

    if search:
        query += " AND (LOWER(fullname) LIKE LOWER(?) OR LOWER(email) LIKE LOWER(?))"
        params.append(f"%{search}%")
        params.append(f"%{search}%")

    query += " ORDER BY id DESC"

    cursor.execute(query, params)
    users = [dict(row) for row in cursor.fetchall()]

    connection.close()
    return users


def get_user_stats():
    """Real counts for the admin user cards."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("SELECT COUNT(*) AS c FROM users")
    total = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM users WHERE LOWER(role) = 'student'")
    students = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM users WHERE LOWER(role) = 'seller'")
    sellers = cursor.fetchone()["c"]

    cursor.execute(
        "SELECT COUNT(*) AS c FROM users WHERE created_at >= datetime('now', '-7 days')"
    )
    this_week = cursor.fetchone()["c"]

    connection.close()

    return {
        "total": total,
        "students": students,
        "sellers": sellers,
        "this_week": this_week,
    }


# -------------------------
# OPTIONAL SAMPLE DATA
# -------------------------
# Only inserts sample rows if the products table is empty, so this
# is safe to call every time the app starts without duplicating data.

def seed_sample_data():

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("SELECT COUNT(*) AS total FROM products")
    if cursor.fetchone()["total"] > 0:
        connection.close()
        return

    sample_products = [
        ("Scientific Calculator FX-570EX", "Ahmad", "Electronics", 65.00, 5, "Approved"),
        ("Engineering Textbook", "Daniel", "Books", 40.00, 3, "Pending"),
        ("Desk Lamp", "Sarah", "Furniture", 20.00, 10, "Reported"),
        ("Gaming Mouse", "Amir", "Electronics", 60.00, 8, "Pending"),
        ("Campus Hoodie (Size L)", "Nadia", "Fashion", 45.00, 4, "Approved"),
    ]

    cursor.executemany("""
        INSERT INTO products (name, seller, category, price, stock, status)
        VALUES (?, ?, ?, ?, ?, ?)
    """, sample_products)

    cursor.execute("""
        INSERT INTO discount_codes (code, discount_percent, active)
        VALUES ('WELCOME10', 10, 1), ('STUDENT20', 20, 1)
    """)

    cursor.execute("""
        INSERT INTO reviews (product_id, reviewer, rating, comment)
        VALUES
            (1, 'Daniel', 5, 'Works perfectly, great condition.'),
            (1, 'Sarah', 4, 'Good price, minor scratches.'),
            (2, 'Amir', 3, 'Some pages are highlighted.')
    """)

    cursor.execute("""
        INSERT INTO orders (user_id, subtotal, discount, total, status)
        VALUES
            (1, 65.00, 0, 65.00, 'Delivered'),
            (2, 60.00, 6.00, 54.00, 'Shipped'),
            (3, 40.00, 0, 40.00, 'Pending')
    """)

    cursor.execute("""
        INSERT INTO order_items (order_id, product_id, quantity, price)
        VALUES
            (1, 1, 1, 65.00),
            (2, 4, 1, 60.00),
            (3, 2, 1, 40.00)
    """)

    connection.commit()
    connection.close()


if __name__ == "__main__":
    init_db()
    seed_sample_data()
    print("Database created successfully!")
