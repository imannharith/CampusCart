"""Chat between a buyer and the seller of a listing."""
from datetime import datetime, timedelta

import database

MAX_LENGTH = 1000

# Timestamps are stored in UTC; CampusCart's students are in Malaysia (UTC+8).
LOCAL_OFFSET = timedelta(hours=8)


def rows(sql, params=()):
    connection = database.get_connection()
    cursor = connection.cursor()
    cursor.execute(sql, params)
    result = [dict(row) for row in cursor.fetchall()]
    connection.close()
    return result


def clean(body):
    """The message text, or None if it's empty or too long to send."""
    body = (body or "").strip()
    return body if 1 <= len(body) <= MAX_LENGTH else None


def local_time(timestamp):
    moment = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S") + LOCAL_OFFSET
    return f"{moment.day} {moment:%b}, {moment.hour % 12 or 12}:{moment:%M %p}"


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


def unread_count(user_id):
    return rows("""SELECT COUNT(*) AS n FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE ? IN (c.buyer_id, c.seller_id) AND m.sender_id != ? AND m.read_at IS NULL""",
        (user_id, user_id))[0]["n"]


def messages(conversation_id, after_id=0):
    return rows("""SELECT id, sender_id, body, created_at FROM messages
        WHERE conversation_id = ? AND id > ? ORDER BY id""", (conversation_id, after_id))


def mark_read(conversation_id, user_id):
    database.atomic([("""UPDATE messages SET read_at = CURRENT_TIMESTAMP
        WHERE conversation_id = ? AND sender_id != ? AND read_at IS NULL""",
        [conversation_id, user_id])])


def send(conversation_id, sender_id, body):
    # The insert re-checks the sender is in the conversation, so a guessed
    # conversation id can't be used to post into someone else's chat.
    database.atomic([
        ("""INSERT INTO messages (conversation_id, sender_id, body)
            SELECT id, ?, ? FROM conversations WHERE id = ? AND ? IN (buyer_id, seller_id)""",
         [sender_id, body, conversation_id, sender_id]),
        ("UPDATE conversations SET last_message_at = CURRENT_TIMESTAMP WHERE id = ?",
         [conversation_id]),
    ])


def start(buyer_id, product, body):
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
