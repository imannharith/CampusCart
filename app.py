import os
import sys
import secrets
import hmac
from datetime import date, timedelta
from functools import wraps
from uuid import uuid4

from flask import Flask, render_template, redirect, url_for, request, session, abort, flash
from werkzeug.security import generate_password_hash, check_password_hash

import database
import admin_functions as admin
import messaging
import notifications

app = Flask(__name__)
app.secret_key = 'campuscart_secret_key_bebas_tukar'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

# The only statuses the admin links are allowed to set.
PRODUCT_STATUSES = {"Approved", "Pending", "Rejected", "Reported", "Removed"}

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

    return url_for("static", filename=f"uploads/{stored_name}")

ADMIN_ACCOUNTS = {
    "zikryman123@gmail.com": "scrypt:32768:8:1$lfJSd8WxZnbJAX6j$6f91f9a88d7409a67970259ce6276ff095810bc16cd24733ca514d155e99d76c6e71cfb2646cd17a0257080664097647ba8998330a562ec1da1465d30bf9e8bf",
    "imannhairurizal@gmail.com": "scrypt:32768:8:1$nDtoMhQAZUKDqApr$603d72c49e6f8f568f62a6c2fa53a5d4ab945cbfb450f1f69ae9ca1e3e3a96fc12ea0b3605370d567651dcb3a64549717bca3872b676d40682af04d8586726f8",
    "priyaankavi0703@gmail.com": "scrypt:32768:8:1$NAoOq6JLC75PWzFu$2f8166558a27e2bc51af2fe58aa0e3eaedf0ccb00f572556ed26d9e2312d9aa6172a09465970abbdbacb9c6521d88db8235665c6ae051749b6b69409c722f8c4",
}

def admin_required(view):

    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get('role') != 'admin' or session.get('user') not in ADMIN_ACCOUNTS:
            return redirect(url_for('admin_login'))
        return view(*args, **kwargs)
    return wrapped

@app.before_request
def enforce_account_status():
    if request.endpoint == 'static' or not session.get('user'):
        return
    if session.get('role') == 'admin' and session['user'] in ADMIN_ACCOUNTS:
        return
    user = database.get_user_by_email(session['user'])
    if user is None or user['account_status'] == 'Suspended':
        session.clear()
        return render_template('login.html', error='Your account is suspended. Please contact CampusCart support.'), 403


@app.context_processor
def moderation_context():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    account = current_user()
    return {'csrf_token': session['csrf_token'], 'current_account': account,
            'unread_messages': messaging.unread_count(account['id']) if account else 0,
            'notifications': notifications.latest(account['id']) if account else [],
            'unread_notifications': notifications.unread_count(account['id']) if account else 0,
            'orders_to_ship': notifications.orders_to_ship(account['id']) if account else 0}


@app.template_filter('chat_time')
def chat_time(timestamp):
    return messaging.local_time(timestamp)


def require_csrf():
    if not hmac.compare_digest(session.get('csrf_token', '').encode(), request.form.get('csrf_token', '').encode()) or not session.get('csrf_token'):
        abort(400, 'This form expired. Reload the page and try again.')


def validate_moderation_form():
    require_csrf()
    reason = request.form.get('reason', '').strip()
    if not 3 <= len(reason) <= 1000:
        abort(400, 'Enter a reason between 3 and 1,000 characters.')
    return reason


try:
    database.ensure_ready()
except Exception as error:
    print(
        "\nCampusCart could not reach the database."
        f"\n  {type(error).__name__}: {error}\n"
        "\nCheck that you're online, and that .env contains a valid"
        "\nTURSO_DATABASE_URL and TURSO_AUTH_TOKEN.\n",
        file=sys.stderr,
    )

    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1)

# HOME PAGE (Dah dikunci)

