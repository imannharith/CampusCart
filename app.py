from datetime import timedelta
from functools import wraps

from flask import Flask, render_template, redirect, url_for, request, session
from werkzeug.security import generate_password_hash, check_password_hash

import database
import admin_functions as admin

app = Flask(__name__)
app.secret_key = 'campuscart_secret_key_bebas_tukar'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)


# Hard-coded admin allowlist -- only these emails/passwords can reach
# the admin panel. Passwords are hashed here, never stored in plain
# text, even though the hashes themselves live in source control.
ADMIN_ACCOUNTS = {
    "zikryman123@gmail.com": "scrypt:32768:8:1$lfJSd8WxZnbJAX6j$6f91f9a88d7409a67970259ce6276ff095810bc16cd24733ca514d155e99d76c6e71cfb2646cd17a0257080664097647ba8998330a562ec1da1465d30bf9e8bf",
    "imannhairurizal@gmail.com": "scrypt:32768:8:1$nDtoMhQAZUKDqApr$603d72c49e6f8f568f62a6c2fa53a5d4ab945cbfb450f1f69ae9ca1e3e3a96fc12ea0b3605370d567651dcb3a64549717bca3872b676d40682af04d8586726f8",
    "priyaankavi0703@gmail.com": "scrypt:32768:8:1$NAoOq6JLC75PWzFu$2f8166558a27e2bc51af2fe58aa0e3eaedf0ccb00f572556ed26d9e2312d9aa6172a09465970abbdbacb9c6521d88db8235665c6ae051749b6b69409c722f8c4",
}


def admin_required(view):
    # Gate for the admin panel routes -- bounces anyone without an
    # admin session to the admin login page instead of rendering.
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get('role') != 'admin':
            return redirect(url_for('admin_login'))
        return view(*args, **kwargs)
    return wrapped

# Make sure the database and its tables exist, and seed a little
# sample data so the admin pages aren't empty on first run.
database.init_db()
database.seed_sample_data()


# -------------------------
# CENTRALIZED PRODUCTS DATA
# -------------------------
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


# -------------------------
# TEMPORARY SHOPPING CART
# -------------------------
cart = {}


# -------------------------
# HOME PAGE (Dah dikunci)
# -------------------------
@app.route("/")
def home():
    # Semak sama ada user dah login atau belum
    if 'user' not in session:
        return redirect(url_for('login'))
        
    return render_template("homepage.html", products=products)


# -------------------------
# PRODUCT CATALOG PAGE (Dulu products.py)
# -------------------------
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


# -------------------------
# ADMIN DASHBOARD
# -------------------------
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
        recent_products=recent_products,
    )


# -------------------------
# ADMIN LISTINGS (PRODUCT MANAGEMENT)
# -------------------------

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


# -------------------------
# ADD PRODUCT
# -------------------------

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


# -------------------------
# EDIT PRODUCT
# -------------------------

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


# -------------------------
# CHANGE PRODUCT STATUS (APPROVE / REJECT / REPORT)
# -------------------------

@app.route("/listings/status/<int:product_id>/<status>")
@admin_required
def change_product_status(product_id, status):

    admin.set_product_status(product_id, status)

    return redirect(url_for("listings"))


# -------------------------
# DELETE PRODUCT
# -------------------------

@app.route("/listings/delete/<int:product_id>")
@admin_required
def delete_product(product_id):

    admin.delete_product(product_id)

    return redirect(url_for("listings"))


# -------------------------
# QUICK STOCK ADJUST
# -------------------------

@app.route("/listings/stock/<int:product_id>/<action>")
@admin_required
def adjust_product_stock(product_id, action):

    amount = 1 if action == "increase" else -1
    admin.adjust_stock(product_id, amount)

    return redirect(url_for("listings"))


# -------------------------
# RENAME CATEGORY
# -------------------------

@app.route("/categories/rename", methods=["POST"])
@admin_required
def rename_category():

    admin.rename_category(
        request.form["old_name"],
        request.form["new_name"],
    )

    return redirect(url_for("listings"))


# -------------------------
# DISCOUNT CODES
# -------------------------

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


# -------------------------
# REVIEWS
# -------------------------

@app.route("/reviews/delete/<int:review_id>")
@admin_required
def delete_review(review_id):

    admin.delete_review(review_id)

    return redirect(url_for("listings"))


# -------------------------
# ORDER MANAGEMENT + SALES REPORTS
# -------------------------

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

    admin.update_order_status(order_id, status)

    return redirect(url_for("reports"))


# -------------------------
# MANAGE USERS (placeholder page)
# -------------------------

@app.route("/users")
@admin_required
def users():
    return render_template("user.html")


# -------------------------
# ADD TO CART
# -------------------------
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


# -------------------------
# VIEW CART
# -------------------------
@app.route("/cart")
def view_cart():
    subtotal = sum(item["price"] * item["quantity"] for item in cart.values())
    return render_template("cart.html", cart=cart, subtotal=subtotal)


# -------------------------
# INCREASE / DECREASE / REMOVE CART
# -------------------------
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

# -------------------------
# CHECKOUT
# -------------------------

@app.route("/checkout")
def checkout():

    # If cart is empty, go back to cart
    if not cart:
        return redirect(url_for("view_cart"))

    # Calculate subtotal
    subtotal = 0

    for item in cart.values():
        subtotal += item["price"] * item["quantity"]

    # No discount yet
    discount = 0

    # Final total
    total = subtotal - discount

    return render_template(
        "checkout.html",
        cart=cart,
        subtotal=subtotal,
        discount=discount,
        total=total
    )

# -------------------------
# PLACE ORDER
# -------------------------

@app.route("/place-order", methods=["POST"])
def place_order():

    # Don't place an empty order
    if not cart:
        return redirect(url_for("view_cart"))

    # Temporary user ID
    # Later this will come from the login system
    user_id = 1

    # Get customer information
    name = request.form["name"]
    phone = request.form["phone"]
    address = request.form["address"]

    # Calculate subtotal
    subtotal = 0

    for item in cart.values():
        subtotal += item["price"] * item["quantity"]

    # No discount yet
    discount = 0

    # Calculate final total
    total = subtotal - discount

    # Connect to database
    connection = database.get_connection()

    cursor = connection.cursor()

    # Create order
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

    # Get newly created order ID
    order_id = cursor.lastrowid


    # Add products to order_items
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


    connection.commit()
    connection.close()


    # Clear cart after successful order
    cart.clear()


    # Show confirmation
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

# -------------------------
# MY ORDERS
# -------------------------

@app.route("/orders")
def view_orders():

    # Temporary user ID
    # Later this will come from the login system
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

# -------------------------
# AUTHENTICATION ROUTES (LOGIN / REGISTER / LOGOUT)
# -------------------------

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

# -------------------------
# RUN APPLICATION
# -------------------------
if __name__ == "__main__":
    app.run(debug=True)