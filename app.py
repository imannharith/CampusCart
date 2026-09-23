import os
import sys
from datetime import date, timedelta
from functools import wraps
from uuid import uuid4

from flask import Flask, render_template, redirect, url_for, request, session, abort
from werkzeug.security import generate_password_hash, check_password_hash

import database
import admin_functions as admin

app = Flask(__name__)
app.secret_key = 'campuscart_secret_key_bebas_tukar'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

# The only statuses the admin links are allowed to set.
PRODUCT_STATUSES = {"Approved", "Pending", "Rejected", "Reported"}

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "uploads")
ALLOWED_IMAGE_TYPES = {"png", "jpg", "jpeg", "gif", "webp"}

def save_product_image(upload):
    """Store an uploaded product photo and return the path to show it
    at, or None if nothing usable was sent.

    The file is saved under a random name keeping only its extension.
    A name chosen by whoever uploaded it must never reach the
    filesystem, and two students both uploading 'photo.jpg' must not
    overwrite each other."""

    if upload is None or not upload.filename or "." not in upload.filename:
        return None

    extension = upload.filename.rsplit(".", 1)[-1].lower()

    if extension not in ALLOWED_IMAGE_TYPES:
        return None

    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    stored_name = f"{uuid4().hex}.{extension}"
    upload.save(os.path.join(UPLOAD_FOLDER, stored_name))

    return url_for("static", filename=f"uploads/{stored_name}")

ADMIN_ACCOUNTS = {
    "zikryman123@gmail.com": "scrypt:32768:8:1$lfJSd8WxZnbJAX6j$6f91f9a88d7409a67970259ce6276ff095810bc16cd24733ca514d155e99d76c6e71cfb2646cd17a0257080664097647ba8998330a562ec1da1465d30bf9e8bf",
    "imannhairurizal@gmail.com": "scrypt:32768:8:1$nDtoMhQAZUKDqApr$603d72c49e6f8f568f62a6c2fa53a5d4ab945cbfb450f1f69ae9ca1e3e3a96fc12ea0b3605370d567651dcb3a64549717bca3872b676d40682af04d8586726f8",
    "priyaankavi0703@gmail.com": "scrypt:32768:8:1$NAoOq6JLC75PWzFu$2f8166558a27e2bc51af2fe58aa0e3eaedf0ccb00f572556ed26d9e2312d9aa6172a09465970abbdbacb9c6521d88db8235665c6ae051749b6b69409c722f8c4",
}

def admin_required(view):

    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get('role') != 'admin':
            return redirect(url_for('admin_login'))
        return view(*args, **kwargs)
    return wrapped

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

cart = {}

# HOME PAGE (Dah dikunci)

@app.route("/")
def home():
    # Semak sama ada user dah login atau belum
    if 'user' not in session:
        return redirect(url_for('login'))

    return render_template(
        "homepage.html",
        products=admin.get_all_products(status="Approved"),
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
    approved = admin.get_all_products(status="Approved")

    filtered_list = [
        product for product in approved
        if (category_query == "all"
            or product["category"].lower() == category_query.lower())
        and search_query.lower() in product["name"].lower()
    ]

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
        dead_listings=admin.get_dead_listings(),
        chart=build_chart_days(admin.get_listings_per_day()),
    )

@app.route("/listings")
@admin_required
def listings():

    category = request.args.get("category", "all")
    status = request.args.get("status", "all")
    search = request.args.get("search", "")
    edit_id = request.args.get("edit", type=int)

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
    )

@app.route("/listings/add", methods=["POST"])
@admin_required
def add_product():

    admin.add_product(
        name=request.form["name"],
        seller=request.form["seller"],
        category=request.form["category"],
        price=float(request.form["price"]),
        stock=int(request.form["stock"]),
        status="Pending",
    )

    return redirect(url_for("listings"))

@app.route("/listings/edit/<int:product_id>", methods=["POST"])
@admin_required
def edit_product(product_id):

    admin.update_product(
        product_id,
        name=request.form["name"],
        seller=request.form["seller"],
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

    admin.set_product_status(product_id, status)

    return redirect(url_for("listings"))

@app.route("/listings/delete/<int:product_id>")
@admin_required
def delete_product(product_id):

    admin.delete_product(product_id)

    return redirect(url_for("listings"))

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
    )

@app.route("/reports/status/<int:order_id>/<status>")
@admin_required
def update_order_status(order_id, status):

    # False means that move isn't legal from where the order is now --
    # delivering something that was never shipped, say.
    if not admin.update_order_status(order_id, status):
        abort(400)

    return redirect(url_for("reports"))

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

@app.route("/add-to-cart/<int:product_id>")
def add_to_cart(product_id):
    product = admin.get_product(product_id)

    # Only approved listings can be bought -- a pending or rejected one
    # is not on sale, even if someone kept the link.
    if product is None or product["status"] != "Approved":
        return "Product not found", 404

    if product_id in cart:
        if cart[product_id]["quantity"] < product["stock"]:
            cart[product_id]["quantity"] += 1
    else:
        cart[product_id] = {
            "name": product["name"],
            "price": product["price"],
            "quantity": 1
        }

    return redirect(url_for("view_cart"))

