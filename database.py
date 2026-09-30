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

    for column in ('removal_reason', 'removed_by', 'removed_at'):
        if column not in columns:
            cursor.execute(f"ALTER TABLE products ADD COLUMN {column} TEXT")
            added.append(f"products.{column}")

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
    ensure_moderation_schema()
    ensure_marketplace_schema()
    ensure_messaging_schema()
    ensure_notifications_schema()
    ensure_reviews_schema()
    sync_order_status()
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
    """, (fullname, email.strip().lower(), password_hash, role))
    connection.commit()
    connection.close()

def get_user_by_email(email):
    # Emails are compared ignoring capitals: Farah@... and farah@... are one person.
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM users WHERE LOWER(email) = LOWER(?)", ((email or "").strip(),))
    user = cursor.fetchone()
    connection.close()
    return user

def get_all_users(search=None):
    """Registered accounts, newest first, optionally filtered by a
    case-insensitive match on name or email."""

    connection = get_connection()
    cursor = connection.cursor()

    query = "SELECT id, fullname, email, role, created_at, account_status FROM users WHERE 1=1"
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

    # Every student can buy and sell, so "selling" means has listed something.
    cursor.execute("SELECT COUNT(DISTINCT seller_id) AS c FROM products WHERE seller_id IS NOT NULL")
    selling = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM users WHERE account_status = 'Suspended'")
    suspended = cursor.fetchone()["c"]

    cursor.execute(
        "SELECT COUNT(*) AS c FROM users WHERE created_at >= datetime('now', '-7 days')"
    )
    this_week = cursor.fetchone()["c"]

    connection.close()

    return {
        "total": total,
        "selling": selling,
        "suspended": suspended,
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



def atomic(statements):
    """libSQL batch executes these statements in one transaction."""
    return _get_client().batch(statements)


def ensure_moderation_schema():
    connection = get_connection()
    cursor = connection.cursor()
    for table, column, definition in [
        ("users", "account_status", "TEXT NOT NULL DEFAULT 'Active'"),
        ("products", "seller_id", "INTEGER"),
        ("orders", "account_linked", "INTEGER NOT NULL DEFAULT 0"),
    ]:
        cursor.execute(f"PRAGMA table_info({table})")
        if column not in {row["name"] for row in cursor.fetchall()}:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    cursor.execute("""CREATE TABLE IF NOT EXISTS account_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        action TEXT NOT NULL, reason TEXT NOT NULL, admin_email TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS listing_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
        product_name TEXT NOT NULL, seller_id INTEGER, reporter_id INTEGER,
        reporter_name TEXT NOT NULL, reason TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '',
        previous_status TEXT NOT NULL DEFAULT 'Approved',
        status TEXT NOT NULL DEFAULT 'Open', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        decision_reason TEXT, decided_by TEXT, resolved_at TEXT)""")
    cursor.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_open_report_per_user
        ON listing_reports(product_id, reporter_id) WHERE status = 'Open'""")
    cursor.execute("""INSERT INTO listing_reports
        (product_id, product_name, seller_id, reporter_name, reason, previous_status, created_at)
        SELECT id, name, seller_id, 'Unknown (legacy report)',
        'This listing was flagged before report details were recorded.', 'Pending',
        COALESCE(reported_at, CURRENT_TIMESTAMP) FROM products
        WHERE status = 'Reported' AND NOT EXISTS (
            SELECT 1 FROM listing_reports WHERE product_id = products.id)""")
    connection.commit()
    connection.close()


