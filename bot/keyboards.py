"""Inline keyboards + tiny formatting helpers."""
from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup as Kb

from . import database as db

# Marker shown next to the Kaspi number. Telegram has no Kaspi glyph and real
# inline "custom emoji" only render for Premium users, so this Unicode stand-in
# is used in plain text. Swap it for anything you like (e.g. "🔴", "📲").
KASPI = "💳"

STATUS_EMOJI = {
    db.ST_PENDING: "🕐 Ожидает",
    db.ST_ACCEPTED: "👩‍🍳 Готовится",
    db.ST_REJECTED: "❌ Отклонён",
    db.ST_COMPLETED: "✅ Доставлен",
}


def money(price, currency: str) -> str:
    return f"{currency}{price:.2f}" if price is not None else ""


def order_total(lines, currency: str) -> str:
    if any(ln["price"] is None for ln in lines):
        return ""
    total = sum(ln["price"] * ln["qty"] for ln in lines)
    return f"\n💰 Итого: {money(total, currency)}"


def order_text(info: dict, currency: str) -> str:
    o, lines, cust = info["order"], info["lines"], info["customer"]
    who = cust["name"] if cust else "?"
    uname = f" (@{cust['username']})" if cust and cust["username"] else ""
    body = "\n".join(
        f"  {ln['qty']} × {ln['name']}"
        + (f" — {money(ln['price'] * ln['qty'], currency)}"
           if ln["price"] is not None else "")
        for ln in lines
    )
    reason = f"\n📝 Причина: {o['reason']}" if o["reason"] else ""
    return (
        f"🧾 Заказ #{o['id']} — {STATUS_EMOJI[o['status']]}\n"
        f"👤 {who}{uname}\n"
        f"📞 {o['phone'] or '—'}\n"
        f"{KASPI} Kaspi: {o['kaspi'] or '—'}\n"
        f"📍 {o['address'] or '—'}\n"
        f"——————————\n{body}{order_total(lines, currency)}\n"
        f"🕐 {o['created_at'][11:16]} UTC ({o['created_at'][:10]}){reason}"
    )


# ------------------------------------------------------------ customer ----

def categories_kb(categories) -> Kb:
    """categories: rows with id + name (from db.customer_categories)."""
    rows = [[Btn(c["name"], callback_data=f"c|{c['id']}")] for c in categories]
    rows.append([Btn("🧺 Моя корзина", callback_data="cart")])
    return Kb(rows)


def subcats_kb(cat_id: int, subs) -> Kb:
    """subs: rows with id + name (from db.customer_subcategories)."""
    rows = [[Btn(s["name"], callback_data=f"s|{s['id']}")] for s in subs]
    rows.append([Btn("⬅️ Назад", callback_data="b|cats"),
                 Btn("🧺 Корзина", callback_data="cart")])
    return Kb(rows)


def items_kb(cat_id: int, items) -> Kb:
    rows = []
    for it in items:
        sold_out = it["quantity"] <= 0
        label = f"{it['name']}" + (" — нет в наличии" if sold_out else "")
        rows.append([Btn(label, callback_data=f"i|{it['id']}")])
    rows.append([Btn("⬅️ Назад", callback_data=f"b|c|{cat_id}"),
                 Btn("🧺 Корзина", callback_data="cart")])
    return Kb(rows)


def item_card_kb(item, sub_id, qty: int) -> Kb:
    iid = item["id"]
    back = Btn("⬅️ Назад", callback_data=f"b|s|{sub_id}")
    if item["quantity"] <= 0:
        return Kb([[Btn("😔 Нет в наличии", callback_data="noop")],
                   [back, Btn("🧺 Корзина", callback_data="cart")]])
    return Kb([
        [Btn("➖", callback_data=f"q|{iid}|{qty}|-"),
         Btn(f"{qty}", callback_data="noop"),
         Btn("➕", callback_data=f"q|{iid}|{qty}|+")],
        [Btn(f"🛒 Добавить {qty} в корзину", callback_data=f"a|{iid}|{qty}")],
        [back, Btn("🧺 Корзина", callback_data="cart")],
    ])


def cart_kb(cart: dict[int, int]) -> Kb:
    rows = []
    for item_id, qty in cart.items():
        it = db.get_item(item_id)
        name = (it["name"] if it else "?")[:18]
        rows.append([
            Btn("➖", callback_data=f"cu|{item_id}|-"),
            Btn(f"{qty} × {name}", callback_data="noop"),
            Btn("➕", callback_data=f"cu|{item_id}|+"),
            Btn("✖", callback_data=f"cx|{item_id}"),
        ])
    bottom = [Btn("☕ Меню", callback_data="menu")]
    if cart:
        bottom = [Btn("🧹 Очистить", callback_data="clr"),
                  Btn("✅ Оформить", callback_data="co")] + bottom
    rows.append(bottom)
    return Kb(rows)


def checkout_kb() -> Kb:
    return Kb([
        [Btn("✅ Оформить заказ", callback_data="ck|place")],
        [Btn("📞 Изменить телефон", callback_data="ck|ph"),
         Btn(f"{KASPI} Изменить Каспи", callback_data="ck|kaspi")],
        [Btn("📍 Изменить адрес", callback_data="ck|ad")],
        [Btn("⬅️ Назад в корзину", callback_data="cart")],
    ])


def kaspi_question_kb() -> Kb:
    return Kb([[Btn("Да", callback_data="ck|kyes"),
                Btn("Нет", callback_data="ck|kno")]])