@app.route("/cart")
def view_cart():
    subtotal = sum(item["price"] * item["quantity"] for item in cart.values())
    return render_template("cart.html", cart=cart, subtotal=subtotal)

@app.route("/increase/<int:product_id>")
def increase_quantity(product_id):
    product = admin.get_product(product_id)
    if product and product_id in cart and cart[product_id]["quantity"] < product["stock"]:
        cart[product_id]["quantity"] += 1
    return redirect(url_for("view_cart"))

@app.route("/decrease/<int:product_id>")
def decrease_quantity(product_id):
    if product_id in cart:
        cart[product_id]["quantity"] -= 1
        if cart[product_id]["quantity"] <= 0:
            del cart[product_id]
    return redirect(url_for("view_cart"))

@app.route("/remove/<int:product_id>")
def remove_from_cart(product_id):
    if product_id in cart:
        del cart[product_id]
    return redirect(url_for("view_cart"))

def price_cart():
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

@app.route("/checkout")
def checkout():

    if not cart:
        return redirect(url_for("view_cart"))

    return render_template("checkout.html", cart=cart, **price_cart())

@app.route("/place-order", methods=["POST"])
def place_order():

    # Make sure the cart is not empty
    if not cart:
        return redirect(url_for("view_cart"))

    # Temporary user ID
    user_id = 1

    # Get customer information
    name = request.form["name"]
    phone = request.form["phone"]
    address = request.form["address"]

    # =========================================
    # PRICE THE ORDER
    # =========================================
    # Same call the checkout page made, so the buyer is charged exactly
    # what they were shown.

    pricing = price_cart()

    subtotal = pricing["subtotal"]
    discount = pricing["discount"]

    # =========================================
    # CALCULATE FINAL TOTAL
    # =========================================

    total = subtotal - discount

    # =========================================
    # SAVE ORDER
    # =========================================

    connection = database.get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO orders
        (user_id, subtotal, discount, total, status)
        VALUES (?, ?, ?, ?, ?)
    """, (
        user_id,
        subtotal,
        discount,
        total,
        "Pending"
    ))

    order_id = cursor.lastrowid

    # =========================================
    # SAVE ORDER ITEMS
    # =========================================

    for product_id, item in cart.items():

        cursor.execute("""
            INSERT INTO order_items
            (order_id, product_id, quantity, price)
            VALUES (?, ?, ?, ?)
        """, (
            order_id,
            product_id,
            item["quantity"],
            item["price"]
        ))

        # Worked out in SQL rather than read-then-write, so two orders
        # placed at the same moment can't both read the same old stock
        # and each write it back one lower.
        cursor.execute("""
            UPDATE products
            SET stock = MAX(0, stock - ?)
            WHERE id = ?
        """, (item["quantity"], product_id))

    # Save changes
    connection.commit()
    connection.close()

    # =========================================
    # CLEAR CART
    # =========================================

    cart.clear()

    # =========================================
    # SHOW ORDER CONFIRMATION
    # =========================================

    return render_template(
        "order_confirmation.html",
        order_id=order_id,
        name=name,
        phone=phone,
        address=address,
        subtotal=subtotal,
        discount=discount,
        total=total
    )

@app.route("/orders")
def view_orders():

    user_id = 1

    connection = database.get_connection()

    cursor = connection.cursor()

    cursor.execute("""
        SELECT *
        FROM orders
        WHERE user_id = ?
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

    return render_template(
        "userprofile.html",
        user=dict(user),
        my_listings=admin.get_all_products(seller=user["fullname"]),
    )

def current_user():
    """The signed-in account, or None. Sellers are matched to their
    products by fullname, because products.seller is a name rather than
    a link to a user row."""

    if 'user' not in session:
        return None

    return database.get_user_by_email(session['user'])

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
        listings=admin.get_all_products(seller=user["fullname"]),
        sales=admin.get_seller_orders(user["fullname"]),
        summary=admin.get_seller_summary(user["fullname"]),
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
    if product is None or product["seller"] != user["fullname"]:
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

    if product is None or product["seller"] != user["fullname"]:
        abort(403)

    admin.delete_product(product_id)

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

    admin.add_product(
        name=request.form["product_name"],
        seller=user["fullname"],
        category=request.form["category"],
        price=float(request.form["price"]),
        stock=int(request.form["stock"]),
        status="Pending",
        description=request.form.get("description"),
        image_url=save_product_image(request.files.get("image_file")),
    )

    return redirect(url_for("profile"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        remember = request.form.get("remember")

        user = database.get_user_by_email(email)

        if user is None or not check_password_hash(user["password_hash"], password):
            return render_template("login.html", error="Incorrect email or password.")

        session.permanent = bool(remember)
        session['user'] = user["email"]
        session['role'] = user["role"]

        return redirect(url_for("home"))

    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        fullname = request.form.get("fullname")
        role = request.form.get("role")
        email = request.form.get("email")
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if password != confirm_password:
            return render_template("register.html", error="Passwords don't match.")

        if database.get_user_by_email(email) is not None:
            return render_template("register.html", error="An account with that email already exists.")

        database.create_user(fullname, email, generate_password_hash(password), role)

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

if __name__ == "__main__":
    app.run(debug=True)