@app.route("/")
def home():
    # Semak sama ada user dah login atau belum
    if 'user' not in session:
        return redirect(url_for('login'))

    user = current_user()
    products = admin.get_all_products(status="Approved", available_only=True)
    if user:
        products = [product for product in products if product.get("seller_id") != user["id"]]

    return render_template(
        "homepage.html",
        products=products,
    )

# PRODUCT CATALOG PAGE (Dulu products.py)

@app.route("/products")
def product_catalog():
    """The shop. Only approved listings are on sale, so a student's
    submission stays invisible until an admin has passed it."""

    category_query = request.args.get("category", "all")
    search_query = request.args.get("search", "").strip()
    sort_query = request.args.get("sort", "default")

    # One query, then filter in Python: the category dropdown has to be
    # built from what is actually on sale anyway, so fetching the
    # approved listings once is cheaper than querying twice.
    approved = admin.get_all_products(status="Approved", available_only=True)

    filtered_list = [
        product for product in approved
        if (category_query == "all"
            or product["category"].lower() == category_query.lower())
        and search_query.lower() in product["name"].lower()
    ]
    user = current_user()
    if user:
        filtered_list = [product for product in filtered_list if product.get("seller_id") != user["id"]]

    if sort_query == "low-high":
        filtered_list.sort(key=lambda product: product["price"])
    elif sort_query == "high-low":
        filtered_list.sort(key=lambda product: product["price"], reverse=True)

    return render_template(
        "products.html",
        products=filtered_list,
        categories=sorted({product["category"] for product in approved}),
        selected_category=category_query,
        search=search_query,
        sort=sort_query,
    )

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

    today = date.today()
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

@app.route("/dashboard")
@admin_required
def dashboard():

    stats = admin.get_dashboard_stats()
    recent_products = admin.get_all_products()[:5]

    return render_template(
        "dashboard.html",
        total_products=stats["total_products"],
        pending_products=stats["pending_products"],
        reported_products=stats["reported_products"],
        approved_products=stats["approved_products"],
        total_users=database.get_user_stats()["total"],
        recent_products=recent_products,
        chart=build_chart_days(admin.get_listings_per_day()),
    )

@app.route("/listings")
@admin_required
def listings():

    category = request.args.get("category", "all")
    status = request.args.get("status", "all")
    search = request.args.get("search", "")
    edit_id = request.args.get("edit", type=int)
    review_product = None
    if "view" in request.args:
        review_id = request.args.get("view", type=int)
        if review_id is None:
            abort(404)
        review_product = admin.get_product(review_id)
        if review_product is None:
            abort(404)

    # Only image URLs are presented; never render arbitrary URL schemes.
    review_image = (review_product or {}).get("image_url") or ""
    if not review_image.startswith(("/static/", "https://", "http://")):
        review_image = ""

    all_products = admin.get_all_products()
    listing_stats = {
        "total": len(all_products),
        "pending": len([p for p in all_products if p["status"] == "Pending"]),
        "approved": len([p for p in all_products if p["status"] == "Approved"]),
        "reported": len([p for p in all_products if p["status"] == "Reported"]),
    }

    return render_template(
        "listings.html",
        products=admin.get_all_products(category, status, search),
        categories=admin.get_all_categories(),
        discount_tiers=admin.get_all_discount_tiers(),
        reviews=admin.get_all_reviews(),
        selected_category=category,
        selected_status=status,
        search=search,
        edit_product=admin.get_product(edit_id) if edit_id else None,
        listing_stats=listing_stats,
        review_product=review_product,
        review_image=review_image,
        seller_accounts=database.get_all_users() if edit_id else [],
    )

@app.route("/listings/edit/<int:product_id>", methods=["POST"])
@admin_required
def edit_product(product_id):

    seller_value = request.form.get('seller_id', '').strip()
    try:
        seller_id = int(seller_value) if seller_value else None
    except ValueError:
        abort(400, 'Choose a valid seller account.')
    if seller_id is not None and not admin.rows('SELECT id FROM users WHERE id = ?', (seller_id,)):
        abort(400, 'Seller account does not exist.')

    admin.update_product(
        product_id,
        name=request.form["name"],
        seller_id=seller_id,
        category=request.form["category"],
        price=float(request.form["price"]),
        stock=int(request.form["stock"]),
    )

    return redirect(url_for("listings"))

