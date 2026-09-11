# CampusCart Admin & Management (Member 3)

This covers the admin console, product and order management, user
accounts and authentication, plus the Python backend behind them.

## How it fits together

```
Browser  →  app.py (routes)  →  admin_functions.py (logic)  →  database.py (data)  →  Turso
```

- **`app.py`** — Flask routes only. Reads form/query data, calls a
  function in `admin_functions.py` or `database.py`, and renders a
  template or redirects. It does not write SQL itself.
- **`admin_functions.py`** — All the admin backend logic: products,
  categories, stock, discount codes, reviews, orders, and sales
  figures. Every function opens a connection, runs a query, closes it.
- **`database.py`** — Table definitions (`init_db()`), the connection
  layer, user account functions, and sample data.

## The database is shared, not local

The project originally used SQLite, which is a **file** (`database.db`)
sitting in the project folder. That meant every laptop had its own
separate copy, so an account registered on one machine did not exist
on any other.

It now runs on **Turso**, which hosts **libSQL** — a fork of SQLite with
a network server in front of it. Same SQL, same tables, but every
query goes over HTTPS to one shared database that all our laptops
connect to.

Because the SQL dialect is unchanged, `admin_functions.py` did not
have to be rewritten. `database.py` returns an object that behaves
like the old sqlite3 connection (`cursor()`, `execute()`, `fetchone()`,
`commit()`, `close()`) while making HTTP calls underneath.

### Setup on a new machine

1. `pip install -r requirements.txt`
2. Create a `.env` file in the project root (see `.env.example`):

   ```
   TURSO_DATABASE_URL=libsql://<database>.turso.io
   TURSO_AUTH_TOKEN=<token>
   ```

   `.env` is gitignored, so the token never reaches GitHub. Get the
   values from a teammate.
3. `python app.py`, then visit `http://127.0.0.1:5000/`

If the database can't be reached, the app refuses to start and prints
why, rather than hanging.

## Database tables

| Table            | Purpose                                          |
|------------------|--------------------------------------------------|
| `users`          | id, fullname, email, password_hash, role, created_at |
| `products`       | id, name, seller, category, price, stock, status  |
| `orders`         | id, user_id, subtotal, discount, total, status, order_date |
| `order_items`    | line items belonging to an order                  |
| `discount_codes` | code, discount_percent, active                    |
| `reviews`        | product_id, reviewer, rating, comment, review_date |
| `cart`           | user_id, product_id, quantity (defined but unused) |

`order_items` is the bridge between orders and products: one order
holds many products and one product appears in many orders, so each
row is one product within one order. It also stores the price at the
time of purchase, so changing a product's price later does not rewrite
past orders.

## Routes

| Route | What it does |
|---|---|
| `/dashboard` | Marketplace figures, weekly activity, work queue, recent listings |
| `/listings` | Add/edit/delete products, approve/reject, stock, categories, discount codes, reviews |
| `/reports` | Sales summary, top selling products, order fulfilment status |
| `/users` | Registered accounts, searchable |
| `/admin/login` | Admin sign-in (separate from the student login) |

## Authentication

Three parts, and they matter together:

1. **Admins are a fixed allowlist.** Admin accounts are not created
   through any signup form. `ADMIN_ACCOUNTS` in `app.py` holds three
   email addresses, so nobody can register their way into the panel.
2. **Passwords are hashed, never stored.** Both student accounts and
   the admin allowlist store a `scrypt` hash. Signing in hashes the
   attempt and compares hashes, so the real password exists nowhere,
   even to someone with full database access.
3. **Every admin route is gated.** An `@admin_required` decorator sits
   on all 15 admin routes. It checks the session for an admin role
   before the view runs, so a logged-in student is turned away the
   same as a stranger. Before this, typing `/dashboard` into the
   address bar was enough to get in.

Student accounts have their own `/login` and `/register`, with an
optional "remember me" that keeps the session for 30 days.

## Still to connect

1. **There is no "sell an item" form.** `admin.add_product()` is only
   called by the admin form on `/listings`, so the only person who can
   put a product on the marketplace is an admin. A student with the
   `seller` role should get a page that inserts into the same
   `products` table with `status="Pending"`, which would then appear
   in the approval queue on `/dashboard` and `/listings`.

2. **The storefront does not read the database.** `app.py` still has a
   hardcoded `products` list at the top, and the homepage and product
   pages use that instead of the `products` table. So approving,
   editing or deleting a product in the admin panel changes nothing
   for shoppers. Whoever owns the storefront should call
   `admin_functions.get_all_products(status="Approved")`.

3. **The cart is in memory, not in the database.** `app.py` keeps a
   module-level `cart` dict, which means it is shared by everyone
   using the same running server and is wiped on restart. The `cart`
   table already exists in the database and is unused.

4. **Orders are not linked to accounts.** `place_order()` and
   `/orders` both hardcode `user_id = 1`, left over from before login
   existed, so every order belongs to the same user.

5. **Discount codes cannot be redeemed.** The admin can create and
   manage them and `validate_discount_code()` is written, but checkout
   hardcodes `discount = 0`.

6. **Stock does not decrease when an order is placed**, so the same
   item can be bought past its stock count.
