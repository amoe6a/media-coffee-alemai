"""Admin side: Excel import, stock, photos, roles."""
import io
import logging

from telegram import Update
from telegram.ext import ContextTypes

from .. import database as db
from .. import keyboards as kb
from ..excel import parse_menu_xlsx
from .common import ensure_user

log = logging.getLogger(__name__)

XLSX_HELP = (
    "Отправьте меню файлом *.xlsx* 📊\n\n"
    "Строка заголовков: *Name, Category, Subcategory, Price, Quantity*\n"
    "• Category — любая категория (создаётся автоматически, если её ещё нет)\n"
    "• Subcategory — любой раздел внутри категории (тоже создаётся автоматически)\n"
    "• Price — необязательно\n"
    "• Фото — вставьте изображения прямо в таблицу рядом со строкой товара, "
    "я подхвачу их автоматически.\n\n"
    "Повторная загрузка обновляет существующие товары по названию (фото сохраняются). "
    "В папке проекта есть готовый `sample_menu.xlsx`."
)


def _is_admin(user) -> bool:
    return user["role"] == db.ROLE_ADMIN


async def _guard(update: Update, context) -> bool:
    user = ensure_user(update, context)
    if _is_admin(user):
        return True
    if update.callback_query:
        await update.callback_query.answer("Только для администраторов 🙂", show_alert=True)
    else:
        await update.message.reply_text("Только для администраторов 🙂")
    return False


# ------------------------------------------------------------- commands ----

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.message.reply_text("🔧 *Панель администратора*",
                                    reply_markup=kb.admin_panel_kb(),
                                    parse_mode="Markdown")


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    rows = db.list_users()
    lines = [
        f"`{u['tg_id']}` — {u['name']}"
        + (f" (@{u['username']})" if u["username"] else "")
        + f" — *{u['role']}*"
        for u in rows
    ]
    await update.message.reply_text(
        "👥 *Зарегистрированные пользователи*\n(появляются здесь после нажатия /start)\n\n"
        + "\n".join(lines)
        + "\n\nНазначить роль:\n`/setrole <id> barista` или `/setrole <id> admin`",
        parse_mode="Markdown")


async def setrole_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    args = context.args or []
    if len(args) != 2 or not args[0].lstrip("-").isdigit() \
            or args[1].lower() not in db.ROLES:
        await update.message.reply_text(
            "Использование: /setrole <telegram_id> <customer|barista|admin>\n"
            "ID можно найти через /users.")
        return
    tg_id, role = int(args[0]), args[1].lower()
    if db.set_role(tg_id, role):
        await update.message.reply_text(f"✅ {tg_id} теперь *{role}*.",
                                        parse_mode="Markdown")
        try:
            await context.bot.send_message(
                tg_id, f"☕ Ваша роль изменена на *{role}*." +
                ("\nТеперь новые заказы будут приходить в этот чат. /queue показывает "
                 "открытые." if role in (db.ROLE_BARISTA, db.ROLE_ADMIN) else ""),
                parse_mode="Markdown")
        except Exception:                                 # noqa: BLE001
            pass
    else:
        await update.message.reply_text(
            "Этот пользователь ещё не писал боту — попросите его нажать "
            "/start, затем проверьте /users.")


# -------------------------------------------------------------- callbacks ----

