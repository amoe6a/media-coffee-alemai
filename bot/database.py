"""SQLite data layer. Plain sqlite3 is plenty for a single coffee shop."""
import os
import sqlite3

_DB_PATH = "data/coffee.db"

ROLE_CUSTOMER = "customer"
ROLE_BARISTA = "barista"
ROLE_ADMIN = "admin"
ROLES = (ROLE_CUSTOMER, ROLE_BARISTA, ROLE_ADMIN)

ST_PENDING = "pending"
ST_ACCEPTED = "accepted"
ST_REJECTED = "rejected"
ST_COMPLETED = "completed"

CATEGORIES = ("Drinks", "Food")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    tg_id      INTEGER PRIMARY KEY,
    name       TEXT,
    username   TEXT,
    role       TEXT NOT NULL DEFAULT 'customer',
    phone      TEXT,
    kaspi      TEXT,
    address    TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS menu_items(
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    category      TEXT NOT NULL,
    subcategory   TEXT NOT NULL,
    price         REAL,
    quantity      INTEGER NOT NULL DEFAULT 0,
    image_file_id TEXT,
    available     INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS orders(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    phone      TEXT,
    kaspi      TEXT,
    address    TEXT,
    reason     TEXT,
    handled_by INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS order_items(
    order_id INTEGER NOT NULL,
    item_id  INTEGER,
    name     TEXT NOT NULL,
    price    REAL,
    qty      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS order_msgs(
    order_id   INTEGER NOT NULL,
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    UNIQUE(order_id, chat_id, message_id)
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _ensure_column(c, table: str, col: str, decl: str) -> None:
    """Add a column to an existing table if it isn't there yet (simple migration)."""
    have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
    if col not in have:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def init_db(path: str) -> None:
    global _DB_PATH
    _DB_PATH = path
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with _conn() as c:
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript(_SCHEMA)
        # migrations for databases created before a column existed
        _ensure_column(c, "users", "kaspi", "TEXT")
        _ensure_column(c, "orders", "kaspi", "TEXT")


# ---------------------------------------------------------------- users ----

def ensure_user(tg_id: int, name: str, username: str | None,
                env_admin_ids: list[int]) -> sqlite3.Row:
    """Create the user on first contact; auto-promote ids listed in ADMIN_IDS."""
    with _conn() as c:
        c.execute(
            "INSERT INTO users(tg_id, name, username) VALUES(?,?,?) "
            "ON CONFLICT(tg_id) DO UPDATE SET name=excluded.name, "
            "username=excluded.username",
            (tg_id, name, username),
        )
        if tg_id in env_admin_ids:
            c.execute("UPDATE users SET role=? WHERE tg_id=?", (ROLE_ADMIN, tg_id))
        return c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()


def get_user(tg_id: int) -> sqlite3.Row | None:
    with _conn() as c:
        return c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()


def set_contact(tg_id: int, phone: str | None = None,
                address: str | None = None, kaspi: str | None = None) -> None:
    with _conn() as c:
        if phone is not None:
            c.execute("UPDATE users SET phone=? WHERE tg_id=?", (phone, tg_id))
        if address is not None:
            c.execute("UPDATE users SET address=? WHERE tg_id=?", (address, tg_id))
        if kaspi is not None:
            c.execute("UPDATE users SET kaspi=? WHERE tg_id=?", (kaspi, tg_id))


def set_role(tg_id: int, role: str) -> bool:
    if role not in ROLES:
        return False
    with _conn() as c:
        cur = c.execute("UPDATE users SET role=? WHERE tg_id=?", (role, tg_id))
        return cur.rowcount > 0


def list_users() -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute(
            "SELECT * FROM users ORDER BY role DESC, created_at"
        ).fetchall()


def staff_ids(env_admin_ids: list[int]) -> list[int]:
    """Everyone who should be pinged about new orders."""
    with _conn() as c:
        rows = c.execute(
            "SELECT tg_id FROM users WHERE role IN (?,?)",
            (ROLE_ADMIN, ROLE_BARISTA),
        ).fetchall()
    ids = {r["tg_id"] for r in rows} | set(env_admin_ids)
    return sorted(ids)


# ----------------------------------------------------------------- menu ----

def upsert_item(name: str, category: str, subcategory: str,
                price: float | None, quantity: int) -> str:
    with _conn() as c:
        row = c.execute(
            "SELECT id FROM menu_items WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()
        if row:
            c.execute(
                "UPDATE menu_items SET category=?, subcategory=?, price=?, "
                "quantity=? WHERE id=?",
                (category, subcategory, price, quantity, row["id"]),
            )
            return "updated"
        c.execute(
            "INSERT INTO menu_items(name, category, subcategory, price, quantity) "
            "VALUES(?,?,?,?,?)",
            (name, category, subcategory, price, quantity),
        )
        return "created"


def get_item(item_id: int) -> sqlite3.Row | None:
    with _conn() as c:
        return c.execute(
            "SELECT * FROM menu_items WHERE id=?", (item_id,)
        ).fetchone()


def get_item_by_name(name: str) -> sqlite3.Row | None:
    with _conn() as c:
        return c.execute(
            "SELECT * FROM menu_items WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()


def subcategories(category: str, only_available: bool = False) -> list[str]:
    q = "SELECT DISTINCT subcategory FROM menu_items WHERE category=?"
    if only_available:
        q += " AND available=1"
    q += " ORDER BY subcategory"
    with _conn() as c:
        return [r["subcategory"] for r in c.execute(q, (category,)).fetchall()]


def items_in(category: str, subcategory: str,
             only_available: bool = False) -> list[sqlite3.Row]:
    q = "SELECT * FROM menu_items WHERE category=? AND subcategory=?"
    if only_available:
        q += " AND available=1"
    q += " ORDER BY name"
    with _conn() as c:
        return c.execute(q, (category, subcategory)).fetchall()


def all_items() -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute(
            "SELECT * FROM menu_items ORDER BY category, subcategory, name"
        ).fetchall()


def items_without_image() -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute(
            "SELECT * FROM menu_items WHERE image_file_id IS NULL ORDER BY name"
        ).fetchall()


def set_image(item_id: int, file_id: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE menu_items SET image_file_id=? WHERE id=?", (file_id, item_id)
        )


def adjust_qty(item_id: int, delta: int) -> int | None:
    """Clamp at zero; returns the new quantity."""
    with _conn() as c:
        c.execute(
            "UPDATE menu_items SET quantity=MAX(0, quantity+?) WHERE id=?",
            (delta, item_id),
        )
        row = c.execute(
            "SELECT quantity FROM menu_items WHERE id=?", (item_id,)
        ).fetchone()
        return row["quantity"] if row else None


def set_qty(item_id: int, qty: int) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE menu_items SET quantity=? WHERE id=?", (max(0, qty), item_id)
        )


def toggle_available(item_id: int) -> int:
    with _conn() as c:
        c.execute(
            "UPDATE menu_items SET available=1-available WHERE id=?", (item_id,)
        )
        return c.execute(
            "SELECT available FROM menu_items WHERE id=?", (item_id,)
        ).fetchone()["available"]


def delete_item(item_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM menu_items WHERE id=?", (item_id,))


# ---------------------------------------------------------------- orders ----

def create_order(user_id: int, phone: str, address: str, kaspi: str,
                 cart: dict[int, int]) -> int:
    """Snapshot names/prices so later menu edits don't rewrite history."""
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO orders(user_id, phone, address, kaspi) VALUES(?,?,?,?)",
            (user_id, phone, address, kaspi),
        )
        order_id = cur.lastrowid
        for item_id, qty in cart.items():
            it = c.execute(
                "SELECT name, price FROM menu_items WHERE id=?", (item_id,)
            ).fetchone()
            name = it["name"] if it else f"item #{item_id}"
            price = it["price"] if it else None
            c.execute(
                "INSERT INTO order_items(order_id, item_id, name, price, qty) "
                "VALUES(?,?,?,?,?)",
                (order_id, item_id, name, price, qty),
            )
        return order_id


def get_order(order_id: int) -> dict | None:
    with _conn() as c:
        o = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not o:
            return None
        lines = c.execute(
            "SELECT * FROM order_items WHERE order_id=?", (order_id,)
        ).fetchall()
        customer = c.execute(
            "SELECT * FROM users WHERE tg_id=?", (o["user_id"],)
        ).fetchone()
        return {"order": o, "lines": lines, "customer": customer}


def user_orders(user_id: int, limit: int = 5) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [get_order(r["id"]) for r in rows]


def open_orders() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id FROM orders WHERE status IN (?,?) ORDER BY id",
            (ST_PENDING, ST_ACCEPTED),
        ).fetchall()
    return [get_order(r["id"]) for r in rows]


def accept_order(order_id: int, barista_id: int) -> tuple[bool, list[str]]:
    """Atomically flip pending->accepted AND decrement stock for every line.

    Returns (ok, shortages). On any shortage the whole transaction rolls
    back, the order stays pending, and the shortage names are reported.
    """
    c = sqlite3.connect(_DB_PATH, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    try:
        c.execute("BEGIN IMMEDIATE")
        cur = c.execute(
            "UPDATE orders SET status=?, handled_by=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status=?",
            (ST_ACCEPTED, barista_id, order_id, ST_PENDING),
        )
        if cur.rowcount == 0:          # already handled by someone else
            c.execute("ROLLBACK")
            return False, ["__already_handled__"]
        shortages = []
        lines = c.execute(
            "SELECT * FROM order_items WHERE order_id=?", (order_id,)
        ).fetchall()
        for ln in lines:
            cur = c.execute(
                "UPDATE menu_items SET quantity=quantity-? "
                "WHERE id=? AND available=1 AND quantity>=?",
                (ln["qty"], ln["item_id"], ln["qty"]),
            )
            if cur.rowcount == 0:
                shortages.append(ln["name"])
        if shortages:
            c.execute("ROLLBACK")
            return False, shortages
        c.execute("COMMIT")
        return True, []
    finally:
        c.close()


def reject_order(order_id: int, barista_id: int, reason: str) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE orders SET status=?, handled_by=?, reason=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status=?",
            (ST_REJECTED, barista_id, reason, order_id, ST_PENDING),
        )
        return cur.rowcount > 0


def complete_order(order_id: int, barista_id: int) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE orders SET status=?, handled_by=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status=?",
            (ST_COMPLETED, barista_id, order_id, ST_ACCEPTED),
        )
        return cur.rowcount > 0


# ---------------------------------------------- order card bookkeeping ----

def remember_order_msg(order_id: int, chat_id: int, message_id: int) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO order_msgs(order_id, chat_id, message_id) "
            "VALUES(?,?,?)",
            (order_id, chat_id, message_id),
        )


def order_msgs(order_id: int) -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute(
            "SELECT * FROM order_msgs WHERE order_id=?", (order_id,)
        ).fetchall()
