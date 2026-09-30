"""
CampusCart -- the web app
-------------------------
This file only connects each web address to its page: it reads what the
browser sent, calls a function to do the work, and shows a page or
redirects. The work itself is in:

    accounts.py          signing up, logging in, and who may open which page
    shop_functions.py    everything students do (shopping, orders, reviews,
                         messages, My Shop, notifications)
    admin_functions.py   the admin panel
    database.py          the connection to Turso
    tables.py            creating the tables when the app starts
"""
import os
import sys
import secrets
import hmac
from datetime import timedelta

from flask import Flask, render_template, redirect, url_for, request, session, abort, flash

import tables
import accounts
import admin_functions as admin
import shop_functions as shop

app = Flask(__name__)

# The key that signs the login cookie. Anyone who knows it can make a cookie
# saying "I'm the admin", so it lives in .env (kept off GitHub), not in code.
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    app.secret_key = secrets.token_hex(32)
    print("No SECRET_KEY in .env -- using a temporary one, so everyone is "
          "logged out whenever the app restarts.", file=sys.stderr)

app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
# The browser won't send the login cookie along with forms posted from other sites.
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'


@app.before_request
def enforce_account_status():
    """A student suspended while logged in is logged out on their next click."""
    if request.endpoint != 'static' and accounts.account_blocked():
        session.clear()
        return render_template('login.html', error='Your account is suspended. Please contact CampusCart support.'), 403


@app.context_processor
def moderation_context():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    account = accounts.current_user()
    return {'csrf_token': session['csrf_token'], 'current_account': account,
            'unread_messages': shop.unread_message_count(account['id']) if account else 0,
            'notifications': shop.latest_notifications(account['id']) if account else [],
            'unread_notifications': shop.unread_notification_count(account['id']) if account else 0,
            'orders_to_ship': shop.orders_to_ship(account['id']) if account else 0,
            # Light or dark mode (Settings > Theme), applied by _nav.html.
            'theme': (account['theme'] if account and 'theme' in account.keys() else None) or 'light',
            # The red number on Support in the admin menu.
            'support_unread': admin.support_unread_count() if accounts.is_admin() else 0}


# How times look on every page (they're stored in UTC, shown in Malaysia time).
app.add_template_filter(shop.local_time, 'chat_time')   # 30 Sep, 1:22 AM
app.add_template_filter(shop.when, 'when')              # 30 Sep 2026, 1:22 AM
app.add_template_filter(shop.day, 'day')                # 30 Sep 2026


def require_csrf():
    if not hmac.compare_digest(session.get('csrf_token', '').encode(), request.form.get('csrf_token', '').encode()) or not session.get('csrf_token'):
        abort(400, 'This form expired. Reload the page and try again.')


@app.before_request
def check_form_token():
    """Every form on the site carries a secret token from the page it was
    on. A form posted from another website can't know it, so anything that
    changes data (all of it is POST) is refused without the right token."""
    if request.method == 'POST':
        require_csrf()


def validate_moderation_form():
    reason = request.form.get('reason', '').strip()
    if not 3 <= len(reason) <= 1000:
        abort(400, 'Enter a reason between 3 and 1,000 characters.')
    return reason


try:
    tables.ensure_ready()
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

    products, _ = shop.products_for_sale(accounts.current_user()["id"] if accounts.current_user() else None)

    return render_template(
        "homepage.html",
        products=products,
        ratings=shop.product_ratings(),
    )

# PRODUCT CATALOG PAGE (Dulu products.py)

@app.route("/products")
def product_catalog():
    """The shop. Only approved listings are on sale, so a student's
    submission stays invisible until an admin has passed it."""

    category_query = request.args.get("category", "all")
    search_query = request.args.get("search", "").strip()
    sort_query = request.args.get("sort", "default")
    user = accounts.current_user()

    products, categories = shop.products_for_sale(user["id"] if user else None,
                                                  category_query, search_query, sort_query)

    return render_template(
        "products.html",
        products=products,
        categories=categories,
        selected_category=category_query,
        search=search_query,
        sort=sort_query,
        ratings=shop.product_ratings(),
    )


