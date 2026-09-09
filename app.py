from flask import Flask, render_template, redirect, url_for, request

import database
import admin_functions as admin

app = Flask(__name__)

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
# HOME PAGE
# -------------------------
@app.route("/")
def home():
    # Pass products to homepage if you want to display featured items
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
def change_product_status(product_id, status):

    admin.set_product_status(product_id, status)

    return redirect(url_for("listings"))


# -------------------------
# DELETE PRODUCT
# -------------------------

@app.route("/listings/delete/<int:product_id>")
def delete_product(product_id):

    admin.delete_product(product_id)

    return redirect(url_for("listings"))


# -------------------------
# QUICK STOCK ADJUST
# -------------------------

@app.route("/listings/stock/<int:product_id>/<action>")
def adjust_product_stock(product_id, action):

    amount = 1 if action == "increase" else -1
    admin.adjust_stock(product_id, amount)

    return redirect(url_for("listings"))


# -------------------------
# RENAME CATEGORY
# -------------------------

@app.route("/categories/rename", methods=["POST"])
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
def add_discount():

    admin.add_discount_code(
        code=request.form["code"],
        discount_percent=float(request.form["discount_percent"]),
    )

    return redirect(url_for("listings"))


@app.route("/discounts/toggle/<int:discount_id>")
def toggle_discount(discount_id):

    admin.toggle_discount_code(discount_id)

    return redirect(url_for("listings"))


@app.route("/discounts/delete/<int:discount_id>")
def delete_discount(discount_id):

    admin.delete_discount_code(discount_id)

    return redirect(url_for("listings"))


# -------------------------
# REVIEWS
# -------------------------

@app.route("/reviews/delete/<int:review_id>")
def delete_review(review_id):

    admin.delete_review(review_id)

    return redirect(url_for("listings"))


# -------------------------
# ORDER MANAGEMENT + SALES REPORTS
# -------------------------

@app.route("/reports")
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
def update_order_status(order_id, status):

    admin.update_order_status(order_id, status)

    return redirect(url_for("reports"))


# -------------------------
# MANAGE USERS (placeholder page)
# -------------------------

@app.route("/users")
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
# RUN APPLICATION
# -------------------------
if __name__ == "__main__":
    app.run(debug=True)