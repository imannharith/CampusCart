"""
Accounts
--------
Everything about who someone is and what they may open:

    1. Students   signing up, logging in, finding an account by email
    2. Admins     the admin accounts and their sign-in
    3. Every page who's logged in, and the checks that keep students out
                  of the admin panel and visitors out of student pages
    4. Settings   editing your profile, changing your password, light or
                  dark mode, and deactivating or deleting your account

Passwords are never stored or compared as plain text: werkzeug turns
each one into a salted hash (generate_password_hash) and checks a typed
password against that hash (check_password_hash).

What the login cookie holds (Flask's signed session):
    session['user']  the account's email
    session['role']  'student' or 'admin'
"""
import re
import secrets
from functools import wraps

from flask import session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

import database


# ====================================================================
# 1. STUDENTS
# Signing up and logging in. Emails are saved in lower case and compared
# ignoring capitals, so Farah@... and farah@... are the same account.
# ====================================================================


def get_user_by_email(email):
    # Emails are compared ignoring capitals: Farah@... and farah@... are one person.
    connection = database.get_connection()
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM users WHERE LOWER(email) = LOWER(?)", ((email or "").strip(),))
    user = cursor.fetchone()
    connection.close()
    return user


def check_sign_up(fullname, email, password, confirm_password):
    """What's wrong with a sign-up, or None if the account can be made."""
    if not 2 <= len(fullname) <= 80:
        return "Enter your full name."
    if len(email) > 120 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return "Enter a valid email address."
    if not 8 <= len(password) <= 128:
        return "Your password needs at least 8 characters."
    if password != confirm_password:
        return "Passwords don't match."
    if get_user_by_email(email) is not None:
        return "An account with that email already exists."
    return None


def create_user(fullname, email, password_hash, role):
    connection = database.get_connection()
    cursor = connection.cursor()
    cursor.execute("""
        INSERT INTO users (fullname, email, password_hash, role)
        VALUES (?, ?, ?, ?)
    """, (fullname, email.strip().lower(), password_hash, role))
    connection.commit()
    connection.close()


def sign_up(fullname, email, password):
    """Save a new student account. The password is stored only as a hash."""
    create_user(fullname, email, generate_password_hash(password), "student")


def check_student_login(email, password):
    """(account, None) if the email and password match, or (None, the message
    to show) if not. The message never says which of email or password was
    wrong, so it can't be used to find out which emails have accounts.

    A student who deactivated their own account turns it back on just by
    logging in again (account["reactivated"] is then True)."""
    user = get_user_by_email(email)
    if user is None or not check_password_hash(user["password_hash"], password):
        return None, "Incorrect email or password."
    if user["account_status"] == "Suspended":
        return None, "Your account is suspended. Please contact CampusCart support."
    user = dict(user)
    user["reactivated"] = user["account_status"] == "Deactivated"
    if user["reactivated"]:
        database.atomic([("UPDATE users SET account_status = 'Active' WHERE id = ?", [user["id"]])])
    return user, None


def log_in(user, remember=False):
    """Start a fresh session for this student. Clearing first means nothing
    from before signing in carries over."""
    session.clear()
    session.permanent = remember          # "Remember me" keeps it for 30 days
    session["user"] = user["email"]
    session["role"] = user["role"]


# ====================================================================
# 2. ADMINS
# Admins aren't in the users table: they're this fixed list, each with a
# password hash. Only these emails can ever open the admin panel.
# ====================================================================


ADMIN_ACCOUNTS = {
    "zikryman123@gmail.com": "scrypt:32768:8:1$lfJSd8WxZnbJAX6j$6f91f9a88d7409a67970259ce6276ff095810bc16cd24733ca514d155e99d76c6e71cfb2646cd17a0257080664097647ba8998330a562ec1da1465d30bf9e8bf",
    "imannhairurizal@gmail.com": "scrypt:32768:8:1$nDtoMhQAZUKDqApr$603d72c49e6f8f568f62a6c2fa53a5d4ab945cbfb450f1f69ae9ca1e3e3a96fc12ea0b3605370d567651dcb3a64549717bca3872b676d40682af04d8586726f8",
    "priyaankavi0703@gmail.com": "scrypt:32768:8:1$NAoOq6JLC75PWzFu$2f8166558a27e2bc51af2fe58aa0e3eaedf0ccb00f572556ed26d9e2312d9aa6172a09465970abbdbacb9c6521d88db8235665c6ae051749b6b69409c722f8c4",
}


def check_admin_login(email, password):
    """True if this is an admin email and the password matches its hash."""
    password_hash = ADMIN_ACCOUNTS.get(email)
    return password_hash is not None and check_password_hash(password_hash, password)


def log_in_admin(email):
    session.clear()
    session["user"] = email
    session["role"] = "admin"


def is_admin_email(email):
    """Admin accounts can't be suspended from the Users page."""
    return email in ADMIN_ACCOUNTS


# ====================================================================
# 3. EVERY PAGE
# Who's logged in, and the checks each page runs before it opens.
# ====================================================================


def is_admin():
    """Logged in as an admin. Both must hold: the session says admin AND the
    email is really on the admin list."""
    return session.get("role") == "admin" and session.get("user") in ADMIN_ACCOUNTS


def current_user():
    """The logged-in student's account, or None (visitors and admins)."""
    if "user" not in session:
        return None
    return get_user_by_email(session["user"])