@app.route("/dashboard")
@accounts.admin_required
def dashboard():

    stats = admin.get_dashboard_stats()
    recent_products = admin.get_all_products()[:5]

    return render_template(
        "dashboard.html",
        total_products=stats["total_products"],
        pending_products=stats["pending_products"],
        reported_products=stats["reported_products"],
        approved_products=stats["approved_products"],
        total_users=admin.get_user_stats()["total"],
        recent_products=recent_products,
        chart=admin.build_chart_days(admin.get_listings_per_day()),
        latest_reviews=shop.latest_reviews(),
    )

@app.route("/listings")
@accounts.admin_required
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
        listing_stats=admin.get_listing_stats(),
        review_product=review_product,
        review_image=review_image,
        review_rating=shop.review_summary(review_product["id"]) if review_product else None,
        seller_accounts=admin.get_all_users() if edit_id else [],
    )

@app.route("/listings/edit/<int:product_id>", methods=["POST"])
@accounts.admin_required
def edit_product(product_id):

    seller_value = request.form.get('seller_id', '').strip()
    try:
        seller_id = int(seller_value) if seller_value else None
    except ValueError:
        abort(400, 'Choose a valid seller account.')
    if seller_id is not None and not admin.user_exists(seller_id):
        abort(400, 'Seller account does not exist.')

    try:
        details = shop.read_listing(request.form, "name")
    except ValueError as problem:
        flash(str(problem), "error")
        return redirect(url_for("listings", edit=product_id))

    admin.update_product(
        product_id,
        name=details["name"],
        seller_id=seller_id,
        category=details["category"],
        price=details["price"],
        stock=details["stock"],
    )

    return redirect(url_for("listings"))

@app.route("/listings/status/<int:product_id>/<status>", methods=["POST"])
@accounts.admin_required
def change_product_status(product_id, status):

    if status not in admin.PRODUCT_STATUSES:
        abort(400)

    if status == "Reported":
        return redirect(url_for("report_listing", product_id=product_id))
    if admin.has_open_reports(product_id):
        return redirect(url_for("moderation_queue", product=product_id))
    admin.set_product_status(product_id, status)
    shop.notify_listing_decided(product_id, status)

    return redirect(url_for("listings"))

@app.route("/listings/delete/<int:product_id>")
@accounts.admin_required
def delete_product(product_id):

    if admin.has_open_reports(product_id):
        return redirect(url_for("moderation_queue", product=product_id))
    return redirect(url_for("listings", view=product_id))


@app.route("/listings/remove/<int:product_id>", methods=["POST"])
@accounts.admin_required
def remove_product(product_id):
    reason = validate_moderation_form()
    if admin.get_product(product_id) is None:
        abort(404)
    if admin.has_open_reports(product_id):
        return resolve_listing_reports(product_id)
    admin.remove_listing(product_id, reason, session['user'])
    shop.notify_listing_decided(product_id, 'Removed', reason)
    flash('Listing removed from sale. Its details and history are preserved.')
    return redirect(url_for('listings', view=product_id))

@app.route("/listings/stock/<int:product_id>/<action>", methods=["POST"])
@accounts.admin_required
def adjust_product_stock(product_id, action):

    if action not in {"increase", "decrease"}:
        abort(400)
    amount = 1 if action == "increase" else -1
    admin.adjust_stock(product_id, amount)

    return redirect(url_for("listings"))

@app.route("/categories/rename", methods=["POST"])
@accounts.admin_required
def rename_category():

    try:
        old_name, new_name = admin.read_category_rename(request.form)
    except ValueError as problem:
        flash(str(problem), "error")
        return redirect(url_for("listings"))

    admin.rename_category(old_name, new_name)

    return redirect(url_for("listings"))

