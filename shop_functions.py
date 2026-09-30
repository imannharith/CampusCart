"""
Shop Functions
--------------
Every database function behind the student side of CampusCart -- the
pages a student sees -- grouped in the same order as the top menu:

    1. Shopping (Home, Products, Cart, Checkout)     2. My Orders & Reviews
    3. Messages     4. My Shop     5. Notifications

The admin panel's functions are in admin_functions.py; signing up and
logging in are in accounts.py.

Every function opens its own short-lived connection through
database.get_connection() and closes it before returning.
"""
import os
import re
from datetime import datetime, timedelta
from uuid import uuid4

import database
# Shared with the admin panel: listings are looked up the same way, and what a
# seller keeps from a sale is worked out by the same rule as the admin's revenue report.
from admin_functions import (PLATFORM_COMMISSION_PERCENT, actual_revenue, get_all_products,
                             get_all_discount_tiers, get_order_items, sync_order_status)

# Timestamps are stored in UTC; CampusCart's students are in Malaysia (UTC+8).
LOCAL_OFFSET = timedelta(hours=8)


def rows(sql, params=()):
    """Run a SELECT and hand back plain dicts. Shared by every section."""
    connection = database.get_connection()
    cursor = connection.cursor()
    cursor.execute(sql, params)
    result = [dict(row) for row in cursor.fetchall()]
    connection.close()
    return result


def local_time(timestamp):
    moment = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S") + LOCAL_OFFSET
    return f"{moment.day} {moment:%b}, {moment.hour % 12 or 12}:{moment:%M %p}"


def malaysia_time(timestamp):
    """A time from the database as a Malaysian date and time, or None.

    The database stores every time in UTC (CURRENT_TIMESTAMP), eight hours
    behind Malaysia, so it is shifted before it is shown to anyone."""
    text = str(timestamp or "").replace("T", " ")[:19]
    for layout in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, layout) + LOCAL_OFFSET
        except ValueError:
            continue
    return None


def when(timestamp):
    """30 Sep 2026, 1:22 AM"""
    moment = malaysia_time(timestamp)
    if moment is None:
        return "Not available"
    return f"{moment.day} {moment:%b %Y}, {moment.hour % 12 or 12}:{moment:%M %p}"


def day(timestamp):
    """30 Sep 2026"""
    moment = malaysia_time(timestamp)
    return f"{moment.day} {moment:%b %Y}" if moment else "Not available"


# ====================================================================
# 1. SHOPPING: HOME, PRODUCTS, CART & CHECKOUT
# What's on sale, what's in a student's cart, and turning the cart
# into an order in one go.
# ====================================================================


def products_for_sale(viewer_id=None, category="all", search="", sort="default"):
    """What a student can buy on the Home and Products pages, and the
    categories to filter by.

    Only approved listings from sellers who aren't suspended, and never the
    student's own. One query, then filtered here: the category list has to
    come from everything on sale anyway, so asking once is cheaper."""

    approved = get_all_products(status="Approved", available_only=True)

    products = [
        product for product in approved
        if (category == "all" or product["category"].lower() == category.lower())
        and search.lower() in product["name"].lower()
        and (viewer_id is None or product.get("seller_id") != viewer_id)
    ]

    if sort == "low-high":
        products.sort(key=lambda product: product["price"])
    elif sort == "high-low":
        products.sort(key=lambda product: product["price"], reverse=True)

    return products, sorted({product["category"] for product in approved})


def get_cart(user_id):
    connection = database.get_connection()
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
    database.atomic([("""DELETE FROM cart WHERE user_id = ? AND EXISTS
        (SELECT 1 FROM products WHERE products.id = cart.product_id AND products.seller_id = ?)""",
        [user_id, user_id])])


