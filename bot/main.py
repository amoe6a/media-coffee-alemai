"""Entry point. Webhook mode when WEBHOOK_URL is set, polling otherwise."""
import logging

from telegram import BotCommand, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          MessageHandler, filters)

from . import config, database as db
from .handlers import admin, barista, common, customer

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("coffee-bot")

COMMANDS = [
    BotCommand("menu", "Посмотреть меню ☕"),
    BotCommand("cart", "Ваша корзина 🧺"),
    BotCommand("myorders", "Ваши последние заказы"),
    BotCommand("help", "Как это работает"),
    BotCommand("cancel", "Отменить текущий ввод"),
    BotCommand("myid", "Показать мой Telegram ID"),
]


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands(COMMANDS)
    me = await app.bot.get_me()
    log.info("Running as @%s", me.username)


def build_app(cfg: config.Config) -> Application:
    db.init_db(cfg.db_path)
    app = Application.builder().token(cfg.bot_token).post_init(_post_init).build()
    app.bot_data["cfg"] = cfg

    # commands
    app.add_handler(CommandHandler("start", common.start))
    app.add_handler(CommandHandler("help", common.help_cmd))
    app.add_handler(CommandHandler("myid", common.myid))
    app.add_handler(CommandHandler("cancel", common.cancel))
    app.add_handler(CommandHandler("menu", customer.menu_cmd))
    app.add_handler(CommandHandler("cart", customer.cart_cmd))
    app.add_handler(CommandHandler("myorders", customer.myorders_cmd))
    app.add_handler(CommandHandler("queue", barista.queue_cmd))
    app.add_handler(CommandHandler("admin", admin.admin_cmd))
    app.add_handler(CommandHandler("users", admin.users_cmd))
    app.add_handler(CommandHandler("setrole", admin.setrole_cmd))

    # inline buttons
    app.add_handler(CallbackQueryHandler(common.noop_cb, pattern=r"^noop$"))
    app.add_handler(CallbackQueryHandler(common.menu_root_cb, pattern=r"^menu$"))
    app.add_handler(CallbackQueryHandler(customer.browse_cb,
                                         pattern=r"^(c|s|i|q|a|b)\|"))
    app.add_handler(CallbackQueryHandler(customer.cart_cb,
                                         pattern=r"^(cart|clr|co)$"))
    app.add_handler(CallbackQueryHandler(customer.cart_cb,
                                         pattern=r"^(cu|cx|ck)\|"))
    app.add_handler(CallbackQueryHandler(barista.order_cb, pattern=r"^o\|"))
    app.add_handler(CallbackQueryHandler(admin.admin_cb, pattern=r"^ad\|"))

    # documents / photos / contacts / plain text (private chats)
    private = filters.ChatType.PRIVATE
    app.add_handler(MessageHandler(
        filters.Document.FileExtension("xlsx") & private, admin.handle_xlsx))
    app.add_handler(MessageHandler(filters.PHOTO & private, admin.handle_photo))
    app.add_handler(MessageHandler(filters.CONTACT & private,
                                   customer.handle_contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & private,
                                   common.text_router))

    app.add_error_handler(common.on_error)
    return app


def main() -> None:
    cfg = config.load()
    app = build_app(cfg)
    allowed = [Update.MESSAGE, Update.CALLBACK_QUERY]

    if cfg.webhook_url:
        url = f"{cfg.webhook_url}/{cfg.url_path}"
        log.info("Webhook mode → %s (listening on 0.0.0.0:%s)", url, cfg.port)
        app.run_webhook(
            listen="0.0.0.0",
            port=cfg.port,
            url_path=cfg.url_path,
            secret_token=cfg.webhook_secret,
            webhook_url=url,
            allowed_updates=allowed,
            drop_pending_updates=True,
        )
    else:
        log.info("WEBHOOK_URL is empty → falling back to long polling "
                 "(fine for a quick test; use the tunnel for webhook mode)")
        app.run_polling(allowed_updates=allowed, drop_pending_updates=True)


if __name__ == "__main__":
    main()