@app.route("/tiers/add", methods=["POST"])
@accounts.admin_required
def add_tier():

    try:
        min_subtotal, percent = admin.read_discount_tier(request.form)
    except ValueError as problem:
        flash(str(problem), "error")
        return redirect(url_for("listings"))

    admin.add_discount_tier(min_subtotal=min_subtotal, discount_percent=percent)

    return redirect(url_for("listings"))

@app.route("/tiers/delete/<int:tier_id>", methods=["POST"])
@accounts.admin_required
def delete_tier(tier_id):

    admin.delete_discount_tier(tier_id)

    return redirect(url_for("listings"))

@app.route("/reviews/delete/<int:review_id>", methods=["POST"])
@accounts.admin_required
def delete_review(review_id):

    admin.delete_review(review_id)
    flash("Review deleted.")

    return redirect(url_for("listings", _anchor="product-reviews"))

@app.route("/reports")
@accounts.admin_required
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

@app.route("/reports/cancel/<int:order_id>", methods=["POST"])
@accounts.admin_required
def admin_cancel_order(order_id):
    """Cancel an entire order -- only for a problem like a report, not
    routine fulfilment. Sellers ship and deliver their own items;
    admin only steps in when something needs overriding."""

    if admin.admin_cancel_order(order_id):
        shop.notify_order_cancelled(order_id)

    # Land back on the Cancelled filter, not the unfiltered list --
    # otherwise the page looks like nothing happened even though it
    # did, since the dropdown resets to "All Orders" either way.
    return redirect(url_for("reports", status="Cancelled"))

@app.route("/reports/reinstate/<int:order_id>", methods=["POST"])
@accounts.admin_required
def admin_reinstate_order(order_id):

    if admin.admin_reinstate_order(order_id):
        shop.notify_order_reinstated(order_id)

    return redirect(url_for("reports"))

@app.route("/users")
@accounts.admin_required
def users():

    search = request.args.get("search", "")

    return render_template(
        "user.html",
        users=admin.get_all_users(search),
        user_stats=admin.get_user_stats(),
        search=search,
    )


@app.route("/add-to-cart/<int:product_id>", methods=["POST"])
@accounts.shopper_required
def add_to_cart(product_id):
    product = admin.get_product(product_id)
    if not admin.available(product):
        abort(404)
    if product.get('seller_id') == accounts.current_user()['id']:
        abort(403, 'You cannot add your own listing to the cart.')
    shop.add_cart_item(accounts.current_user()['id'], product_id)
    return redirect(url_for('view_cart'))


@app.route('/cart')
@accounts.shopper_required
def view_cart():
    shop.remove_owned_cart_items(accounts.current_user()['id'])
    cart = shop.get_cart(accounts.current_user()['id'])
    return render_template('cart.html', cart=cart, subtotal=shop.price_cart(cart)['subtotal'])


@app.route('/increase/<int:product_id>', methods=['POST'])
@accounts.shopper_required
def increase_quantity(product_id):
    product = admin.get_product(product_id)
    if product and product.get('seller_id') == accounts.current_user()['id']:
        abort(403, 'You cannot buy your own listing.')
    shop.add_cart_item(accounts.current_user()['id'], product_id, existing_only=True)
    return redirect(url_for('view_cart'))


@app.route('/decrease/<int:product_id>', methods=['POST'])
@accounts.shopper_required
def decrease_quantity(product_id):
    shop.decrease_cart_item(accounts.current_user()['id'], product_id)
    return redirect(url_for('view_cart'))


@app.route('/remove/<int:product_id>', methods=['POST'])
@accounts.shopper_required
def remove_from_cart(product_id):
    shop.remove_cart_item(accounts.current_user()['id'], product_id)
    return redirect(url_for('view_cart'))