def add_cart_item(user_id, product_id, existing_only=False):
    # A single upsert prevents two requests from losing an increment or exceeding stock.
    database.atomic([("""INSERT INTO cart(user_id, product_id, quantity)
        SELECT ?, id, 1 FROM products WHERE id = ? AND status = 'Approved' AND stock > 0
        AND seller_id IS NOT ?
        AND NOT EXISTS (SELECT 1 FROM users WHERE id = products.seller_id AND account_status = 'Suspended')
        AND (? = 0 OR EXISTS (SELECT 1 FROM cart WHERE user_id = ? AND product_id = products.id))
        ON CONFLICT(user_id, product_id) DO UPDATE SET quantity = cart.quantity + 1
        WHERE cart.quantity < (SELECT stock FROM products WHERE id = excluded.product_id)""",
        [user_id, product_id, user_id, int(existing_only), user_id])])


def decrease_cart_item(user_id, product_id):
    database.atomic([
        ('UPDATE cart SET quantity = quantity - 1 WHERE user_id = ? AND product_id = ?', [user_id, product_id]),
        ('DELETE FROM cart WHERE user_id = ? AND product_id = ? AND quantity <= 0', [user_id, product_id]),
    ])


def remove_cart_item(user_id, product_id):
    database.atomic([('DELETE FROM cart WHERE user_id = ? AND product_id = ?', [user_id, product_id])])


def checkout_cart(user_id, cart, pricing, buyer_name, phone, address):
    """Commit the checked cart, order, stock and cart removal as one transaction.

    The first statement only creates an order if the entire snapshot still matches.
    Every later statement depends on that order, so stale/duplicate requests do nothing.
    """
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
    database.atomic(statements)
    connection = database.get_connection()
    cursor = connection.cursor()
    cursor.execute('SELECT id FROM orders WHERE checkout_token = ?', (token,))
    order = cursor.fetchone()
    connection.close()
    return order['id'] if order else None


def price_cart(cart):
    """Work out what the cart costs, including any discount.

    Every page that shows a price or charges one goes through here.
    While checkout and place_order each did their own arithmetic, the
    page could quote one total and the saved order record another."""

    subtotal = sum(item["price"] * item["quantity"] for item in cart.values())

    # Tiers come back biggest threshold first, so the first one the
    # subtotal clears is the best one it qualifies for.
    percent = next(
        (
            tier["discount_percent"]
            for tier in get_all_discount_tiers()
            if subtotal >= tier["min_subtotal"]
        ),
        0,
    )

    discount = subtotal * (percent / 100)

    return {
        "subtotal": subtotal,
        "discount_percent": percent,
        "discount": discount,
        "total": subtotal - discount,
    }


def check_delivery_details(name, phone, address):
    """What's wrong with the checkout details, or None if they're usable.
    The seller posts the item here, so it has to be a real name, phone and address."""
    if not 2 <= len(name) <= 80:
        return "Enter your full name."
    if not re.fullmatch(r"[0-9+\-\s()]{7,20}", phone) or sum(c.isdigit() for c in phone) < 7:
        return "Enter a phone number, like 012-345 6789."
    if not 5 <= len(address) <= 300:
        return "Enter the address the seller should send your items to."
    return None


# ====================================================================
# 2. MY ORDERS & REVIEWS
# A buyer rates an item (1 to 5 stars, optional comment) once it has been
# delivered. Only the buyer, only after delivery, only once per purchase.
# ====================================================================


def buyer_orders(user_id):
    """Everything this student has bought, newest first, each order with its
    items -- every item has its own status, because each seller ships their own."""
    orders = rows("SELECT * FROM orders WHERE user_id = ? AND account_linked = 1 ORDER BY id DESC", (user_id,))
    for order in orders:
        order["items"] = get_order_items(order["id"])
    return orders


def recent_orders(user_id, limit=5):
    """The last few orders, with how many items each had (for the Profile page)."""
    return rows("""SELECT orders.*, (SELECT SUM(quantity) FROM order_items WHERE order_id = orders.id) AS item_count
        FROM orders WHERE user_id = ? AND account_linked = 1 ORDER BY id DESC LIMIT ?""", (user_id, limit))


MAX_REVIEW_LENGTH = 500


