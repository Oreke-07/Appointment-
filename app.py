import os
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-me")
app.config["DATABASE"] = os.path.join(app.root_path, "appointments.db")

STATUS_LABELS = {
    "pending": "Pending",
    "confirmed": "Confirmed",
    "completed": "Completed",
    "cancelled": "Cancelled",
}
STATUS_CLASSES = {
    "pending": "badge-pending",
    "confirmed": "badge-confirmed",
    "completed": "badge-completed",
    "cancelled": "badge-cancelled",
}
DEFAULT_SERVICES = [
    {"name": "Consultation", "category": "General", "duration": 30, "price": 49.0},
    {"name": "Checkup", "category": "Routine", "duration": 45, "price": 69.0},
    {"name": "Follow-up", "category": "Support", "duration": 20, "price": 39.0},
]
USER_ROLE_USER = "user"
USER_ROLE_ADMIN = "admin"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def ensure_column(db, table, name, column_type, default=None):
    columns = [row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()]
    if name not in columns:
        sql = f"ALTER TABLE {table} ADD COLUMN {name} {column_type}"
        if default is not None:
            sql += f" DEFAULT {default}"
        db.execute(sql)


def init_db():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            phone TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            category TEXT,
            duration INTEGER,
            price REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            service_id INTEGER,
            service TEXT NOT NULL,
            appointment_date TEXT NOT NULL,
            appointment_time TEXT NOT NULL,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            reminder_minutes INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (service_id) REFERENCES services(id)
        )
        """
    )

    ensure_column(db, "users", "phone", "TEXT")
    ensure_column(db, "users", "role", "TEXT", "'user'")
    ensure_column(db, "appointments", "service_id", "INTEGER")
    ensure_column(db, "appointments", "status", "TEXT", "'pending'")
    ensure_column(db, "appointments", "reminder_minutes", "INTEGER")
    ensure_column(db, "appointments", "updated_at", "TIMESTAMP")

    db.commit()

    if not db.execute("SELECT 1 FROM services LIMIT 1").fetchone():
        for service in DEFAULT_SERVICES:
            db.execute(
                "INSERT OR IGNORE INTO services (name, category, duration, price) VALUES (?, ?, ?, ?)",
                (service["name"], service["category"], service["duration"], service["price"]),
            )
        db.commit()

    if not db.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
        admin_username = os.getenv("ADMIN_USERNAME", "admin")
        admin_email = os.getenv("ADMIN_EMAIL", "admin@appointment.local")
        admin_password = os.getenv("ADMIN_PASSWORD", "Admin@123")
        db.execute(
            "INSERT OR IGNORE INTO users (username, email, password, role) VALUES (?, ?, ?, ?)",
            (
                admin_username,
                admin_email,
                generate_password_hash(admin_password),
                USER_ROLE_ADMIN,
            ),
        )
        db.commit()


with app.app_context():
    init_db()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != USER_ROLE_ADMIN:
            flash("Administrator access required.", "warning")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)

    return wrapped


def get_service_options():
    db = get_db()
    return db.execute(
        "SELECT id, name, category, duration, price FROM services ORDER BY name"
    ).fetchall()


def get_service_name(service_id, fallback):
    if not service_id:
        return fallback
    service = get_db().execute(
        "SELECT name FROM services WHERE id = ?", (service_id,)
    ).fetchone()
    return service["name"] if service else fallback


@app.context_processor
def utility_processor():
    return {
        "status_labels": STATUS_LABELS,
        "status_classes": STATUS_CLASSES,
        "service_choices": get_service_options(),
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not username or not email or not password or not confirm_password:
            flash("All fields are required.", "danger")
            return render_template("register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")

        db = get_db()
        existing = db.execute(
            "SELECT id FROM users WHERE username=? OR email=?",
            (username, email),
        ).fetchone()
        if existing:
            flash("Username or email already exists.", "danger")
            return render_template("register.html")

        password_hash = generate_password_hash(password)
        db.execute(
            "INSERT INTO users (username, email, phone, password, role) VALUES (?, ?, ?, ?, ?)",
            (username, email, phone, password_hash, USER_ROLE_USER),
        )
        db.commit()
        user = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        session["user_id"] = user["id"]
        session["username"] = username
        session["role"] = USER_ROLE_USER
        flash("Registration successful.", "success")
        return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        db = get_db()
        user = db.execute(
            "SELECT id, username, password, role FROM users WHERE username=?",
            (username,),
        ).fetchone()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            flash("Logged in successfully.", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    user = db.execute(
        "SELECT id, username, email, phone, role FROM users WHERE id=?",
        (session["user_id"],),
    ).fetchone()

    counts = {
        status: db.execute(
            "SELECT COUNT(1) FROM appointments WHERE user_id=? AND status=?",
            (session["user_id"], status),
        ).fetchone()[0]
        for status in STATUS_LABELS
    }

    upcoming = db.execute(
        """
        SELECT a.id, a.service_id, a.service, a.appointment_date, a.appointment_time, a.notes, a.status,
               a.reminder_minutes, s.duration, s.price
        FROM appointments a
        LEFT JOIN services s ON s.id = a.service_id
        WHERE a.user_id = ? AND a.status IN ('pending', 'confirmed')
        ORDER BY a.appointment_date, a.appointment_time
        LIMIT 5
        """,
        (session["user_id"],),
    ).fetchall()

    return render_template(
        "user/dashboard.html",
        user=user,
        upcoming=upcoming,
        counts=counts,
    )


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    db = get_db()
    user = db.execute(
        "SELECT id, username, email, phone, password FROM users WHERE id=?",
        (session["user_id"],),
    ).fetchone()

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        current_password = request.form.get("current_password", "").strip()
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not username or not email:
            flash("Username and email are required.", "danger")
            return render_template("user/profile.html", user=user)

        existing = db.execute(
            "SELECT id FROM users WHERE (username=? OR email=?) AND id!=?",
            (username, email, session["user_id"]),
        ).fetchone()
        if existing:
            flash("That username or email is already in use.", "danger")
            return render_template("user/profile.html", user=user)

        password_hash = user["password"] if "password" in user.keys() else None
        if new_password:
            if not current_password:
                flash("Please enter your current password to change your password.", "danger")
                return render_template("user/profile.html", user=user)
            row = db.execute("SELECT password FROM users WHERE id=?", (session["user_id"],)).fetchone()
            if not check_password_hash(row["password"], current_password):
                flash("Current password is incorrect.", "danger")
                return render_template("user/profile.html", user=user)
            if new_password != confirm_password:
                flash("New passwords do not match.", "danger")
                return render_template("user/profile.html", user=user)
            password_hash = generate_password_hash(new_password)

        db.execute(
            "UPDATE users SET username=?, email=?, phone=?, password=? WHERE id=?",
            (username, email, phone, password_hash, session["user_id"]),
        )
        db.commit()
        session["username"] = username
        flash("Your profile was updated.", "success")
        return redirect(url_for("profile"))

    return render_template("user/profile.html", user=user)


@app.route("/book-appointment", methods=["GET", "POST"])
@login_required
def book_appointment():
    services = get_service_options()
    if request.method == "POST":
        service_id = request.form.get("service_id")
        appointment_date = request.form.get("appointment_date", "").strip()
        appointment_time = request.form.get("appointment_time", "").strip()
        notes = request.form.get("notes", "").strip()
        reminder_minutes = request.form.get("reminder_minutes", "").strip()

        if not service_id or not appointment_date or not appointment_time:
            flash("Please complete the required fields.", "danger")
            return render_template("user/book_appointment.html", services=services)

        service_row = get_db().execute(
            "SELECT id, name FROM services WHERE id = ?",
            (service_id,),
        ).fetchone()
        if not service_row:
            flash("Selected service is invalid.", "danger")
            return render_template("user/book_appointment.html", services=services)

        reminder_value = None
        if reminder_minutes.isdigit():
            reminder_value = int(reminder_minutes)

        db = get_db()
        db.execute(
            "INSERT INTO appointments (user_id, service_id, service, appointment_date, appointment_time, notes, reminder_minutes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session["user_id"],
                service_row["id"],
                service_row["name"],
                appointment_date,
                appointment_time,
                notes,
                reminder_value,
            ),
        )
        db.commit()
        flash("Appointment booked successfully.", "success")
        return redirect(url_for("my_appointments"))

    return render_template("user/book_appointment.html", services=services)


@app.route("/my-appointments")
@login_required
def my_appointments():
    db = get_db()
    status_filter = request.args.get("status", "all")
    search = request.args.get("search", "").strip()
    query = [
        "SELECT a.id, a.service_id, a.service, a.appointment_date, a.appointment_time,",
        "a.notes, a.status, a.reminder_minutes, s.duration, s.price",
        "FROM appointments a",
        "LEFT JOIN services s ON s.id = a.service_id",
        "WHERE a.user_id = ?",
    ]
    params = [session["user_id"]]

    if status_filter != "all":
        query.append("AND a.status = ?")
        params.append(status_filter)

    if search:
        query.append("AND (COALESCE(s.name, a.service) LIKE ? OR a.notes LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    query.append("ORDER BY a.appointment_date, a.appointment_time")
    appointments = db.execute(" ".join(query), params).fetchall()

    return render_template(
        "user/my_appointment.html",
        appointments=appointments,
        selected_status=status_filter,
        search_query=search,
    )


@app.route("/appointment/<int:appointment_id>/edit", methods=["GET", "POST"])
@login_required
def edit_appointment(appointment_id):
    db = get_db()
    appointment = db.execute(
        "SELECT * FROM appointments WHERE id = ? AND user_id = ?",
        (appointment_id, session["user_id"]),
    ).fetchone()
    if not appointment:
        flash("Appointment not found.", "danger")
        return redirect(url_for("my_appointments"))

    if appointment["status"] == "cancelled":
        flash("Cancelled appointments cannot be edited.", "warning")
        return redirect(url_for("my_appointments"))

    services = get_service_options()
    if request.method == "POST":
        service_id = request.form.get("service_id")
        appointment_date = request.form.get("appointment_date", "").strip()
        appointment_time = request.form.get("appointment_time", "").strip()
        notes = request.form.get("notes", "").strip()
        status = request.form.get("status", appointment["status"])
        reminder_minutes = request.form.get("reminder_minutes", "").strip()

        if not service_id or not appointment_date or not appointment_time:
            flash("Please complete the required fields.", "danger")
            return render_template(
                "user/edit_appointment.html",
                appointment=appointment,
                services=services,
            )

        service_row = db.execute(
            "SELECT id, name FROM services WHERE id = ?",
            (service_id,),
        ).fetchone()
        if not service_row:
            flash("Selected service is invalid.", "danger")
            return render_template(
                "user/edit_appointment.html",
                appointment=appointment,
                services=services,
            )

        reminder_value = None
        if reminder_minutes.isdigit():
            reminder_value = int(reminder_minutes)

        db.execute(
            """
            UPDATE appointments
            SET service_id = ?, service = ?, appointment_date = ?, appointment_time = ?, notes = ?, status = ?, reminder_minutes = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                service_row["id"],
                service_row["name"],
                appointment_date,
                appointment_time,
                notes,
                status,
                reminder_value,
                datetime.utcnow(),
                appointment_id,
                session["user_id"],
            ),
        )
        db.commit()
        flash("Appointment updated successfully.", "success")
        return redirect(url_for("my_appointments"))

    return render_template(
        "user/edit_appointment.html",
        appointment=appointment,
        services=services,
    )


