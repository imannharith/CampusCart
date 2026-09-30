"""
Tables
------
Creates CampusCart's tables the first time, and adds any column or table
introduced since, every time the app starts. Safe to run again and again:
everything is "IF NOT EXISTS" or checked first.

Run it on its own with  python tables.py  to set up a new database.
"""
import threading

import database
import admin_functions as admin


EXPECTED_TABLES = {
    "cart", "orders", "order_items",
    "discount_tiers", "products", "reviews", "users",
}


def missing_tables():
    """Which of the app's tables aren't in the database yet.

    Asks for the whole table list in one query rather than checking a
    single table and assuming the rest followed. A database that has
    some tables but not others would otherwise look finished, and the
    missing ones would never get created."""

    connection = database.get_connection()
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

    database.check_reachable()

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

    connection = database.get_connection()
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


def ensure_columns():
    """Add columns introduced after a database was first created.

    CREATE TABLE IF NOT EXISTS silently does nothing to a table that
    already exists, so a new column never reaches an older database
    through init_db(). This checks for each one and adds what's
    missing, the column-level version of missing_tables()."""

    added = []

    connection = database.get_connection()
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
    ensure_settings_schema()
    admin.sync_order_status()
    return added


def ensure_moderation_schema():
    connection = database.get_connection()
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
    connection = database.get_connection()
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
    database.atomic([
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
    connection = database.get_connection()
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
    connection = database.get_connection()
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
    connection = database.get_connection()
    cursor = connection.cursor()
    cursor.execute('PRAGMA table_info(reviews)')
    have = {row['name'] for row in cursor.fetchall()}
    for column, definition in [('reviewer_id', 'INTEGER'), ('order_item_id', 'INTEGER'), ('product_name', 'TEXT')]:
        if column not in have:
            cursor.execute(f'ALTER TABLE reviews ADD COLUMN {column} {definition}')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS one_review_per_purchase ON reviews(order_item_id)')
    connection.commit()
    connection.close()


def ensure_settings_schema():
    """Settings: each student's light/dark choice, and Contact Us -- a chat
    between one student and the admins. from_admin says which side wrote each
    message; admin_email says which admin replied."""
    connection = database.get_connection()
    cursor = connection.cursor()
    cursor.execute('PRAGMA table_info(users)')
    if 'theme' not in {row['name'] for row in cursor.fetchall()}:
        cursor.execute("ALTER TABLE users ADD COLUMN theme TEXT NOT NULL DEFAULT 'light'")
    cursor.execute("""CREATE TABLE IF NOT EXISTS support_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        from_admin INTEGER NOT NULL DEFAULT 0,
        admin_email TEXT,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        read_at TEXT)""")
    cursor.execute('CREATE INDEX IF NOT EXISTS support_for_user ON support_messages(user_id, id)')
    connection.commit()
    connection.close()


def seed_sample_data():

    connection = database.get_connection()
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
    ensure_columns()
    seed_sample_data()
    print("Database created successfully!")
