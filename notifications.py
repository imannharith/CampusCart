"""Notifications: short "something happened" messages for one student,
shown under the bell in the top bar.

Each one is saved when the thing happens (an order placed, a listing
approved, an item shipped), so it is still there the next time the
student signs in. Chat messages are not copied in here -- Messages
already has its own red count.
"""
import database

# The bell's drop-down shows this many; older ones are simply not listed.
SHOWN = 8


def rows(sql, params=()):
    connection = database.get_connection()
    cursor = connection.cursor()
    cursor.execute(sql, params)
    result = [dict(row) for row in cursor.fetchall()]
    connection.close()
    return result


def send(user_id, text, link):
    if user_id is None:
        return
    database.atomic([('INSERT INTO notifications (user_id, text, link) VALUES (?, ?, ?)',
                      [user_id, text, link])])


def latest(user_id):
    return rows('SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT ?',
                (user_id, SHOWN))


def unread_count(user_id):
    return rows('SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND read_at IS NULL',
                (user_id,))[0]['n']


def open_one(notification_id, user_id):
    """Mark it read and return where it points -- only for its owner."""
    found = rows('SELECT link FROM notifications WHERE id = ? AND user_id = ?',
                 (notification_id, user_id))
    if not found:
        return None
    database.atomic([('UPDATE notifications SET read_at = CURRENT_TIMESTAMP WHERE id = ? AND read_at IS NULL',
                      [notification_id])])
    return found[0]['link']


def mark_all_read(user_id):
    database.atomic([('UPDATE notifications SET read_at = CURRENT_TIMESTAMP WHERE user_id = ? AND read_at IS NULL',
                      [user_id])])


def orders_to_ship(user_id):
    """Sales of this seller's items still waiting to be sent -- the red
    number on My Shop. Worked out live, so it drops as soon as they ship."""
    return rows("SELECT COUNT(*) AS n FROM order_items WHERE seller_id = ? AND status = 'Pending'",
                (user_id,))[0]['n']


# --- The events that create a notification ------------------------------

def new_order(order_id):
    """Tell each seller in the order what was bought from them."""
    lines = rows('''SELECT i.seller_id, i.product_name, i.quantity, o.buyer_name
        FROM order_items i JOIN orders o ON o.id = i.order_id WHERE i.order_id = ?''', (order_id,))
    for line in lines:
        send(line['seller_id'],
             f"{line['buyer_name'] or 'Someone'} bought {line['quantity']} × {line['product_name']}. Time to ship it!",
             '/my-shop')


def listing_decided(product_id, status, reason=None):
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
    send(product['seller_id'], text, '/my-shop')


def order_item_moved(item_id):
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
        send(line['user_id'], text, '/orders')


def order_cancelled_by_admin(order_id):
    found = rows('SELECT user_id FROM orders WHERE id = ? AND account_linked = 1', (order_id,))
    if found:
        send(found[0]['user_id'], f"Order #{order_id} was cancelled by CampusCart.", '/orders')


def order_reinstated_by_admin(order_id):
    found = rows('SELECT user_id FROM orders WHERE id = ? AND account_linked = 1', (order_id,))
    if found:
        send(found[0]['user_id'], f"Order #{order_id} is back on. The seller will send it soon.", '/orders')


def review_left(order_item_id):
    """Tell the seller someone rated what they sold."""
    found = rows('''SELECT r.rating, r.reviewer, r.product_id, i.product_name, i.seller_id
        FROM reviews r JOIN order_items i ON i.id = r.order_item_id WHERE r.order_item_id = ?''', (order_item_id,))
    if found:
        review = found[0]
        send(review['seller_id'],
             f"{review['reviewer']} gave {review['product_name']} {review['rating']} out of 5 stars.",
             f"/products/{review['product_id']}/reviews")
