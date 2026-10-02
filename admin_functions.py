"""
Admin & Management Functions
-----------------------------
Every database function behind the admin panel, grouped by the admin
page it belongs to:

    1. Listings     2. Users     3. Reports     4. Dashboard
    5. Moderation     6. Support     7. Settings

The student side of the site (cart, orders, messages, My Shop,
notifications) is in shop_functions.py.

Every function opens its own short-lived connection via
get_connection() and closes it before returning, which keeps things
simple and avoids leaving connections open between requests.
"""

from datetime import datetime, timedelta, timezone

import database
from database import get_connection


def rows(sql, params=()):
    """Run a SELECT and hand back plain dicts. Shared by every section."""
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(sql, params)
    result = [dict(row) for row in cursor.fetchall()]
    connection.close()
    return result


# ====================================================================
# 1. LISTINGS
# Admin > Listings page: products, approving them, stock and removal.
# ====================================================================


def get_all_products(category=None, status=None, search=None, seller_id=None, available_only=False):
    """Return products, optionally filtered by category, status, seller account ID,
    and/or a case-insensitive search on the product name."""

    connection = get_connection()
    cursor = connection.cursor()

    query = "SELECT * FROM products WHERE 1=1"
    params = []

    if category and category.lower() != "all":
        query += " AND LOWER(category) = LOWER(?)"
        params.append(category)

    if status and status.lower() != "all":
        query += " AND LOWER(status) = LOWER(?)"
        params.append(status)

    if available_only:
        query += " AND NOT EXISTS (SELECT 1 FROM users WHERE users.id = products.seller_id AND account_status != 'Active')"

    if search:
        query += " AND LOWER(name) LIKE LOWER(?)"
        params.append(f"%{search}%")

    if seller_id is not None:
        query += " AND seller_id = ?"
        params.append(seller_id)

    query += " ORDER BY id DESC"

    cursor.execute(query, params)
    products = [dict(row) for row in cursor.fetchall()]

    connection.close()
    return products


def get_product(product_id):
    """Return a single product as a dict, or None if not found."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
    row = cursor.fetchone()

    connection.close()
    return dict(row) if row else None


def add_product(name, seller, category, price, stock, status="Pending",
                description=None, image_url=None, seller_id=None):
    """Insert a new product and return its new id."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO products (name, seller, category, price, stock, status,
                              description, image_url, seller_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (name, seller, category, price, stock, status, description, image_url, seller_id))

    connection.commit()
    new_id = cursor.lastrowid

    connection.close()
    return new_id


def update_product(product_id, name, seller_id, category, price, stock):
    """Update a product's core details."""

    connection = get_connection()
    cursor = connection.cursor()

    if seller_id is not None:
        cursor.execute('SELECT id FROM users WHERE id = ?', (seller_id,))
        if cursor.fetchone() is None:
            connection.close()
            raise ValueError('Seller account does not exist.')
    cursor.execute("""
        UPDATE products SET seller_id = ?,
            seller = COALESCE((SELECT fullname FROM users WHERE id = ?), seller),
            name = ?, category = ?, price = ?, stock = ? WHERE id = ?
    """, (seller_id, seller_id, name, category, price, stock, product_id))

    connection.commit()
    connection.close()


def delete_product(product_id):
    """Delete a product, and its photo if it was uploaded to CampusCart.

    Order history is left alone even for a deleted product -- a sale
    really happened, and a past order shouldn't quietly change."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("DELETE FROM photos WHERE '/photos/' || id = (SELECT image_url FROM products WHERE id = ?)", (product_id,))
    cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))

    connection.commit()
    connection.close()


def set_product_status(product_id, status):
    """Approve, reject, or flag a product as reported.
    Expected statuses: 'Approved', 'Pending', 'Rejected', 'Reported'."""

    connection = get_connection()
    cursor = connection.cursor()

    # Stamp the moment it was reported, so the admin can see whether a
    # flag is fresh or has been sitting unattended. Cleared when the
    # product moves to any other status.
    if status.lower() == "reported":
        cursor.execute(
            "UPDATE products SET status = ?, reported_at = datetime('now') WHERE id = ?",
            (status, product_id)
        )
    else:
        cursor.execute(
            "UPDATE products SET status = ?, reported_at = NULL WHERE id = ?",
            (status, product_id)
        )

    connection.commit()
    connection.close()


def adjust_stock(product_id, amount):
    """Increase (positive amount) or decrease (negative amount) stock,
    never letting it go below zero. Returns the new stock level."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("SELECT stock FROM products WHERE id = ?", (product_id,))
    row = cursor.fetchone()

    if row is None:
        connection.close()
        return None

    new_stock = max(0, row["stock"] + amount)

    cursor.execute(
        "UPDATE products SET stock = ? WHERE id = ?",
        (new_stock, product_id)
    )

    connection.commit()
    connection.close()
    return new_stock