async def admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback prefix: ad|..."""
    if not await _guard(update, context):
        return
    q = update.callback_query
    parts = q.data.split("|")
    action = parts[1]

    if action == "panel":
        await q.answer()
        await _safe_edit(q, "🔧 *Панель администратора*", kb.admin_panel_kb())

    elif action == "xl":
        await q.answer()
        await q.message.reply_text(XLSX_HELP, parse_mode="Markdown")

    elif action == "items":
        items = db.all_items()
        await q.answer()
        if not items:
            await _safe_edit(q, "Меню пусто — добавьте товар (➕) или импортируйте Excel.",
                             kb.admin_panel_kb())
            return
        await _safe_edit(q, "🧾 *Товары* (нажмите для управления)",
                         kb.admin_items_kb(items))

    # ---- categories ----
    elif action == "cats":
        await q.answer()
        await _safe_edit(q, "🗂 *Категории*\nНажмите 🗑 чтобы удалить категорию "
                         "(вместе с её подкатегориями и товарами).",
                         kb.admin_categories_kb(db.list_categories()))

    elif action == "catadd":
        context.user_data["await_cat"] = True
        await q.answer()
        await q.message.reply_text("Введите название новой категории "
                                   "(например «Напитки»), или /cancel.")

    elif action == "catdel":
        info = db.category_delete_counts(int(parts[2]))
        await q.answer()
        if not info:
            await _safe_edit(q, "Категория не найдена.", kb.admin_panel_kb())
            return
        name, n_items, n_subs = info
        await q.message.reply_text(
            f"Удалить категорию *{name}*?\nБудут также удалены: подкатегорий "
            f"— {n_subs}, товаров — {n_items}.",
            reply_markup=kb.admin_confirm_delete_cat_kb(int(parts[2])),
            parse_mode="Markdown")

    elif action == "catdel2":
        info = db.category_delete_counts(int(parts[2]))
        db.delete_category(int(parts[2]))
        await q.answer("Удалено")
        name = info[0] if info else "Категория"
        await _safe_edit(q, f"🗑 Категория *{name}* удалена.",
                         kb.admin_categories_kb(db.list_categories()))

    # ---- subcategories ----
    elif action == "subs":
        await q.answer()
        await _safe_edit(q, "🏷 *Подкатегории*\nНажмите 🗑 чтобы удалить "
                         "(вместе с её товарами).",
                         kb.admin_subcats_kb(db.list_subcategories()))

    elif action == "subadd":
        cats = db.list_categories()
        await q.answer()
        if not cats:
            await q.message.reply_text("Сначала добавьте хотя бы одну категорию "
                                       "(🗂 Категории).")
            return
        await q.message.reply_text(
            "Выберите категорию, в которую добавить подкатегорию:",
            reply_markup=kb.pick_category_kb(cats, "ad|subfor", "ad|subs"))

    elif action == "subfor":
        cat = db.get_category(int(parts[2]))
        await q.answer()
        if not cat:
            await q.message.reply_text("Категория не найдена.")
            return
        context.user_data["await_sub"] = cat["name"]
        await q.message.reply_text(
            f"Введите название подкатегории для «{cat['name']}» "
            "(например «Кофе»), или /cancel.")

    elif action == "subdel":
        info = db.subcategory_delete_counts(int(parts[2]))
        await q.answer()
        if not info:
            await _safe_edit(q, "Подкатегория не найдена.", kb.admin_panel_kb())
            return
        cat, name, n_items = info
        await q.message.reply_text(
            f"Удалить подкатегорию *{cat} · {name}*?\nБудут также удалены "
            f"товаров: {n_items}.",
            reply_markup=kb.admin_confirm_delete_sub_kb(int(parts[2])),
            parse_mode="Markdown")

    elif action == "subdel2":
        info = db.subcategory_delete_counts(int(parts[2]))
        db.delete_subcategory(int(parts[2]))
        await q.answer("Удалено")
        label = f"{info[0]} · {info[1]}" if info else "Подкатегория"
        await _safe_edit(q, f"🗑 Подкатегория *{label}* удалена.",
                         kb.admin_subcats_kb(db.list_subcategories()))

    # ---- manual add item ----
    elif action == "additem":
        cats = db.list_categories()
        await q.answer()
        if not cats:
            await q.message.reply_text("Сначала добавьте категорию (🗂 Категории) "
                                       "и подкатегорию (🏷 Подкатегории).")
            return
        context.user_data["new_item"] = {}
        await q.message.reply_text(
            "➕ *Новый товар.* Выберите категорию:",
            reply_markup=kb.pick_category_kb(cats, "ad|nicat", "ad|panel"),
            parse_mode="Markdown")

    elif action == "nicat":
        cat = db.get_category(int(parts[2]))
        await q.answer()
        if not cat:
            await q.message.reply_text("Категория не найдена.")
            return
        subs = db.list_subcategories(cat["name"])
        if not subs:
            await q.message.reply_text(
                f"В категории «{cat['name']}» нет подкатегорий. Сначала добавьте "
                "их в 🏷 Подкатегории, затем повторите ➕ Добавить товар.")
            return
        context.user_data.setdefault("new_item", {})["category"] = cat["name"]
        await q.message.reply_text(
            f"Категория: *{cat['name']}*. Теперь выберите подкатегорию:",
            reply_markup=kb.pick_subcategory_kb(subs, "ad|nisub", "ad|additem"),
            parse_mode="Markdown")

    elif action == "nisub":
        sub = db.get_subcategory(int(parts[2]))
        await q.answer()
        if not sub:
            await q.message.reply_text("Подкатегория не найдена.")
            return
        ni = context.user_data.setdefault("new_item", {})
        ni["category"] = sub["category"]
        ni["subcategory"] = sub["name"]
        context.user_data["await_item_name"] = True
        await q.message.reply_text(
            f"Раздел: *{sub['category']} · {sub['name']}*.\n"
            "Введите название товара:", parse_mode="Markdown")

    elif action == "niskip":                            # finish add w/o photo
        item_id = context.user_data.pop("await_item_photo", None)
        context.user_data.pop("new_item", None)
        await q.answer("Готово")
        if item_id:
            await _send_admin_card(q, context, item_id)

    # ---- edit item name / price ----
    elif action == "ename":
        context.user_data["await_ename"] = int(parts[2])
        await q.answer()
        await q.message.reply_text("Введите новое название товара, или /cancel.")

    elif action == "eprice":
        context.user_data["await_eprice"] = int(parts[2])
        await q.answer()
        await q.message.reply_text(
            "Введите новую цену (например 4.50). Чтобы убрать цену — отправьте «-». "
            "Или /cancel.")

    elif action == "item":
        await q.answer()
        await _send_admin_card(q, context, int(parts[2]))

    elif action == "st":                               # stock +- delta
        item_id, delta = int(parts[2]), int(parts[3])
        db.adjust_qty(item_id, delta)
        await q.answer()
        await _update_admin_card(q, context, item_id)

    elif action == "set":
        context.user_data["await_stock"] = int(parts[2])
        await q.answer()
        await q.message.reply_text("Введите точное количество (или /cancel).")

    elif action == "av":
        db.toggle_available(int(parts[2]))
        await q.answer()
        await _update_admin_card(q, context, int(parts[2]))

    elif action == "del":
        item = db.get_item(int(parts[2]))
        await q.answer()
        if item:
            await q.message.reply_text(
                f"Удалить *{item['name']}* из меню?",
                reply_markup=kb.admin_confirm_delete_kb(item["id"]),
                parse_mode="Markdown")

    elif action == "del2":
        item = db.get_item(int(parts[2]))
        db.delete_item(int(parts[2]))
        await q.answer("Удалено")
        await _safe_edit(q, f"🗑 *{item['name'] if item else 'Товар'}* удалён.",
                         kb.admin_panel_kb())

    elif action == "img":
        items = db.all_items()
        missing = len(db.items_without_image())
        await q.answer()
        if not items:
            await q.message.reply_text("Меню пусто — сначала импортируйте товары.")
            return
        await q.message.reply_text(
            f"🖼 Выберите товар, затем отправьте его фото.\n"
            f"(без фото пока: {missing} — отмечены ⬜)\n\n"
            "Подсказка: отправьте фото с *названием товара в подписи*.",
            reply_markup=kb.admin_img_pick_kb(items), parse_mode="Markdown")

    elif action == "imgfor":
        item = db.get_item(int(parts[2]))
        if not item:
            await q.answer("Товар не найден.", show_alert=True)
            return
        context.user_data["await_img"] = item["id"]
        await q.answer()
        await q.message.reply_text(
            f"📷 Теперь отправьте фото для *{item['name']}* (или /cancel).",
            parse_mode="Markdown")

    elif action == "users":
        await q.answer()
        update_msg = Update(update.update_id, message=q.message)
        # reuse the command body by faking a message update is fragile;
        # simply render here instead:
        rows = db.list_users()
        lines = [f"`{u['tg_id']}` — {u['name']} — *{u['role']}*" for u in rows]
        await q.message.reply_text(
            "👥 *Пользователи*\n" + "\n".join(lines) +
            "\n\n`/setrole <id> barista` — назначить роль.",
            parse_mode="Markdown")


async def _safe_edit(q, text, markup) -> None:
    try:
        await q.edit_message_text(text, reply_markup=markup,
                                  parse_mode="Markdown")
    except Exception:                                   # noqa: BLE001
        await q.message.reply_text(text, reply_markup=markup,
                                   parse_mode="Markdown")


def _admin_caption(item, currency) -> str:
    return (f"*{item['name']}*\n{item['category']} · {item['subcategory']}\n"
            f"💵 {kb.money(item['price'], currency) or '—'}   "
            f"📦 {item['quantity']} шт.   "
            f"{'✅ виден' if item['available'] else '🚫 скрыт'}")


async def _send_admin_card(q, context, item_id: int) -> None:
    item = db.get_item(item_id)
    if not item:
        await q.message.reply_text("Товар не найден.")
        return
    currency = context.bot_data["cfg"].currency
    caption = _admin_caption(item, currency)
    markup = kb.admin_item_kb(item)
    if item["image_file_id"]:
        await q.message.reply_photo(item["image_file_id"], caption=caption,
                                    reply_markup=markup, parse_mode="Markdown")
    else:
        await q.message.reply_text(caption, reply_markup=markup,
                                   parse_mode="Markdown")


async def _update_admin_card(q, context, item_id: int) -> None:
    item = db.get_item(item_id)
    if not item:
        return
    currency = context.bot_data["cfg"].currency
    caption = _admin_caption(item, currency)
    markup = kb.admin_item_kb(item)
    try:
        if q.message.photo:
            await q.edit_message_caption(caption=caption, reply_markup=markup,
                                         parse_mode="Markdown")
        else:
            await q.edit_message_text(caption, reply_markup=markup,
                                      parse_mode="Markdown")
    except Exception:                                   # noqa: BLE001
        pass


# ----------------------------------------------------------- text/photo ----

def _parse_price(text: str):
    """Return (ok, price_or_None). '-' means no price."""
    text = text.strip()
    if text in ("-", "—", ""):
        return True, None
    try:
        val = round(float(text.replace(",", ".")), 2)
        return (True, val) if val >= 0 else (False, None)
    except ValueError:
        return False, None


async def _send_card_to_message(message, context, item_id: int) -> None:
    item = db.get_item(item_id)
    if not item:
        await message.reply_text("Товар не найден.")
        return
    currency = context.bot_data["cfg"].currency
    caption = _admin_caption(item, currency)
    markup = kb.admin_item_kb(item)
    if item["image_file_id"]:
        await message.reply_photo(item["image_file_id"], caption=caption,
                                  reply_markup=markup, parse_mode="Markdown")
    else:
        await message.reply_text(caption, reply_markup=markup,
                                 parse_mode="Markdown")


async def maybe_admin_text(update: Update,
                           context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle every admin text-input state (stock, names, prices, etc.)."""
    ud = context.user_data
    if not any(ud.get(k) for k in
               ("await_stock", "await_cat", "await_sub", "await_item_name",
                "await_item_price", "await_ename", "await_eprice")):
        return False
    user = ensure_user(update, context)
    if not _is_admin(user):
        for k in ("await_stock", "await_cat", "await_sub", "await_item_name",
                  "await_item_price", "await_ename", "await_eprice"):
            ud.pop(k, None)
        return False
    text = update.message.text.strip()

    # --- exact stock for an existing item ---
    if ud.get("await_stock"):
        item_id = ud["await_stock"]
        if not text.isdigit():
            await update.message.reply_text("Просто число, пожалуйста (или /cancel).")
            return True
        ud.pop("await_stock", None)
        db.set_qty(item_id, int(text))
        item = db.get_item(item_id)
        await update.message.reply_text(
            f"📦 Остаток *{item['name']}* установлен: *{item['quantity']}*.",
            parse_mode="Markdown")
        return True

    # --- new category ---
    if ud.get("await_cat"):
        ok, res = db.add_category(text)
        if not ok and res == "empty":
            await update.message.reply_text("Название пустое — попробуйте ещё раз "
                                            "или /cancel.")
            return True
        ud.pop("await_cat", None)
        if not ok and res == "exists":
            await update.message.reply_text("Такая категория уже есть.")
        else:
            await update.message.reply_text(f"✅ Категория «{res}» добавлена.")
        await update.message.reply_text(
            "🗂 *Категории*", reply_markup=kb.admin_categories_kb(db.list_categories()),
            parse_mode="Markdown")
        return True

    # --- new subcategory (await_sub holds the parent category name) ---
    if ud.get("await_sub"):
        category = ud["await_sub"]
        ok, res = db.add_subcategory(category, text)
        if not ok and res == "empty":
            await update.message.reply_text("Название пустое — попробуйте ещё раз "
                                            "или /cancel.")
            return True
        ud.pop("await_sub", None)
        if not ok and res == "exists":
            await update.message.reply_text("Такая подкатегория уже есть.")
        else:
            await update.message.reply_text(
                f"✅ Подкатегория «{res}» добавлена в «{category}».")
        await update.message.reply_text(
            "🏷 *Подкатегории*",
            reply_markup=kb.admin_subcats_kb(db.list_subcategories()),
            parse_mode="Markdown")
        return True

    # --- manual add item: name ---
    if ud.get("await_item_name"):
        ni = ud.get("new_item") or {}
        if not ni.get("category") or not ni.get("subcategory"):
            ud.pop("await_item_name", None); ud.pop("new_item", None)
            await update.message.reply_text("Что-то пошло не так — начните заново "
                                            "через ➕ Добавить товар.")
            return True
        name = db.clean_name(text, 60)
        if not name:
            await update.message.reply_text("Название пустое — попробуйте ещё раз.")
            return True
        if db.get_item_by_name(name):
            await update.message.reply_text("Товар с таким названием уже есть. "
                                            "Введите другое название или /cancel.")
            return True
        ni["name"] = name
        ud["new_item"] = ni
        ud.pop("await_item_name", None)
        ud["await_item_price"] = True
        await update.message.reply_text(
            "Введите цену товара (например 4.50). Если без цены — отправьте «-».")
        return True

    # --- manual add item: price (then create + ask for photo) ---
    if ud.get("await_item_price"):
        ok, price = _parse_price(text)
        if not ok:
            await update.message.reply_text("Не понял цену. Введите число "
                                            "(например 4.50) или «-».")
            return True
        ni = ud.get("new_item") or {}
        ud.pop("await_item_price", None)
        item_id, msg = db.create_menu_item(ni.get("category"), ni.get("subcategory"),
                                           ni.get("name", ""), price)
        if not item_id:
            ud.pop("new_item", None)
            await update.message.reply_text("Не удалось создать товар (возможно, "
                                            "название занято). Начните заново.")
            return True
        ud["await_item_photo"] = item_id
        await update.message.reply_text(
            "✅ Товар создан (остаток 0 — задайте его в карточке товара).\n"
            "Отправьте фото товара или нажмите «Пропустить».",
            reply_markup=kb.skip_photo_kb())
        return True

    # --- edit name ---
    if ud.get("await_ename"):
        item_id = ud["await_ename"]
        ok, res = db.rename_item(item_id, text)
        if not ok and res == "empty":
            await update.message.reply_text("Название пустое — попробуйте ещё раз.")
            return True
        ud.pop("await_ename", None)
        if not ok and res == "exists":
            await update.message.reply_text("Такое название уже занято другим товаром.")
        else:
            await update.message.reply_text("✅ Название обновлено.")
        await _send_card_to_message(update.message, context, item_id)
        return True

    # --- edit price ---
    if ud.get("await_eprice"):
        item_id = ud["await_eprice"]
        ok, price = _parse_price(text)
        if not ok:
            await update.message.reply_text("Не понял цену. Введите число "
                                            "(например 4.50) или «-».")
            return True
        ud.pop("await_eprice", None)
        db.set_price(item_id, price)
        await update.message.reply_text("✅ Цена обновлена.")
        await _send_card_to_message(update.message, context, item_id)
        return True

    return False