@app.route("/listings/status/<int:product_id>/<status>")
@admin_required
def change_product_status(product_id, status):

    if status not in PRODUCT_STATUSES:
        abort(400)

    if status == "Reported":
        return redirect(url_for("report_listing", product_id=product_id))
    if admin.rows("SELECT id FROM listing_reports WHERE product_id = ? AND status = 'Open'", (product_id,)):
        return redirect(url_for("moderation_queue", product=product_id))
    admin.set_product_status(product_id, status)
    notifications.listing_decided(product_id, status)

    return redirect(url_for("listings"))

@app.route("/listings/delete/<int:product_id>")
@admin_required
def delete_product(product_id):

    if admin.rows("SELECT id FROM listing_reports WHERE product_id = ? AND status = 'Open'", (product_id,)):
        return redirect(url_for("moderation_queue", product=product_id))
    return redirect(url_for("listings", view=product_id))


@app.route("/listings/remove/<int:product_id>", methods=["POST"])
@admin_required
def remove_product(product_id):
    reason = validate_moderation_form()
    if admin.get_product(product_id) is None:
        abort(404)
    if admin.rows("SELECT id FROM listing_reports WHERE product_id = ? AND status = 'Open'", (product_id,)):
        return resolve_listing_reports(product_id)
    database.atomic([("UPDATE products SET status = 'Removed', removal_reason = ?, removed_by = ?, removed_at = CURRENT_TIMESTAMP WHERE id = ? AND status != 'Removed'",
                      [reason, session['user'], product_id])])
    notifications.listing_decided(product_id, 'Removed', reason)
    flash('Listing removed from sale. Its details and history are preserved.')
    return redirect(url_for('listings', view=product_id))

@app.route("/listings/stock/<int:product_id>/<action>")
@admin_required
def adjust_product_stock(product_id, action):

    amount = 1 if action == "increase" else -1
    admin.adjust_stock(product_id, amount)

    return redirect(url_for("listings"))

@app.route("/categories/rename", methods=["POST"])
@admin_required
def rename_category():

    admin.rename_category(
        request.form["old_name"],
        request.form["new_name"],
    )

    return redirect(url_for("listings"))

@app.route("/tiers/add", methods=["POST"])
@admin_required
def add_tier():

    admin.add_discount_tier(
        min_subtotal=float(request.form["min_subtotal"]),
        discount_percent=float(request.form["discount_percent"]),
    )

    return redirect(url_for("listings"))

@app.route("/tiers/delete/<int:tier_id>")
@admin_required
def delete_tier(tier_id):

    admin.delete_discount_tier(tier_id)

    return redirect(url_for("listings"))

@app.route("/reviews/delete/<int:review_id>")
@admin_required
def delete_review(review_id):

    admin.delete_review(review_id)

    return redirect(url_for("listings"))

@app.route("/reports")
@admin_required
def reports():

    status = request.args.get("status", "all")

    orders = admin.get_all_orders(status)
    for order in orders:
        order["line_items"] = admin.get_order_items(order["id"])

    return render_template(
        "reports.html",
        orders=orders,
        selected_status=status,
        sales=admin.get_sales_summary(),
        top_products=admin.get_top_selling_products(),
        sellers=admin.get_seller_activity(),
        platform=admin.get_platform_revenue(),
        dead_listings=admin.get_dead_listings(),
    )

@app.route("/reports/cancel/<int:order_id>")
@admin_required
def admin_cancel_order(order_id):
    """Cancel an entire order -- only for a problem like a report, not
    routine fulfilment. Sellers ship and deliver their own items;
    admin only steps in when something needs overriding."""

    if admin.admin_cancel_order(order_id):
        notifications.order_cancelled_by_admin(order_id)

    # Land back on the Cancelled filter, not the unfiltered list --
    # otherwise the page looks like nothing happened even though it
    # did, since the dropdown resets to "All Orders" either way.
    return redirect(url_for("reports", status="Cancelled"))