@app.route('/checkout')
@accounts.shopper_required
def checkout():
    shop.remove_owned_cart_items(accounts.current_user()['id'])
    cart = shop.get_cart(accounts.current_user()['id'])
    if not cart:
        return redirect(url_for('view_cart'))
    return render_template('checkout.html', cart=cart, **shop.price_cart(cart))


@app.route('/place-order', methods=['POST'])
@accounts.shopper_required
def place_order():
    user = accounts.current_user()
    shop.remove_owned_cart_items(user['id'])
    cart = shop.get_cart(user['id'])
    if not cart:
        return redirect(url_for('view_cart'))
    name = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()
    address = request.form.get('address', '').strip()
    pricing = shop.price_cart(cart)

    error = shop.check_delivery_details(name, phone, address)
    if error:
        return render_template('checkout.html', cart=cart, error=error, form=request.form, **pricing), 400

    order_id = shop.checkout_cart(user['id'], cart, pricing, name, phone, address)
    if order_id is None:
        abort(409, 'Your cart or an item’s availability changed. Review your cart before trying again.')
    shop.notify_new_order(order_id)
    return render_template('order_confirmation.html', order_id=order_id,
                           name=name, phone=phone, address=address,
                           subtotal=pricing['subtotal'], discount=pricing['discount'], total=pricing['total'])


@app.route("/orders")
def view_orders():

    user = accounts.current_user()
    if user is None:
        return redirect(url_for("login"))
    return render_template("orders.html", orders=shop.buyer_orders(user["id"]),
                           my_reviews=shop.my_reviews(user["id"]))

@app.route("/profile")
def profile():

    if 'user' not in session:
        return redirect(url_for('login'))

    user = accounts.current_user()

    if user is None:
        return redirect(url_for('logout'))

    return render_template("userprofile.html", user=dict(user), recent_orders=shop.recent_orders(user["id"]))


@app.route("/notifications/<int:notification_id>")
def open_notification(notification_id):
    user = accounts.current_user()
    if user is None:
        return redirect(url_for("login"))
    link = shop.open_notification(notification_id, user["id"])
    if link is None:
        abort(404)
    return redirect(link)

@app.route("/notifications/read-all", methods=["POST"])
def read_all_notifications():
    user = accounts.current_user()
    if user is None:
        return redirect(url_for("login"))
    shop.mark_notifications_read(user["id"])
    # Only ever back to a page on this site.
    back = request.form.get("next", "")
    return redirect(back if back.startswith("/") and not back.startswith("//") else url_for("home"))

@app.route("/products/<int:product_id>/reviews")
def product_reviews(product_id):
    """Everything buyers have said about one listing."""

    product = admin.get_product(product_id)

    # Shown while the listing is on sale, and always to its own seller.
    user = accounts.current_user()
    if product is None or (not admin.available(product) and not (user and product["seller_id"] == user["id"])):
        abort(404)

    return render_template(
        "product_reviews.html",
        product=product,
        reviews=shop.reviews_for_product(product_id),
        summary=shop.review_summary(product_id),
        seller_rating=shop.seller_rating(product["seller_id"]) if product["seller_id"] else None,
    )

@app.route("/orders/items/<int:item_id>/review", methods=["GET", "POST"])
def write_review(item_id):
    """Rate something you bought, once it has been delivered."""

    user = accounts.current_user()
    if user is None:
        return redirect(url_for("login"))

    bought = shop.purchase_to_review(item_id, user["id"])
    if bought is None:
        abort(404)

    # Afterwards, show the review on the listing -- or My Orders if the
    # listing has since been taken down.
    if admin.available(admin.get_product(bought["product_id"])):
        done = url_for("product_reviews", product_id=bought["product_id"], _anchor="reviews")
    else:
        done = url_for("view_orders")

    if bought["review_id"]:
        return redirect(done)
    if bought["status"] != "Delivered":
        abort(400, "You can review this once it has been delivered.")

    if request.method == "POST":
        try:
            rating, comment = shop.clean_review(request.form.get("rating"), request.form.get("comment"))
        except ValueError as problem:
            return render_template("write_review.html", item=bought, error=str(problem),
                                   form=request.form), 400
        shop.add_review(bought, user, rating, comment)
        shop.notify_review_left(item_id)
        return redirect(done)

    return render_template("write_review.html", item=bought, form={})