def clean_review(rating, comment):
    """The rating as a whole number from 1 to 5 and the tidied comment.
    Raises ValueError with a message to show if either is wrong."""
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        raise ValueError("Pick from 1 to 5 stars.")
    if not 1 <= rating <= 5:
        raise ValueError("Pick from 1 to 5 stars.")
    comment = (comment or "").strip()
    if len(comment) > MAX_REVIEW_LENGTH:
        raise ValueError(f"Keep your review under {MAX_REVIEW_LENGTH} characters.")
    return rating, comment


def purchase_to_review(order_item_id, user_id):
    """One line of this student's own order, with its review if they left one.
    None if it isn't theirs -- someone else's purchase looks like no purchase."""
    found = rows('''SELECT i.id, i.product_id, i.product_name, i.quantity, i.status, i.seller, i.seller_id,
            o.id AS order_id, r.id AS review_id
        FROM order_items i
        JOIN orders o ON o.id = i.order_id
        LEFT JOIN reviews r ON r.order_item_id = i.id
        WHERE i.id = ? AND o.user_id = ? AND o.account_linked = 1''', (order_item_id, user_id))
    return found[0] if found else None


def add_review(purchase, user, rating, comment):
    """Save the review. The INSERT itself re-checks that the item was
    delivered and not already reviewed, so two quick clicks can't make two."""
    database.atomic([('''INSERT INTO reviews
            (product_id, product_name, reviewer, reviewer_id, order_item_id, rating, comment)
        SELECT i.product_id, i.product_name, ?, ?, i.id, ?, ?
        FROM order_items i
        WHERE i.id = ? AND i.status = 'Delivered'
          AND NOT EXISTS (SELECT 1 FROM reviews WHERE order_item_id = i.id)''',
        [user['fullname'], user['id'], rating, comment, purchase['id']])])


def reviews_for_product(product_id):
    return rows('SELECT * FROM reviews WHERE product_id = ? ORDER BY id DESC', (product_id,))


def review_summary(product_id):
    """Average, how many, and how many of each star (for the bar chart)."""
    counts = {row['rating']: row['n'] for row in rows(
        'SELECT rating, COUNT(*) AS n FROM reviews WHERE product_id = ? GROUP BY rating', (product_id,))}
    total = sum(counts.values())
    average = sum(stars * n for stars, n in counts.items()) / total if total else None
    return {
        'count': total,
        'average': round(average, 1) if average else None,
        'bars': [{'stars': stars, 'n': counts.get(stars, 0),
                  'percent': round(counts.get(stars, 0) / total * 100) if total else 0}
                 for stars in range(5, 0, -1)],
    }


def product_ratings():
    """{product_id: {'average': 4.5, 'count': 2}} for every reviewed product,
    in one query, so a page of product cards doesn't ask once per card."""
    return {row['product_id']: {'average': round(row['average'], 1), 'count': row['n']}
            for row in rows('''SELECT product_id, AVG(rating) AS average, COUNT(*) AS n
                               FROM reviews GROUP BY product_id''')}


def seller_rating(seller_id):
    """The average over every review of everything this seller has sold."""
    found = rows('''SELECT AVG(r.rating) AS average, COUNT(*) AS n FROM reviews r
        JOIN order_items i ON i.id = r.order_item_id WHERE i.seller_id = ?''', (seller_id,))[0]
    return {'average': round(found['average'], 1), 'count': found['n']} if found['n'] else None


def my_reviews(user_id):
    """{order_item_id: review} for this student's own reviews (for My Orders)."""
    return {row['order_item_id']: row for row in rows(
        'SELECT * FROM reviews WHERE reviewer_id = ? AND order_item_id IS NOT NULL', (user_id,))}


def latest_reviews(limit=5):
    return rows('SELECT * FROM reviews ORDER BY id DESC LIMIT ?', (limit,))


# ====================================================================
# 3. MESSAGES
# Chat between a buyer and the seller of a listing.
# ====================================================================


MAX_MESSAGE_LENGTH = 1000


def clean_message(body):
    """The message text, or None if it's empty or too long to send."""
    body = (body or "").strip()
    return body if 1 <= len(body) <= MAX_MESSAGE_LENGTH else None


