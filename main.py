import os, json, asyncio, logging, datetime as dt
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

import feedparser
from dotenv import load_dotenv
from groq import AsyncGroq
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.request import HTTPXRequest
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, ContextTypes, filters)
from fastapi import FastAPI, Request, Response, status

load_dotenv()
logging.basicConfig(level=logging.INFO)

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
OWNER_CHAT_ID_RAW = os.environ.get("OWNER_CHAT_ID", "").strip()
OWNER_ID = int(OWNER_CHAT_ID_RAW) if OWNER_CHAT_ID_RAW.isdigit() else None
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "").strip()
SMTP_USER = os.getenv("SMTP_USER", "").strip()  # verified sender address
OWNER_EMAIL = os.getenv("OWNER_EMAIL", SMTP_USER).strip()

GROQ_KEY = os.environ.get("GROQ_API_KEY", "").strip()
groq = AsyncGroq(api_key=GROQ_KEY)

FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://feeds.npr.org/1004/rss.xml",
]


async def ask(system: str, user: str, history=None) -> str:
    msgs = [{"role": "system", "content": system}]
    msgs += history or []
    msgs.append({"role": "user", "content": user})
    try:
        r = await groq.chat.completions.create(model=MODEL, messages=msgs, temperature=0.5)
        return r.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"Groq error: {e}")
        return f"⚠️ Error communicating with Groq: {e}"


def owner_only(fn):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if OWNER_ID is None:
            user = update.effective_user
            if update.message:
                await update.message.reply_text(
                    f"⚠️ *OWNER_CHAT_ID is not set in .env!*\n\n"
                    f"Your Telegram User ID is: `{user.id}`\n\n"
                    f"Set this in your `.env` file:\n`OWNER_CHAT_ID={user.id}`\n"
                    f"Then restart the bot.",
                    parse_mode="Markdown"
                )
            return
        if update.effective_user.id != OWNER_ID:
            return
        return await fn(update, context)
    return wrapper


# ---------- News ----------
async def build_digest(topic: str = "") -> str:
    def fetch():
        items = []
        for url in FEEDS:
            for e in feedparser.parse(url).entries[:12]:
                items.append(f"- {e.title}: {getattr(e, 'summary', '')[:200]}")
        return items
    items = await asyncio.to_thread(fetch)
    focus = f"Focus on: {topic}." if topic else ""
    return await ask(
        "You write concise world news briefings for a phone screen. "
        "Group by theme, max 8 bullets, one line each. Only use the headlines given.",
        f"{focus}\n\nHeadlines:\n" + "\n".join(items),
    )


@owner_only
async def news(update, context):
    await update.message.reply_text("Fetching headlines...")
    await update.message.reply_text(await build_digest(" ".join(context.args)))


async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(context.job.chat_id, await build_digest())


@owner_only
async def daily(update, context):
    """/daily 08:00  (server time)  |  /daily off"""
    chat_id = update.effective_chat.id
    if not context.job_queue:
        return await update.message.reply_text("⚠️ Daily digest scheduling requires continuous background worker mode.")
    for j in context.job_queue.get_jobs_by_name(f"daily-{chat_id}"):
        j.schedule_removal()
    if not context.args or context.args[0] == "off":
        return await update.message.reply_text("Daily digest off.")
    h, m = map(int, context.args[0].split(":"))
    context.job_queue.run_daily(daily_job, dt.time(h, m), chat_id=chat_id,
                                name=f"daily-{chat_id}")
    await update.message.reply_text(f"Daily digest set for {context.args[0]}.")


# ---------- Contacts ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONTACTS_FILE = os.path.join(BASE_DIR, "contacts.json")
if os.environ.get("VERCEL") or not os.access(BASE_DIR, os.W_OK):
    _tmp_path = "/tmp/contacts.json"
    if not os.path.exists(_tmp_path) and os.path.exists(CONTACTS_FILE):
        try:
            import shutil
            shutil.copyfile(CONTACTS_FILE, _tmp_path)
        except Exception:
            pass
    CONTACTS_FILE = _tmp_path


def load_contacts() -> dict:
    try:
        with open(CONTACTS_FILE, "r", encoding="utf-8") as f:
            c = json.load(f)
    except FileNotFoundError:
        c = {}
    if OWNER_EMAIL:
        if "myself" not in c:
            c["myself"] = OWNER_EMAIL
        if "me" not in c:
            c["me"] = OWNER_EMAIL
    return c


def save_contacts(c: dict):
    with open(CONTACTS_FILE, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=2)


