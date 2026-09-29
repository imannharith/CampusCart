"""
Admin & Management Functions
-----------------------------
Every database function behind the admin panel (and the seller's
My Shop page), grouped in the same order as the admin menu:

    1. Listings     2. Users     3. Reports
    4. My Shop      5. Dashboard 6. Moderation

Every function opens its own short-lived connection via
get_connection() and closes it before returning, which keeps things
simple and avoids leaving connections open between requests.
"""

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
# Admin > Listings page: products, approval status, stock,
# categories, discount tiers and reviews.
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
        query += " AND NOT EXISTS (SELECT 1 FROM users WHERE users.id = products.seller_id AND account_status = 'Suspended')"

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
    """Delete a product and its reviews.

    Order history is left alone even for a deleted product -- a sale
    really happened, and a past order shouldn't quietly change. A
    review has no such reason to survive: once the product it's about
    is gone, it's just a 'Deleted product' row with nothing left to
    say, so it goes with the product rather than sitting there."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("DELETE FROM reviews WHERE product_id = ?", (product_id,))
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


def update_stock(product_id, new_stock):
    """Directly set a product's stock level."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "UPDATE products SET stock = ? WHERE id = ?",
        (new_stock, product_id)
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


def get_low_stock_products(threshold=3):
    """Return products at or below a stock threshold, for restock alerts."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT * FROM products WHERE stock <= ? ORDER BY stock ASC",
        (threshold,)
    )
    products = [dict(row) for row in cursor.fetchall()]

    connection.close()
    return products


def get_all_categories():
    """Return a sorted list of distinct category names in use."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("SELECT DISTINCT category FROM products ORDER BY category ASC")
    categories = [row["category"] for row in cursor.fetchall()]

    connection.close()
    return categories


def rename_category(old_name, new_name):
    """Rename a category across every product that uses it.
    Returns the number of products updated."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "UPDATE products SET category = ? WHERE LOWER(category) = LOWER(?)",
        (new_name, old_name)
    )

    connection.commit()
    updated = cursor.rowcount

    connection.close()
    return updated


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


def get_all_reviews():
    """Return every review, joined with the product name it belongs to."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT reviews.*, products.name AS product_name
        FROM reviews
        LEFT JOIN products ON products.id = reviews.product_id
        ORDER BY reviews.review_date DESC
    """)
    reviews = [dict(row) for row in cursor.fetchall()]

    connection.close()
    return reviews


def get_reviews_for_product(product_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT * FROM reviews WHERE product_id = ? ORDER BY review_date DESC",
        (product_id,)
    )
    reviews = [dict(row) for row in cursor.fetchall()]

    connection.close()
    return reviews


def add_review(product_id, reviewer, rating, comment=""):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO reviews (product_id, reviewer, rating, comment)
        VALUES (?, ?, ?, ?)
    """, (product_id, reviewer, rating, comment))

    connection.commit()
    new_id = cursor.lastrowid

    connection.close()
    return new_id