def find_conversation(buyer_id, seller_id, product_id):
    found = rows("""SELECT id FROM conversations
        WHERE buyer_id = ? AND seller_id = ? AND IFNULL(product_id, 0) = ?""",
        (buyer_id, seller_id, product_id or 0))
    return found[0]["id"] if found else None


def conversation_for(conversation_id, user_id):
    """The conversation, but only for one of the two people in it."""
    found = rows("""SELECT c.*, b.fullname AS buyer_name, s.fullname AS seller_name
        FROM conversations c
        JOIN users b ON b.id = c.buyer_id
        JOIN users s ON s.id = c.seller_id
        WHERE c.id = ? AND ? IN (c.buyer_id, c.seller_id)""", (conversation_id, user_id))
    return found[0] if found else None


def inbox(user_id):
    """Every conversation this person is in, as buyer or seller, newest first."""
    return rows("""SELECT c.id, c.product_id, c.product_name, c.last_message_at,
            CASE WHEN c.buyer_id = ? THEN s.fullname ELSE b.fullname END AS other_name,
            CASE WHEN c.buyer_id = ? THEN 'Seller' ELSE 'Buyer' END AS other_role,
            (SELECT body FROM messages m WHERE m.conversation_id = c.id
                ORDER BY m.id DESC LIMIT 1) AS last_body,
            (SELECT sender_id FROM messages m WHERE m.conversation_id = c.id
                ORDER BY m.id DESC LIMIT 1) AS last_sender_id,
            (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id
                AND m.sender_id != ? AND m.read_at IS NULL) AS unread
        FROM conversations c
        JOIN users b ON b.id = c.buyer_id
        JOIN users s ON s.id = c.seller_id
        WHERE ? IN (c.buyer_id, c.seller_id)
        ORDER BY c.last_message_at DESC, c.id DESC""", (user_id, user_id, user_id, user_id))


def unread_message_count(user_id):
    return rows("""SELECT COUNT(*) AS n FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE ? IN (c.buyer_id, c.seller_id) AND m.sender_id != ? AND m.read_at IS NULL""",
        (user_id, user_id))[0]["n"]


def chat_messages(conversation_id, after_id=0):
    return rows("""SELECT id, sender_id, body, created_at FROM messages
        WHERE conversation_id = ? AND id > ? ORDER BY id""", (conversation_id, after_id))


def mark_chat_read(conversation_id, user_id):
    database.atomic([("""UPDATE messages SET read_at = CURRENT_TIMESTAMP
        WHERE conversation_id = ? AND sender_id != ? AND read_at IS NULL""",
        [conversation_id, user_id])])


def send_message(conversation_id, sender_id, body):
    # The insert re-checks the sender is in the conversation, so a guessed
    # conversation id can't be used to post into someone else's chat.
    database.atomic([
        ("""INSERT INTO messages (conversation_id, sender_id, body)
            SELECT id, ?, ? FROM conversations WHERE id = ? AND ? IN (buyer_id, seller_id)""",
         [sender_id, body, conversation_id, sender_id]),
        ("UPDATE conversations SET last_message_at = CURRENT_TIMESTAMP WHERE id = ?",
         [conversation_id]),
    ])


def start_chat(buyer_id, product, body):
    """Open the buyer's chat about this listing and post the first message
    together, so an empty conversation is never left in anyone's inbox.
    If the chat already exists, the message just joins it."""
    key = [buyer_id, product["seller_id"], product["id"]]
    database.atomic([
        ("""INSERT OR IGNORE INTO conversations (buyer_id, seller_id, product_id, product_name)
            VALUES (?, ?, ?, ?)""", key + [product["name"]]),
        ("""INSERT INTO messages (conversation_id, sender_id, body)
            SELECT id, ?, ? FROM conversations
            WHERE buyer_id = ? AND seller_id = ? AND IFNULL(product_id, 0) = ?""",
         [buyer_id, body] + key),
        ("""UPDATE conversations SET last_message_at = CURRENT_TIMESTAMP
            WHERE buyer_id = ? AND seller_id = ? AND IFNULL(product_id, 0) = ?""", key),
    ])
    return find_conversation(*key)


