from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)


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
# ADMIN DASHBOARD & LISTINGS
# -------------------------
@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.route("/listings")
def listings():
    return render_template("listings.html")


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