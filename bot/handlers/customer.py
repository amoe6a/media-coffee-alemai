"""Customer side: browse → cart → checkout → order placed."""
import re

from telegram import (KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
                      Update)
from telegram.ext import ContextTypes

from .. import database as db
from .. import keyboards as kb
from .common import clear_states, ensure_user
from . import barista

MAX_PER_LINE = 20


def _normalize_kz_phone(raw: str) -> str | None:
    """Return a cleaned Kazakhstani number, or None if it isn't one.

    Accepts +7XXXXXXXXXX or 8XXXXXXXXXX (11 digits), ignoring spaces,
    dashes and brackets.
    """
    s = re.sub(r"[\s\-()]", "", raw or "")
    if re.fullmatch(r"\+7\d{10}", s) or re.fullmatch(r"8\d{10}", s):
        return s
    return None


def _cart(context) -> dict[int, int]:
    return context.user_data.setdefault("cart", {})


def _card_caption(item, currency: str) -> str:
    price = f"\n💵 {kb.money(item['price'], currency)}" if item["price"] is not None else ""
    stock = ("😔 Нет в наличии" if item["quantity"] <= 0
             else f"📦 Осталось {item['quantity']}")
    return (f"*{item['name']}*\n{item['category']} · {item['subcategory']}"
            f"{price}\n{stock}")


async def _send_item_card(message, item, qty: int, currency: str):
    caption = _card_caption(item, currency)
    markup = kb.item_card_kb(item, qty)
    if item["image_file_id"]:
        await message.reply_photo(item["image_file_id"], caption=caption,
                                  reply_markup=markup, parse_mode="Markdown")
    else:
        await message.reply_text(caption, reply_markup=markup,
                                 parse_mode="Markdown")


# ------------------------------------------------------------- commands ----

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ensure_user(update, context)
    await update.message.reply_text("Что желаете?",
                                    reply_markup=kb.categories_kb())


async def cart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ensure_user(update, context)
    await _show_cart(update.message, context, edit=False)


async def myorders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = ensure_user(update, context)
    currency = context.bot_data["cfg"].currency
    orders = db.user_orders(user["tg_id"])
    if not orders:
        await update.message.reply_text("Заказов пока нет — /menu, чтобы это исправить ☕")
        return
    text = "\n\n".join(kb.order_text(o, currency) for o in orders)
    await update.message.reply_text("*Ваши последние заказы*\n\n" + text,
                                    parse_mode="Markdown")


# ------------------------------------------------------------ browsing ----