def resolve_recipients(raw: str) -> list[str]:
    contacts, out = load_contacts(), []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "@" in part:
            out.append(part)
        elif part.lower() in contacts:
            out.append(contacts[part.lower()])
        else:
            raise ValueError(f"Unknown contact: {part}")
    if not out:
        raise ValueError("No recipients")
    return out


@owner_only
async def contact(update, context):
    """/contact add john john@gmail.com | /contact remove john | /contact list"""
    args, contacts = context.args, load_contacts()
    if args and args[0] == "add" and len(args) == 3 and "@" in args[2]:
        contacts[args[1].lower()] = args[2]
        save_contacts(contacts)
        return await update.message.reply_text(f"Saved {args[1]}.")
    if args and args[0] == "remove" and len(args) == 2:
        contacts.pop(args[1].lower(), None)
        save_contacts(contacts)
        return await update.message.reply_text(f"Removed {args[1]}.")
    if args and args[0] == "list":
        txt = "\n".join(f"{k}: {v}" for k, v in contacts.items()) or "No contacts yet."
        return await update.message.reply_text(txt)
    await update.message.reply_text(
        "/contact add john john@gmail.com\n/contact remove john\n/contact list")


# ---------- Email ----------
def send_email(to: str, subject: str, body: str):
    if not SENDGRID_API_KEY:
        raise ValueError("SENDGRID_API_KEY is not set in environment variables.")
    if not SMTP_USER:
        raise ValueError("SMTP_USER (your verified sender address) is not set.")
    message = Mail(
        from_email=SMTP_USER,
        to_emails=to,
        subject=subject,
        plain_text_content=body,
    )
    sg = SendGridAPIClient(SENDGRID_API_KEY)
    try:
        response = sg.send(message)
    except Exception as e:
        err_msg = str(e)
        if hasattr(e, "body") and e.body:
            try:
                body_str = e.body.decode("utf-8") if isinstance(e.body, bytes) else str(e.body)
                err_data = json.loads(body_str)
                if "errors" in err_data and err_data["errors"]:
                    err_msg = err_data["errors"][0].get("message", body_str)
                else:
                    err_msg = body_str
            except Exception:
                err_msg = f"{e} - {getattr(e, 'body', '')}"
        raise ValueError(err_msg)
    if response.status_code >= 400:
        raise ValueError(f"SendGrid error {response.status_code}: {response.body}")


@owner_only
async def email(update, context):
    """/email a@gmail.com, john | Subject | what the email should say"""
    try:
        raw_to, subject, brief = [p.strip() for p in
                                  update.message.text.split(" ", 1)[1].split("|", 2)]
    except Exception:
        return await update.message.reply_text(
            "Format:\n/email a@gmail.com, john | Subject | what to say")
    try:
        to = ", ".join(resolve_recipients(raw_to))
    except ValueError as e:
        return await update.message.reply_text(f"{e}. Use /contact add name email")
    body = await ask("Write a clear, polite, concise email body. No subject line.", brief)
    context.user_data["draft"] = (to, subject, body)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Send", callback_data="send"),
        InlineKeyboardButton("Cancel", callback_data="cancel")]])
    await update.message.reply_text(f"To: {to}\nSubject: {subject}\n\n{body}",
                                    reply_markup=kb)


async def email_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != OWNER_ID:
        return
    draft = context.user_data.pop("draft", None)
    if q.data == "send" and draft:
        try:
            await asyncio.to_thread(send_email, *draft)
            await q.edit_message_text(f"✅ Sent to {draft[0]}.")
        except Exception as e:
            logging.error(f"Email send error: {e}")
            await q.edit_message_text(f"❌ Failed to send email: {e}")
    else:
        await q.edit_message_text("Cancelled.")


# ---------- Chat ----------
def get_chat_system_prompt() -> str:
    contacts = load_contacts()
    contacts_str = json.dumps(contacts) if contacts else "{}"
    return f"""You are a helpful personal assistant.
If the user asks you to send an email, reply with ONLY this JSON and nothing else:
{{"action":"send_email","to":"<email address>","subject":"...","body":"..."}}
Known contacts: {contacts_str}. Use them to resolve names to addresses.
If the recipient is not in known contacts and no email was provided, ask the user for their email address.
For anything else, reply normally."""