def get_all_categories():
    """Return a sorted list of distinct category names in use."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("SELECT DISTINCT category FROM products ORDER BY category ASC")
    categories = [row["category"] for row in cursor.fetchall()]

    connection.close()
    return categories


def get_listing_stats():
    """The four cards at the top of the Listings page (also used by the Dashboard)."""
    counts = {row["status"]: row["n"] for row in rows("SELECT status, COUNT(*) AS n FROM products GROUP BY status")}
    return {
        "total": sum(counts.values()),
        "pending": counts.get("Pending", 0),
        "approved": counts.get("Approved", 0),
        "reported": counts.get("Reported", 0),
    }


def remove_listing(product_id, reason, actor):
    """Take a listing off sale for a reason. The row is kept, with who removed
    it, why and when, so its history is never lost."""
    database.atomic([("""UPDATE products SET status = 'Removed', removal_reason = ?, removed_by = ?,
            removed_at = CURRENT_TIMESTAMP WHERE id = ? AND status != 'Removed'""",
                      [reason, actor, product_id])])


def next_listing_to_review():
    """The oldest listing still waiting for an admin, or None when there are none.
    After approving or rejecting one, the admin is taken straight to this one."""
    found = rows("SELECT id FROM products WHERE status = 'Pending' ORDER BY id LIMIT 1")
    return found[0]["id"] if found else None


# ====================================================================
# 2. USERS
# Admin > Users page: one account's full record, and suspending
# or reactivating it (every change is logged with a reason).
# ====================================================================


def user_details(user_id):
    users = rows('SELECT id, fullname, email, role, created_at, account_status FROM users WHERE id = ?', (user_id,))
    if not users:
        return None
    return dict(user=users[0],
                listings=rows('SELECT * FROM products WHERE seller_id = ? ORDER BY id DESC', (user_id,)),
                orders=rows('SELECT * FROM orders WHERE user_id = ? AND account_linked = 1 ORDER BY id DESC', (user_id,)),
                reports=rows('SELECT * FROM listing_reports WHERE seller_id = ? OR reporter_id = ? ORDER BY id DESC', (user_id, user_id)),
                history=rows('SELECT * FROM account_actions WHERE user_id = ? ORDER BY id DESC', (user_id,)))


def change_account(user_id, action, reason, actor):
    state = {'Suspend': 'Suspended', 'Reactivate': 'Active'}[action]
    database.atomic([
        ('''INSERT INTO account_actions (user_id, action, reason, admin_email)
            SELECT id, ?, ?, ? FROM users WHERE id = ? AND account_status != ?''',
         [action, reason, actor, user_id, state]),
        ('UPDATE users SET account_status = ? WHERE id = ?', [state, user_id]),
    ])


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
    """The four cards at the top of the Users page, in one trip to the database.
    Every student can buy and sell, so "selling" means has listed something."""
    return rows("""SELECT
        (SELECT COUNT(*) FROM users) AS total,
        (SELECT COUNT(DISTINCT seller_id) FROM products WHERE seller_id IS NOT NULL) AS selling,
        (SELECT COUNT(*) FROM users WHERE account_status = 'Suspended') AS suspended,
        (SELECT COUNT(*) FROM users WHERE created_at >= datetime('now', '-7 days')) AS this_week""")[0]


def user_exists(user_id):
    return bool(rows('SELECT id FROM users WHERE id = ?', (user_id,)))


# ====================================================================
# 3. REPORTS
# Admin > Reports page: orders, sales numbers, platform revenue
# and what each seller is doing.
# ====================================================================


# Each seller moves their own items along, so an order's overall status is
# worked out from its items rather than set by hand:
#   still waiting for the buyer to pay      -> Awaiting payment
#   every item cancelled                    -> Cancelled
#   every item left has been delivered      -> Delivered
#   at least one item shipped or delivered  -> Shipped
#   otherwise                               -> Pending
ORDER_STATUS_FROM_ITEMS = """CASE
    WHEN EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = orders.id AND i.status = 'Awaiting payment') THEN 'Awaiting payment'
    WHEN NOT EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = orders.id AND i.status != 'Cancelled') THEN 'Cancelled'
    WHEN NOT EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = orders.id AND i.status NOT IN ('Delivered', 'Cancelled')) THEN 'Delivered'
    WHEN EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = orders.id AND i.status IN ('Shipped', 'Delivered')) THEN 'Shipped'
    ELSE 'Pending' END"""


# A line counts as sold unless it was cancelled or is still waiting for
# payment -- used by every revenue figure, so unpaid orders never count.
SOLD_LINE = "LOWER(order_items.status) NOT IN ('cancelled', 'awaiting payment')"
# An order that really happened: paid online, or placed before online payment.
REAL_ORDER = "(orders.payment_status IS NULL OR orders.payment_status = 'Paid')"


def order_is_paid(order_id):
    """True if the buyer paid this order online (so cancelling it means a refund)."""
    found = rows("SELECT payment_status FROM orders WHERE id = ?", (order_id,))
    return bool(found) and found[0]["payment_status"] == "Paid"


def can_reinstate(order_id):
    """A cancelled line can only come back if the order was really paid (or
    is from before online payment) -- never an order nobody paid for."""
    return bool(rows(f"SELECT 1 FROM orders WHERE id = ? AND {REAL_ORDER}", (order_id,)))


def sync_order_status(order_id=None):
    """Bring one order's status (or every order's, with no id) in line with its items."""
    database.atomic([(f"""UPDATE orders SET status = {ORDER_STATUS_FROM_ITEMS}
        WHERE (? IS NULL OR id = ?) AND EXISTS (SELECT 1 FROM order_items WHERE order_id = orders.id)""",
             [order_id, order_id])])


def get_all_orders(status=None):
    """Return orders, optionally filtered by status, newest first."""

    connection = get_connection()
    cursor = connection.cursor()

    if status and status.lower() != "all":
        cursor.execute(
            "SELECT * FROM orders WHERE LOWER(status) = LOWER(?) ORDER BY order_date DESC",
            (status,)
        )
    else:
        cursor.execute("SELECT * FROM orders ORDER BY order_date DESC")

    orders = [dict(row) for row in cursor.fetchall()]

    connection.close()
    return orders


def get_order_items(order_id=None):
    """Return the line items for a single order (or, with no id, for every
    order at once -- the Reports page fetches them all in one trip).

    product_name is the name captured when the order was placed, the
    same way price already is -- so a line for a since-deleted product
    still reads correctly here instead of as 'Deleted product'. Only
    an order placed before that column existed falls back to today's
    product name (or the placeholder, if the product is gone too)."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            order_items.id,
            order_items.order_id,
            order_items.product_id,
            order_items.quantity,
            order_items.price,
            order_items.status,
            order_items.cancelled_at,
            order_items.refund_status,
            order_items.seller,
            order_items.seller_id,
            products.status AS product_status,
            COALESCE(order_items.product_name, products.name) AS product_name
        FROM order_items
        LEFT JOIN products ON products.id = order_items.product_id
        WHERE ? IS NULL OR order_items.order_id = ?
        ORDER BY order_items.id
    """, (order_id, order_id))
    items = [dict(row) for row in cursor.fetchall()]

    connection.close()
    return items


def admin_cancel_order(order_id):
    """Cancel every still-outstanding line in an order -- an admin's
    response to a problem like a report, not routine fulfilment.

    Only Pending or Shipped lines are touched. A Delivered item is
    already in the buyer's hands, so cancelling the order must not
    hand its stock back as if it were still on the shelf; reversing a
    completed handover is a returns process, not this. Returns True if
    anything was cancelled."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT id, status, product_id, quantity FROM order_items WHERE order_id = ?",
        (order_id,)
    )
    items = [dict(row) for row in cursor.fetchall()]

    if not items:
        connection.close()
        return False

    changed = False

    paid = order_is_paid(order_id)

    for item in items:
        if item["status"].lower() in ("pending", "shipped"):
            cursor.execute(
                "UPDATE products SET stock = MAX(0, stock + ?) WHERE id = ?",
                (item["quantity"], item["product_id"])
            )
            # The buyer paid for this item online, so they're owed their money back.
            cursor.execute(
                "UPDATE order_items SET status = 'Cancelled', cancelled_at = datetime('now'), "
                "refund_status = CASE WHEN ? THEN 'Owed' ELSE refund_status END WHERE id = ?",
                (1 if paid else 0, item["id"])
            )
            changed = True

    connection.commit()
    connection.close()
    sync_order_status(order_id)
    return changed