# ====================================================================
# 4. MY SHOP (SELLER SIDE)
# The seller's own shop page: their sales, their earnings, and moving
# their own order lines along (Pending > Shipped > Delivered).
# ====================================================================


# The categories a student can pick when selling (the Sell form's dropdown).
SELL_CATEGORIES = {"books", "electronics", "fashion", "accessories"}


def read_listing(form, name_field, min_stock=0):
    """The listing details typed into a form, checked and tidied.
    Raises ValueError with a message to show if anything is wrong."""

    name = (form.get(name_field) or "").strip()
    category = (form.get("category") or "").strip()
    description = (form.get("description") or "").strip()

    if not 1 <= len(name) <= 120:
        raise ValueError("Give the item a name (up to 120 characters).")
    if not 1 <= len(category) <= 40:
        raise ValueError("Choose a category.")
    if len(description) > 1000:
        raise ValueError("Keep the description under 1,000 characters.")

    try:
        price = round(float(form.get("price", "")), 2)
    except ValueError:
        raise ValueError("Price must be a number, like 25.00.")
    # Written this way round so "nan" and "inf" fail too.
    if not 0.01 <= price <= 100000:
        raise ValueError("Price must be between RM 0.01 and RM 100,000.")

    try:
        stock = int(form.get("stock", ""))
    except ValueError:
        raise ValueError("Quantity must be a whole number.")
    if not min_stock <= stock <= 9999:
        raise ValueError(f"Quantity must be between {min_stock} and 9,999.")

    return {"name": name, "category": category, "price": price,
            "stock": stock, "description": description}


UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "uploads")


# JPG and PNG are what phones and laptops save photos as. Each one is
# recognised by the first bytes of the file itself, so renaming some
# other file to "photo.jpg" does not get it through.
PHOTO_TYPES = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
}


MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5 MB