async def handle_photo(update: Update,
                       context: ContextTypes.DEFAULT_TYPE) -> None:
    user = ensure_user(update, context)
    if not _is_admin(user):
        return
    file_id = update.message.photo[-1].file_id

    # photo for a just-created manual item
    new_item_id = context.user_data.pop("await_item_photo", None)
    if new_item_id:
        context.user_data.pop("new_item", None)
        if db.get_item(new_item_id):
            db.set_image(new_item_id, file_id)
            await update.message.reply_text("🖼 Фото добавлено. Товар готов:")
            await _send_card_to_message(update.message, context, new_item_id)
            return

    item_id = context.user_data.pop("await_img", None)
    if item_id:
        item = db.get_item(item_id)
        if item:
            db.set_image(item_id, file_id)
            await update.message.reply_text(
                f"🖼 Фото сохранено для *{item['name']}*.", parse_mode="Markdown")
            return

    caption = (update.message.caption or "").strip()
    if caption:
        item = db.get_item_by_name(caption)
        if item:
            db.set_image(item["id"], file_id)
            await update.message.reply_text(
                f"🖼 Фото сохранено для *{item['name']}*.", parse_mode="Markdown")
            return
        await update.message.reply_text(
            f"Нет товара с названием «{caption}». Проверьте /admin → Товары.")
        return
    await update.message.reply_text(
        "Чтобы прикрепить это фото: добавьте в подпись точное название товара "
        "или выберите товар в /admin → 🖼 Фото товаров.")