@app.route("/reports/reinstate/<int:order_id>")
@admin_required
def admin_reinstate_order(order_id):

    admin.admin_reinstate_order(order_id)

    return redirect(url_for("reports", status="Pending"))

@app.route("/users")
@admin_required
def users():

    search = request.args.get("search", "")

    return render_template(
        "user.html",
        users=database.get_all_users(search),
        user_stats=database.get_user_stats(),
        search=search,
    )

def shopper_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped


@app.route("/add-to-cart/<int:product_id>")
@shopper_required
def add_to_cart(product_id):
    product = admin.get_product(product_id)
    if not admin.available(product):
        abort(404)
    if product.get('seller_id') == current_user()['id']:
        abort(403, 'You cannot add your own listing to the cart.')
    database.add_cart_item(current_user()['id'], product_id)
    return redirect(url_for('view_cart'))


@app.route('/cart')
@shopper_required
def view_cart():
    database.remove_owned_cart_items(current_user()['id'])
    cart = database.get_cart(current_user()['id'])
    return render_template('cart.html', cart=cart, subtotal=price_cart(cart)['subtotal'])


@app.route('/increase/<int:product_id>')
@shopper_required
def increase_quantity(product_id):
    product = admin.get_product(product_id)
    if product and product.get('seller_id') == current_user()['id']:
        abort(403, 'You cannot buy your own listing.')
    database.add_cart_item(current_user()['id'], product_id, existing_only=True)
    return redirect(url_for('view_cart'))


@app.route('/decrease/<int:product_id>')
@shopper_required
def decrease_quantity(product_id):
    database.decrease_cart_item(current_user()['id'], product_id)
    return redirect(url_for('view_cart'))


@app.route('/remove/<int:product_id>')
@shopper_required
def remove_from_cart(product_id):
    database.remove_cart_item(current_user()['id'], product_id)
    return redirect(url_for('view_cart'))


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
            for tier in admin.get_all_discount_tiers()
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

@app.route('/checkout')
@shopper_required
def checkout():
    database.remove_owned_cart_items(current_user()['id'])
    cart = database.get_cart(current_user()['id'])
    if not cart:
        return redirect(url_for('view_cart'))
    return render_template('checkout.html', cart=cart, **price_cart(cart))


@app.route('/place-order', methods=['POST'])
@shopper_required
def place_order():
    user = current_user()
    database.remove_owned_cart_items(user['id'])
    cart = database.get_cart(user['id'])
    if not cart:
        return redirect(url_for('view_cart'))
    name = request.form['name']
    phone = request.form['phone']
    address = request.form['address']
    pricing = price_cart(cart)
    order_id = database.checkout_cart(user['id'], cart, pricing, name)
    if order_id is None:
        abort(409, 'Your cart or an item’s availability changed. Review your cart before trying again.')
    notifications.new_order(order_id)
    return render_template('order_confirmation.html', order_id=order_id,
                           name=name, phone=phone, address=address,
                           subtotal=pricing['subtotal'], discount=pricing['discount'], total=pricing['total'])


@app.route("/orders")
def view_orders():

    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    user_id = user["id"]

    connection = database.get_connection()

    cursor = connection.cursor()

    cursor.execute("""
        SELECT *
        FROM orders
        WHERE user_id = ? AND account_linked = 1
        ORDER BY order_date DESC
    """, (user_id,))

    orders = cursor.fetchall()

    connection.close()

    return render_template(
        "orders.html",
        orders=orders
    )

@app.route("/profile")
def profile():

    if 'user' not in session:
        return redirect(url_for('login'))

    user = database.get_user_by_email(session['user'])

    if user is None:
        return redirect(url_for('logout'))

    return render_template("userprofile.html", user=dict(user))