@app.route("/messages")
def messages_inbox():
    user = accounts.current_user()

    if user is None:
        return redirect(url_for('login'))

    return render_template("messages.html", conversations=shop.inbox(user["id"]))

@app.route("/messages/new/<int:product_id>", methods=["GET", "POST"])
def message_seller(product_id):
    """Start a chat with the seller of this listing. Nothing is saved until
    the first message is sent, so browsing doesn't leave empty chats."""

    user = accounts.current_user()

    if user is None:
        return redirect(url_for('login'))

    product = admin.get_product(product_id)

    if not admin.available(product) or product["seller_id"] is None:
        abort(404)

    if product["seller_id"] == user["id"]:
        abort(400, "This is your own listing.")

    if request.method == "POST":
        body = shop.clean_message(request.form.get("body"))
        if body is None:
            return render_template("conversation.html", conversation=None, product=product,
                                   messages=[], error="Write a message first."), 400
        conversation_id = shop.start_chat(user["id"], product, body)
        return redirect(url_for("conversation", conversation_id=conversation_id))

    existing = shop.find_conversation(user["id"], product["seller_id"], product_id)
    if existing:
        return redirect(url_for("conversation", conversation_id=existing))

    return render_template("conversation.html", conversation=None, product=product, messages=[])

@app.route("/messages/<int:conversation_id>", methods=["GET", "POST"])
def conversation(conversation_id):
    user = accounts.current_user()

    if user is None:
        return redirect(url_for('login'))

    chat = shop.conversation_for(conversation_id, user["id"])

    # Someone else's chat looks the same as one that doesn't exist.
    if chat is None:
        abort(404)

    if request.method == "POST":
        body = shop.clean_message(request.form.get("body"))
        if body is not None:
            shop.send_message(conversation_id, user["id"], body)
        return redirect(url_for("conversation", conversation_id=conversation_id) + "#latest")

    shop.mark_chat_read(conversation_id, user["id"])

    return render_template(
        "conversation.html",
        conversation=chat,
        product=admin.get_product(chat["product_id"]) if chat["product_id"] else None,
        messages=shop.chat_messages(conversation_id),
    )

@app.route("/messages/<int:conversation_id>/updates")
def conversation_updates(conversation_id):
    """New messages since the last one on screen, for an open chat to poll."""

    user = accounts.current_user()

    if user is None:
        abort(401)

    if shop.conversation_for(conversation_id, user["id"]) is None:
        abort(404)

    new = shop.chat_messages(conversation_id, request.args.get("after", 0, type=int))

    if new:
        shop.mark_chat_read(conversation_id, user["id"])

    return {"messages": [
        {"id": m["id"], "mine": m["sender_id"] == user["id"],
         "body": m["body"], "time": shop.local_time(m["created_at"])}
        for m in new
    ]}

@app.route("/my-shop")
def seller_page():
    """A seller's own listings and sales. Everything here is scoped to
    the signed-in account -- a seller never sees another's items."""

    user = accounts.current_user()

    if user is None:
        return redirect(url_for('login'))

    return render_my_shop(user)

def render_my_shop(user, sell_error=None, sell_form=None):
    """My Shop, optionally with a problem shown on the Sell form and what
    the seller had typed put back, so they don't have to start again."""

    return render_template(
        "sellerpage.html",
        user=dict(user),
        listings=admin.get_all_products(seller_id=user["id"]),
        sales=shop.get_seller_orders(user["id"]),
        summary=shop.get_seller_summary(user["id"]),
        categories=admin.get_all_categories(),
        ratings=shop.product_ratings(),
        seller_rating=shop.seller_rating(user["id"]),
        sell_error=sell_error,
        sell_form=sell_form or {},
    ), 400 if sell_error else 200

