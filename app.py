from flask import Flask, render_template, redirect, url_for

app = Flask(__name__)


# -------------------------
# TEMPORARY PRODUCTS
# -------------------------

products = [
    {
        "id": 1,
        "name": "Campus T-Shirt",
        "price": 25.00,
        "stock": 10
    },
    {
        "id": 2,
        "name": "Campus Hoodie",
        "price": 50.00,
        "stock": 5
    },
    {
        "id": 3,
        "name": "Student Notebook",
        "price": 10.00,
        "stock": 20
    }
]


# -------------------------
# TEMPORARY SHOPPING CART
# -------------------------

cart = {}


# -------------------------
# HOME
# -------------------------

@app.route("/")
def home():
    return render_template("homepage.html")


# -------------------------
# ADMIN DASHBOARD
# -------------------------

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


# -------------------------
# ADMIN LISTINGS
# -------------------------

@app.route("/listings")
def listings():
    return render_template("listings.html")


# -------------------------
# TEST ADD TO CART
# -------------------------

@app.route("/test-add/<int:product_id>")
def test_add(product_id):
    return add_to_cart(product_id)


# -------------------------
# ADD TO CART
# -------------------------

@app.route("/add-to-cart/<int:product_id>")
def add_to_cart(product_id):

    # Find product
    product = next(
        (
            product
            for product in products
            if product["id"] == product_id
        ),
        None
    )

    # Product doesn't exist
    if product is None:
        return "Product not found", 404

    # Product already exists in cart
    if product_id in cart:

        # Check stock
        if cart[product_id]["quantity"] < product["stock"]:
            cart[product_id]["quantity"] += 1

    else:

        # Add new product
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

    subtotal = 0

    for item in cart.values():

        subtotal += (
            item["price"] *
            item["quantity"]
        )

    return render_template(
        "cart.html",
        cart=cart,
        subtotal=subtotal
    )


# -------------------------
# INCREASE QUANTITY
# -------------------------

@app.route("/increase/<int:product_id>")
def increase_quantity(product_id):

    product = next(
        (
            product
            for product in products
            if product["id"] == product_id
        ),
        None
    )

    if product_id in cart:

        # Don't allow quantity above stock
        if cart[product_id]["quantity"] < product["stock"]:
            cart[product_id]["quantity"] += 1

    return redirect(url_for("view_cart"))


# -------------------------
# DECREASE QUANTITY
# -------------------------

@app.route("/decrease/<int:product_id>")
def decrease_quantity(product_id):

    if product_id in cart:

        cart[product_id]["quantity"] -= 1

        # Remove when quantity reaches zero
        if cart[product_id]["quantity"] <= 0:
            del cart[product_id]

    return redirect(url_for("view_cart"))


# -------------------------
# REMOVE FROM CART
# -------------------------

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