def save_product_image(upload):
    """Store an uploaded product photo and return the path to show it at.

    Raises ValueError with a message for the seller if the file is
    missing, too big, or not really a JPG or PNG.

    The file is saved under a random name. A name chosen by whoever
    uploaded it must never reach the filesystem, and two students both
    uploading 'photo.jpg' must not overwrite each other."""

    if upload is None or not upload.filename:
        raise ValueError("Please add a photo of the item.")

    data = upload.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError("That photo is over 5 MB. Please pick a smaller one.")

    extension = next((ext for start, ext in PHOTO_TYPES.items() if data.startswith(start)), None)
    if extension is None:
        raise ValueError("Photos must be JPG or PNG.")

    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    stored_name = f"{uuid4().hex}.{extension}"
    with open(os.path.join(UPLOAD_FOLDER, stored_name), "wb") as photo:
        photo.write(data)

    return f"/static/uploads/{stored_name}"


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

    connection = database.get_connection()
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

    connection = database.get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT status, product_id, quantity, order_id FROM order_items WHERE id = ?",
        (item_id,)
    )
    row = cursor.fetchone()

    if row is None:
        connection.close()
        return False

    if status.lower() not in ORDER_ITEM_TRANSITIONS.get(row["status"].lower(), set()):
        connection.close()
        return False

    # Always saved as "Shipped", never "shipped", so the order's status
    # can be worked out from its items.
    status = status.capitalize()

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
    sync_order_status(row["order_id"])
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

    connection = database.get_connection()
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
            orders.phone         AS buyer_phone,
            orders.address       AS buyer_address,
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
        line["actual_amount"] = round(actual_revenue(
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
# 5. NOTIFICATIONS
# The bell: short "something happened" messages saved for one student,
# so they are still there next time. Chat has its own red number instead.
# ====================================================================


# The bell's drop-down shows this many; older ones are simply not listed.
NOTIFICATIONS_SHOWN = 8


def notify(user_id, text, link):
    if user_id is None:
        return
    database.atomic([('INSERT INTO notifications (user_id, text, link) VALUES (?, ?, ?)',
                      [user_id, text, link])])


def latest_notifications(user_id):
    return rows('SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT ?',
                (user_id, NOTIFICATIONS_SHOWN))


def unread_notification_count(user_id):
    return rows('SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND read_at IS NULL',
                (user_id,))[0]['n']


def open_notification(notification_id, user_id):
    """Mark it read and return where it points -- only for its owner."""
    found = rows('SELECT link FROM notifications WHERE id = ? AND user_id = ?',
                 (notification_id, user_id))
    if not found:
        return None
    database.atomic([('UPDATE notifications SET read_at = CURRENT_TIMESTAMP WHERE id = ? AND read_at IS NULL',
                      [notification_id])])
    return found[0]['link']


def mark_notifications_read(user_id):
    database.atomic([('UPDATE notifications SET read_at = CURRENT_TIMESTAMP WHERE user_id = ? AND read_at IS NULL',
                      [user_id])])


def orders_to_ship(user_id):
    """Sales of this seller's items still waiting to be sent -- the red
    number on My Shop. Worked out live, so it drops as soon as they ship."""
    return rows("SELECT COUNT(*) AS n FROM order_items WHERE seller_id = ? AND status = 'Pending'",
                (user_id,))[0]['n']


def notify_new_order(order_id):
    """Tell each seller in the order what was bought from them."""
    lines = rows('''SELECT i.seller_id, i.product_name, i.quantity, o.buyer_name
        FROM order_items i JOIN orders o ON o.id = i.order_id WHERE i.order_id = ?''', (order_id,))
    for line in lines:
        notify(line['seller_id'],
             f"{line['buyer_name'] or 'Someone'} bought {line['quantity']} × {line['product_name']}. Time to ship it!",
             '/my-shop')


def notify_listing_decided(product_id, status, reason=None):
    """Tell the seller an admin approved, rejected or removed their listing."""
    found = rows('SELECT seller_id, name FROM products WHERE id = ?', (product_id,))
    if not found:
        return
    product = found[0]
    text = {
        'Approved': f"Your listing \"{product['name']}\" was approved and is now in the shop.",
        'Rejected': f"Your listing \"{product['name']}\" was not approved.",
        'Removed': f"Your listing \"{product['name']}\" was removed by CampusCart.",
    }.get(status)
    if text is None:
        return
    if reason:
        text += f" Reason: {reason}"
    notify(product['seller_id'], text, '/my-shop')


def notify_order_item_moved(item_id):
    """Tell the buyer their item was shipped, delivered, cancelled or reinstated."""
    found = rows('''SELECT i.product_name, i.status, o.id AS order_id, o.user_id
        FROM order_items i JOIN orders o ON o.id = i.order_id
        WHERE i.id = ? AND o.account_linked = 1''', (item_id,))
    if not found:
        return
    line = found[0]
    text = {
        'Shipped': f"{line['product_name']} is on its way.",
        'Delivered': f"{line['product_name']} was marked as delivered.",
        'Cancelled': f"The seller cancelled {line['product_name']} from order #{line['order_id']}.",
        'Pending': f"{line['product_name']} from order #{line['order_id']} is back on.",
    }.get(line['status'])
    if text:
        notify(line['user_id'], text, '/orders')


def notify_order_cancelled(order_id):
    found = rows('SELECT user_id FROM orders WHERE id = ? AND account_linked = 1', (order_id,))
    if found:
        notify(found[0]['user_id'], f"Order #{order_id} was cancelled by CampusCart.", '/orders')


def notify_order_reinstated(order_id):
    found = rows('SELECT user_id FROM orders WHERE id = ? AND account_linked = 1', (order_id,))
    if found:
        notify(found[0]['user_id'], f"Order #{order_id} is back on. The seller will send it soon.", '/orders')


def notify_review_left(order_item_id):
    """Tell the seller someone rated what they sold."""
    found = rows('''SELECT r.rating, r.reviewer, r.product_id, i.product_name, i.seller_id
        FROM reviews r JOIN order_items i ON i.id = r.order_item_id WHERE r.order_item_id = ?''', (order_item_id,))
    if found:
        review = found[0]
        notify(review['seller_id'],
             f"{review['reviewer']} gave {review['product_name']} {review['rating']} out of 5 stars.",
             f"/products/{review['product_id']}/reviews")