@app.route("/my-shop/stock/<int:product_id>/<action>", methods=["POST"])
def seller_adjust_stock(product_id, action):
    """Restock or reduce one of your own listings."""

    user = accounts.current_user()

    if user is None:
        return redirect(url_for('login'))

    product = admin.get_product(product_id)

    # Owning the listing is the whole authorisation check -- without it
    # anyone signed in could edit anyone else's stock by guessing an id.
    if product is None or product["seller_id"] != user["id"]:
        abort(403)
    if action not in {"increase", "decrease"}:
        abort(400)

    admin.adjust_stock(product_id, 1 if action == "increase" else -1)

    return redirect(url_for("seller_page"))

@app.route("/my-shop/delete/<int:product_id>", methods=["POST"])
def seller_delete_listing(product_id):
    """Take your own listing down."""

    user = accounts.current_user()

    if user is None:
        return redirect(url_for('login'))

    product = admin.get_product(product_id)

    if product is None or product["seller_id"] != user["id"]:
        abort(403)

    if admin.has_open_reports(product_id):
        abort(409, 'This listing is under review and cannot be deleted until the reports are resolved.')
    admin.delete_product(product_id)

    return redirect(url_for("seller_page"))

@app.route("/my-shop/orders/status/<int:item_id>/<status>", methods=["POST"])
def seller_update_order_status(item_id, status):
    """A seller shipping or delivering one of their own sold items.

    Ownership is the authorisation check: the line has to belong to a
    product this seller listed, or anyone signed in could move any
    seller's order by guessing an item id."""

    user = accounts.current_user()

    if user is None:
        return redirect(url_for('login'))

    item = shop.get_order_item(item_id)

    if item is None or item["seller_id"] != user["id"]:
        abort(403)

    if not shop.update_order_item_status(item_id, status):
        abort(400)
    shop.notify_order_item_moved(item_id)

    return redirect(url_for("seller_page"))