# --------------------------------------------------------------- barista ----

def order_kb(status: str, order_id: int) -> Kb | None:
    if status == db.ST_PENDING:
        return Kb([[Btn("✅ Принять", callback_data=f"o|a|{order_id}"),
                    Btn("❌ Отклонить", callback_data=f"o|r|{order_id}")]])
    if status == db.ST_ACCEPTED:
        return Kb([[Btn("🏁 Отметить доставленным", callback_data=f"o|d|{order_id}")]])
    return None


# ----------------------------------------------------------------- admin ----

def admin_panel_kb() -> Kb:
    return Kb([
        [Btn("➕ Добавить товар", callback_data="ad|additem")],
        [Btn("🧾 Товары и остатки", callback_data="ad|items")],
        [Btn("🗂 Категории", callback_data="ad|cats"),
         Btn("🏷 Подкатегории", callback_data="ad|subs")],
        [Btn("📊 Импорт меню из Excel", callback_data="ad|xl")],
        [Btn("🖼 Фото товаров", callback_data="ad|img")],
        [Btn("👥 Пользователи и роли", callback_data="ad|users")],
    ])


def admin_items_kb(items) -> Kb:
    rows = [[Btn(f"{'✅' if it['available'] else '🚫'} {it['name']} · {it['quantity']} шт.",
                 callback_data=f"ad|item|{it['id']}")]
            for it in items]
    rows.append([Btn("⬅️ Панель администратора", callback_data="ad|panel")])
    return Kb(rows)


def admin_item_kb(item) -> Kb:
    iid = item["id"]
    return Kb([
        [Btn("−10", callback_data=f"ad|st|{iid}|-10"),
         Btn("−1", callback_data=f"ad|st|{iid}|-1"),
         Btn("＋1", callback_data=f"ad|st|{iid}|1"),
         Btn("＋10", callback_data=f"ad|st|{iid}|10")],
        [Btn("✏️ Точный остаток", callback_data=f"ad|set|{iid}"),
         Btn("🖼 Фото", callback_data=f"ad|imgfor|{iid}")],
        [Btn("✏️ Название", callback_data=f"ad|ename|{iid}"),
         Btn("💵 Цена", callback_data=f"ad|eprice|{iid}")],
        [Btn("🙈 Скрыть из меню" if item["available"] else "👀 Показать в меню",
             callback_data=f"ad|av|{iid}"),
         Btn("🗑 Удалить", callback_data=f"ad|del|{iid}")],
        [Btn("⬅️ Все товары", callback_data="ad|items")],
    ])


def admin_confirm_delete_kb(item_id: int) -> Kb:
    return Kb([[Btn("🗑 Да, удалить", callback_data=f"ad|del2|{item_id}"),
                Btn("↩️ Нет", callback_data=f"ad|item|{item_id}")]])


def admin_img_pick_kb(items) -> Kb:
    rows = [[Btn(("🖼 " if it["image_file_id"] else "⬜ ") + it["name"],
                 callback_data=f"ad|imgfor|{it['id']}")]
            for it in items]
    rows.append([Btn("⬅️ Панель администратора", callback_data="ad|panel")])
    return Kb(rows)


# -- categories / subcategories management --

def admin_categories_kb(categories) -> Kb:
    rows = [[Btn("➕ Добавить категорию", callback_data="ad|catadd")]]
    for c in categories:
        rows.append([Btn(f"🗑 {c['name']}", callback_data=f"ad|catdel|{c['id']}")])
    rows.append([Btn("⬅️ Панель администратора", callback_data="ad|panel")])
    return Kb(rows)


def admin_confirm_delete_cat_kb(cat_id: int) -> Kb:
    return Kb([[Btn("🗑 Да, удалить", callback_data=f"ad|catdel2|{cat_id}"),
                Btn("↩️ Нет", callback_data="ad|cats")]])


def admin_subcats_kb(subs) -> Kb:
    rows = [[Btn("➕ Добавить подкатегорию", callback_data="ad|subadd")]]
    for s in subs:
        rows.append([Btn(f"🗑 {s['category']} · {s['name']}",
                         callback_data=f"ad|subdel|{s['id']}")])
    rows.append([Btn("⬅️ Панель администратора", callback_data="ad|panel")])
    return Kb(rows)


def admin_confirm_delete_sub_kb(sub_id: int) -> Kb:
    return Kb([[Btn("🗑 Да, удалить", callback_data=f"ad|subdel2|{sub_id}"),
                Btn("↩️ Нет", callback_data="ad|subs")]])


def pick_category_kb(categories, prefix: str, back: str) -> Kb:
    """A category chooser. Each button is '<prefix>|<cat_id>'."""
    rows = [[Btn(c["name"], callback_data=f"{prefix}|{c['id']}")] for c in categories]
    rows.append([Btn("⬅️ Назад", callback_data=back)])
    return Kb(rows)


def pick_subcategory_kb(subs, prefix: str, back: str) -> Kb:
    """A subcategory chooser. Each button is '<prefix>|<sub_id>'."""
    rows = [[Btn(s["name"], callback_data=f"{prefix}|{s['id']}")] for s in subs]
    rows.append([Btn("⬅️ Назад", callback_data=back)])
    return Kb(rows)


def skip_photo_kb() -> Kb:
    return Kb([[Btn("⏭ Пропустить (без фото)", callback_data="ad|niskip")]])
