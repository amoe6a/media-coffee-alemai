"""/start, /help, /myid, /cancel + the shared text router."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from .. import database as db
from .. import keyboards as kb

log = logging.getLogger(__name__)

STATE_KEYS = ("await_phone", "await_kaspi", "await_address", "reject_order",
              "await_stock", "await_img", "await_cat", "await_sub",
              "await_item_name", "await_item_price", "await_item_photo",
              "await_ename", "await_eprice", "new_item")


def ensure_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    return db.ensure_user(u.id, u.full_name, u.username,
                          context.bot_data["cfg"].admin_ids)


def clear_states(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in STATE_KEYS:
        context.user_data.pop(k, None)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = ensure_user(update, context)
    role = user["role"]
    extra = ""
    if role == db.ROLE_ADMIN:
        extra = "\n\n🔧 Вы *администратор* — откройте панель командой /admin."
    elif role == db.ROLE_BARISTA:
        extra = "\n\n☕ Вы *бариста* — новые заказы будут появляться здесь. /queue показывает открытые."
    await update.message.reply_text(
        f"Здравствуйте, {user['name']}! Добро пожаловать в нашу кофейню ☕\n"
        "Смотрите меню, наполняйте корзину — и мы доставим заказ к вашей двери."
        + extra,
        reply_markup=kb.categories_kb(db.customer_categories()),
        parse_mode="Markdown",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = ensure_user(update, context)
    text = (
        "*Команды для клиентов*\n"
        "/menu — посмотреть меню\n"
        "/cart — открыть корзину\n"
        "/myorders — ваши последние заказы\n"
        "/cancel — отменить текущий ввод\n"
        "/myid — показать ваш Telegram ID\n"
    )
    if user["role"] in (db.ROLE_BARISTA, db.ROLE_ADMIN):
        text += "\n*Бариста*\n/queue — открытые заказы (принять / отклонить / завершить)\n"
    if user["role"] == db.ROLE_ADMIN:
        text += (
            "\n*Администратор*\n"
            "/admin — импорт меню, остатки, фото\n"
            "/users — список пользователей с ID\n"
            "/setrole <id> <customer|barista|admin>\n"
        )
    await update.message.reply_text(text, parse_mode="Markdown")


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ensure_user(update, context)
    await update.message.reply_text(
        f"Ваш Telegram ID: `{update.effective_user.id}`", parse_mode="Markdown"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_states(context)
    await update.message.reply_text("Хорошо, отменено. /menu — когда будете готовы ☕")


async def menu_root_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline 'Menu' button -> show categories."""
    q = update.callback_query
    await q.answer()
    cats = db.customer_categories()
    if not cats:
        await q.message.reply_text("Меню пока пусто — загляните чуть позже ☕")
        return
    await q.message.reply_text("Что желаете?",
                               reply_markup=kb.categories_kb(cats))


async def noop_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Plain text in private chat: feed it to whoever is waiting for input."""
    from . import admin, barista, customer  # local import avoids cycles

    ensure_user(update, context)
    if await barista.maybe_reject_reason(update, context):
        return
    if await admin.maybe_admin_text(update, context):
        return
    if await customer.maybe_checkout_text(update, context):
        return
    await update.message.reply_text("Откройте /menu, чтобы посмотреть, или /help ☕")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error: %s", context.error)