@app.route("/appointment/<int:appointment_id>/cancel", methods=["POST"])
@login_required
def cancel_appointment(appointment_id):
    db = get_db()
    appointment = db.execute(
        "SELECT id, status FROM appointments WHERE id = ? AND user_id = ?",
        (appointment_id, session["user_id"]),
    ).fetchone()
    if not appointment:
        flash("Appointment not found.", "danger")
        return redirect(url_for("my_appointments"))

    if appointment["status"] == "cancelled":
        flash("Appointment is already cancelled.", "warning")
        return redirect(url_for("my_appointments"))

    db.execute(
        "UPDATE appointments SET status = 'cancelled', updated_at = ? WHERE id = ?",
        (datetime.utcnow(), appointment_id),
    )
    db.commit()
    flash("Appointment cancelled.", "info")
    return redirect(url_for("my_appointments"))


@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    db = get_db()
    totals = {
        "users": db.execute("SELECT COUNT(1) FROM users").fetchone()[0],
        "services": db.execute("SELECT COUNT(1) FROM services").fetchone()[0],
        "appointments": db.execute("SELECT COUNT(1) FROM appointments").fetchone()[0],
        "pending": db.execute(
            "SELECT COUNT(1) FROM appointments WHERE status='pending'"
        ).fetchone()[0],
    }
    recent = db.execute(
        """
        SELECT a.id, a.appointment_date, a.appointment_time, a.status, COALESCE(s.name, a.service) AS service_name, u.username
        FROM appointments a
        LEFT JOIN services s ON s.id = a.service_id
        LEFT JOIN users u ON u.id = a.user_id
        ORDER BY a.created_at DESC
        LIMIT 8
        """
    ).fetchall()
    return render_template("admin/dashboard.html", totals=totals, recent=recent)


