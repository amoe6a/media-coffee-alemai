"""Barista side: receive order cards, accept / reject (with reason) / complete."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from .. import database as db
from .. import keyboards as kb
from .common import ensure_user

log = logging.getLogger(__name__)


def _is_staff(user) -> bool:
    return user["role"] in (db.ROLE_BARISTA, db.ROLE_ADMIN)


async def _send_order_card(context, chat_id: int, order_id: int) -> None:
    info = db.get_order(order_id)
    if not info:
        return
    currency = context.bot_data["cfg"].currency
    try:
        msg = await context.bot.send_message(
            chat_id, kb.order_text(info, currency),
            reply_markup=kb.order_kb(info["order"]["status"], order_id))
        db.remember_order_msg(order_id, chat_id, msg.message_id)
    except Exception as e:                              # noqa: BLE001
        log.warning("Could not deliver order card to %s: %s", chat_id, e)


async def notify_new_order(context, order_id: int) -> None:
    cfg = context.bot_data["cfg"]
    recipients = db.staff_ids(cfg.admin_ids)
    if not recipients:
        log.warning("Order #%s placed but no staff registered!", order_id)
    for chat_id in recipients:
        await _send_order_card(context, chat_id, order_id)


async def _refresh_cards(context, order_id: int) -> None:
    """Update every staff member's copy of the order card."""
    info = db.get_order(order_id)
    if not info:
        return
    currency = context.bot_data["cfg"].currency
    text = kb.order_text(info, currency)
    markup = kb.order_kb(info["order"]["status"], order_id)
    for row in db.order_msgs(order_id):
        try:
            await context.bot.edit_message_text(
                text, chat_id=row["chat_id"], message_id=row["message_id"],
                reply_markup=markup)
        except Exception:                               # noqa: BLE001
            pass                                        # deleted / unchanged


async def _notify_customer(context, order_id: int) -> None:
    info = db.get_order(order_id)
    if not info:
        return
    o = info["order"]
    msgs = {
        db.ST_ACCEPTED: f"👩‍🍳 Заказ #{o['id']} принят — мы его готовим!",
        db.ST_REJECTED: (f"😔 Заказ #{o['id']} отклонён.\n"
                         f"Причина: {o['reason'] or '—'}"),
        db.ST_COMPLETED: f"✅ Заказ #{o['id']} доставлен. Приятного аппетита! ☕",
    }
    text = msgs.get(o["status"])
    if text:
        try:
            await context.bot.send_message(o["user_id"], text)
        except Exception as e:                          # noqa: BLE001
            log.warning("Could not notify customer %s: %s", o["user_id"], e)


# ------------------------------------------------------------- handlers ----

async def queue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = ensure_user(update, context)
    if not _is_staff(user):
        await update.message.reply_text("Эта команда для баристы/администраторов.")
        return
    orders = db.open_orders()
    if not orders:
        await update.message.reply_text("Открытых заказов нет — можно передохнуть ☕")
        return
    await update.message.reply_text(f"📋 Открытых заказов: {len(orders)}:")
    for info in orders:
        await _send_order_card(context, update.effective_chat.id,
                               info["order"]["id"])


async def order_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback prefix: o|a / o|r / o|d"""
    q = update.callback_query
    user = ensure_user(update, context)
    if not _is_staff(user):
        await q.answer("Только для баристы 🙂", show_alert=True)
        return
    _, action, order_id = q.data.split("|")
    order_id = int(order_id)

    if action == "a":
        ok, shortages = db.accept_order(order_id, user["tg_id"])
        if ok:
            await q.answer("Принято ✅")
            await _refresh_cards(context, order_id)
            await _notify_customer(context, order_id)
        elif shortages == ["__already_handled__"]:
            await q.answer("Уже обработано кем-то другим.", show_alert=True)
            await _refresh_cards(context, order_id)
        else:
            await q.answer("Недостаточно на складе: " + ", ".join(shortages)
                           + ". Пополните через /admin или отклоните заказ.",
                           show_alert=True)

    elif action == "r":
        info = db.get_order(order_id)
        if not info or info["order"]["status"] != db.ST_PENDING:
            await q.answer("Этот заказ больше не в ожидании.", show_alert=True)
            await _refresh_cards(context, order_id)
            return
        context.user_data["reject_order"] = order_id
        await q.answer()
        await q.message.reply_text(
            f"✍️ Введите причину отклонения заказа #{order_id} "
            "(она будет отправлена клиенту) или /cancel.")

    elif action == "d":
        if db.complete_order(order_id, user["tg_id"]):
            await q.answer("Завершено 🏁")
            await _refresh_cards(context, order_id)
            await _notify_customer(context, order_id)
        else:
            await q.answer("Этот заказ не в статусе «принят».",
                           show_alert=True)


async def maybe_reject_reason(update: Update,
                              context: ContextTypes.DEFAULT_TYPE) -> bool:
    order_id = context.user_data.get("reject_order")
    if not order_id:
        return False
    user = ensure_user(update, context)
    if not _is_staff(user):
        context.user_data.pop("reject_order", None)
        return False
    reason = update.message.text.strip()[:200]
    context.user_data.pop("reject_order", None)
    if db.reject_order(order_id, user["tg_id"], reason):
        await update.message.reply_text(f"Заказ #{order_id} отклонён. "
                                        "Клиент уведомлён.")
        await _refresh_cards(context, order_id)
        await _notify_customer(context, order_id)
    else:
        await update.message.reply_text("Этот заказ больше не в ожидании.")
    return True
