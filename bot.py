"""Xarajatlar boti.  Ishga tushirish:

    python bot.py
"""
import logging
import os
from datetime import timedelta

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import core

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("bot")

conn = core.init_db(os.environ.get("DB_PATH", "data.db"))
HTML = ParseMode.HTML


# ------------------------------------------------------------ klaviaturalar
def contact_kb():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Raqamni ulashish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def main_kb(user_id):
    rows = [
        [InlineKeyboardButton(f"🏪 {r['name']}", callback_data=f"r:{r['id']}")]
        for r in core.list_restaurants(conn, user_id)
    ]
    rows.append([InlineKeyboardButton("➕ Yangi joy qo'shish", callback_data="add")])
    return InlineKeyboardMarkup(rows)


def rest_kb():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📅 Kunlik ma'lumot", callback_data="day")],
            [InlineKeyboardButton("📆 Oylik ma'lumot", callback_data="month")],
            [InlineKeyboardButton("🔎 Ma'lumot izlash", callback_data="find")],
            [InlineKeyboardButton("↩️ Oxirgisini o'chirish", callback_data="undo")],
            [InlineKeyboardButton("⬅️ Orqaga", callback_data="menu")],
        ]
    )


# ------------------------------------------------------------ yordamchilar
async def say(update, text, markup=None, parse_mode=HTML):
    await update.effective_message.reply_text(
        text, reply_markup=markup, parse_mode=parse_mode
    )


async def say_chunks(update, chunks, markup):
    for i, chunk in enumerate(chunks):
        last = i == len(chunks) - 1
        await say(update, chunk, markup if last else None)


def current(update):
    """(user_id, tanlangan restoran) — profil ulanmagan bo'lsa (None, None)."""
    chat_id = update.effective_chat.id
    user_id = core.get_user_by_chat(conn, chat_id)
    if user_id is None:
        return None, None
    rid = core.get_selected(conn, chat_id)
    rest = core.get_restaurant(conn, rid, user_id) if rid else None
    return user_id, rest


async def ask_contact(update):
    await say(
        update,
        "👋 Assalomu alaykum!\n📲 Boshlash uchun telefon raqamingizni ulashing — "
        "🔐 barcha ma'lumotlaringiz shu profilda saqlanadi.\n\n"
        "👇 Pastdagi «📱 Raqamni ulashish» tugmasini bosing.",
        contact_kb(),
        parse_mode=None,
    )


async def show_main(update, ctx, user_id):
    if not core.list_restaurants(conn, user_id):
        ctx.user_data["state"] = "rest_name"
        await say(
            update,
            "🏪 Do'kon, restoran yoki kafe nomini kiriting:\n✍️ Masalan: Plov markaz",
            ReplyKeyboardRemove(),
            parse_mode=None,
        )
        return
    ctx.user_data.pop("state", None)
    await say(update, "🏪 Qaysi joy bilan ishlaymiz? Tanlang 👇", main_kb(user_id), parse_mode=None)


async def show_rest(update, rest):
    await say(
        update,
        f"🏪 <b>{core.esc(rest['name'])}</b>\n\n"
        "✍️ Xarajat yozish uchun shunchaki yozing:\n"
        "<code>Piyoz 10 kg 8000</code>\n"
        "💡 Narx — 1 kg / 1 dona uchun",
        rest_kb(),
    )


# ------------------------------------------------------------ xabarlar
async def on_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    user_id, _ = current(update)
    if user_id is None:
        await ask_contact(update)
    else:
        await show_main(update, ctx, user_id)