def delete_review(review_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("DELETE FROM reviews WHERE id = ?", (review_id,))

    connection.commit()
    connection.close()


def get_average_rating(product_id):
    """Return the average rating for a product, rounded to 1 decimal,
    or None if it has no reviews yet."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT AVG(rating) AS avg_rating FROM reviews WHERE product_id = ?",
        (product_id,)
    )
    row = cursor.fetchone()

    connection.close()

    if row is None or row["avg_rating"] is None:
        return None
    return round(row["avg_rating"], 1)


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


# ====================================================================
# 3. REPORTS
# Admin > Reports page: orders, sales numbers, platform revenue
# and what each seller is doing.
# ====================================================================


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


def get_order_items(order_id):
    """Return the line items for a single order.

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
            COALESCE(order_items.product_name, products.name) AS product_name
        FROM order_items
        LEFT JOIN products ON products.id = order_items.product_id
        WHERE order_items.order_id = ?
    """, (order_id,))
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

    for item in items:
        if item["status"].lower() in ("pending", "shipped"):
            cursor.execute(
                "UPDATE products SET stock = MAX(0, stock + ?) WHERE id = ?",
                (item["quantity"], item["product_id"])
            )
            cursor.execute(
                "UPDATE order_items SET status = 'Cancelled', cancelled_at = datetime('now') WHERE id = ?",
                (item["id"],)
            )
            changed = True

    if changed:
        cursor.execute("UPDATE orders SET status = 'Cancelled' WHERE id = ?", (order_id,))

    connection.commit()
    connection.close()
    return changed


def admin_reinstate_order(order_id):
    """Undo an admin cancellation -- puts every cancelled line in the
    order back to Pending and takes its stock back out. Returns True
    if anything changed."""

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

    for item in items:
        if item["status"].lower() == "cancelled":
            cursor.execute(
                "UPDATE products SET stock = MAX(0, stock + ?) WHERE id = ?",
                (-item["quantity"], item["product_id"])
            )
            cursor.execute(
                "UPDATE order_items SET status = 'Pending', cancelled_at = NULL WHERE id = ?",
                (item["id"],)
            )
            changed = True

    cursor.execute("UPDATE orders SET status = 'Pending' WHERE id = ?", (order_id,))

    connection.commit()
    connection.close()
    return changed


def get_sales_summary():
    """Return overall sales figures for the dashboard/reports page."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("SELECT COUNT(*) AS total_orders FROM orders")
    total_orders = cursor.fetchone()["total_orders"]

    cursor.execute(
        "SELECT COALESCE(SUM(total), 0) AS total_revenue FROM orders "
        "WHERE LOWER(status) != 'cancelled'"
    )
    total_revenue = cursor.fetchone()["total_revenue"]

    cursor.execute(
        "SELECT COALESCE(SUM(discount), 0) AS total_discount FROM orders"
    )
    total_discount = cursor.fetchone()["total_discount"]

    avg_order_value = (total_revenue / total_orders) if total_orders else 0

    connection.close()

    return {
        "total_orders": total_orders,
        "total_revenue": round(total_revenue, 2),
        "total_discount_given": round(total_discount, 2),
        "average_order_value": round(avg_order_value, 2),
    }


def get_top_selling_products(limit=5):
    """Return the best-selling products by total quantity sold.

    Grouped by order_items.product_id with the name falling back to
    the snapshot taken at purchase, so a deleted product's past sales
    still count here instead of dropping out of the ranking. Cancelled
    lines are excluded -- they were never actually sold."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            order_items.product_id AS id,
            COALESCE(order_items.product_name, products.name) AS name,
            SUM(order_items.quantity) AS units_sold,
            SUM(order_items.quantity * order_items.price) AS revenue
        FROM order_items
        LEFT JOIN products ON products.id = order_items.product_id
        WHERE LOWER(order_items.status) != 'cancelled'
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


def _actual_revenue(quantity, price, order_subtotal, order_discount):
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


def get_platform_revenue():
    """CampusCart's own commission earnings across the whole
    marketplace -- the platform's data, not any one seller's.

    Based on what buyers actually paid, with each order's discount
    shared out across its lines, not the sticker price -- otherwise
    the commission would be charged on money a discount meant nobody
    ever collected. Cancelled lines are excluded, since nothing was
    actually sold."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            order_items.quantity,
            order_items.price,
            orders.subtotal AS order_subtotal,
            orders.discount AS order_discount
        FROM order_items
        JOIN orders ON orders.id = order_items.order_id
        WHERE LOWER(order_items.status) != 'cancelled'
    """)
    rows = cursor.fetchall()

    connection.close()

    gross = sum(
        _actual_revenue(row["quantity"], row["price"], row["order_subtotal"], row["order_discount"])
        for row in rows
    )
    commission = round(gross * PLATFORM_COMMISSION_PERCENT / 100, 2)

    return {
        "gross_sales": round(gross, 2),
        "commission_percent": PLATFORM_COMMISSION_PERCENT,
        "commission_earned": commission,
    }


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


# ====================================================================
# 4. MY SHOP (SELLER SIDE)
# The seller's own shop page: their sales, their earnings, and
# moving their own order lines along (Pending > Shipped > Delivered).
# ====================================================================


# Which statuses an order is allowed to move to from where it is now.
# 'Delivered' is the end of the road: the goods are with the buyer, so
# taking them back is a returns process rather than an status flip.
# A sale is fulfilled by whichever seller listed the item, not by an
# admin acting as a courier -- so status lives on each order_items row
# rather than the order as a whole. One order can hold items from
# several sellers, and each seller only ever moves their own line.
ORDER_ITEM_TRANSITIONS = {
    "pending": {"shipped", "cancelled"},
    "shipped": {"delivered", "cancelled"},
    "delivered": set(),
    "cancelled": {"pending"},
}


def get_order_item(item_id):
    """Return an order line with its original seller account ID and snapshots."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute('SELECT * FROM order_items WHERE id = ?', (item_id,))
    row = cursor.fetchone()

    connection.close()
    return dict(row) if row else None


def update_order_item_status(item_id, status):
    """Move one order line to a new status. Returns True if applied,
    False if that move isn't legal from where the line is now.

    Cancelling this line returns its quantity to the product's stock;
    reinstating it takes that quantity back out. Only this line's
    product is touched -- a buyer cancelling one seller's item in a
    mixed order never affects another seller's stock."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT status, product_id, quantity FROM order_items WHERE id = ?",
        (item_id,)
    )
    row = cursor.fetchone()

    if row is None:
        connection.close()
        return False

    if status.lower() not in ORDER_ITEM_TRANSITIONS.get(row["status"].lower(), set()):
        connection.close()
        return False

    was_cancelled = row["status"].lower() == "cancelled"
    now_cancelled = status.lower() == "cancelled"

    if was_cancelled != now_cancelled:
        direction = 1 if now_cancelled else -1
        cursor.execute(
            "UPDATE products SET stock = MAX(0, stock + ?) WHERE id = ?",
            (direction * row["quantity"], row["product_id"])
        )

    # Stamped only while the line is actually cancelled, so a seller's
    # own dashboard can quietly stop showing it once it's old -- and
    # so reinstating it clears the clock rather than leaving a stale
    # date behind.
    if now_cancelled:
        cursor.execute(
            "UPDATE order_items SET status = ?, cancelled_at = datetime('now') WHERE id = ?",
            (status, item_id)
        )
    else:
        cursor.execute(
            "UPDATE order_items SET status = ?, cancelled_at = NULL WHERE id = ?",
            (status, item_id)
        )

    connection.commit()
    connection.close()
    return True


def get_seller_orders(seller_id, cancelled_max_age_days=2):
    """What this seller has sold, newest first.

    Returns order *lines*, not orders. One order can hold items from
    several sellers, so a seller is shown their own items and the
    state of each -- never anybody else's.

    Matched by order_items.seller_id, captured when the order was placed,
    rather than by joining to products -- so a sale a seller made
    doesn't vanish from their own history just because the product was
    deleted afterwards. product_name is the same kind of snapshot.

    A line cancelled more than cancelled_max_age_days ago is left out,
    so old dead sales don't pile up on a seller's own page. Nothing is
    deleted -- admin's own order view (get_order_items) still sees every
    line regardless of age, since that one needs the full history."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            order_items.id,
            order_items.order_id,
            order_items.quantity,
            order_items.price,
            order_items.status  AS status,
            COALESCE(order_items.product_name, products.name) AS product_name,
            products.image_url  AS image_url,
            orders.order_date   AS order_date,
            orders.buyer_name    AS buyer_name,
            orders.subtotal      AS order_subtotal,
            orders.discount      AS order_discount
        FROM order_items
        LEFT JOIN products ON products.id = order_items.product_id
        JOIN orders ON orders.id = order_items.order_id
        WHERE order_items.seller_id = ?
          AND NOT (
              LOWER(order_items.status) = 'cancelled'
              AND order_items.cancelled_at IS NOT NULL
              AND order_items.cancelled_at < datetime('now', ?)
          )
        ORDER BY orders.order_date DESC
    """, (seller_id, f"-{cancelled_max_age_days} days"))
    lines = [dict(row) for row in cursor.fetchall()]

    connection.close()

    for line in lines:
        line["actual_amount"] = round(_actual_revenue(
            line["quantity"], line["price"],
            line["order_subtotal"], line["order_discount"]
        ), 2)

    return lines


def get_seller_summary(seller_id):
    """Headline figures for a seller's own dashboard.

    'gross_sales' is what buyers actually paid for this seller's
    items -- their share of each order after any discount, not the
    sticker price -- and 'earned' is that amount minus CampusCart's
    commission. A seller isn't expecting money that was never coming
    to them, whether that's because of the platform's cut or a
    discount the buyer redeemed."""

    listings = get_all_products(seller_id=seller_id)
    lines = get_seller_orders(seller_id)

    live = [p for p in listings if p["status"] == "Approved"]
    sold = [line for line in lines if line["status"].lower() != "cancelled"]

    gross = sum(line["actual_amount"] for line in sold)
    commission = round(gross * PLATFORM_COMMISSION_PERCENT / 100, 2)

    return {
        "listings": len(listings),
        "live": len(live),
        "awaiting_review": len([p for p in listings if p["status"] == "Pending"]),
        "sold_out": len([p for p in live if p["stock"] <= 0]),
        "units_sold": sum(line["quantity"] for line in sold),
        "gross_sales": round(gross, 2),
        "commission_percent": PLATFORM_COMMISSION_PERCENT,
        "commission_paid": commission,
        "earned": round(gross - commission, 2),
    }


# ====================================================================
# 5. DASHBOARD
# Admin > Dashboard: the headline cards and the listings chart.
# ====================================================================


def get_dashboard_stats():
    """Return the summary counts shown as cards on the admin dashboard."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("SELECT COUNT(*) AS total FROM products")
    total_products = cursor.fetchone()["total"]

    cursor.execute(
        "SELECT COUNT(*) AS total FROM products WHERE LOWER(status) = 'pending'"
    )
    pending_products = cursor.fetchone()["total"]

    cursor.execute(
        "SELECT COUNT(*) AS total FROM products WHERE LOWER(status) = 'reported'"
    )
    reported_products = cursor.fetchone()["total"]

    cursor.execute(
        "SELECT COUNT(*) AS total FROM products WHERE LOWER(status) = 'approved'"
    )
    approved_products = cursor.fetchone()["total"]

    connection.close()

    return {
        "total_products": total_products,
        "pending_products": pending_products,
        "reported_products": reported_products,
        "approved_products": approved_products,
    }


def get_listings_per_day(days=7):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
       SELECT DATE(created_at) AS day, COUNT(*) AS listings
       FROM products
       WHERE created_at >= datetime('now', ?)
       GROUP BY DATE(created_at)
    """, (f"-{days} days",))
    rows = [dict(row) for row in cursor.fetchall()]

    connection.close()
    return rows


# ====================================================================
# 6. MODERATION
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
    return not rows("SELECT id FROM users WHERE id = ? AND account_status = 'Suspended'", (product.get('seller_id'),))