@app.route("/admin/users")
@login_required
@admin_required
def admin_users():
    users = get_db().execute(
        "SELECT id, username, email, phone, role, created_at FROM users ORDER BY created_at DESC"
    ).fetchall()
    return render_template("admin/users.html", users=users)


@app.route("/admin/users/<int:user_id>/toggle-role", methods=["POST"])
@login_required
@admin_required
def toggle_user_role(user_id):
    db = get_db()
    user = db.execute("SELECT id, role FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin_users"))

    next_role = USER_ROLE_ADMIN if user["role"] == USER_ROLE_USER else USER_ROLE_USER
    db.execute("UPDATE users SET role = ? WHERE id = ?", (next_role, user_id))
    db.commit()
    flash(f"User role updated to {next_role}.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/services", methods=["GET", "POST"])
@login_required
@admin_required
def admin_services():
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        duration = request.form.get("duration", "").strip()
        price = request.form.get("price", "").strip()

        if not name or not duration or not price:
            flash("Service name, duration, and price are required.", "danger")
            return redirect(url_for("admin_services"))

        db.execute(
            "INSERT OR IGNORE INTO services (name, category, duration, price) VALUES (?, ?, ?, ?)",
            (name, category, int(duration), float(price)),
        )
        db.commit()
        flash("Service created.", "success")
        return redirect(url_for("admin_services"))

    services = db.execute(
        "SELECT id, name, category, duration, price, created_at FROM services ORDER BY name"
    ).fetchall()
    return render_template("admin/services.html", services=services)


