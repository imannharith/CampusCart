"""
Admin & Management Functions
-----------------------------
Python functions backing the admin dashboard, product management,
and order management pages: products, categories, stock, discount
tiers, reviews, and sales information.

Every function opens its own short-lived connection via
database.get_connection() and closes it before returning, which
keeps things simple and avoids leaving connections open between
requests.
"""

from database import get_connection

def get_all_products(category=None, status=None, search=None, seller=None):
    """Return products, optionally filtered by category, status, seller,
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

    if search:
        query += " AND LOWER(name) LIKE LOWER(?)"
        params.append(f"%{search}%")

    if seller:
        query += " AND seller = ?"
        params.append(seller)

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
                description=None, image_url=None):
    """Insert a new product and return its new id."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO products (name, seller, category, price, stock, status,
                              description, image_url, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (name, seller, category, price, stock, status, description, image_url))

    connection.commit()
    new_id = cursor.lastrowid

    connection.close()
    return new_id

def update_product(product_id, name, seller, category, price, stock):
    """Update a product's core details."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        UPDATE products
        SET name = ?, seller = ?, category = ?, price = ?, stock = ?
        WHERE id = ?
    """, (name, seller, category, price, stock, product_id))

    connection.commit()
    connection.close()

def delete_product(product_id):
    """Delete a product by id."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))

    connection.commit()
    connection.close()

def set_product_status(product_id, status):
    """Approve, reject, or flag a product as reported.
    Expected statuses: 'Approved', 'Pending', 'Rejected', 'Reported'."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "UPDATE products SET status = ? WHERE id = ?",
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

def get_dead_listings():
    """Approved products with nothing left in stock. They still show up
    in the shop, so a buyer can open one and find it unbuyable -- an
    admin should restock or unlist them."""

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
    """Return the line items for a single order, with product names."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT order_items.*, products.name AS product_name
        FROM order_items
        LEFT JOIN products ON products.id = order_items.product_id
        WHERE order_items.order_id = ?
    """, (order_id,))
    items = [dict(row) for row in cursor.fetchall()]

    connection.close()
    return items

# Which statuses an order is allowed to move to from where it is now.
# 'Delivered' is the end of the road: the goods are with the buyer, so
# taking them back is a returns process rather than an status flip.
ORDER_TRANSITIONS = {
    "pending": {"shipped", "cancelled"},
    "shipped": {"delivered", "cancelled"},
    "delivered": set(),
    "cancelled": {"pending"},
}

def update_order_status(order_id, status):
    """Move an order to a new status, returning True if it was applied
    and False if that move isn't allowed from where the order is now.

    Cancelling hands the items back to the seller's stock, and
    reinstating a cancelled order takes them out again. Stock only
    moves when the order crosses into or out of 'Cancelled', so a
    Pending order being marked Shipped leaves stock alone."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
    row = cursor.fetchone()

    if row is None:
        connection.close()
        return False

    if status.lower() not in ORDER_TRANSITIONS.get(row["status"].lower(), set()):
        connection.close()
        return False

    was_cancelled = row["status"].lower() == "cancelled"
    now_cancelled = status.lower() == "cancelled"

    if was_cancelled != now_cancelled:
        cursor.execute(
            "SELECT product_id, quantity FROM order_items WHERE order_id = ?",
            (order_id,)
        )
        items = [dict(item) for item in cursor.fetchall()]

        direction = 1 if now_cancelled else -1

        for item in items:
            cursor.execute(
                "UPDATE products SET stock = MAX(0, stock + ?) WHERE id = ?",
                (direction * item["quantity"], item["product_id"])
            )

    cursor.execute(
        "UPDATE orders SET status = ? WHERE id = ?",
        (status, order_id)
    )

    connection.commit()
    connection.close()
    return True

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
    """Return the best-selling products by total quantity sold."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            products.id,
            products.name,
            SUM(order_items.quantity) AS units_sold,
            SUM(order_items.quantity * order_items.price) AS revenue
        FROM order_items
        JOIN products ON products.id = order_items.product_id
        GROUP BY products.id
        ORDER BY units_sold DESC
        LIMIT ?
    """, (limit,))
    top_products = [dict(row) for row in cursor.fetchall()]

    connection.close()
    return top_products

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
