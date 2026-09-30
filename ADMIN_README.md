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
   SECRET_KEY=<any long random text>
   ```

   `.env` is gitignored, so the token never reaches GitHub. Get the
   Turso values from a teammate. `SECRET_KEY` signs the login cookie;
   make your own with `python -c "import secrets; print(secrets.token_hex(32))"`.
   Without it the app still runs, but everyone is logged out on every restart.
   Add `FLASK_DEBUG=1` only while developing: debug mode's error page can run code.
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
| `cart`           | user_id, product_id, quantity; unique per account/product |

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

## Stock, and why orders have to move it

Until this week the `stock` column never changed. Buying something left
it untouched, so the number shown in the admin panel was decoration:
the same item could be sold indefinitely, and nothing in the system
could tell that a listing was finished.

Most listings here are a single second-hand item, so stock is less an
inventory count than an availability flag — dropping from 1 to 0 *is*
the sale. It stays a number rather than a "sold" boolean because the
sell form asks students how many they have, and some do sell multiples
(hoodies, printed notes).

Three pieces now keep it honest:

1. **Placing an order reduces stock.** `place_order()` runs
   `SET stock = MAX(0, stock - ?)` for each line item. The new value is
   worked out by the database rather than read into Python and written
   back, so two orders placed at the same moment cannot both read the
   old number and each save it one lower. `MAX(0, ...)` stops it going
   negative.

2. **Cancelling an order returns the stock**, and reinstating a
   cancelled order takes it out again. `update_order_status()` compares
   the order's existing status against the new one and only moves stock
   when it crosses into or out of `Cancelled`. Without that comparison,
   pressing Cancel twice — or refreshing the page afterwards — would
   hand the same items back twice and invent stock from nothing.

3. **The dashboard shows sold-out listings.** `get_dead_listings()`
   returns products that are still `Approved` but have no stock left.
   Those are listings a shopper can open and not be able to buy, so
   they need an admin to restock or remove them.

## Order statuses are a sequence, not a set

An order moves Pending → Shipped → Delivered, and some moves make no
sense: delivering something never shipped, cancelling something the
buyer already has. `ORDER_TRANSITIONS` in `admin_functions.py` lists
what each status is allowed to become, and `update_order_status()`
refuses anything else and returns `False`, which the route turns into
a 400.

`Delivered` is deliberately final. Once the goods are with the buyer,
undoing that is a returns process, not a status change.

The reports page also only renders the buttons that are legal for each
order. That is a convenience, not the safeguard — hiding a button does
not stop somebody typing the URL, so the rule has to live on the
server. The template and the transition table do different jobs.

## One price, worked out in one place

`checkout()` and `place_order()` used to each do their own arithmetic —
one to show a total on the page, the other to save it. Two sums of the
same cart is one sum too many: any difference between them means the
buyer agrees to one figure and is charged another.

Both now call `price_cart(cart)`, which returns the subtotal, the discount
and the total together. Because the quote and the charge come from the
same call, they cannot drift apart.

It also decides the discount, from the **spend tiers** in the
`discount_tiers` table: by default RM50 earns 5% off, RM100 earns 10%,
RM200 earns 15%. Tiers are read biggest-threshold-first, so an order
gets the best one it qualifies for.

The percentages are fixed rather than randomised. A random discount
would have meant the page showing one price and the saved order
recording another, because `checkout()` and `place_order()` each call
the pricing function separately and would roll different numbers.

Nothing here is hardcoded — the tiers are rows an admin manages on
`/listings`, so changing what a discount is worth needs no code change.

Promo codes were removed from the project; the buyer-facing redemption
was taken out, so the admin panel for managing codes went with it
rather than being left as a page that configured nothing.

## Repairing a half-built database

`ensure_ready()` used to decide whether the schema needed creating by
checking for one table, `users`. A database that had `users` but was
missing another table therefore looked complete, `init_db()` never ran,
and the missing table was never created — leaving an error that
restarting could not fix. It now compares the full list of tables
against `EXPECTED_TABLES` and fills in whatever is absent. Still one
query on a normal start.

## Marketplace account integration

The homepage and catalog read approved database listings and hide linked listings
from suspended sellers. Each signed-in account has its own persistent cart in the
`cart` table. Cart routes take the account ID from the session, never from a form
or URL parameter. Logging out or restarting the app does not erase the cart.
The former shared in-memory cart cannot be migrated because it had no owner.

Checkout reads current product prices and saves the order, line items, stock
changes, and removal of that buyer's cart in one libSQL batch transaction. It
checks the cart quantities, prices, availability, seller IDs and stock again inside
the transaction. Changed, sold-out or duplicate checkout snapshots do not create an
order or clear the cart. Other accounts' carts stay untouched.

Seller profile listings, My Shop, sales summaries, admin seller activity, and
stock/delete/fulfilment ownership checks use account IDs. Order lines snapshot the
seller ID at purchase, so later renaming, reassignment, or deletion of the listing
does not transfer the sale to a different seller. Names remain display text.
Admins choose seller accounts by ID, with name/email shown in the listing editor.

Startup performs a one-time backfill for unlinked listings and order lines whose
seller name identifies exactly one existing account. Ambiguous or unmatched
records stay unlinked and remain visible to admins. A later signup with the same
name cannot claim these records. Admins can assign unlinked listings explicitly.

## Account and listing moderation

- **Users → View account** shows linked listings, verified purchases, reports,
  and account action history. Suspend/reactivate requires a reason and records
  the admin and UTC time. Suspension blocks login and existing sessions and
  hides linked listings from shoppers; admins can still inspect them.
- **Moderation** groups open reports by listing and filters decision history.
  Students report from the product catalog; admins can report from listing
  details. Reports contain the reporter, reason, optional supporting text/links,
  and time. Submission temporarily marks the listing Reported and takes it off
  sale. One decision resolves all open reports for that listing. Dismiss restores
  its previous status; Remove sets Rejected and preserves the report records.
- New forms use POST, CSRF tokens, server-side validation, and admin authorization.
  Account changes and report decisions use transactional libSQL batches.
- Startup adds `users.account_status`, `products.seller_id`,
  `orders.account_linked`, `account_actions`, and `listing_reports` automatically.
  Existing uniquely matching seller names are linked; ambiguous names are left
  unlinked. Old Reported listings receive a legacy report with unknown reporter
  and return to Pending if dismissed, because their previous status is unknown.
- Checkout now saves the signed-in account ID. Older orders used the hardcoded
  ID 1, so they remain in admin sales reports but are excluded from personal
  purchase history rather than assigned to the wrong account.
- Supporting evidence is text/reference links, not file uploads. Decision history
  stays available after a listing is later deleted.

Run the isolated SQLite-backed regression suite with
`python3 -m unittest discover -s tests -v`. It does not connect to or modify Turso.