async def on_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    # Faqat o'zining raqami: boshqa odamning kontaktini yuborib bo'lmaydi
    if contact.user_id != update.effective_user.id:
        await say(
            update,
            "⚠️ Iltimos, faqat o'zingizning raqamingizni ulashing "
            "(pastdagi tugma orqali).",
            contact_kb(),
            parse_mode=None,
        )
        return
    try:
        user_id = core.get_or_create_user(conn, contact.phone_number)
    except core.InputError as e:
        await say(update, f"❌ {e}", parse_mode=None)
        return
    core.link_chat(conn, update.effective_chat.id, user_id)
    await say(update, "🎉 Raqamingiz ulandi, xush kelibsiz!", ReplyKeyboardRemove(), parse_mode=None)
    await show_main(update, ctx, user_id)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    chat_id = update.effective_chat.id
    user_id, rest = current(update)
    if user_id is None:
        await ask_contact(update)
        return
    core.purge_old(conn)  # 1 oydan eski kunlik ma'lumotlarni tozalash
    state = ctx.user_data.get("state")

    # --- yangi restoran nomi
    if state == "rest_name":
        try:
            rid = core.add_restaurant(conn, user_id, text)
        except core.InputError as e:
            await say(update, f"❌ {e}", parse_mode=None)
            return
        ctx.user_data.pop("state", None)
        core.set_selected(conn, chat_id, rid)
        await show_rest(update, core.get_restaurant(conn, rid, user_id))
        return

    # --- sana bo'yicha izlash
    if state == "date":
        if rest is None:
            await show_main(update, ctx, user_id)
            return
        d = core.parse_date(text)
        if d is None:
            await say(
                update,
                "❌ Sana noto'g'ri. Shunday yozing: 23.05.2026",
                parse_mode=None,
            )
            return
        t = core.today()
        if d > t:
            await say(update, "⏳ Bu sana hali kelmagan.", parse_mode=None)
            return
        if d < t - timedelta(days=core.RETENTION_DAYS):
            await say(
                update,
                f"❌ Bu sana saqlanmagan: ma'lumotlar {core.RETENTION_DAYS} kun "
                "saqlanadi.",
                parse_mode=None,
            )
            return
        ctx.user_data.pop("state", None)
        await say(update, core.day_report(conn, rest, d), rest_kb())
        return

    # --- oddiy xarajat kiritish
    if rest is None:
        await show_main(update, ctx, user_id)
        return
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    ok, bad = [], []
    for ln in lines[:50]:  # bir xabarda ko'pi bilan 50 qator
        try:
            parsed = core.parse_line(ln)
        except core.ParseError as e:
            bad.append(f"❌ <code>{core.esc(ln)}</code> — {core.esc(e)}")
            continue
        core.add_item(conn, rest["id"], parsed)
        ok.append(
            f"✅ <b>{core.esc(parsed['name'])}</b> — "
            f"{core.fmt_qty(parsed['qty'])}"
            f"{(' ' + parsed['unit']) if parsed['unit'] else ''}"
            f" = {core.fmt(parsed['total'])} so'm"
        )
    out = ok + bad
    if ok:
        total = core.day_total(conn, rest["id"])
        out.append(
            f"\n🏪 {core.esc(rest['name'])} — bugun jami: <b>{core.fmt(total)}</b> so'm"
        )
    await say(update, "\n".join(out) or "📭 Bo'sh xabar.", rest_kb())


# ------------------------------------------------------------ tugmalar
async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    chat_id = update.effective_chat.id
    user_id, rest = current(update)
    if user_id is None:
        await ask_contact(update)
        return

    if data == "menu":
        await show_main(update, ctx, user_id)
        return
    if data == "add":
        ctx.user_data["state"] = "rest_name"
        await say(update, "🏪 Yangi do'kon, restoran yoki kafe nomini kiriting:\n✍️ Masalan: Plov markaz", parse_mode=None)
        return
    if data.startswith("r:"):
        try:
            chosen = core.get_restaurant(conn, int(data[2:]), user_id)
        except ValueError:
            chosen = None
        if chosen is None:
            await show_main(update, ctx, user_id)
            return
        core.set_selected(conn, chat_id, chosen["id"])
        ctx.user_data.pop("state", None)
        await show_rest(update, chosen)
        return

    if rest is None:  # restoran tanlanmagan
        await show_main(update, ctx, user_id)
        return

    if data == "day":
        ctx.user_data.pop("state", None)
        await say(update, core.day_report(conn, rest, core.today()), rest_kb())
    elif data == "month":
        ctx.user_data.pop("state", None)
        t = core.today()
        blocks = core.monthly_blocks(conn, rest, t - timedelta(days=core.MONTH_DAYS - 1), t)
        await say_chunks(update, core.pack_blocks(blocks), rest_kb())
    elif data == "find":
        ctx.user_data["state"] = "date"
        await say(
            update,
            "🔎 Qaysi sanadagi ma'lumot kerak?\n"
            "🗓 Kun.oy.yil ko'rinishida yozing, masalan: 23.05.2026",
            parse_mode=None,
        )
    elif data == "undo":
        item = core.delete_last(conn, rest["id"])
        if item is None:
            await say(update, "🤷 O'chiradigan yozuv yo'q.", rest_kb(), parse_mode=None)
        else:
            await say(
                update,
                f"🗑 O'chirildi: <b>{core.esc(item['name'])}</b> — "
                f"{core.fmt(item['total'])} so'm",
                rest_kb(),
            )


# ------------------------------------------------------------ ishga tushirish
def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN o'zgaruvchisi o'rnatilmagan")
    core.purge_old(conn)
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler(["start", "menu"], on_start))
    app.add_handler(MessageHandler(filters.CONTACT, on_contact))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Bot ishga tushdi")
    app.run_polling()


if __name__ == "__main__":
    main()