@owner_only
async def chat(update, context):
    hist = context.user_data.setdefault("hist", [])
    user_text = update.message.text
    system_prompt = get_chat_system_prompt()

    reply = await ask(system_prompt, user_text, hist)

    clean_reply = reply.strip()
    if clean_reply.startswith("```"):
        clean_reply = clean_reply.strip("`")
        if clean_reply.startswith("json"):
            clean_reply = clean_reply[4:].strip()

    try:
        data = json.loads(clean_reply)
        if isinstance(data, dict) and data.get("action") == "send_email":
            to = data.get("to", "").strip()
            subject = data.get("subject", "No subject").strip()
            body = data.get("body", "").strip()

            if not to or "@" not in to:
                await update.message.reply_text(f"⚠️ Could not find email address for {to}. Use `/contact add <name> <email>` to add them.")
                return

            await update.message.reply_text(f"⏳ Sending email to {to}...")
            try:
                await asyncio.to_thread(send_email, to, subject, body)
                await update.message.reply_text(f"✅ Email sent to {to}\n\n📌 *Subject:* {subject}\n\n{body}", parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Email send error: {e}")
                await update.message.reply_text(f"❌ Failed to send email: {e}")
            return
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    hist += [{"role": "user", "content": user_text},
             {"role": "assistant", "content": reply}]
    del hist[:-12]
    await update.message.reply_text(reply)


@owner_only
async def clear_chat(update, context):
    context.user_data["hist"] = []
    await update.message.reply_text("Chat history cleared.")


@owner_only
async def start(update, context):
    await update.message.reply_text(
        "Commands:\n"
        "/news [topic]\n"
        "/daily 08:00 | off\n"
        "/email to1, to2 | subject | what to say\n"
        "/contact add|remove|list\n"
        "/clear - reset chat memory\n\n"
        "Or just send a message to chat.")


def build_application() -> Application:
    """Builds and configures the python-telegram-bot Application instance."""
    req = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    bot_app = Application.builder().token(TG_TOKEN).request(req).build()
    bot_app.add_handler(CommandHandler(["start", "help"], start))
    bot_app.add_handler(CommandHandler(["clear", "reset"], clear_chat))
    bot_app.add_handler(CommandHandler("news", news))
    bot_app.add_handler(CommandHandler("daily", daily))
    bot_app.add_handler(CommandHandler("email", email))
    bot_app.add_handler(CommandHandler("contact", contact))
    bot_app.add_handler(CallbackQueryHandler(email_buttons))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    return bot_app


# -------------------------------------------------------------
# Webhook / Serverless (Vercel) Entrypoint
# -------------------------------------------------------------
# Top-level 'app' exported for Vercel Python runtime
app = FastAPI(
    title="Personal Telegram AI Assistant Bot",
    description="Webhook server for Telegram bot on Vercel or cloud runtimes.",
    version="1.0.0",
)

_ptb_app: Application | None = None
_ptb_initialized: bool = False


async def get_ptb_app() -> Application:
    """Lazily initializes the PTB Application for serverless webhook handling."""
    global _ptb_app, _ptb_initialized
    if _ptb_app is None:
        _ptb_app = build_application()
    if not _ptb_initialized:
        await _ptb_app.initialize()
        await _ptb_app.start()
        _ptb_initialized = True
    return _ptb_app


@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "Personal Telegram AI Assistant Bot",
        "mode": "serverless-webhook",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/")
@app.post("/webhook")
@app.post("/api/webhook")
async def telegram_webhook(request: Request):
    """Handles incoming Telegram updates forwarded via Webhook."""
    if not TG_TOKEN:
        return Response("TELEGRAM_TOKEN is not configured", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    try:
        data = await request.json()
        bot_app = await get_ptb_app()
        update = Update.de_json(data, bot_app.bot)
        await bot_app.process_update(update)
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        logging.error(f"Error processing Telegram update: {e}", exc_info=True)
        return Response(content=str(e), status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@app.get("/set-webhook")
async def set_webhook_endpoint(url: str = ""):
    """Convenience endpoint to register the webhook with Telegram."""
    if not TG_TOKEN:
        return {"error": "TELEGRAM_TOKEN is not set in environment variables."}
    if not url:
        return {
            "error": "Missing 'url' query parameter. Example: /set-webhook?url=https://your-vercel-domain.vercel.app/api/webhook"
        }

    bot_app = await get_ptb_app()
    success = await bot_app.bot.set_webhook(url=url)
    info = await bot_app.bot.get_webhook_info()
    return {
        "success": success,
        "webhook_url": info.url,
        "has_custom_certificate": info.has_custom_certificate,
        "pending_update_count": info.pending_update_count,
    }


# -------------------------------------------------------------
# Local / Worker (Railway / Render / VPS) Entrypoint
# -------------------------------------------------------------
def run_polling():
    if not TG_TOKEN:
        print("\n[ERROR] TELEGRAM_TOKEN is missing! Please configure it in your .env file.\n")
        return
    print("Starting Telegram bot in Long Polling mode...")
    bot_app = build_application()
    bot_app.run_polling()


if __name__ == "__main__":
    run_polling()

