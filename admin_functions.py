"""
Admin & Management Functions
-----------------------------
Python functions backing the admin dashboard, product management,
and order management pages: products, categories, stock, discount
codes, reviews, and sales information.

Every function opens its own short-lived connection via
database.get_connection() and closes it before returning, which
keeps things simple and avoids leaving connections open between
requests.
"""

from database import get_connection


# =========================================================
# PRODUCTS
# =========================================================

def get_all_products(category=None, status=None, search=None):
    """Return products, optionally filtered by category, status,
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


def add_product(name, seller, category, price, stock, status="Pending"):
    """Insert a new product and return its new id."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO products (name, seller, category, price, stock, status)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (name, seller, category, price, stock, status))

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


# =========================================================
# STOCK
# =========================================================

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


# =========================================================
# CATEGORIES
# =========================================================
# Categories aren't a separate table -- they live on each product --
# so "managing" categories means reading the distinct list in use
# and being able to bulk re-assign every product from an old
# category name to a new one (e.g. renaming "Furniture" to "Home").

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


# =========================================================
# DISCOUNT CODES
# =========================================================

def get_all_discount_codes():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("SELECT * FROM discount_codes ORDER BY id DESC")
    codes = [dict(row) for row in cursor.fetchall()]

    connection.close()
    return codes


def add_discount_code(code, discount_percent, active=True):
    """Create a new discount code. Returns its new id, or None if the
    code already exists (codes must be unique)."""

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("""
            INSERT INTO discount_codes (code, discount_percent, active)
            VALUES (?, ?, ?)
        """, (code.upper().strip(), discount_percent, int(active)))

        connection.commit()
        new_id = cursor.lastrowid

    except Exception:
        new_id = None

    connection.close()
    return new_id


def toggle_discount_code(discount_id):
    """Flip a discount code between active and inactive."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "UPDATE discount_codes SET active = 1 - active WHERE id = ?",
        (discount_id,)
    )

    connection.commit()
    connection.close()


def delete_discount_code(discount_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("DELETE FROM discount_codes WHERE id = ?", (discount_id,))

    connection.commit()
    connection.close()


def validate_discount_code(code):
    """Return the discount percent for an active code, or None if the
    code doesn't exist or isn't active. Used when applying a code at
    checkout."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT discount_percent FROM discount_codes WHERE code = ? AND active = 1",
        (code.upper().strip(),)
    )
    row = cursor.fetchone()

    connection.close()
    return row["discount_percent"] if row else None


# =========================================================
# REVIEWS
# =========================================================

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


# =========================================================
# ORDERS
# =========================================================

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


def update_order_status(order_id, status):
    """Update an order's status (e.g. Pending, Shipped, Delivered, Cancelled)."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "UPDATE orders SET status = ? WHERE id = ?",
        (status, order_id)
    )

    connection.commit()
    connection.close()


# =========================================================
# SALES INFORMATION
# =========================================================

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


# =========================================================
# DASHBOARD STATS
# =========================================================

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

    connection.close()

    return {
        "total_products": total_products,
        "pending_products": pending_products,
        "reported_products": reported_products,
    }