@app.route("/request-sell", methods=["POST"])
def request_sell():
    """A student submitting their own item. It goes in as Pending so it
    lands in the admin approval queue rather than straight on the shop."""

    if 'user' not in session:
        return redirect(url_for('login'))

    user = accounts.current_user()

    if user is None:
        return redirect(url_for('logout'))

    # Check the details before saving the photo, so a mistake doesn't
    # leave an unused photo behind in the uploads folder.
    try:
        details = shop.read_listing(request.form, "product_name", min_stock=1)
        if details["category"] not in shop.SELL_CATEGORIES:
            raise ValueError("Choose a category from the list.")
        image_url = shop.save_product_image(request.files.get("image_file"))
    except ValueError as problem:
        return render_my_shop(user, sell_error=str(problem), sell_form=request.form)

    admin.add_product(
        name=details["name"],
        seller=user["fullname"],
        seller_id=user["id"],
        category=details["category"],
        price=details["price"],
        stock=details["stock"],
        status="Pending",
        description=details["description"],
        image_url=image_url,
    )

    return redirect(url_for("seller_page"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = request.form.get("remember")

        user, error = accounts.check_student_login(email, password)
        if error:
            return render_template("login.html", error=error), 403 if "suspended" in error else 200

        accounts.log_in(user, remember=bool(remember))
        if user["reactivated"]:
            shop.notify(user["id"], "Welcome back! Your account is active again and your listings are back in the shop.", "/my-shop")

        return redirect(url_for("home"))

    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        fullname = request.form.get("fullname", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        error = accounts.check_sign_up(fullname, email, password, confirm_password)
        if error:
            return render_template("register.html", error=error, fullname=fullname, email=email)

        accounts.sign_up(fullname, email, password)

        return redirect(url_for("login"))

    return render_template("register.html")

@app.route("/logout")
def logout():
    if accounts.log_out():
        return redirect(url_for("admin_login"))
    return redirect(url_for("login"))

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not accounts.check_admin_login(email, password):
            return render_template("admin_login.html", error="Incorrect email or password.")

        accounts.log_in_admin(email)

        return redirect(url_for("dashboard"))

    return render_template("admin_login.html")

@app.route('/users/<int:user_id>')
@accounts.admin_required
def user_detail(user_id):
    details = admin.user_details(user_id)
    if details is None:
        abort(404)
    return render_template('user.html', detail_view=True, **details, protected=accounts.is_admin_email(details['user']['email']))


@app.route('/users/<int:user_id>/status', methods=['POST'])
@accounts.admin_required
def user_status(user_id):
    reason = validate_moderation_form()
    details = admin.user_details(user_id)
    if details is None:
        abort(404)
    if accounts.is_admin_email(details['user']['email']) or details['user']['role'] == 'admin':
        abort(403, 'Administrator accounts cannot be suspended here.')
    if details['user']['account_status'] == 'Deleted':
        abort(400, 'This student deleted their account.')
    action = request.form.get('action')
    if action not in {'Suspend', 'Reactivate'}:
        abort(400)
    admin.change_account(user_id, action, reason, session['user'])
    flash('Account suspended.' if action == 'Suspend' else 'Account reactivated.')
    return redirect(url_for('user_detail', user_id=user_id))


@app.route('/moderation')
@accounts.admin_required
def moderation_queue():
    state = request.args.get('status', 'All')
    if state not in {'Open', 'Dismissed', 'Removed', 'All'}:
        abort(400)
    product_id = request.args.get('product', type=int)
    reports = admin.get_listing_reports(state, product_id)
    return render_template('moderation.html', reports=reports, selected_status=state, product_id=product_id)


@app.route('/moderation/<int:product_id>/resolve', methods=['POST'])
@accounts.admin_required
def resolve_listing_reports(product_id):
    reason = validate_moderation_form()
    decision = request.form.get('decision')
    if decision not in {'Dismissed', 'Removed'}:
        abort(400)
    admin.resolve_reports(product_id, decision, reason, session['user'])
    if decision == 'Removed':
        shop.notify_listing_decided(product_id, 'Removed', reason)
    flash('Reports resolved. The decision has been saved in history.')
    return redirect(url_for('moderation_queue', status=decision, product=product_id))


@app.route('/products/<int:product_id>/report', methods=['GET', 'POST'])
def report_listing(product_id):
    user = accounts.current_user()
    is_admin = accounts.is_admin()
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


# ======================================================================
# SETTINGS (the gear in the top menu)
# ======================================================================

@app.route("/settings")
@accounts.shopper_required
def settings():
    return render_template("settings.html")


@app.route("/settings/profile", methods=["GET", "POST"])
@accounts.shopper_required
def settings_profile():
    """Edit Profile: your name and email."""
    user = accounts.current_user()
    if request.method == "POST":
        fullname = request.form.get("fullname", "").strip()
        email = request.form.get("email", "").strip().lower()
        error = accounts.check_profile_update(user, fullname, email, request.form.get("password", ""))
        if error:
            return render_template("settings_profile.html", error=error, form=request.form), 400
        accounts.update_profile(user, fullname, email)
        flash("Your profile has been updated.")
        return redirect(url_for("settings_profile"))
    return render_template("settings_profile.html", form=dict(user))


@app.route("/settings/security", methods=["GET", "POST"])
@accounts.shopper_required
def settings_security():
    """Login & Security: change your password."""
    user = accounts.current_user()
    if request.method == "POST":
        new = request.form.get("new_password", "")
        error = accounts.check_password_change(user, request.form.get("current_password", ""),
                                               new, request.form.get("confirm_password", ""))
        if error:
            return render_template("settings_security.html", error=error), 400
        accounts.change_password(user, new)
        flash("Your password has been changed.")
        return redirect(url_for("settings_security"))
    return render_template("settings_security.html")


@app.route("/settings/theme", methods=["POST"])
@accounts.shopper_required
def settings_theme():
    """Theme Mode: light or dark, saved on your account."""
    accounts.set_theme(accounts.current_user(), request.form.get("theme"))
    return redirect(url_for("settings"))


@app.route("/settings/help")
@accounts.shopper_required
def help_center():
    """Help Center: pick Buying or Selling."""
    return render_template("help_center.html", topics=shop.HELP_TOPICS)


@app.route("/settings/help/<topic>")
@accounts.shopper_required
def help_topic(topic):
    """The 10 questions and answers for buying or for selling."""
    if topic not in shop.HELP_TOPICS:
        abort(404)
    return render_template("help_topic.html", topic=shop.HELP_TOPICS[topic], key=topic,
                           other=[(key, value["title"]) for key, value in shop.HELP_TOPICS.items() if key != topic])


@app.route("/settings/contact", methods=["GET", "POST"])
@accounts.shopper_required
def contact_us():
    """Contact Us: chat with the CampusCart admins."""
    user = accounts.current_user()
    if request.method == "POST":
        body = shop.clean_message(request.form.get("body"))
        if body is None:
            return render_template("contact_us.html", messages=shop.support_thread(user["id"]),
                                   error="Write a message first (up to 1,000 characters)."), 400
        shop.send_support_message(user["id"], body)
        return redirect(url_for("contact_us", _anchor="latest"))
    shop.mark_support_read(user["id"])
    return render_template("contact_us.html", messages=shop.support_thread(user["id"]))


@app.route("/settings/privacy", methods=["GET", "POST"])
@accounts.shopper_required
def data_privacy():
    """Data & Privacy: deactivate (undo by logging in) or delete for good."""
    user = accounts.current_user()
    if request.method == "POST":
        action = request.form.get("action")
        if action not in {"deactivate", "delete"}:
            abort(400)
        error = accounts.check_account_closing(user, request.form.get("password", ""),
                                               request.form.get("confirm", ""), deleting=action == "delete")
        if error:
            return render_template("data_privacy.html", error=error, failed=action,
                                   open_orders=accounts.open_orders(user["id"])), 400
        if action == "deactivate":
            accounts.deactivate_account(user)
            return render_template("login.html", error="Your account is deactivated. Log in again any time to switch it back on.")
        accounts.delete_account(user)
        return render_template("login.html", error="Your account has been deleted. Thank you for using CampusCart.")
    return render_template("data_privacy.html", open_orders=accounts.open_orders(user["id"]))


# ======================================================================
# ADMIN > SUPPORT (answering Contact Us messages)
# ======================================================================

@app.route("/support")
@accounts.admin_required
def support_inbox():
    return render_template("support.html", threads=admin.support_inbox(), student=None)


@app.route("/support/<int:user_id>", methods=["GET", "POST"])
@accounts.admin_required
def support_thread(user_id):
    details = admin.user_details(user_id)
    if details is None:
        abort(404)
    if request.method == "POST":
        body = shop.clean_message(request.form.get("body"))
        if body is not None:
            admin.reply_to_student(user_id, session["user"], body)
            shop.notify(user_id, "CampusCart support replied to your message.", "/settings/contact")
        return redirect(url_for("support_thread", user_id=user_id, _anchor="latest"))
    admin.mark_support_read_by_admin(user_id)
    return render_template("support.html", threads=admin.support_inbox(), student=details["user"],
                           messages=shop.support_thread(user_id))



@app.errorhandler(500)
def server_error(error):
    """Shown if something still breaks (e.g. Turso is really down) -- a
    friendly page with a Try again button, instead of plain grey text.
    The full error is still printed in the terminal running app.py."""
    return render_template("error.html"), 500

if __name__ == "__main__":
    # Debug mode shows an in-browser console that can run code, so it is
    # only switched on deliberately (FLASK_DEBUG=1 in .env), never by default.
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
