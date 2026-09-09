# CampusCart Admin & Management (Member 3)

This covers the admin dashboard, product management, and order
management pages, plus the Python backend behind them.

## How it fits together

```
Browser  →  app.py (routes)  →  admin_functions.py (logic)  →  database.py (data)  →  database.db
```

- **`app.py`** — Flask routes only. Reads form/query data, calls a
  function in `admin_functions.py`, and renders a template or redirects.
  It does not talk to the database directly.
- **`admin_functions.py`** — All the actual backend logic: products,
  categories, stock, discount codes, reviews, orders, and sales figures.
  Every function opens its own connection, runs a query, and closes it.
- **`database.py`** — Table definitions (`init_db()`), a connection
  helper (`get_connection()`), and optional sample data
  (`seed_sample_data()`).

## Database tables that exist right now

| Table            | Purpose                                          |
|-------------------|---------------------------------------------------|
| `products`         | id, name, seller, category, price, stock, status |
| `orders`            | id, user_id, subtotal, discount, total, status, order_date |
| `order_items`       | line items belonging to an order |
| `discount_codes`    | code, discount_percent, active |
| `reviews`           | product_id, reviewer, rating, comment, review_date |
| `cart`              | user_id, product_id, quantity (not yet wired up anywhere) |

## Routes currently live

| Route | What it does |
|---|---|
| `/dashboard` | Admin dashboard stats + recent listings |
| `/listings` | Product management: add/edit/delete, approve/reject, stock, categories, discounts, reviews |
| `/reports` | Order management: sales summary, top products, order status |
| `/users` | Static user list (not wired to a real `users` table yet) |

## Things a teammate will need to connect

This is the important part for handing off:

1. **There's no public "sell an item" form yet.** Right now `admin.add_product()`
   only gets called by the admin form on `/listings`. Someone needs to build
   a public page where a student submits a product, which should insert into
   the same `products` table with `status="Pending"` — then it will
   automatically show up for approval on `/listings`.

2. **The homepage (`homepage.html`) is a static mockup.** It doesn't pull
   from `products` at all. Whoever owns the homepage should query
   `admin_functions.get_all_products(status="Approved")` and loop over
   the results instead of the 3 hardcoded product cards.

3. **The shopping cart is a separate, temporary system.** `app.py` currently
   has an in-memory `products` list and `cart` dict at the top, unrelated to
   the database. It resets every time the server restarts. Whoever owns
   checkout should decide whether to move the cart into the real `products`
   table and the `cart` table that already exists in the database (it's
   defined but unused).

4. **`iman/`'s product browsing uses a separate JSON file**
   (`iman/data/products.json`), a third, disconnected source of product
   data. For a single consistent marketplace, this should eventually read
   from the same `products` table too, via `admin_functions.get_all_products()`.

5. **There's no `users` table yet.** `/users` currently just shows fake
   hardcoded rows. Whoever handles registration/login should add a `users`
   table to `database.py` and wire `/users` up to it.

## Running it

```
pip install flask
python app.py
```

Then visit `http://127.0.0.1:5000/dashboard`.

The database (`database.db`) is created and seeded automatically the
first time the app runs (`database.init_db()` + `database.seed_sample_data()`
are called at the top of `app.py`). Deleting `database.db` and restarting
the app gives you a fresh one.