async def browse_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback prefixes: c| s| i| q| a| b|"""
    q = update.callback_query
    ensure_user(update, context)
    currency = context.bot_data["cfg"].currency
    parts = q.data.split("|")
    kind = parts[0]

    if kind == "c":                                   # category -> subcats
        category = parts[1]
        subs = db.subcategories(category, only_available=True)
        if not subs:
            await q.answer("Здесь пока пусто 😕", show_alert=True)
            return
        await q.answer()
        await q.edit_message_text(f"*{category}* — выберите раздел:",
                                  reply_markup=kb.subcats_kb(category, subs),
                                  parse_mode="Markdown")

    elif kind == "s":                                 # subcat -> item list
        category, sub = parts[1], parts[2]
        items = db.items_in(category, sub, only_available=True)
        if not items:
            await q.answer("Здесь пока пусто 😕", show_alert=True)
            return
        await q.answer()
        await q.edit_message_text(f"*{category} · {sub}*",
                                  reply_markup=kb.items_kb(category, sub, items),
                                  parse_mode="Markdown")

    elif kind == "i":                                 # item -> photo card
        item = db.get_item(int(parts[1]))
        if not item:
            await q.answer("Этого товара больше нет.", show_alert=True)
            return
        await q.answer()
        await _send_item_card(q.message, item, 1, currency)

    elif kind == "q":                                 # qty +/- on the card
        item = db.get_item(int(parts[1]))
        if not item:
            await q.answer("Этого товара больше нет.", show_alert=True)
            return
        qty = int(parts[2]) + (1 if parts[3] == "+" else -1)
        qty = max(1, min(qty, MAX_PER_LINE, max(item["quantity"], 1)))
        await q.answer()
        try:
            await q.edit_message_reply_markup(kb.item_card_kb(item, qty))
        except Exception:                             # noqa: BLE001  (not modified)
            pass

    elif kind == "a":                                 # add to cart
        item = db.get_item(int(parts[1]))
        qty = int(parts[2])
        if not item or item["quantity"] <= 0:
            await q.answer("Извините, нет в наличии!", show_alert=True)
            return
        cart = _cart(context)
        cart[item["id"]] = min(cart.get(item["id"], 0) + qty, MAX_PER_LINE)
        await q.answer(f"Добавлено {qty} × {item['name']} 🛒")
        try:
            await q.edit_message_reply_markup(kb.item_card_kb(item, 1))
        except Exception:                             # noqa: BLE001
            pass

    elif kind == "b":                                 # back navigation
        await q.answer()
        target = parts[1]
        if target == "cats":
            await q.edit_message_text("Что желаете?",
                                      reply_markup=kb.categories_kb())
        elif target == "c":
            category = parts[2]
            subs = db.subcategories(category, only_available=True)
            await q.edit_message_text(f"*{category}* — выберите раздел:",
                                      reply_markup=kb.subcats_kb(category, subs),
                                      parse_mode="Markdown")
        elif target == "s":                           # from a photo card
            category, sub = parts[2], parts[3]
            items = db.items_in(category, sub, only_available=True)
            try:
                await q.message.delete()
            except Exception:                         # noqa: BLE001
                pass
            await q.message.chat.send_message(
                f"*{category} · {sub}*",
                reply_markup=kb.items_kb(category, sub, items),
                parse_mode="Markdown")


# ----------------------------------------------------------------- cart ----

async def _show_cart(message, context, edit: bool) -> None:
    currency = context.bot_data["cfg"].currency
    cart = _cart(context)
    if not cart:
        text = "Ваша корзина пуста 🧺"
    else:
        lines, total, total_ok = [], 0.0, True
        for item_id, qty in cart.items():
            it = db.get_item(item_id)
            if not it:
                continue
            p = it["price"]
            if p is None:
                total_ok = False
                lines.append(f"• {qty} × {it['name']}")
            else:
                total += p * qty
                lines.append(f"• {qty} × {it['name']} — {kb.money(p * qty, currency)}")
            if qty > it["quantity"]:
                lines[-1] += f"  ⚠️ в наличии только {it['quantity']}"
        text = "🧺 *Ваша корзина*\n" + "\n".join(lines)
        if total_ok and lines:
            text += f"\n\n💰 Итого: {kb.money(total, currency)}"
    markup = kb.cart_kb(cart)
    if edit:
        await message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await message.reply_text(text, reply_markup=markup, parse_mode="Markdown")


async def cart_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback prefixes: cart clr co cu| cx| ck|"""
    q = update.callback_query
    user = ensure_user(update, context)
    data = q.data
    cart = _cart(context)

    if data == "cart":
        await q.answer()
        try:
            await _show_cart(q.message, context, edit=True)
        except Exception:                              # photo card can't be edited to text
            await _show_cart(q.message, context, edit=False)

    elif data == "clr":
        cart.clear()
        await q.answer("Корзина очищена")
        await _show_cart(q.message, context, edit=True)

    elif data.startswith("cu|"):
        _, item_id, op = data.split("|")
        item_id = int(item_id)
        if item_id in cart:
            cart[item_id] = max(1, min(cart[item_id] + (1 if op == "+" else -1),
                                       MAX_PER_LINE))
        await q.answer()
        await _show_cart(q.message, context, edit=True)

    elif data.startswith("cx|"):
        cart.pop(int(data.split("|")[1]), None)
        await q.answer("Удалено")
        await _show_cart(q.message, context, edit=True)

    elif data == "co":                                 # checkout
        if not cart:
            await q.answer("Корзина пуста!", show_alert=True)
            return
        await q.answer()
        await _start_checkout(q.message, context, user)

    elif data.startswith("ck|"):
        await _checkout_cb(update, context, user)


# ------------------------------------------------------------- checkout ----

def _checkout_summary(user, context) -> str:
    currency = context.bot_data["cfg"].currency
    cart = _cart(context)
    lines = []
    for item_id, qty in cart.items():
        it = db.get_item(item_id)
        if it:
            lines.append(f"• {qty} × {it['name']}")
    return ("*Подтвердите заказ* 📋\n" + "\n".join(lines)
            + f"\n\n📞 {user['phone']}"
            + f"\n{kb.KASPI} Kaspi: {user['kaspi']}"
            + f"\n📍 {user['address']}")


async def _start_checkout(message, context, user) -> None:
    if not user["phone"]:
        await _ask_phone(message, context)
    elif not user["kaspi"]:
        await _ask_kaspi_question(message, context)
    elif not user["address"]:
        await _ask_address(message, context)
    else:
        await message.reply_text(_checkout_summary(user, context),
                                 reply_markup=kb.checkout_kb(),
                                 parse_mode="Markdown")


async def _ask_phone(message, context) -> None:
    context.user_data["await_phone"] = True
    btn = KeyboardButton("📱 Поделиться номером", request_contact=True)
    await message.reply_text(
        "По какому номеру с вами может связаться курьер?\n"
        "Нажмите кнопку ниже или просто введите его. (/cancel — отмена)",
        reply_markup=ReplyKeyboardMarkup([[btn]], resize_keyboard=True,
                                         one_time_keyboard=True),
    )


async def _ask_kaspi_question(message, context) -> None:
    """The Да/Нет step asked right after we have a phone number."""
    await message.reply_text(
        "Чтобы принять заказ, мы отправляем удаленную оплату на Каспи. "
        "У вас Каспи на этом номере?",
        reply_markup=kb.kaspi_question_kb(),
    )


