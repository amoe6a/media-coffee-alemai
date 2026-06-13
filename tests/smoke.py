"""Offline sanity check — no Telegram token or network needed:

    BOT_TOKEN=123:ABC python tests/smoke.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("BOT_TOKEN", "123:ABC")

from bot import config, database as db          # noqa: E402
from bot.excel import parse_menu_xlsx           # noqa: E402
from bot.main import build_app                  # noqa: E402


def check(label, cond):
    print(("✔" if cond else "✘"), label)
    if not cond:
        sys.exit(1)


# ---------------------------------------------------------------- database
tmp = tempfile.mkdtemp()
db.init_db(os.path.join(tmp, "t.db"))

u = db.ensure_user(1, "Alice", "alice", env_admin_ids=[1])
check("env admin auto-promote", u["role"] == "admin")
db.ensure_user(2, "Bob", None, env_admin_ids=[1])
check("setrole barista", db.set_role(2, "barista"))
check("staff ids", db.staff_ids([1]) == [1, 2])

check("upsert create", db.upsert_item("Latte", "Drinks", "Coffee", 4.5, 10) == "created")
check("upsert update", db.upsert_item("latte", "Drinks", "Coffee", 4.0, 8) == "updated")
db.upsert_item("Croissant", "Food", "Snacks", 2.5, 1)
latte = db.get_item_by_name("LATTE")
check("nocase lookup + update", latte and latte["quantity"] == 8 and latte["price"] == 4.0)
check("subcategories", db.subcategories("Drinks") == ["Coffee"])
check("adjust clamps at 0", db.adjust_qty(latte["id"], -100) == 0)
db.set_qty(latte["id"], 8)

cro = db.get_item_by_name("Croissant")
db.ensure_user(3, "Cara", None, env_admin_ids=[])
db.set_contact(3, phone="+1 555", address="Main st 1", kaspi="+77018889900")
check("kaspi stored on user", db.get_user(3)["kaspi"] == "+77018889900")
order_id = db.create_order(3, "+1 555", "Main st 1", "+77018889900",
                           {latte["id"]: 2, cro["id"]: 2})
check("kaspi snapshotted on order", db.get_order(order_id)["order"]["kaspi"] == "+77018889900")

ok, shortages = db.accept_order(order_id, 2)
check("accept blocked by shortage", not ok and shortages == ["Croissant"])
check("rollback kept latte stock", db.get_item(latte["id"])["quantity"] == 8)
check("order still pending", db.get_order(order_id)["order"]["status"] == "pending")

db.set_qty(cro["id"], 5)
ok, shortages = db.accept_order(order_id, 2)
check("accept succeeds", ok and not shortages)
check("stock decremented", db.get_item(latte["id"])["quantity"] == 6
      and db.get_item(cro["id"])["quantity"] == 3)
ok2, why = db.accept_order(order_id, 1)
check("double accept guarded", not ok2 and why == ["__already_handled__"])
check("complete", db.complete_order(order_id, 2))

o2 = db.create_order(3, "+1 555", "Main st 1", "+77018889900", {latte["id"]: 1})
check("reject", db.reject_order(o2, 2, "Out of milk"))
check("reason stored", db.get_order(o2)["order"]["reason"] == "Out of milk")

db.remember_order_msg(order_id, 1, 100)
db.remember_order_msg(order_id, 1, 100)          # duplicate ignored
check("order msg bookkeeping", len(db.order_msgs(order_id)) == 1)

# ------------------------------------------------------------------- excel
sample = os.path.join(os.path.dirname(__file__), "..", "sample_menu.xlsx")
with open(sample, "rb") as f:
    parsed = parse_menu_xlsx(f.read())
check("sample parses 11 rows", len(parsed.rows) == 11)
check("sample has 3 embedded photos", len(parsed.images) == 3)
check("no parse errors", not parsed.errors)
check("price optional handling", all(r["price"] is not None for r in parsed.rows))

bad = parse_menu_xlsx(b"not an xlsx")
check("garbage input handled", bad.errors and not bad.rows)

# -------------------------------------------------------------- app build
cfg = config.load()
app = build_app(cfg)
check("app builds with handlers", len(app.handlers[0]) >= 15)
check("webhook path derived", len(cfg.url_path) == 24 and len(cfg.webhook_secret) == 32)

# ------------------------------------------------------ KZ phone validation
from bot.handlers.customer import _normalize_kz_phone   # noqa: E402
check("KZ +7 accepted", _normalize_kz_phone("+7 701 888 99 00") == "+77018889900")
check("KZ 8 accepted", _normalize_kz_phone("8(701)888-99-00") == "87018889900")
check("non-KZ rejected", _normalize_kz_phone("+1 555 123 4567") is None)
check("too short rejected", _normalize_kz_phone("+7 701 88") is None)
check("garbage rejected", _normalize_kz_phone("call me") is None)

print("\nAll smoke tests passed ✅")