@app.route("/admin/services/<int:service_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_service(service_id):
    db = get_db()
    db.execute("DELETE FROM services WHERE id = ?", (service_id,))
    db.commit()
    flash("Service removed.", "info")
    return redirect(url_for("admin_services"))


@app.route("/admin/appointments")
@login_required
@admin_required
def admin_appointments():
    status_filter = request.args.get("status", "all")
    query = [
        "SELECT a.id, a.appointment_date, a.appointment_time, a.status, a.notes, a.reminder_minutes,",
        "COALESCE(s.name, a.service) AS service_name, u.username, u.email",
        "FROM appointments a",
        "LEFT JOIN services s ON s.id = a.service_id",
        "LEFT JOIN users u ON u.id = a.user_id",
    ]
    params = []
    if status_filter != "all":
        query.append("WHERE a.status = ?")
        params.append(status_filter)
    query.append("ORDER BY a.appointment_date, a.appointment_time")
    appointments = get_db().execute(" ".join(query), params).fetchall()

    return render_template(
        "admin/appointments.html",
        appointments=appointments,
        selected_status=status_filter,
    )


@app.route("/admin/appointments/<int:appointment_id>/status", methods=["POST"])
@login_required
@admin_required
def admin_update_appointment_status(appointment_id):
    new_status = request.form.get("status")
    if new_status not in STATUS_LABELS:
        flash("Invalid status selected.", "danger")
        return redirect(url_for("admin_appointments"))

    db = get_db()
    db.execute(
        "UPDATE appointments SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, datetime.utcnow(), appointment_id),
    )
    db.commit()
    flash("Appointment status updated.", "success")
    return redirect(url_for("admin_appointments"))


@app.route("/health")
def health():
    return {"status": "ok"}, 200


if __name__ == "__main__":
    app.run(debug=True)

import os
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-me")
app.config["DATABASE"] = os.path.join(app.root_path, "appointments.db")

STATUS_LABELS = {
    "pending": "Pending",
    "confirmed": "Confirmed",
    "completed": "Completed",
    "cancelled": "Cancelled",
}
STATUS_CLASSES = {
    "pending": "badge-pending",
    "confirmed": "badge-confirmed",
    "completed": "badge-completed",
    "cancelled": "badge-cancelled",
}
DEFAULT_SERVICES = [
    {"name": "Consultation", "category": "General", "duration": 30, "price": 49.0},
    {"name": "Checkup", "category": "Routine", "duration": 45, "price": 69.0},
    {"name": "Follow-up", "category": "Support", "duration": 20, "price": 39.0},
]
USER_ROLE_USER = "user"
USER_ROLE_ADMIN = "admin"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def ensure_column(db, table, name, column_type, default=None):
    columns = [row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()]
    if name not in columns:
        sql = f"ALTER TABLE {table} ADD COLUMN {name} {column_type}"
        if default is not None:
            sql += f" DEFAULT {default}"
        db.execute(sql)


def init_db():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            phone TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            category TEXT,
            duration INTEGER,
            price REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            service_id INTEGER,
            service TEXT NOT NULL,
            appointment_date TEXT NOT NULL,
            appointment_time TEXT NOT NULL,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            reminder_minutes INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (service_id) REFERENCES services(id)
        )
        """
    )

    ensure_column(db, "users", "phone", "TEXT")
    ensure_column(db, "users", "role", "TEXT", "'user'")
    ensure_column(db, "appointments", "service_id", "INTEGER")
    ensure_column(db, "appointments", "status", "TEXT", "'pending'")
    ensure_column(db, "appointments", "reminder_minutes", "INTEGER")
    ensure_column(db, "appointments", "updated_at", "TIMESTAMP")

    db.commit()

    if not db.execute("SELECT 1 FROM services LIMIT 1").fetchone():
        for service in DEFAULT_SERVICES:
            db.execute(
                "INSERT OR IGNORE INTO services (name, category, duration, price) VALUES (?, ?, ?, ?)",
                (service["name"], service["category"], service["duration"], service["price"]),
            )
        db.commit()

    if not db.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
        admin_username = os.getenv("ADMIN_USERNAME", "admin")
        admin_email = os.getenv("ADMIN_EMAIL", "admin@appointment.local")
        admin_password = os.getenv("ADMIN_PASSWORD", "Admin@123")
        db.execute(
            "INSERT OR IGNORE INTO users (username, email, password, role) VALUES (?, ?, ?, ?)",
            (
                admin_username,
                admin_email,
                generate_password_hash(admin_password),
                USER_ROLE_ADMIN,
            ),
        )
        db.commit()


with app.app_context():
    init_db()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != USER_ROLE_ADMIN:
            flash("Administrator access required.", "warning")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)

    return wrapped


def get_service_options():
    db = get_db()
    return db.execute(
        "SELECT id, name, category, duration, price FROM services ORDER BY name"
    ).fetchall()


def get_service_name(service_id, fallback):
    if not service_id:
        return fallback
    service = get_db().execute(
        "SELECT name FROM services WHERE id = ?", (service_id,)
    ).fetchone()
    return service["name"] if service else fallback


@app.context_processor
def utility_processor():
    return {
        "status_labels": STATUS_LABELS,
        "status_classes": STATUS_CLASSES,
        "service_choices": get_service_options(),
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not username or not email or not password or not confirm_password:
            flash("All fields are required.", "danger")
            return render_template("register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")

        db = get_db()
        existing = db.execute(
            "SELECT id FROM users WHERE username=? OR email=?",
            (username, email),
        ).fetchone()
        if existing:
            flash("Username or email already exists.", "danger")
            return render_template("register.html")

        password_hash = generate_password_hash(password)
        db.execute(
            "INSERT INTO users (username, email, phone, password, role) VALUES (?, ?, ?, ?, ?)",
            (username, email, phone, password_hash, USER_ROLE_USER),
        )
        db.commit()
        user = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        session["user_id"] = user["id"]
        session["username"] = username
        session["role"] = USER_ROLE_USER
        flash("Registration successful.", "success")
        return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        db = get_db()
        user = db.execute(
            "SELECT id, username, password, role FROM users WHERE username=?",
            (username,),
        ).fetchone()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            flash("Logged in successfully.", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    user = db.execute(
        "SELECT id, username, email, phone, role FROM users WHERE id=?",
        (session["user_id"],),
    ).fetchone()

    counts = {
        status: db.execute(
            "SELECT COUNT(1) FROM appointments WHERE user_id=? AND status=?",
            (session["user_id"], status),
        ).fetchone()[0]
        for status in STATUS_LABELS
    }

    upcoming = db.execute(
        """
        SELECT a.id, a.service_id, a.service, a.appointment_date, a.appointment_time, a.notes, a.status,
               a.reminder_minutes, s.duration, s.price
        FROM appointments a
        LEFT JOIN services s ON s.id = a.service_id
        WHERE a.user_id = ? AND a.status IN ('pending', 'confirmed')
        ORDER BY a.appointment_date, a.appointment_time
        LIMIT 5
        """,
        (session["user_id"],),
    ).fetchall()

    return render_template(
        "user/dashboard.html",
        user=user,
        upcoming=upcoming,
        counts=counts,
    )


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    db = get_db()
    user = db.execute(
        "SELECT id, username, email, phone, password FROM users WHERE id=?",
        (session["user_id"],),
    ).fetchone()

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        current_password = request.form.get("current_password", "").strip()
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not username or not email:
            flash("Username and email are required.", "danger")
            return render_template("user/profile.html", user=user)

        existing = db.execute(
            "SELECT id FROM users WHERE (username=? OR email=?) AND id!=?",
            (username, email, session["user_id"]),
        ).fetchone()
        if existing:
            flash("That username or email is already in use.", "danger")
            return render_template("user/profile.html", user=user)

        password_hash = user["password"] if "password" in user.keys() else None
        if new_password:
            if not current_password:
                flash("Please enter your current password to change your password.", "danger")
                return render_template("user/profile.html", user=user)
            row = db.execute("SELECT password FROM users WHERE id=?", (session["user_id"],)).fetchone()
            if not check_password_hash(row["password"], current_password):
                flash("Current password is incorrect.", "danger")
                return render_template("user/profile.html", user=user)
            if new_password != confirm_password:
                flash("New passwords do not match.", "danger")
                return render_template("user/profile.html", user=user)
            password_hash = generate_password_hash(new_password)

        db.execute(
            "UPDATE users SET username=?, email=?, phone=?, password=? WHERE id=?",
            (username, email, phone, password_hash, session["user_id"]),
        )
        db.commit()
        session["username"] = username
        flash("Your profile was updated.", "success")
        return redirect(url_for("profile"))

    return render_template("user/profile.html", user=user)


@app.route("/book-appointment", methods=["GET", "POST"])
@login_required
def book_appointment():
    services = get_service_options()
    if request.method == "POST":
        service_id = request.form.get("service_id")
        appointment_date = request.form.get("appointment_date", "").strip()
        appointment_time = request.form.get("appointment_time", "").strip()
        notes = request.form.get("notes", "").strip()
        reminder_minutes = request.form.get("reminder_minutes", "").strip()

        if not service_id or not appointment_date or not appointment_time:
            flash("Please complete the required fields.", "danger")
            return render_template("user/book_appointment.html", services=services)

        service_row = get_db().execute(
            "SELECT id, name FROM services WHERE id = ?",
            (service_id,),
        ).fetchone()
        if not service_row:
            flash("Selected service is invalid.", "danger")
            return render_template("user/book_appointment.html", services=services)

        reminder_value = None
        if reminder_minutes.isdigit():
            reminder_value = int(reminder_minutes)

        db = get_db()
        db.execute(
            "INSERT INTO appointments (user_id, service_id, service, appointment_date, appointment_time, notes, reminder_minutes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session["user_id"],
                service_row["id"],
                service_row["name"],
                appointment_date,
                appointment_time,
                notes,
                reminder_value,
            ),
        )
        db.commit()
        flash("Appointment booked successfully.", "success")
        return redirect(url_for("my_appointments"))

    return render_template("user/book_appointment.html", services=services)


@app.route("/my-appointments")
@login_required
def my_appointments():
    db = get_db()
    status_filter = request.args.get("status", "all")
    search = request.args.get("search", "").strip()
    query = [
        "SELECT a.id, a.service_id, a.service, a.appointment_date, a.appointment_time,",
        "a.notes, a.status, a.reminder_minutes, s.duration, s.price",
        "FROM appointments a",
        "LEFT JOIN services s ON s.id = a.service_id",
        "WHERE a.user_id = ?",
    ]
    params = [session["user_id"]]

    if status_filter != "all":
        query.append("AND a.status = ?")
        params.append(status_filter)

    if search:
        query.append("AND (COALESCE(s.name, a.service) LIKE ? OR a.notes LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    query.append("ORDER BY a.appointment_date, a.appointment_time")
    appointments = db.execute(" ".join(query), params).fetchall()

    return render_template(
        "user/my_appointment.html",
        appointments=appointments,
        selected_status=status_filter,
        search_query=search,
    )


@app.route("/appointment/<int:appointment_id>/edit", methods=["GET", "POST"])
@login_required
def edit_appointment(appointment_id):
    db = get_db()
    appointment = db.execute(
        "SELECT * FROM appointments WHERE id = ? AND user_id = ?",
        (appointment_id, session["user_id"]),
    ).fetchone()
    if not appointment:
        flash("Appointment not found.", "danger")
        return redirect(url_for("my_appointments"))

    if appointment["status"] == "cancelled":
        flash("Cancelled appointments cannot be edited.", "warning")
        return redirect(url_for("my_appointments"))

    services = get_service_options()
    if request.method == "POST":
        service_id = request.form.get("service_id")
        appointment_date = request.form.get("appointment_date", "").strip()
        appointment_time = request.form.get("appointment_time", "").strip()
        notes = request.form.get("notes", "").strip()
        status = request.form.get("status", appointment["status"])
        reminder_minutes = request.form.get("reminder_minutes", "").strip()

        if not service_id or not appointment_date or not appointment_time:
            flash("Please complete the required fields.", "danger")
            return render_template(
                "user/edit_appointment.html",
                appointment=appointment,
                services=services,
            )

        service_row = db.execute(
            "SELECT id, name FROM services WHERE id = ?",
            (service_id,),
        ).fetchone()
        if not service_row:
            flash("Selected service is invalid.", "danger")
            return render_template(
                "user/edit_appointment.html",
                appointment=appointment,
                services=services,
            )

        reminder_value = None
        if reminder_minutes.isdigit():
            reminder_value = int(reminder_minutes)

        db.execute(
            """
            UPDATE appointments
            SET service_id = ?, service = ?, appointment_date = ?, appointment_time = ?, notes = ?, status = ?, reminder_minutes = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                service_row["id"],
                service_row["name"],
                appointment_date,
                appointment_time,
                notes,
                status,
                reminder_value,
                datetime.utcnow(),
                appointment_id,
                session["user_id"],
            ),
        )
        db.commit()
        flash("Appointment updated successfully.", "success")
        return redirect(url_for("my_appointments"))

    return render_template(
        "user/edit_appointment.html",
        appointment=appointment,
        services=services,
    )


