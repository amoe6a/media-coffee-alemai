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
    "• Category — `Drinks` или `Food`\n"
    "• Subcategory — например Coffee, Tea, Beverages / Snacks, Desserts, Meals\n"
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
            await _safe_edit(q, "Меню пусто — сначала импортируйте файл Excel.",
                             kb.admin_panel_kb())
            return
        await _safe_edit(q, "🧾 *Товары* (нажмите для управления)",
                         kb.admin_items_kb(items))

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

async def maybe_set_stock(update: Update,
                          context: ContextTypes.DEFAULT_TYPE) -> bool:
    item_id = context.user_data.get("await_stock")
    if not item_id:
        return False
    user = ensure_user(update, context)
    if not _is_admin(user):
        context.user_data.pop("await_stock", None)
        return False
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("Просто число, пожалуйста (или /cancel).")
        return True
    context.user_data.pop("await_stock", None)
    db.set_qty(item_id, int(text))
    item = db.get_item(item_id)
    await update.message.reply_text(
        f"📦 Остаток *{item['name']}* установлен: *{item['quantity']}*.",
        parse_mode="Markdown")
    return True


async def handle_photo(update: Update,
                       context: ContextTypes.DEFAULT_TYPE) -> None:
    user = ensure_user(update, context)
    if not _is_admin(user):
        return
    file_id = update.message.photo[-1].file_id

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
