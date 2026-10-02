# CampusCart

A marketplace for university students to buy and sell second-hand items on
campus, such as books, electronics, room accessories and clothes, with an
admin panel to keep it safe and running.

**Live site:** https://campuscart-ecc8.onrender.com
(free hosting: the first visit after a quiet spell can take about a minute to wake up)

## What it does

**Students**
- Browse and search listings by category, and view a seller's shop
- Add items to a cart and check out, with quantities limited by stock
- Pay through CampusPay, a QR payment page (a simulated payment: no real money moves)
- Track orders through each stage, from paid to delivered
- Message a seller about an item
- Sell items from My Shop: list with a photo, set stock, and ship orders
- Get notifications (the bell) when something happens to their orders
- Settings: profile, password, light or dark mode, Help Centre and Contact Us

**Admins**
- Dashboard with headline numbers and a chart of new listings
- Manage listings, users, reported listings and orders
- Moderate accounts and listings, and answer Contact Us messages

More detail on the admin side is in [ADMIN_README.md](ADMIN_README.md).

## Built with

- **Python** with **Flask** (web framework) and **Jinja2** templates
- **Turso** (online SQLite database), so every computer and the live site share the same data
- **segno** to draw the payment QR codes
- **gunicorn** to run the live site on **Render**
- HTML and CSS, with a little JavaScript

## Installation

You need **Python 3.10 or newer** and an internet connection (the database is online).

1. Download the code:

       git clone https://github.com/imannharith/CampusCart.git
       cd CampusCart

2. Install the libraries:

       pip install -r requirements.txt

## Configuration

The app reads its settings from a file called `.env` in the project folder.
It is kept out of git on purpose, because it holds the database password.

1. Copy the example file:

       cp .env.example .env

2. Fill in the three values:

   | Setting | What it is |
   |---|---|
   | `TURSO_DATABASE_URL` | the address of the Turso database (starts with `libsql://`) |
   | `TURSO_AUTH_TOKEN` | the password that lets the app use that database |
   | `SECRET_KEY` | a random string that signs the login cookie; make one with `python -c "import secrets; print(secrets.token_hex(32))"` |

   Ask a teammate for the Turso values, or get them from the Turso website.

When the app starts, it checks that every table exists and creates anything
missing, so a new database sets itself up.

## Running it

    python app.py

Then open http://127.0.0.1:5000 in a browser.

- Students sign up at **Register**, then log in.
- Admins log in at http://127.0.0.1:5000/admin/login (admin emails are a fixed list in `accounts.py`).
- On a Mac, if port 5000 shows a blank or "403" page, turn off AirPlay Receiver in
  System Settings > General > AirDrop & Handoff.

## Project layout

    CampusCart/
    ├── app.py              the routes: one function per web address
    ├── accounts.py         sign up, log in, and the checks that guard each page
    ├── shop_functions.py   the student side: shopping, orders, messages, My Shop, payment
    ├── admin_functions.py  the admin panel, in the same 7 sections as its menu
    ├── database.py         the connection to Turso
    ├── tables.py           the table definitions, checked when the app starts
    ├── requirements.txt    the Python libraries to install
    ├── .env.example        a template for your .env settings
    ├── templates/          the HTML pages (Jinja2)
    └── static/             CSS, the logo and the original product photos

New product photos are stored in the database (the `photos` table), so they
show up on every computer and on the live site.