@app.route("/appointment/<int:appointment_id>/cancel", methods=["POST"])
@login_required
def cancel_appointment(appointment_id):
    db = get_db()
    appointment = db.execute(
        "SELECT id, status FROM appointments WHERE id = ? AND user_id = ?",
        (appointment_id, session["user_id"]),
    ).fetchone()
    if not appointment:
        flash("Appointment not found.", "danger")
        return redirect(url_for("my_appointments"))

    if appointment["status"] == "cancelled":
        flash("Appointment is already cancelled.", "warning")
        return redirect(url_for("my_appointments"))

    db.execute(
        "UPDATE appointments SET status = 'cancelled', updated_at = ? WHERE id = ?",
        (datetime.utcnow(), appointment_id),
    )
    db.commit()
    flash("Appointment cancelled.", "info")
    return redirect(url_for("my_appointments"))


@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    db = get_db()
    totals = {
        "users": db.execute("SELECT COUNT(1) FROM users").fetchone()[0],
        "services": db.execute("SELECT COUNT(1) FROM services").fetchone()[0],
        "appointments": db.execute("SELECT COUNT(1) FROM appointments").fetchone()[0],
        "pending": db.execute(
            "SELECT COUNT(1) FROM appointments WHERE status='pending'"
        ).fetchone()[0],
    }
    recent = db.execute(
        """
        SELECT a.id, a.appointment_date, a.appointment_time, a.status, COALESCE(s.name, a.service) AS service_name, u.username
        FROM appointments a
        LEFT JOIN services s ON s.id = a.service_id
        LEFT JOIN users u ON u.id = a.user_id
        ORDER BY a.created_at DESC
        LIMIT 8
        """
    ).fetchall()
    return render_template("admin/dashboard.html", totals=totals, recent=recent)


