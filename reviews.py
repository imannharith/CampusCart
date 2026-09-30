"""Reviews: a buyer rates an item (1 to 5 stars, with an optional comment)
after it has been delivered to them.

The rules, all checked here rather than trusted from the page:
  * only the person who bought it can review it,
  * only once it is Delivered (you can't judge something you haven't got),
  * only once per purchase (order_item_id is unique in the table).
"""
import database

MAX_COMMENT = 500


def rows(sql, params=()):
    connection = database.get_connection()
    cursor = connection.cursor()
    cursor.execute(sql, params)
    result = [dict(row) for row in cursor.fetchall()]
    connection.close()
    return result


def clean(rating, comment):
    """The rating as a whole number from 1 to 5 and the tidied comment.
    Raises ValueError with a message to show if either is wrong."""
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        raise ValueError("Pick from 1 to 5 stars.")
    if not 1 <= rating <= 5:
        raise ValueError("Pick from 1 to 5 stars.")
    comment = (comment or "").strip()
    if len(comment) > MAX_COMMENT:
        raise ValueError(f"Keep your review under {MAX_COMMENT} characters.")
    return rating, comment


# --- Can this student review this purchase? --------------------------------

def purchase(order_item_id, user_id):
    """One line of this student's own order, with its review if they left one.
    None if it isn't theirs -- someone else's purchase looks like no purchase."""
    found = rows('''SELECT i.id, i.product_id, i.product_name, i.quantity, i.status, i.seller, i.seller_id,
            o.id AS order_id, r.id AS review_id
        FROM order_items i
        JOIN orders o ON o.id = i.order_id
        LEFT JOIN reviews r ON r.order_item_id = i.id
        WHERE i.id = ? AND o.user_id = ? AND o.account_linked = 1''', (order_item_id, user_id))
    return found[0] if found else None


def add(purchase, user, rating, comment):
    """Save the review. The INSERT itself re-checks that the item was
    delivered and not already reviewed, so two quick clicks can't make two."""
    database.atomic([('''INSERT INTO reviews
            (product_id, product_name, reviewer, reviewer_id, order_item_id, rating, comment)
        SELECT i.product_id, i.product_name, ?, ?, i.id, ?, ?
        FROM order_items i
        WHERE i.id = ? AND i.status = 'Delivered'
          AND NOT EXISTS (SELECT 1 FROM reviews WHERE order_item_id = i.id)''',
        [user['fullname'], user['id'], rating, comment, purchase['id']])])


# --- Showing reviews -------------------------------------------------------

def for_product(product_id):
    return rows('SELECT * FROM reviews WHERE product_id = ? ORDER BY id DESC', (product_id,))


def summary(product_id):
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


def ratings():
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


def mine(user_id):
    """{order_item_id: review} for this student's own reviews (for My Orders)."""
    return {row['order_item_id']: row for row in rows(
        'SELECT * FROM reviews WHERE reviewer_id = ? AND order_item_id IS NOT NULL', (user_id,))}


def latest(limit=5):
    return rows('SELECT * FROM reviews ORDER BY id DESC LIMIT ?', (limit,))