def ensure_marketplace_schema():
    connection = get_connection()
    cursor = connection.cursor()
    for table, column, definition in [
        ('order_items', 'seller_id', 'INTEGER'),
        ('orders', 'checkout_token', 'TEXT'),
        # Where the seller sends the item, typed in at checkout.
        ('orders', 'phone', 'TEXT'),
        ('orders', 'address', 'TEXT'),
    ]:
        cursor.execute(f'PRAGMA table_info({table})')
        if column not in {row['name'] for row in cursor.fetchall()}:
            cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS order_checkout_token ON orders(checkout_token)')
    cursor.execute('CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)')
    connection.commit()
    connection.close()
    # Backfill only once: a future signup must never claim an old listing by name.
    atomic([
        ("""UPDATE products SET seller_id = (SELECT id FROM users WHERE fullname = products.seller)
            WHERE seller_id IS NULL AND (SELECT COUNT(*) FROM users WHERE fullname = products.seller) = 1
            AND NOT EXISTS (SELECT 1 FROM schema_migrations WHERE name = 'seller_accounts_v1')""", []),
        ("""UPDATE order_items SET seller_id = (SELECT id FROM users WHERE fullname = order_items.seller)
            WHERE seller_id IS NULL AND (SELECT COUNT(*) FROM users WHERE fullname = order_items.seller) = 1
            AND NOT EXISTS (SELECT 1 FROM schema_migrations WHERE name = 'seller_accounts_v1')""", []),
        ("""UPDATE listing_reports SET seller_id = (SELECT seller_id FROM products WHERE id = listing_reports.product_id)
            WHERE seller_id IS NULL AND NOT EXISTS (SELECT 1 FROM schema_migrations WHERE name = 'seller_accounts_v1')""", []),
        ("INSERT OR IGNORE INTO schema_migrations(name) VALUES ('seller_accounts_v1')", []),
        # Consolidate any legacy duplicate cart entries before adding uniqueness.
        ("""UPDATE cart SET quantity = (SELECT SUM(MAX(other.quantity, 1)) FROM cart other
            WHERE other.user_id = cart.user_id AND other.product_id = cart.product_id)
            WHERE id IN (SELECT MIN(id) FROM cart GROUP BY user_id, product_id)
            AND NOT EXISTS (SELECT 1 FROM schema_migrations WHERE name = 'account_carts_v1')""", []),
        ("DELETE FROM cart WHERE id NOT IN (SELECT MIN(id) FROM cart GROUP BY user_id, product_id)", []),
        ('CREATE UNIQUE INDEX IF NOT EXISTS cart_account_product ON cart(user_id, product_id)', []),
        ("INSERT OR IGNORE INTO schema_migrations(name) VALUES ('account_carts_v1')", []),
    ])


def ensure_messaging_schema():
    """Buyer-seller chat. One conversation per buyer, seller and item, so a
    student asking about two listings from the same seller gets two threads.
    product_name is kept on the conversation so the thread still says what
    it was about after the listing is deleted."""
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        buyer_id INTEGER NOT NULL,
        seller_id INTEGER NOT NULL,
        product_id INTEGER,
        product_name TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_message_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    cursor.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_conversation_per_item
        ON conversations(buyer_id, seller_id, IFNULL(product_id, 0))""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        read_at TEXT)""")
    cursor.execute('CREATE INDEX IF NOT EXISTS messages_in_conversation ON messages(conversation_id, id)')
    connection.commit()
    connection.close()


def ensure_notifications_schema():
    """The bell in the top bar. link is the page the notification opens;
    read_at stays empty until the student has seen it."""
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        link TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        read_at TEXT)""")
    cursor.execute('CREATE INDEX IF NOT EXISTS notifications_for_user ON notifications(user_id, id)')
    connection.commit()
    connection.close()


def ensure_reviews_schema():
    """Reviews are tied to one purchase (order_item_id), so only a buyer can
    leave one and only once. product_name is kept so a review still reads
    correctly after the listing is deleted."""
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute('PRAGMA table_info(reviews)')
    have = {row['name'] for row in cursor.fetchall()}
    for column, definition in [('reviewer_id', 'INTEGER'), ('order_item_id', 'INTEGER'), ('product_name', 'TEXT')]:
        if column not in have:
            cursor.execute(f'ALTER TABLE reviews ADD COLUMN {column} {definition}')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS one_review_per_purchase ON reviews(order_item_id)')
    connection.commit()
    connection.close()


# Each seller moves their own items along, so an order's overall status is
# worked out from its items rather than set by hand:
#   every item cancelled                    -> Cancelled
#   every item left has been delivered      -> Delivered
#   at least one item shipped or delivered  -> Shipped
#   otherwise                               -> Pending
ORDER_STATUS_FROM_ITEMS = """CASE
    WHEN NOT EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = orders.id AND i.status != 'Cancelled') THEN 'Cancelled'
    WHEN NOT EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = orders.id AND i.status NOT IN ('Delivered', 'Cancelled')) THEN 'Delivered'
    WHEN EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = orders.id AND i.status IN ('Shipped', 'Delivered')) THEN 'Shipped'
    ELSE 'Pending' END"""


def sync_order_status(order_id=None):
    """Bring one order's status (or every order's, with no id) in line with its items."""
    atomic([(f"""UPDATE orders SET status = {ORDER_STATUS_FROM_ITEMS}
        WHERE (? IS NULL OR id = ?) AND EXISTS (SELECT 1 FROM order_items WHERE order_id = orders.id)""",
             [order_id, order_id])])


def get_cart(user_id):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("""SELECT cart.product_id, cart.quantity,
        COALESCE(products.name, 'Deleted listing') AS name,
        COALESCE(products.price, 0) AS price, products.seller, products.seller_id,
        products.stock, products.status FROM cart LEFT JOIN products ON products.id = cart.product_id
        WHERE cart.user_id = ? ORDER BY cart.id""", (user_id,))
    cart = {row['product_id']: dict(row) for row in cursor.fetchall()}
    connection.close()
    return cart