@app.route("/admin/users")
@login_required
@admin_required
def admin_users():
    users = get_db().execute(
        "SELECT id, username, email, phone, role, created_at FROM users ORDER BY created_at DESC"
    ).fetchall()
    return render_template("admin/users.html", users=users)


@app.route("/admin/users/<int:user_id>/toggle-role", methods=["POST"])
@login_required
@admin_required
def toggle_user_role(user_id):
    db = get_db()
    user = db.execute("SELECT id, role FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin_users"))

    next_role = USER_ROLE_ADMIN if user["role"] == USER_ROLE_USER else USER_ROLE_USER
    db.execute("UPDATE users SET role = ? WHERE id = ?", (next_role, user_id))
    db.commit()
    flash(f"User role updated to {next_role}.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/services", methods=["GET", "POST"])
@login_required
@admin_required
def admin_services():
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        duration = request.form.get("duration", "").strip()
        price = request.form.get("price", "").strip()

        if not name or not duration or not price:
            flash("Service name, duration, and price are required.", "danger")
            return redirect(url_for("admin_services"))

        db.execute(
            "INSERT OR IGNORE INTO services (name, category, duration, price) VALUES (?, ?, ?, ?)",
            (name, category, int(duration), float(price)),
        )
        db.commit()
        flash("Service created.", "success")
        return redirect(url_for("admin_services"))

    services = db.execute(
        "SELECT id, name, category, duration, price, created_at FROM services ORDER BY name"
    ).fetchall()
    return render_template("admin/services.html", services=services)


