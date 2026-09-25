import os
import threading
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

EXPECTED_TABLES = {
    "cart", "orders", "order_items",
    "discount_tiers", "products", "reviews", "users",
}

def ensure_columns():
    """Add columns introduced after a database was first created.

    CREATE TABLE IF NOT EXISTS silently does nothing to a table that
    already exists, so a new column never reaches an older database
    through init_db(). This checks for each one and adds what's
    missing, the column-level version of missing_tables()."""

    added = []

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("PRAGMA table_info(products)")
    columns = {row["name"] for row in cursor.fetchall()}

    if "reported_at" not in columns:
        cursor.execute("ALTER TABLE products ADD COLUMN reported_at TIMESTAMP")
        added.append("products.reported_at")

    cursor.execute("PRAGMA table_info(orders)")
    order_columns = {row["name"] for row in cursor.fetchall()}

    if "buyer_name" not in order_columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN buyer_name TEXT")
        added.append("orders.buyer_name")
        # No way to recover who placed an old order -- the name was
        # only ever shown on the confirmation page, never saved. Those
        # rows just show as unknown from here on.

    cursor.execute("PRAGMA table_info(order_items)")
    order_item_columns = {row["name"] for row in cursor.fetchall()}

    if "status" not in order_item_columns:
        cursor.execute("ALTER TABLE order_items ADD COLUMN status TEXT NOT NULL DEFAULT 'Pending'")
        added.append("order_items.status")

        # Every line item just inherited 'Pending' from the column
        # default, which would forget that some orders were already
        # further along. Copy each item's starting point from its
        # order, the last place that progress was recorded.
        cursor.execute("""
            UPDATE order_items
            SET status = (SELECT status FROM orders WHERE orders.id = order_items.order_id)
        """)

    if "cancelled_at" not in order_item_columns:
        cursor.execute("ALTER TABLE order_items ADD COLUMN cancelled_at TIMESTAMP")
        added.append("order_items.cancelled_at")

        # Anything that was already Cancelled before this column
        # existed had no moment recorded, so the 2-day countdown for
        # tidying it off the seller's page could never start. Give it
        # one now rather than hiding it forever or showing it forever.
        cursor.execute("""
            UPDATE order_items SET cancelled_at = datetime('now')
            WHERE LOWER(status) = 'cancelled' AND cancelled_at IS NULL
        """)

    if "product_name" not in order_item_columns:
        cursor.execute("ALTER TABLE order_items ADD COLUMN product_name TEXT")
        added.append("order_items.product_name")

        # A price was already captured at the moment of purchase, on
        # the reasoning that a later price change shouldn't rewrite a
        # past order. The name needs the same treatment for the same
        # reason -- otherwise a deleted product's past sales read as
        # 'Deleted product' instead of what was actually bought. Copy
        # it from products now, while the link between them still
        # exists, for every order placed before this column did.
        cursor.execute("""
            UPDATE order_items
            SET product_name = (
                SELECT name FROM products WHERE products.id = order_items.product_id
            )
            WHERE product_name IS NULL
        """)

    if "seller" not in order_item_columns:
        cursor.execute("ALTER TABLE order_items ADD COLUMN seller TEXT")
        added.append("order_items.seller")

        # A seller's own sales list finds their items by this column
        # rather than joining to products, so a deleted product can't
        # make a past sale disappear from the seller's own history.
        # Backfilled the same way as product_name, while the link to
        # the product still exists.
        cursor.execute("""
            UPDATE order_items
            SET seller = (
                SELECT seller FROM products WHERE products.id = order_items.product_id
            )
            WHERE seller IS NULL
        """)

    connection.commit()
    connection.close()
    return added

def missing_tables():
    """Which of the app's tables aren't in the database yet.

    Asks for the whole table list in one query rather than checking a
    single table and assuming the rest followed. A database that has
    some tables but not others would otherwise look finished, and the
    missing ones would never get created."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    present = {row["name"] for row in cursor.fetchall()}

    connection.close()
    return EXPECTED_TABLES - present

def ensure_ready(timeout=20):
    """One query on a database that's already set up.

    Runs on a daemon thread with a deadline: the libsql client blocks
    indefinitely if the server accepts the connection but never
    answers, and a web app that hangs on boot with no output is worse
    than one that refuses to start with a reason."""

    check_reachable()

    outcome = {}

    def work():
        try:
            if missing_tables():
                init_db()
                seed_sample_data()
            ensure_columns()
            outcome["ready"] = True
        except Exception as error:
            outcome["error"] = error

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout)

    if "error" in outcome:
        raise outcome["error"]

    if "ready" not in outcome:
        raise TimeoutError(f"the database did not respond within {timeout}s")

def init_db():

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cart (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 1
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        subtotal REAL NOT NULL,
        discount REAL NOT NULL DEFAULT 0,
        total REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'Pending',
        order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        buyer_name TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        price REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'Pending',
        cancelled_at TIMESTAMP,
        product_name TEXT,
        seller TEXT
    )
    """)

    # Spend-based discounts. min_subtotal is unique so two tiers can't
    # claim the same threshold and leave which one applies to chance.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS discount_tiers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        min_subtotal REAL UNIQUE NOT NULL,
        discount_percent REAL NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        seller TEXT NOT NULL,
        category TEXT NOT NULL,
        price REAL NOT NULL,
        stock INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'Pending',
        description TEXT,
        image_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        reported_at TIMESTAMP
    )
    """)

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

    # Starting tiers, so a new or repaired database has working spend
    # discounts rather than none at all. Only filled when the table is
    # empty -- an admin removing every tier is a choice, not a gap.
    cursor.execute("SELECT COUNT(*) AS total FROM discount_tiers")
    if cursor.fetchone()["total"] == 0:
        cursor.execute("""
            INSERT INTO discount_tiers (min_subtotal, discount_percent)
            VALUES (50, 5), (100, 10), (200, 15)
        """)

    connection.commit()
    connection.close()

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