def account_blocked():
    """True if someone is logged in as a student whose account is no longer
    active (suspended, deactivated or deleted) -- they're logged out on their
    very next click."""
    if not session.get("user") or is_admin():
        return False
    user = get_user_by_email(session["user"])
    return user is None or user["account_status"] != "Active"


def log_out():
    """End the session. Returns True if it was an admin's, so they can be
    sent back to the admin sign-in page."""
    was_admin = session.get("role") == "admin"
    session.pop("user", None)
    session.pop("role", None)
    return was_admin


def admin_required(view):
    """Put above an admin page: anyone who isn't an admin is sent to the admin sign-in."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin():
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


def shopper_required(view):
    """Put above a student page: anyone not logged in is sent to the login page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


# ====================================================================
# 4. SETTINGS
# What a student can change about their own account from the Settings page.
# Anything that could lock someone out or hand the account to someone else
# (a new email, a new password, deleting) asks for the current password.
# ====================================================================


def check_profile_update(user, fullname, email, password):
    """What's wrong with a new name/email, or None if it can be saved."""
    if not 2 <= len(fullname) <= 80:
        return "Enter your full name."
    if len(email) > 120 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return "Enter a valid email address."
    if email != user["email"]:
        taken = get_user_by_email(email)
        if taken is not None and taken["id"] != user["id"]:
            return "Another account already uses that email."
        if not check_password_hash(user["password_hash"], password):
            return "Enter your current password to change your email."
    return None


def update_profile(user, fullname, email):
    """Save the new name and email. Your live listings show the new name
    straight away; past orders keep the name they were placed under."""
    database.atomic([
        ("UPDATE users SET fullname = ?, email = ? WHERE id = ?", [fullname, email.lower(), user["id"]]),
        ("UPDATE products SET seller = ? WHERE seller_id = ?", [fullname, user["id"]]),
    ])
    session["user"] = email.lower()       # the login cookie follows the new email


def check_password_change(user, current, new, confirm):
    """What's wrong with a password change, or None if it can be saved."""
    if not check_password_hash(user["password_hash"], current):
        return "Your current password is wrong."
    if not 8 <= len(new) <= 128:
        return "Your new password needs at least 8 characters."
    if new != confirm:
        return "The new passwords don't match."
    if new == current:
        return "Pick a password different from your current one."
    return None


def change_password(user, new):
    database.atomic([("UPDATE users SET password_hash = ? WHERE id = ?",
                      [generate_password_hash(new), user["id"]])])


THEMES = {"light", "dark"}


def set_theme(user, theme):
    """Light or dark mode, saved on the account so it follows you to any device."""
    if theme in THEMES:
        database.atomic([("UPDATE users SET theme = ? WHERE id = ?", [theme, user["id"]])])


def open_orders(user_id):
    """How many orders are still on their way -- sales this student hasn't
    finished, or purchases that haven't arrived."""
    connection = database.get_connection()
    cursor = connection.cursor()
    cursor.execute("""SELECT
        (SELECT COUNT(*) FROM order_items WHERE seller_id = ? AND status IN ('Pending', 'Shipped')) +
        (SELECT COUNT(*) FROM order_items i JOIN orders o ON o.id = i.order_id
            WHERE o.user_id = ? AND o.account_linked = 1 AND i.status IN ('Pending', 'Shipped')) AS n""",
        (user_id, user_id))
    waiting = cursor.fetchone()["n"]
    connection.close()
    return waiting


def check_account_closing(user, password, typed="", deleting=False):
    """What stops this student deactivating/deleting their account, or None."""
    if not check_password_hash(user["password_hash"], password):
        return "Your password is wrong."
    if deleting and typed.strip() != "DELETE":
        return 'Type DELETE in capitals to confirm.'
    waiting = open_orders(user["id"])
    if waiting:
        return (f"You have {waiting} order item(s) still on the way. Finish or cancel them "
                "first, so nobody is left waiting for something that won't come.")
    return None


def deactivate_account(user):
    """Switch the account off: listings disappear from the shop and you're
    logged out. Logging in again switches it back on -- nothing is lost."""
    database.atomic([("UPDATE users SET account_status = 'Deactivated' WHERE id = ?", [user["id"]])])
    log_out()


def delete_account(user):
    """Delete for good. Personal details are wiped and the account can never be
    logged into again. Orders stay (the other person in each sale still needs
    their record), but under "Deleted user" instead of your name."""
    database.atomic([
        ("""UPDATE products SET status = 'Removed', removal_reason = 'The seller deleted their account',
            removed_by = 'account deleted', removed_at = CURRENT_TIMESTAMP
            WHERE seller_id = ? AND status != 'Removed'""", [user["id"]]),
        ("DELETE FROM cart WHERE user_id = ?", [user["id"]]),
        ("DELETE FROM notifications WHERE user_id = ?", [user["id"]]),
        ("DELETE FROM support_messages WHERE user_id = ?", [user["id"]]),
        ("""UPDATE users SET fullname = 'Deleted user', email = ?, password_hash = ?,
            account_status = 'Deleted' WHERE id = ?""",
         [f"deleted-{user['id']}@deleted.campuscart", generate_password_hash(secrets.token_urlsafe(32)), user["id"]]),
    ])
    session.clear()