@app.route("/admin/services/<int:service_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_service(service_id):
    db = get_db()
    db.execute("DELETE FROM services WHERE id = ?", (service_id,))
    db.commit()
    flash("Service removed.", "info")
    return redirect(url_for("admin_services"))


@app.route("/admin/appointments")
@login_required
@admin_required
def admin_appointments():
    status_filter = request.args.get("status", "all")
    query = [
        "SELECT a.id, a.appointment_date, a.appointment_time, a.status, a.notes, a.reminder_minutes,",
        "COALESCE(s.name, a.service) AS service_name, u.username, u.email",
        "FROM appointments a",
        "LEFT JOIN services s ON s.id = a.service_id",
        "LEFT JOIN users u ON u.id = a.user_id",
    ]
    params = []
    if status_filter != "all":
        query.append("WHERE a.status = ?")
        params.append(status_filter)
    query.append("ORDER BY a.appointment_date, a.appointment_time")
    appointments = get_db().execute(" ".join(query), params).fetchall()

    return render_template(
        "admin/appointments.html",
        appointments=appointments,
        selected_status=status_filter,
    )


@app.route("/admin/appointments/<int:appointment_id>/status", methods=["POST"])
@login_required
@admin_required
def admin_update_appointment_status(appointment_id):
    new_status = request.form.get("status")
    if new_status not in STATUS_LABELS:
        flash("Invalid status selected.", "danger")
        return redirect(url_for("admin_appointments"))

    db = get_db()
    db.execute(
        "UPDATE appointments SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, datetime.utcnow(), appointment_id),
    )
    db.commit()
    flash("Appointment status updated.", "success")
    return redirect(url_for("admin_appointments"))


@app.route("/health")
def health():
    return {"status": "ok"}, 200


if __name__ == "__main__":
    app.run(debug=True)