def current_user():
    """Return the signed-in account, or None."""

    if 'user' not in session:
        return None

    return database.get_user_by_email(session['user'])

@app.route("/notifications/<int:notification_id>")
def open_notification(notification_id):
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    link = notifications.open_one(notification_id, user["id"])
    if link is None:
        abort(404)
    return redirect(link)

@app.route("/notifications/read-all", methods=["POST"])
def read_all_notifications():
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    require_csrf()
    notifications.mark_all_read(user["id"])
    # Only ever back to a page on this site.
    back = request.form.get("next", "")
    return redirect(back if back.startswith("/") and not back.startswith("//") else url_for("home"))

@app.route("/messages")
def messages_inbox():
    user = current_user()

    if user is None:
        return redirect(url_for('login'))

    return render_template("messages.html", conversations=messaging.inbox(user["id"]))

@app.route("/messages/new/<int:product_id>", methods=["GET", "POST"])
def message_seller(product_id):
    """Start a chat with the seller of this listing. Nothing is saved until
    the first message is sent, so browsing doesn't leave empty chats."""

    user = current_user()

    if user is None:
        return redirect(url_for('login'))

    product = admin.get_product(product_id)

    if not admin.available(product) or product["seller_id"] is None:
        abort(404)

    if product["seller_id"] == user["id"]:
        abort(400, "This is your own listing.")

    if request.method == "POST":
        require_csrf()
        body = messaging.clean(request.form.get("body"))
        if body is None:
            return render_template("conversation.html", conversation=None, product=product,
                                   messages=[], error="Write a message first."), 400
        conversation_id = messaging.start(user["id"], product, body)
        return redirect(url_for("conversation", conversation_id=conversation_id))

    existing = messaging.find_conversation(user["id"], product["seller_id"], product_id)
    if existing:
        return redirect(url_for("conversation", conversation_id=existing))

    return render_template("conversation.html", conversation=None, product=product, messages=[])

@app.route("/messages/<int:conversation_id>", methods=["GET", "POST"])
def conversation(conversation_id):
    user = current_user()

    if user is None:
        return redirect(url_for('login'))

    chat = messaging.conversation_for(conversation_id, user["id"])

    # Someone else's chat looks the same as one that doesn't exist.
    if chat is None:
        abort(404)

    if request.method == "POST":
        require_csrf()
        body = messaging.clean(request.form.get("body"))
        if body is not None:
            messaging.send(conversation_id, user["id"], body)
        return redirect(url_for("conversation", conversation_id=conversation_id) + "#latest")

    messaging.mark_read(conversation_id, user["id"])

    return render_template(
        "conversation.html",
        conversation=chat,
        product=admin.get_product(chat["product_id"]) if chat["product_id"] else None,
        messages=messaging.messages(conversation_id),
    )

@app.route("/messages/<int:conversation_id>/updates")
def conversation_updates(conversation_id):
    """New messages since the last one on screen, for an open chat to poll."""

    user = current_user()

    if user is None:
        abort(401)

    if messaging.conversation_for(conversation_id, user["id"]) is None:
        abort(404)

    new = messaging.messages(conversation_id, request.args.get("after", 0, type=int))

    if new:
        messaging.mark_read(conversation_id, user["id"])

    return {"messages": [
        {"id": m["id"], "mine": m["sender_id"] == user["id"],
         "body": m["body"], "time": messaging.local_time(m["created_at"])}
        for m in new
    ]}

@app.route("/my-shop")
def seller_page():
    """A seller's own listings and sales. Everything here is scoped to
    the signed-in account -- a seller never sees another's items."""

    user = current_user()

    if user is None:
        return redirect(url_for('login'))

    return render_template(
        "sellerpage.html",
        user=dict(user),
        listings=admin.get_all_products(seller_id=user["id"]),
        sales=admin.get_seller_orders(user["id"]),
        summary=admin.get_seller_summary(user["id"]),
        categories=admin.get_all_categories(),
    )