def admin_reinstate_order(order_id):
    """Undo an admin cancellation -- puts every cancelled line in the
    order back to Pending and takes its stock back out. Returns True
    if anything changed."""

    # Never bring back an order nobody paid for.
    if not can_reinstate(order_id):
        return False

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT id, status, product_id, quantity, refund_status FROM order_items WHERE order_id = ?",
        (order_id,)
    )
    items = [dict(row) for row in cursor.fetchall()]

    if not items:
        connection.close()
        return False

    changed = False

    for item in items:
        # A line whose money has already gone back to the buyer stays cancelled.
        if item["status"].lower() == "cancelled" and item["refund_status"] != "Refunded":
            cursor.execute(
                "UPDATE products SET stock = MAX(0, stock + ?) WHERE id = ?",
                (-item["quantity"], item["product_id"])
            )
            cursor.execute(
                "UPDATE order_items SET status = 'Pending', cancelled_at = NULL, refund_status = NULL WHERE id = ?",
                (item["id"],)
            )
            changed = True

    connection.commit()
    connection.close()
    sync_order_status(order_id)
    return changed


def get_refunds_owed():
    """Paid items that were cancelled: the buyer is owed that money back.
    An admin sends the money back and then marks it done here, which tells
    the buyer."""
    lines = rows(f"""SELECT order_items.*, orders.buyer_name, orders.payment_ref, orders.payment_channel,
            orders.subtotal AS order_subtotal, orders.discount AS order_discount
        FROM order_items JOIN orders ON orders.id = order_items.order_id
        WHERE order_items.refund_status = 'Owed' ORDER BY order_items.cancelled_at""")
    for line in lines:
        line["refund_amount"] = round(actual_revenue(line["quantity"], line["price"],
                                                     line["order_subtotal"], line["order_discount"]), 2)
    return lines