def remove_owned_cart_items(user_id):
    """Remove legacy cart rows for listings owned by the current account."""
    atomic([("""DELETE FROM cart WHERE user_id = ? AND EXISTS
        (SELECT 1 FROM products WHERE products.id = cart.product_id AND products.seller_id = ?)""",
        [user_id, user_id])])


def add_cart_item(user_id, product_id, existing_only=False):
    # A single upsert prevents two requests from losing an increment or exceeding stock.
    atomic([("""INSERT INTO cart(user_id, product_id, quantity)
        SELECT ?, id, 1 FROM products WHERE id = ? AND status = 'Approved' AND stock > 0
        AND seller_id IS NOT ?
        AND NOT EXISTS (SELECT 1 FROM users WHERE id = products.seller_id AND account_status = 'Suspended')
        AND (? = 0 OR EXISTS (SELECT 1 FROM cart WHERE user_id = ? AND product_id = products.id))
        ON CONFLICT(user_id, product_id) DO UPDATE SET quantity = cart.quantity + 1
        WHERE cart.quantity < (SELECT stock FROM products WHERE id = excluded.product_id)""",
        [user_id, product_id, user_id, int(existing_only), user_id])])


def decrease_cart_item(user_id, product_id):
    atomic([
        ('UPDATE cart SET quantity = quantity - 1 WHERE user_id = ? AND product_id = ?', [user_id, product_id]),
        ('DELETE FROM cart WHERE user_id = ? AND product_id = ? AND quantity <= 0', [user_id, product_id]),
    ])


def remove_cart_item(user_id, product_id):
    atomic([('DELETE FROM cart WHERE user_id = ? AND product_id = ?', [user_id, product_id])])


def checkout_cart(user_id, cart, pricing, buyer_name, phone, address):
    """Commit the checked cart, order, stock and cart removal as one transaction.

    The first statement only creates an order if the entire snapshot still matches.
    Every later statement depends on that order, so stale/duplicate requests do nothing.
    """
    from uuid import uuid4
    token = uuid4().hex
    guards = ['(SELECT COUNT(*) FROM cart WHERE user_id = ?) = ?',
              "EXISTS (SELECT 1 FROM users WHERE id = ? AND account_status = 'Active')"]
    args = [user_id, pricing['subtotal'], pricing['discount'], pricing['total'], buyer_name, phone, address, token,
            user_id, len(cart), user_id]
    for product_id, item in cart.items():
        guards.append("""EXISTS (SELECT 1 FROM cart c JOIN products p ON p.id = c.product_id
            WHERE c.user_id = ? AND c.product_id = ? AND c.quantity = ? AND p.price = ?
            AND p.seller_id IS ? AND p.name = ? AND p.seller = ?
            AND p.status = 'Approved' AND p.stock >= c.quantity AND c.quantity > 0
            AND p.seller_id IS NOT ?
            AND NOT EXISTS (SELECT 1 FROM users WHERE id = p.seller_id AND account_status = 'Suspended'))""")
        args.extend([user_id, product_id, item['quantity'], item['price'], item['seller_id'], item['name'], item['seller'], user_id])
    if not cart:
        return None
    statements = [("""INSERT INTO orders (user_id, subtotal, discount, total, status, buyer_name, phone, address, account_linked, checkout_token)
        SELECT ?, ?, ?, ?, 'Pending', ?, ?, ?, 1, ? WHERE """ + ' AND '.join(guards), args)]
    for product_id, item in cart.items():
        statements.extend([
            ("""INSERT INTO order_items (order_id, product_id, quantity, price, product_name, seller, seller_id)
                SELECT id, ?, ?, ?, ?, ?, ? FROM orders WHERE checkout_token = ?""",
             [product_id, item['quantity'], item['price'], item['name'], item['seller'], item['seller_id'], token]),
            ("""UPDATE products SET stock = stock - ? WHERE id = ?
                AND EXISTS (SELECT 1 FROM orders WHERE checkout_token = ?)""", [item['quantity'], product_id, token]),
        ])
    statements.append(('DELETE FROM cart WHERE user_id = ? AND EXISTS (SELECT 1 FROM orders WHERE checkout_token = ?)', [user_id, token]))
    atomic(statements)
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute('SELECT id FROM orders WHERE checkout_token = ?', (token,))
    order = cursor.fetchone()
    connection.close()
    return order['id'] if order else None


if __name__ == "__main__":
    init_db()
    ensure_columns()
    seed_sample_data()
    print("Database created successfully!")