@app.route("/my-shop/stock/<int:product_id>/<action>")
def seller_adjust_stock(product_id, action):
    """Restock or reduce one of your own listings."""

    user = current_user()

    if user is None:
        return redirect(url_for('login'))

    product = admin.get_product(product_id)

    # Owning the listing is the whole authorisation check -- without it
    # anyone signed in could edit anyone else's stock by guessing an id.
    if product is None or product["seller_id"] != user["id"]:
        abort(403)

    admin.adjust_stock(product_id, 1 if action == "increase" else -1)

    return redirect(url_for("seller_page"))

@app.route("/my-shop/delete/<int:product_id>", methods=["POST"])
def seller_delete_listing(product_id):
    """Take your own listing down."""

    user = current_user()

    if user is None:
        return redirect(url_for('login'))

    product = admin.get_product(product_id)

    if product is None or product["seller_id"] != user["id"]:
        abort(403)

    if admin.rows("SELECT id FROM listing_reports WHERE product_id = ? AND status = 'Open'", (product_id,)):
        abort(409, 'This listing is under review and cannot be deleted until the reports are resolved.')
    admin.delete_product(product_id)

    return redirect(url_for("seller_page"))

@app.route("/my-shop/orders/status/<int:item_id>/<status>")
def seller_update_order_status(item_id, status):
    """A seller shipping or delivering one of their own sold items.

    Ownership is the authorisation check: the line has to belong to a
    product this seller listed, or anyone signed in could move any
    seller's order by guessing an item id."""

    user = current_user()

    if user is None:
        return redirect(url_for('login'))

    item = admin.get_order_item(item_id)

    if item is None or item["seller_id"] != user["id"]:
        abort(403)

    if not admin.update_order_item_status(item_id, status):
        abort(400)
    notifications.order_item_moved(item_id)

    return redirect(url_for("seller_page"))