def mark_refunded(item_id):
    """Record that the money for this cancelled item went back to the buyer.
    Returns who to tell (user_id, product_name), or None if nothing was owed."""
    owed = rows("""SELECT order_items.product_name, orders.user_id FROM order_items
        JOIN orders ON orders.id = order_items.order_id
        WHERE order_items.id = ? AND order_items.refund_status = 'Owed'""", (item_id,))
    if not owed:
        return None
    database.atomic([("""UPDATE order_items SET refund_status = 'Refunded', refunded_at = CURRENT_TIMESTAMP
        WHERE id = ? AND refund_status = 'Owed'""", [item_id])])
    return owed[0]


def get_top_selling_products(limit=5):
    """Return the best-selling products by total quantity sold.

    Grouped by order_items.product_id with the name falling back to
    the snapshot taken at purchase, so a deleted product's past sales
    still count here instead of dropping out of the ranking. Cancelled
    lines are excluded -- they were never actually sold."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(f"""
        SELECT
            order_items.product_id AS id,
            COALESCE(order_items.product_name, products.name) AS name,
            SUM(order_items.quantity) AS units_sold,
            SUM(order_items.quantity * order_items.price) AS revenue
        FROM order_items
        LEFT JOIN products ON products.id = order_items.product_id
        WHERE {SOLD_LINE}
        GROUP BY order_items.product_id
        ORDER BY units_sold DESC
        LIMIT ?
    """, (limit,))
    top_products = [dict(row) for row in cursor.fetchall()]

    connection.close()
    return top_products


def get_dead_listings():
    """Approved products with nothing left in stock. They still show up
    in the shop, so a buyer can open one and find it unbuyable."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT * FROM products
        WHERE stock <= 0 AND LOWER(status) = 'approved'
        ORDER BY name ASC
    """)
    products = [dict(row) for row in cursor.fetchall()]

    connection.close()
    return products


# CampusCart's cut of every sale, whatever the seller listed it for.
# One number, changed in one place if the rate ever changes.
PLATFORM_COMMISSION_PERCENT = 5


def actual_revenue(quantity, price, order_subtotal, order_discount):
    """What one line actually contributed to what the buyer paid, once
    the order's discount is shared out across every line by its slice
    of the subtotal -- a discount code or spend tier applies to the
    whole cart, and an order can hold items from several sellers, so
    the saving has to be split rather than landing on whichever item
    happens to be first."""

    line_total = quantity * price

    if not order_subtotal:
        return line_total

    share_of_cart = line_total / order_subtotal
    return line_total - (share_of_cart * order_discount)


def get_seller_activity():
    """Account-scoped activity; unlinked legacy names are explicitly separate."""
    connection = get_connection()
    cursor = connection.cursor()
    sellers = {}
    for table, fields in [
        ('products', """COUNT(*) AS listings,
            SUM(status = 'Pending') AS pending, SUM(status = 'Approved') AS approved,
            SUM(status = 'Reported') AS reported, MAX(reported_at) AS last_reported"""),
        ('order_items', """SUM(status = 'Pending') AS awaiting,
            SUM(status = 'Shipped') AS shipped, SUM(status = 'Delivered') AS delivered"""),
    ]:
        cursor.execute(f"""SELECT seller_id, COALESCE(
            (SELECT fullname FROM users WHERE id = seller_id), MAX(seller), 'Unknown seller') AS seller,
            {fields} FROM {table} GROUP BY seller_id, CASE WHEN seller_id IS NULL THEN seller END""")
        for row in cursor.fetchall():
            row = dict(row)
            key = ('account', row['seller_id']) if row['seller_id'] is not None else ('legacy', row['seller'])
            entry = sellers.setdefault(key, dict(listings=0, pending=0, approved=0, reported=0,
                                                 awaiting=0, shipped=0, delivered=0, last_reported=None))
            entry.update(row)
    connection.close()
    return sorted(sellers.values(), key=lambda seller: (-seller['reported'], seller['seller'].lower()))


def get_sales_summary(days=None):
    """The money cards on Reports (and "this week" on the Dashboard).

    Worked out item by item, from what buyers actually paid for things that
    were really sold: a cancelled item or an unpaid checkout never counts,
    even when the rest of its order does, and each order's discount is shared
    out across its items. CampusCart's 5% is taken from the same total, so
    the cards always agree. days=7 limits it to the last week."""
    since = "AND orders.order_date >= datetime('now', ?)" if days else ""
    args = (f"-{days} days",) if days else ()

    lines = rows(f"""SELECT order_items.order_id, order_items.quantity, order_items.price,
            orders.subtotal AS order_subtotal, orders.discount AS order_discount
        FROM order_items JOIN orders ON orders.id = order_items.order_id
        WHERE {SOLD_LINE} AND {REAL_ORDER} {since}""", args)

    revenue = sum(actual_revenue(l["quantity"], l["price"], l["order_subtotal"], l["order_discount"]) for l in lines)
    before_discount = sum(l["quantity"] * l["price"] for l in lines)
    orders_with_sales = len({l["order_id"] for l in lines})
    total_orders = rows(f"SELECT COUNT(*) AS n FROM orders WHERE {REAL_ORDER} {since}", args)[0]["n"]

    return {
        "total_orders": total_orders,
        "total_revenue": round(revenue, 2),
        "total_discount_given": round(before_discount - revenue, 2),
        "average_order_value": round(revenue / orders_with_sales, 2) if orders_with_sales else 0,
    }


def get_platform_revenue(gross_sales):
    """CampusCart's own earnings on the Reports page: its 5% of all sales
    (gross_sales is the Sales card's total, from get_sales_summary)."""
    return {
        "gross_sales": gross_sales,
        "commission_percent": PLATFORM_COMMISSION_PERCENT,
        "commission_earned": round(gross_sales * PLATFORM_COMMISSION_PERCENT / 100, 2),
    }


# ====================================================================
# 4. DASHBOARD
# Admin > Dashboard: the headline cards and the listings chart.
# ====================================================================


def get_listings_per_day(days=7):

    connection = get_connection()
    cursor = connection.cursor()

    # Grouped by the Malaysian day (UTC + 8 hours), the same days the chart labels.
    cursor.execute("""
       SELECT DATE(created_at, '+8 hours') AS day, COUNT(*) AS listings
       FROM products
       WHERE created_at >= datetime('now', ?)
       GROUP BY DATE(created_at, '+8 hours')
    """, (f"-{days} days",))
    rows = [dict(row) for row in cursor.fetchall()]

    connection.close()
    return rows


def build_chart_days(rows, days=7, baseline=5):
    """Turn the daily counts from the database into one slot per bar.

    The query only returns days that actually had a listing, so days
    with none are missing entirely and have to be filled in with zero.

    Heights are a percentage of the scale, not of the busiest day. If
    the busiest day set the scale, the tallest bar would always be
    100% and a week with four listings would look identical to a week
    with four hundred. The baseline keeps a quiet week looking quiet,
    and the scale is reported so the template can label the axis."""

    counts = {row["day"]: row["listings"] for row in rows}

    today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()  # today in Malaysia (UTC+8)
    slots = []

    for days_ago in range(days - 1, -1, -1):
        day = today - timedelta(days=days_ago)
        slots.append({
            "label": day.strftime("%a"),
            "name": day.strftime("%A"),
            "count": counts.get(day.isoformat(), 0),
        })

    peak = max(slot["count"] for slot in slots)
    scale = max(peak, baseline)

    # Name the busiest day for the subtitle (the first one if there is a tie).
    busiest = next(slot for slot in slots if slot["count"] == peak)
    peak_day = busiest["name"] if peak > 0 else None

    for slot in slots:
        slot["height"] = round(slot["count"] / scale * 100)

    return {
        "days": slots,
        "peak": peak,
        "peak_day": peak_day,
        "scale": scale,
        "total": sum(slot["count"] for slot in slots),
    }


# ====================================================================
# 5. MODERATION
# Admin > Moderation page: students report a listing, an admin
# removes it or dismisses the report. Also decides whether a listing
# may be shown at all (approved, and its seller is not suspended).
# ====================================================================


def submit_report(product_id, reporter_id, reporter_name, reason, evidence):
    # The first report remembers the publication state; later reports inherit it.
    database.atomic([
        ('''INSERT INTO listing_reports
            (product_id, product_name, seller_id, reporter_id, reporter_name, reason, evidence, previous_status)
            SELECT id, name, seller_id, ?, ?, ?, ?, COALESCE(
                (SELECT previous_status FROM listing_reports WHERE product_id = products.id AND status = 'Open' ORDER BY id LIMIT 1), status)
            FROM products WHERE id = ? AND status IN ('Approved', 'Pending', 'Reported')
            AND NOT EXISTS (SELECT 1 FROM listing_reports WHERE product_id = products.id AND reporter_id IS ? AND status = 'Open')''',
         [reporter_id, reporter_name, reason, evidence, product_id, reporter_id]),
        ('''UPDATE products SET status = 'Reported', reported_at = COALESCE(reported_at, CURRENT_TIMESTAMP)
            WHERE id = ? AND EXISTS (SELECT 1 FROM listing_reports WHERE product_id = products.id AND status = 'Open')''', [product_id]),
    ])
    saved = rows("SELECT id FROM listing_reports WHERE product_id = ? AND reporter_id IS ? AND status = 'Open' ORDER BY id DESC LIMIT 1",
                 (product_id, reporter_id))
    return saved[0]['id'] if saved else None


def resolve_reports(product_id, decision, reason, actor):
    # Resolve the whole listing once, including all students' open reports.
    database.atomic([
        ('''UPDATE products SET status = CASE WHEN ? = 'Removed' THEN 'Removed' ELSE
                COALESCE((SELECT previous_status FROM listing_reports WHERE product_id = products.id AND status = 'Open' ORDER BY id LIMIT 1), 'Pending') END,
            reported_at = NULL WHERE id = ? AND EXISTS
                (SELECT 1 FROM listing_reports WHERE product_id = products.id AND status = 'Open')''', [decision, product_id]),
        ('''UPDATE listing_reports SET status = ?, decision_reason = ?, decided_by = ?, resolved_at = CURRENT_TIMESTAMP
            WHERE product_id = ? AND status = 'Open' ''', [decision, reason, actor, product_id]),
    ])


def available(product):
    if not product or product['status'] != 'Approved':
        return False
    return not rows("SELECT id FROM users WHERE id = ? AND account_status != 'Active'", (product.get('seller_id'),))


def has_open_reports(product_id):
    """True while a listing has reports waiting on an admin decision."""
    return bool(rows("SELECT id FROM listing_reports WHERE product_id = ? AND status = 'Open'", (product_id,)))


def get_listing_reports(status="All", product_id=None):
    """Reports for the Moderation page, newest first, optionally only one
    status (Open / Dismissed / Removed) or one listing."""
    query = """SELECT listing_reports.*, EXISTS (SELECT 1 FROM products WHERE products.id = listing_reports.product_id)
        AS listing_exists FROM listing_reports WHERE 1=1"""
    args = []
    if status != "All":
        query += " AND status = ?"
        args.append(status)
    if product_id:
        query += " AND product_id = ?"
        args.append(product_id)
    return rows(query + " ORDER BY id DESC", args)


def report_counts():
    """{"Open": 2, "Dismissed": 5, "Removed": 1, "All": 8} for the Moderation tabs."""
    counts = {"Open": 0, "Dismissed": 0, "Removed": 0}
    for row in rows("SELECT status, COUNT(*) AS n FROM listing_reports GROUP BY status"):
        counts[row["status"]] = row["n"]
    counts["All"] = sum(counts.values())
    return counts


# ====================================================================
# 6. SUPPORT
# Admin > Support: messages students send from Settings > Contact Us, and
# the admins' replies. Any admin can answer any student.
# ====================================================================


def support_inbox():
    """One row per student who has written in, newest conversation first,
    with how many of their messages no admin has read yet."""
    return rows("""SELECT u.id AS user_id, u.fullname, u.email,
            m.body AS last_body, m.from_admin AS last_from_admin, m.created_at AS last_at,
            (SELECT COUNT(*) FROM support_messages x
                WHERE x.user_id = u.id AND x.from_admin = 0 AND x.read_at IS NULL) AS unread
        FROM support_messages m JOIN users u ON u.id = m.user_id
        WHERE m.id = (SELECT MAX(id) FROM support_messages WHERE user_id = m.user_id)
        ORDER BY m.id DESC""")


def support_unread_count():
    """The red number on Support in the admin menu."""
    return rows("SELECT COUNT(*) AS n FROM support_messages WHERE from_admin = 0 AND read_at IS NULL")[0]["n"]


def mark_support_read_by_admin(user_id):
    database.atomic([("""UPDATE support_messages SET read_at = CURRENT_TIMESTAMP
        WHERE user_id = ? AND from_admin = 0 AND read_at IS NULL""", [user_id])])


def reply_to_student(user_id, admin_email, body):
    database.atomic([("""INSERT INTO support_messages (user_id, from_admin, admin_email, body)
        SELECT id, 1, ?, ? FROM users WHERE id = ?""", [admin_email, body, user_id])])


def support_waiting_count():
    """Conversations whose last message is from the student (for the Dashboard)."""
    return len([thread for thread in support_inbox() if not thread["last_from_admin"]])


# ====================================================================
# 7. SETTINGS
# Admin > Settings: how many listings each category has, and the
# spend-more-save-more discount tiers.
# ====================================================================


def get_all_discount_tiers():
    """Spend thresholds and what each one earns, biggest threshold
    first so the first tier a subtotal clears is the one that applies."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("SELECT * FROM discount_tiers ORDER BY min_subtotal DESC")
    tiers = [dict(row) for row in cursor.fetchall()]

    connection.close()
    return tiers


def add_discount_tier(min_subtotal, discount_percent):
    """Create a spend tier. Returns its new id, or None if a tier
    already exists at that threshold."""

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("""
            INSERT INTO discount_tiers (min_subtotal, discount_percent)
            VALUES (?, ?)
        """, (min_subtotal, discount_percent))

        connection.commit()
        new_id = cursor.lastrowid

    except Exception:
        new_id = None

    connection.close()
    return new_id


def delete_discount_tier(tier_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("DELETE FROM discount_tiers WHERE id = ?", (tier_id,))

    connection.commit()
    connection.close()


def read_discount_tier(form):
    """The minimum spend and percentage from the Add tier form.
    Raises ValueError with a message to show if either is wrong."""
    try:
        min_subtotal = float(form.get("min_subtotal", ""))
        percent = float(form.get("discount_percent", ""))
    except ValueError:
        min_subtotal = percent = -1
    if not (0 <= min_subtotal <= 100000 and 1 <= percent <= 100):
        raise ValueError("A discount needs a minimum spend of RM 0 or more and a percentage from 1 to 100.")
    return min_subtotal, percent


def get_category_counts():
    """{"books": {"listings": 10, "live": 9}, ...}: every category in use, with
    how many listings it has and how many of those are on sale."""
    return {row["category"]: row for row in rows("""SELECT category, COUNT(*) AS listings,
            SUM(CASE WHEN status = 'Approved' THEN 1 ELSE 0 END) AS live
        FROM products GROUP BY category""")}
