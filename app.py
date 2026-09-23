import os
import sys
from datetime import date, timedelta
from functools import wraps

from flask import Flask, render_template, redirect, url_for, request, session, abort
from werkzeug.security import generate_password_hash, check_password_hash

import database
import admin_functions as admin

app = Flask(__name__)
app.secret_key = 'campuscart_secret_key_bebas_tukar'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

# The only statuses the admin links are allowed to set.
PRODUCT_STATUSES = {"Approved", "Pending", "Rejected", "Reported"}
ORDER_STATUSES = {"Pending", "Shipped", "Delivered", "Cancelled"}

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

# Menggantikan products.json & products.py
products = [
    {
        "id": 1,
        "name": "Scientific Calculator FX-570EX",
        "category": "electronics",
        "price": 65.00,
        "stock": 5,
        "rating": 4.8,
        "image": "https://images.unsplash.com/photo-1587145820266-a5951ee6f620?w=400"
    },
    {
        "id": 2,
        "name": "Basic Python & Data Structures Book",
        "category": "books",
        "price": 25.00,
        "stock": 2,
        "rating": 5.0,
        "image": "https://images.unsplash.com/photo-1532012197267-da84d127e765?w=400"
    },
    {
        "id": 3,
        "name": "Rechargeable LED Desk Lamp",
        "category": "accessories",
        "price": 18.00,
        "stock": 10,
        "rating": 4.5,
        "image": "https://images.unsplash.com/photo-1507473885765-e6ed057f782c?w=400"
    },
    {
        "id": 4,
        "name": "Campus Hoodie (Size L)",
        "category": "fashion",
        "price": 45.00,
        "stock": 4,
        "rating": 4.7,
        "image": "https://images.unsplash.com/photo-1556905055-8f358a7a47b2?w=400"
    }
]

cart = {}

# HOME PAGE (Dah dikunci)

@app.route("/")
def home():
    # Semak sama ada user dah login atau belum
    if 'user' not in session:
        return redirect(url_for('login'))

    return render_template("homepage.html", products=products)

# PRODUCT CATALOG PAGE (Dulu products.py)

@app.route("/products")
def product_catalog():
    # Tangkap parameter search & filter dari URL
    category_query = request.args.get('category', 'all')
    search_query = request.args.get('search', '')
    sort_query = request.args.get('sort', 'default')

    filtered_list = []

    # Filter Logic
    for product in products:
        matches_category = (category_query == "all" or product['category'].lower() == category_query.lower())
        matches_search = (search_query.lower() in product['name'].lower())

        if matches_category and matches_search:
            filtered_list.append(product)

    # Sorting Logic
    if sort_query == "low-high":
        filtered_list = sorted(filtered_list, key=lambda x: x['price'])
    elif sort_query == "high-low":
        filtered_list = sorted(filtered_list, key=lambda x: x['price'], reverse=True)

    return render_template("products.html", products=filtered_list)

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
        discount_codes=admin.get_all_discount_codes(),
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

@app.route("/discounts/add", methods=["POST"])
@admin_required
def add_discount():

    admin.add_discount_code(
        code=request.form["code"],
        discount_percent=float(request.form["discount_percent"]),
    )

    return redirect(url_for("listings"))

@app.route("/discounts/toggle/<int:discount_id>")
@admin_required
def toggle_discount(discount_id):

    admin.toggle_discount_code(discount_id)

    return redirect(url_for("listings"))

@app.route("/discounts/delete/<int:discount_id>")
@admin_required
def delete_discount(discount_id):

    admin.delete_discount_code(discount_id)

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
    )

@app.route("/reports/status/<int:order_id>/<status>")
@admin_required
def update_order_status(order_id, status):

    if status not in ORDER_STATUSES:
        abort(400)

    admin.update_order_status(order_id, status)

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
    product = next((p for p in products if p["id"] == product_id), None)

    if product is None:
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
    product = next((p for p in products if p["id"] == product_id), None)
    if product_id in cart and cart[product_id]["quantity"] < product["stock"]:
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

@app.route("/checkout")
def checkout():

    if not cart:
        return redirect(url_for("view_cart"))

    subtotal = 0

    for item in cart.values():
        subtotal += item["price"] * item["quantity"]

    discount = 0

    total = subtotal - discount

    return render_template(
        "checkout.html",
        cart=cart,
        subtotal=subtotal,
        discount=discount,
        total=total
    )
@app.route("/apply-promo", methods=["POST"])
def apply_promo():

    if not cart:
        return redirect(url_for("view_cart"))

    # Get the promo code entered by the customer
    promo_code = request.form.get("promo_code", "").strip().upper()

    # Calculate subtotal
    subtotal = 0

    for item in cart.values():
        subtotal += item["price"] * item["quantity"]

    # Default values
    discount = 0
    discount_percent = 0

    # Connect to database
    connection = database.get_connection()
    cursor = connection.cursor()

    # Check whether promo code exists and is active
    cursor.execute("""
        SELECT discount_percent
        FROM discount_codes
        WHERE code = ? AND active = 1
    """, (promo_code,))

    result = cursor.fetchone()

    connection.close()

    # If promo code is valid
    if result:

        discount_percent = result[0]

        discount = subtotal * (discount_percent / 100)

        total = subtotal - discount

        message = (
            f"Promo code applied! "
            f"You saved RM {discount:.2f}."
        )

    # If promo code is invalid
    else:

        discount = 0
        total = subtotal

        message = "Invalid or inactive promo code."

    return render_template(
        "checkout.html",
        cart=cart,
        subtotal=subtotal,
        discount=discount,
        discount_percent=discount_percent,
        total=total,
        promo_code=promo_code,
        message=message
    )

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
    # CALCULATE SUBTOTAL
    # =========================================

    subtotal = 0

    for item in cart.values():
        subtotal += item["price"] * item["quantity"]

    # =========================================
    # GET PROMO CODE
    # =========================================

    promo_code = request.form.get("promo_code", "").strip().upper()

    # Default discount
    discount = 0

    # =========================================
    # CHECK PROMO CODE
    # =========================================

    if promo_code:

        connection = database.get_connection()
        cursor = connection.cursor()

        cursor.execute("""
            SELECT discount_percent
            FROM discount_codes
            WHERE code = ? AND active = 1
        """, (promo_code,))

        result = cursor.fetchone()

        connection.close()

        # Apply discount if promo code is valid
        if result:
            discount_percent = result[0]

            discount = subtotal * (discount_percent / 100)

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
        image_url=request.form.get("image_url") or None,
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