async def _ask_kaspi_number(message, context) -> None:
    context.user_data["await_kaspi"] = True
    await message.reply_text(
        "Введите номер Каспи для удаленной оплаты:",
        reply_markup=ReplyKeyboardRemove(),
    )


async def _ask_address(message, context) -> None:
    context.user_data["await_address"] = True
    await message.reply_text(
        "Куда доставить? Введите адрес. (/cancel — отмена)",
        reply_markup=ReplyKeyboardRemove(),
    )


async def _advance_after_contact(message, context, tg_id: int) -> None:
    """Once a contact detail is saved, move to address or the final summary."""
    user = db.get_user(tg_id)
    if not user["address"]:
        await _ask_address(message, context)
    else:
        await message.reply_text(_checkout_summary(user, context),
                                 reply_markup=kb.checkout_kb(),
                                 parse_mode="Markdown")


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.pop("await_phone", None):
        return
    user = ensure_user(update, context)
    db.set_contact(user["tg_id"], phone=update.message.contact.phone_number)
    await _after_phone(update, context)


async def maybe_checkout_text(update: Update,
                              context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = ensure_user(update, context)
    text = update.message.text.strip()

    if context.user_data.get("await_phone"):
        if sum(ch.isdigit() for ch in text) < 5:
            await update.message.reply_text("Это не похоже на номер телефона — "
                                            "попробуйте ещё раз или /cancel.")
            return True
        context.user_data.pop("await_phone", None)
        db.set_contact(user["tg_id"], phone=text)
        await _after_phone(update, context)
        return True

    if context.user_data.get("await_kaspi"):
        norm = _normalize_kz_phone(text)
        if not norm:
            await update.message.reply_text(
                "Это не похоже на номер Каспи (Казахстан). Номер должен "
                "начинаться с +7 или 8 и содержать 11 цифр. "
                "Попробуйте ещё раз или /cancel.")
            return True
        context.user_data.pop("await_kaspi", None)
        db.set_contact(user["tg_id"], kaspi=norm)
        await update.message.reply_text("Номер Каспи сохранён ✅",
                                        reply_markup=ReplyKeyboardRemove())
        await _advance_after_contact(update.message, context, user["tg_id"])
        return True

    if context.user_data.get("await_address"):
        if len(text) < 5:
            await update.message.reply_text("Адрес слишком короткий — "
                                            "попробуйте ещё раз или /cancel.")
            return True
        context.user_data.pop("await_address", None)
        db.set_contact(user["tg_id"], address=text)
        user = db.get_user(user["tg_id"])
        await update.message.reply_text("Принято 👌",
                                        reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text(_checkout_summary(user, context),
                                        reply_markup=kb.checkout_kb(),
                                        parse_mode="Markdown")
        return True
    return False


async def _after_phone(update: Update, context) -> None:
    """Phone is saved — now ask whether Kaspi is on that number."""
    await update.message.reply_text("Телефон сохранён ✅",
                                    reply_markup=ReplyKeyboardRemove())
    await _ask_kaspi_question(update.message, context)


async def _checkout_cb(update: Update, context, user) -> None:
    q = update.callback_query
    action = q.data.split("|")[1]

    if action == "ph":
        await q.answer()
        await _ask_phone(q.message, context)
    elif action == "ad":
        await q.answer()
        await _ask_address(q.message, context)
    elif action == "kaspi":                            # edit Kaspi from summary
        await q.answer()
        await _ask_kaspi_number(q.message, context)
    elif action == "kyes":                             # "Да" → Kaspi = phone
        user = db.get_user(user["tg_id"])
        if not user["phone"]:
            await q.answer()
            await _ask_phone(q.message, context)
            return
        db.set_contact(user["tg_id"], kaspi=user["phone"])
        await q.answer("Каспи на этом номере ✅")
        await _advance_after_contact(q.message, context, user["tg_id"])
    elif action == "kno":                              # "Нет" → ask Kaspi number
        await q.answer()
        await _ask_kaspi_number(q.message, context)
    elif action == "place":
        cart = _cart(context)
        if not cart:
            await q.answer("Корзина пуста!", show_alert=True)
            return
        user = db.get_user(user["tg_id"])
        if not user["phone"] or not user["kaspi"] or not user["address"]:
            await q.answer("Сначала нужны телефон, Каспи и адрес.",
                           show_alert=True)
            return
        order_id = db.create_order(user["tg_id"], user["phone"],
                                   user["address"], user["kaspi"], cart)
        cart.clear()
        clear_states(context)
        await q.answer()
        await q.edit_message_text(
            f"🎉 Заказ *#{order_id}* оформлен!\n"
            "Бариста скоро его подтвердит — я буду держать вас в курсе здесь.",
            parse_mode="Markdown")
        await barista.notify_new_order(context, order_id)