@app.route("/request-sell", methods=["POST"])
def request_sell():
    """A student submitting their own item. It goes in as Pending so it
    lands in the admin approval queue rather than straight on the shop."""

    if 'user' not in session:
        return redirect(url_for('login'))

    user = database.get_user_by_email(session['user'])

    if user is None:
        return redirect(url_for('logout'))

    require_csrf()

    try:
        image_url = save_product_image(request.files.get("image_file"))
    except ValueError as problem:
        flash(str(problem))
        return redirect(url_for("seller_page") + "#sell")

    admin.add_product(
        name=request.form["product_name"],
        seller=user["fullname"],
        seller_id=user["id"],
        category=request.form["category"],
        price=float(request.form["price"]),
        stock=int(request.form["stock"]),
        status="Pending",
        description=request.form.get("description"),
        image_url=image_url,
    )

    return redirect(url_for("seller_page"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        remember = request.form.get("remember")

        user = database.get_user_by_email(email)

        if user is None or not check_password_hash(user["password_hash"], password):
            return render_template("login.html", error="Incorrect email or password.")

        if user["account_status"] == "Suspended":
            return render_template("login.html", error="Your account is suspended. Please contact CampusCart support."), 403

        session.clear()
        session.permanent = bool(remember)
        session['user'] = user["email"]
        session['role'] = user["role"]

        return redirect(url_for("home"))

    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        fullname = request.form.get("fullname")
        email = request.form.get("email")
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if password != confirm_password:
            return render_template("register.html", error="Passwords don't match.")

        if database.get_user_by_email(email) is not None:
            return render_template("register.html", error="An account with that email already exists.")

        database.create_user(fullname, email, generate_password_hash(password), "student")

        return redirect(url_for("login"))

    return render_template("register.html")

@app.route("/logout")
def logout():
    was_admin = session.get('role') == 'admin'

    # Buang data user dari session & hantar balik ke login page
    session.pop('user', None)
    session.pop('role', None)

    if was_admin:
        return redirect(url_for("admin_login"))
    return redirect(url_for("login"))

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        password_hash = ADMIN_ACCOUNTS.get(email)

        if password_hash is None or not check_password_hash(password_hash, password):
            return render_template("admin_login.html", error="Incorrect email or password.")

        session['user'] = email
        session['role'] = 'admin'

        return redirect(url_for("dashboard"))

    return render_template("admin_login.html")

@app.route('/users/<int:user_id>')
@admin_required
def user_detail(user_id):
    details = admin.user_details(user_id)
    if details is None:
        abort(404)
    return render_template('user.html', detail_view=True, **details, protected=details['user']['email'] in ADMIN_ACCOUNTS)


@app.route('/users/<int:user_id>/status', methods=['POST'])
@admin_required
def user_status(user_id):
    reason = validate_moderation_form()
    details = admin.user_details(user_id)
    if details is None:
        abort(404)
    if details['user']['email'] in ADMIN_ACCOUNTS or details['user']['role'] == 'admin':
        abort(403, 'Administrator accounts cannot be suspended here.')
    action = request.form.get('action')
    if action not in {'Suspend', 'Reactivate'}:
        abort(400)
    admin.change_account(user_id, action, reason, session['user'])
    flash('Account suspended.' if action == 'Suspend' else 'Account reactivated.')
    return redirect(url_for('user_detail', user_id=user_id))


@app.route('/moderation')
@admin_required
def moderation_queue():
    state = request.args.get('status', 'All')
    if state not in {'Open', 'Dismissed', 'Removed', 'All'}:
        abort(400)
    product_id = request.args.get('product', type=int)
    query = 'SELECT listing_reports.*, EXISTS (SELECT 1 FROM products WHERE products.id = listing_reports.product_id) AS listing_exists FROM listing_reports WHERE 1=1'
    args = []
    if state != 'All':
        query += ' AND status = ?'
        args.append(state)
    if product_id:
        query += ' AND product_id = ?'
        args.append(product_id)
    reports = admin.rows(query + ' ORDER BY id DESC', args)
    return render_template('moderation.html', reports=reports, selected_status=state, product_id=product_id)


@app.route('/moderation/<int:product_id>/resolve', methods=['POST'])
@admin_required
def resolve_listing_reports(product_id):
    reason = validate_moderation_form()
    decision = request.form.get('decision')
    if decision not in {'Dismissed', 'Removed'}:
        abort(400)
    admin.resolve_reports(product_id, decision, reason, session['user'])
    if decision == 'Removed':
        notifications.listing_decided(product_id, 'Removed', reason)
    flash('Reports resolved. The decision has been saved in history.')
    return redirect(url_for('moderation_queue', status=decision, product=product_id))


@app.route('/products/<int:product_id>/report', methods=['GET', 'POST'])
def report_listing(product_id):
    user = current_user()
    is_admin = session.get('role') == 'admin' and session.get('user') in ADMIN_ACCOUNTS
    if user is None and not is_admin:
        return redirect(url_for('login'))
    product = admin.get_product(product_id)
    if product is None or product['status'] not in {'Approved', 'Reported', 'Pending'}:
        abort(404)
    if product['status'] == 'Pending' and not is_admin:
        abort(404)
    if request.method == 'POST':
        reason = validate_moderation_form()
        evidence = request.form.get('evidence', '').strip()
        if len(evidence) > 2000:
            abort(400, 'Evidence must be at most 2,000 characters.')
        report_id = admin.submit_report(product_id, user['id'] if user else None,
                                             session['user'], reason, evidence)
        if report_id is None:
            abort(409, 'The listing changed before your report was saved. Reload and try again.')
        return render_template('report_listing.html', product=product, submitted=True, is_admin=is_admin, report_id=report_id)
    return render_template('report_listing.html', product=product, submitted=False, is_admin=is_admin)


if __name__ == "__main__":
    app.run(debug=True)