# --------------------------------------------------------------- Excel ----

async def handle_xlsx(update: Update,
                      context: ContextTypes.DEFAULT_TYPE) -> None:
    user = ensure_user(update, context)
    if not _is_admin(user):
        await update.message.reply_text(
            "Загружать файлы меню могут только администраторы 🙂")
        return
    doc = update.message.document
    if doc.file_size and doc.file_size > 19 * 1024 * 1024:
        await update.message.reply_text(
            "Файл больше лимита Telegram в 20 МБ для ботов — уменьшите "
            "фото внутри и попробуйте снова.")
        return
    status = await update.message.reply_text("📥 Читаю меню…")
    tg_file = await doc.get_file()
    data = bytes(await tg_file.download_as_bytearray())

    parsed = parse_menu_xlsx(data)
    if not parsed.rows and parsed.errors:
        await status.edit_text("❌ Импорт не удался:\n• " +
                               "\n• ".join(parsed.errors[:10]))
        return

    created = updated = 0
    for r in parsed.rows:
        res = db.upsert_item(r["name"], r["category"], r["subcategory"],
                             r["price"], r["quantity"])
        created += res == "created"
        updated += res == "updated"

    # Embedded images: send each to this chat once to obtain a Telegram
    # file_id, store it on the item, then delete the temporary message.
    attached = 0
    row_to_name = {r["row"]: r["name"] for r in parsed.rows}
    for row_no, img_bytes in parsed.images.items():
        item = db.get_item_by_name(row_to_name[row_no])
        if not item:
            continue
        try:
            msg = await update.message.chat.send_photo(
                io.BytesIO(img_bytes), caption=f"(импортирую {item['name']}…)")
            db.set_image(item["id"], msg.photo[-1].file_id)
            attached += 1
            try:
                await msg.delete()
            except Exception:                           # noqa: BLE001
                pass
        except Exception as e:                          # noqa: BLE001
            log.warning("Image for %s failed: %s", item["name"], e)
            parsed.errors.append(
                f"Фото для «{item['name']}» не удалось загрузить — прикрепите его "
                "вручную (фото + название товара в подписи).")

    missing = [it["name"] for it in db.items_without_image()]
    report = (f"✅ Меню импортировано!\n"
              f"• новых товаров: {created}\n"
              f"• обновлено: {updated}\n"
              f"• фото прикреплено: {attached}")
    if parsed.skipped_images:
        report += f"\n• изображений без совпадения со строкой: {parsed.skipped_images}"
    if parsed.errors:
        report += "\n\n⚠️ Замечания:\n• " + "\n• ".join(parsed.errors[:10])
    if missing:
        report += ("\n\n🖼 Ещё без фото: " + ", ".join(missing[:15])
                   + "\nОтправьте фото с названием товара в подписи или "
                     "используйте /admin → Фото товаров.")
    await status.edit_text(report)
