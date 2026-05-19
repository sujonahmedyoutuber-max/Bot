"""
Telegram Task & Reward Bot - FULLY WORKING v2.0
✅ Force Join properly works
✅ All buttons work
✅ Admin panel fully functional
✅ Admin can add/remove withdraw methods with min/max limits
✅ Admin can set support contact
✅ Duplicate gift code use prevented
✅ Balance refunded on withdrawal rejection
✅ Ready for Railway
"""

import asyncio
import logging
import os
import sqlite3
import random
import string
from datetime import datetime

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ==================== CONFIGURATION ====================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set in .env file")

ADMIN_USER_IDS = [int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DB_PATH = "bot_data.db"

# ==================== DATABASE ====================
def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

async def run_db_query(query: str, params: tuple = (), fetch: str = "none"):
    def _query():
        with db_connect() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            if fetch == "one":
                return cursor.fetchone()
            elif fetch == "all":
                return cursor.fetchall()
            else:
                conn.commit()
                return None
    return await asyncio.to_thread(_query)

async def init_db():
    queries = [
        """CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            balance REAL DEFAULT 0,
            total_earned REAL DEFAULT 0,
            referred_by INTEGER,
            is_banned INTEGER DEFAULT 0,
            joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            task_link TEXT,
            reward REAL NOT NULL,
            enabled INTEGER DEFAULT 1
        )""",
        """CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            task_id INTEGER,
            screenshot_file_id TEXT,
            status TEXT DEFAULT 'pending',
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            payment_method TEXT,
            account_info TEXT,
            status TEXT DEFAULT 'pending',
            requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS withdraw_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            method_name TEXT UNIQUE NOT NULL,
            min_amount REAL DEFAULT 10,
            max_amount REAL DEFAULT 0,
            enabled INTEGER DEFAULT 1,
            instructions TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS giftcodes (
            code TEXT PRIMARY KEY,
            reward REAL,
            usage_limit INTEGER DEFAULT 0,
            used_count INTEGER DEFAULT 0,
            expiry_date TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS giftcode_uses (
            code TEXT,
            user_id INTEGER,
            PRIMARY KEY (code, user_id)
        )""",
        """CREATE TABLE IF NOT EXISTS referrals (
            referrer_id INTEGER,
            referred_id INTEGER UNIQUE,
            bonus_paid INTEGER DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_username TEXT UNIQUE NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )"""
    ]
    for q in queries:
        await run_db_query(q)

    defaults = [
        ("min_withdraw", "10"),
        ("referral_bonus", "5"),
        ("withdraw_enabled", "true"),
        ("support_username", ""),
    ]
    for key, val in defaults:
        await run_db_query("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))

    default_methods = [
        ("bKash",  50,  5000, "Send your bKash number"),
        ("Nagad",  50,  5000, "Send your Nagad number"),
        ("Rocket", 50,  5000, "Send your Rocket number"),
        ("Bank",  100,     0, "Send your bank account details"),
    ]
    for name, mn, mx, inst in default_methods:
        await run_db_query(
            "INSERT OR IGNORE INTO withdraw_methods (method_name, min_amount, max_amount, instructions) VALUES (?,?,?,?)",
            (name, mn, mx, inst)
        )

# ==================== HELPERS ====================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

async def get_setting(key: str, default: str = "") -> str:
    row = await run_db_query("SELECT value FROM settings WHERE key=?", (key,), "one")
    return row["value"] if row else default

async def set_setting(key: str, value: str):
    await run_db_query("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))

async def get_user_balance(user_id: int) -> float:
    row = await run_db_query("SELECT balance FROM users WHERE user_id=?", (user_id,), "one")
    return float(row["balance"]) if row else 0.0

async def update_balance(user_id: int, amount: float):
    await run_db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    if amount > 0:
        await run_db_query("UPDATE users SET total_earned = total_earned + ? WHERE user_id=?", (amount, user_id))

async def register_user(user_id: int, username: str = None, full_name: str = None, ref: int = None):
    existing = await run_db_query("SELECT user_id FROM users WHERE user_id=?", (user_id,), "one")
    if existing:
        await run_db_query(
            "UPDATE users SET username=?, full_name=? WHERE user_id=?",
            (username, full_name, user_id)
        )
        return

    if ref == user_id:
        ref = None

    await run_db_query(
        "INSERT INTO users (user_id, username, full_name, referred_by) VALUES (?,?,?,?)",
        (user_id, username, full_name, ref)
    )

    if ref:
        ref_exists = await run_db_query("SELECT user_id FROM users WHERE user_id=?", (ref,), "one")
        if ref_exists:
            bonus = float(await get_setting("referral_bonus", "5"))
            await update_balance(ref, bonus)
            await run_db_query(
                "INSERT OR IGNORE INTO referrals (referrer_id, referred_id, bonus_paid) VALUES (?,?,1)",
                (ref, user_id)
            )

# ==================== FORCE JOIN ====================
async def check_force_join(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    channels = await run_db_query("SELECT channel_username FROM channels", fetch="all")
    if not channels:
        return True
    for row in channels:
        try:
            member = await context.bot.get_chat_member(f"@{row['channel_username']}", user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception:
            return False
    return True

async def show_force_join_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channels = await run_db_query("SELECT channel_username FROM channels", fetch="all")
    msg = "⛔ *You must join these channels first:*\n\n"
    for ch in channels:
        msg += f"📢 @{ch['channel_username']}\n"
    msg += "\n✅ After joining, send /start again."
    await update.message.reply_text(msg, parse_mode="Markdown")

# ==================== KEYBOARDS ====================
def get_main_keyboard(admin: bool = False) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton("👤 Account"),    KeyboardButton("💰 Wallet")],
        [KeyboardButton("👥 Team"),       KeyboardButton("🎁 Invite")],
        [KeyboardButton("📋 Tasks"),      KeyboardButton("🏆 Leaderboard")],
        [KeyboardButton("💳 Withdraw"),   KeyboardButton("🎁 Redeem")],
        [KeyboardButton("📢 Channels"),   KeyboardButton("🆘 Support")],
    ]
    if admin:
        buttons.append([KeyboardButton("🔧 Admin Panel")])
    buttons.append([KeyboardButton("🏠 Home")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_admin_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton("➕ Add Task"),         KeyboardButton("❌ Remove Task")],
        [KeyboardButton("📥 Pending Tasks"),     KeyboardButton("💸 Withdraw Requests")],
        [KeyboardButton("🎁 Create Gift Code"),  KeyboardButton("📢 Broadcast")],
        [KeyboardButton("📊 Analytics"),         KeyboardButton("👤 Manage User")],
        [KeyboardButton("➕ Add Channel"),        KeyboardButton("❌ Remove Channel")],
        [KeyboardButton("💳 Add Method"),         KeyboardButton("🗑 Remove Method")],
        [KeyboardButton("⚙️ Method Limits"),      KeyboardButton("🔘 Toggle Withdraw")],
        [KeyboardButton("💰 Min Withdraw"),       KeyboardButton("👥 Referral Bonus")],
        [KeyboardButton("📞 Set Support"),        KeyboardButton("🏠 Home")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_back_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🔙 Back"), KeyboardButton("🏠 Home")]],
        resize_keyboard=True
    )

def get_method_keyboard(methods) -> ReplyKeyboardMarkup:
    buttons = []
    row = []
    for m in methods:
        row.append(KeyboardButton(f"💳 {m['method_name']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([KeyboardButton("🔙 Back"), KeyboardButton("🏠 Home")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# ==================== USER MENUS ====================
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "🏠 *Main Menu* — Choose an option:",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(is_admin(update.effective_user.id))
    )

async def show_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = await run_db_query("SELECT * FROM users WHERE user_id=?", (uid,), "one")
    if not user:
        await update.message.reply_text("Account not found. Send /start.")
        return
    refs = await run_db_query("SELECT COUNT(*) as c FROM referrals WHERE referrer_id=?", (uid,), "one")
    text = (
        f"👤 *Your Account*\n\n"
        f"🆔 ID: `{user['user_id']}`\n"
        f"👤 Name: {user['full_name'] or 'N/A'}\n"
        f"📛 Username: @{user['username'] or 'N/A'}\n"
        f"💰 Balance: `{user['balance']:.2f}` coins\n"
        f"📈 Total Earned: `{user['total_earned']:.2f}` coins\n"
        f"👥 Referrals: `{refs['c'] if refs else 0}`\n"
        f"📅 Joined: {str(user['joined_date'])[:10] if user['joined_date'] else 'N/A'}"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    balance = await get_user_balance(uid)
    min_w = await get_setting("min_withdraw", "10")
    methods = await run_db_query("SELECT * FROM withdraw_methods WHERE enabled=1", fetch="all")
    m_text = ""
    for m in methods:
        mx = f" | Max: {m['max_amount']:.0f}" if float(m["max_amount"]) > 0 else ""
        m_text += f"  • *{m['method_name']}* — Min: {m['min_amount']:.0f}{mx} coins\n"
    text = (
        f"💰 *Wallet*\n\n"
        f"💵 Balance: `{balance:.2f}` coins\n"
        f"📉 Global Min Withdraw: `{min_w}` coins\n\n"
        f"💳 *Withdraw Methods:*\n{m_text if m_text else 'No methods available.'}"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

async def show_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    refs = await run_db_query("SELECT COUNT(*) as c FROM referrals WHERE referrer_id=?", (uid,), "one")
    bonus = await get_setting("referral_bonus", "5")
    ref_list = await run_db_query(
        "SELECT u.full_name, u.username FROM referrals r "
        "JOIN users u ON r.referred_id=u.user_id WHERE r.referrer_id=? LIMIT 10",
        (uid,), "all"
    )
    text = (
        f"👥 *Your Team*\n\n"
        f"Total Referrals: `{refs['c'] if refs else 0}`\n"
        f"Bonus per Referral: `{bonus}` coins\n\n"
    )
    if ref_list:
        text += "📋 *Recent Referrals:*\n"
        for r in ref_list:
            name = f"@{r['username']}" if r['username'] else (r['full_name'] or "Unknown")
            text += f"  • {name}\n"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

async def show_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bot_info = await context.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{uid}"
    bonus = await get_setting("referral_bonus", "5")
    text = (
        f"🎁 *Your Invite Link*\n\n"
        f"`{link}`\n\n"
        f"✅ Earn `{bonus}` coins for every friend who joins!\n"
        f"Share and start earning 🎉"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

async def show_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channels = await run_db_query("SELECT channel_username FROM channels", fetch="all")
    if not channels:
        text = "📢 No required channels configured."
    else:
        text = "📢 *Required Channels:*\n\n"
        for ch in channels:
            text += f"• @{ch['channel_username']}\n"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    support_username = await get_setting("support_username", "")
    if support_username:
        text = (
            f"🆘 *Support*\n\n"
            f"For any issues, contact us:\n"
            f"👤 @{support_username}\n\n"
            f"We'll get back to you as soon as possible."
        )
    else:
        text = "🆘 *Support*\n\nFor any issues, please contact the admin."
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

# ==================== TASKS ====================
async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = await run_db_query("SELECT * FROM tasks WHERE enabled=1", fetch="all")
    if not tasks:
        await update.message.reply_text("📋 No tasks available right now.", reply_markup=get_back_keyboard())
        return
    msg = "📋 *Available Tasks:*\n\n"
    for t in tasks:
        msg += f"🔸 ID `{t['id']}` — *{t['title']}*\n"
        msg += f"   💰 Reward: `{t['reward']:.2f}` coins\n\n"
    msg += "📌 Send a Task ID to view details."
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_back_keyboard())
    context.user_data["awaiting_task_id"] = True

async def show_task_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int):
    task = await run_db_query("SELECT * FROM tasks WHERE id=? AND enabled=1", (task_id,), "one")
    if not task:
        await update.message.reply_text("❌ Task not found.")
        return
    already = await run_db_query(
        "SELECT id FROM submissions WHERE user_id=? AND task_id=? AND status IN ('pending','approved')",
        (update.effective_user.id, task_id), "one"
    )
    footer = "\n⚠️ You already submitted this task." if already else f"\n👉 To start, send: /do_{task_id}"
    text = (
        f"📌 *{task['title']}*\n\n"
        f"📝 Description: {task['description'] or 'N/A'}\n"
        f"🔗 Link: {task['task_link'] or 'N/A'}\n"
        f"💰 Reward: `{task['reward']:.2f}` coins"
        f"{footer}"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

# ==================== WITHDRAW ====================
async def withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await get_setting("withdraw_enabled", "true") != "true":
        await update.message.reply_text("❌ Withdrawals are currently disabled.")
        return

    balance = await get_user_balance(update.effective_user.id)
    min_w = float(await get_setting("min_withdraw", "10"))

    if balance < min_w:
        await update.message.reply_text(
            f"❌ Minimum withdraw: `{min_w:.2f}` coins\n"
            f"Your balance: `{balance:.2f}` coins",
            parse_mode="Markdown"
        )
        return

    methods = await run_db_query("SELECT * FROM withdraw_methods WHERE enabled=1", fetch="all")
    if not methods:
        await update.message.reply_text("❌ No withdraw methods available.")
        return

    context.user_data.clear()
    context.user_data["withdraw_state"] = "method"
    context.user_data["withdraw_methods"] = {m["method_name"]: dict(m) for m in methods}

    text = "💳 *Choose a Withdraw Method:*\n\n"
    for m in methods:
        mx = f" | Max: {m['max_amount']:.0f}" if float(m["max_amount"]) > 0 else ""
        text += f"• *{m['method_name']}* — Min: {m['min_amount']:.0f}{mx} coins\n"

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_method_keyboard(methods))

async def process_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("withdraw_state")
    text = update.message.text

    if state == "method":
        method_name = text.replace("💳 ", "").strip()
        methods = context.user_data.get("withdraw_methods", {})
        if method_name not in methods:
            await update.message.reply_text("❌ Please select a valid method from the keyboard.")
            return
        m = methods[method_name]
        context.user_data["withdraw_method"] = method_name
        context.user_data["withdraw_method_data"] = m
        context.user_data["withdraw_state"] = "amount"
        balance = await get_user_balance(update.effective_user.id)
        mx_text = f" (Max: {m['max_amount']:.0f})" if float(m["max_amount"]) > 0 else ""
        await update.message.reply_text(
            f"💰 Enter amount to withdraw:\n"
            f"Min: `{m['min_amount']:.0f}` coins{mx_text}\n"
            f"Your balance: `{balance:.2f}` coins",
            parse_mode="Markdown", reply_markup=get_back_keyboard()
        )

    elif state == "amount":
        try:
            amount = float(text)
            m = context.user_data.get("withdraw_method_data", {})
            balance = await get_user_balance(update.effective_user.id)
            min_a = float(m.get("min_amount", 10))
            max_a = float(m.get("max_amount", 0))
            if amount < min_a:
                await update.message.reply_text(f"❌ Minimum amount is `{min_a:.0f}` coins.", parse_mode="Markdown")
                return
            if max_a > 0 and amount > max_a:
                await update.message.reply_text(f"❌ Maximum amount is `{max_a:.0f}` coins.", parse_mode="Markdown")
                return
            if amount > balance:
                await update.message.reply_text(f"❌ Insufficient balance. You have `{balance:.2f}` coins.", parse_mode="Markdown")
                return
            context.user_data["withdraw_amount"] = amount
            context.user_data["withdraw_state"] = "account"
            inst = m.get("instructions", "")
            inst_text = f"\n💡 {inst}" if inst else ""
            await update.message.reply_text(
                f"📝 Enter your account details:{inst_text}",
                parse_mode="Markdown", reply_markup=get_back_keyboard()
            )
        except ValueError:
            await update.message.reply_text("❌ Invalid amount. Enter a number.")

    elif state == "account":
        uid = update.effective_user.id
        amount = context.user_data["withdraw_amount"]
        method = context.user_data["withdraw_method"]
        await update_balance(uid, -amount)
        await run_db_query(
            "INSERT INTO withdrawals (user_id, amount, payment_method, account_info) VALUES (?,?,?,?)",
            (uid, amount, method, text)
        )
        await update.message.reply_text(
            f"✅ *Withdrawal Request Submitted!*\n\n"
            f"💰 Amount: `{amount:.2f}` coins\n"
            f"💳 Method: {method}\n"
            f"📝 Account: {text}\n\n"
            f"An admin will process it shortly.",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(is_admin(uid))
        )
        context.user_data.clear()
        for admin_id in ADMIN_USER_IDS:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"💸 *New Withdrawal Request!*\n\n"
                    f"👤 User: `{uid}`\n"
                    f"💰 Amount: {amount:.2f} coins\n"
                    f"💳 Method: {method}\n"
                    f"📝 Account: {text}",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

# ==================== REDEEM ====================
async def redeem_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["redeem_state"] = True
    await update.message.reply_text("🎁 Enter your gift code:", reply_markup=get_back_keyboard())

async def process_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    uid = update.effective_user.id
    gift = await run_db_query("SELECT * FROM giftcodes WHERE code=?", (code,), "one")
    if not gift:
        await update.message.reply_text("❌ Invalid gift code.")
    elif gift["expiry_date"] and datetime.now() > datetime.fromisoformat(gift["expiry_date"]):
        await update.message.reply_text("❌ This code has expired.")
    elif int(gift["usage_limit"]) > 0 and int(gift["used_count"]) >= int(gift["usage_limit"]):
        await update.message.reply_text("❌ This code has reached its usage limit.")
    else:
        used = await run_db_query("SELECT 1 FROM giftcode_uses WHERE code=? AND user_id=?", (code, uid), "one")
        if used:
            await update.message.reply_text("❌ You have already used this code.")
        else:
            await update_balance(uid, float(gift["reward"]))
            await run_db_query("UPDATE giftcodes SET used_count=used_count+1 WHERE code=?", (code,))
            await run_db_query("INSERT INTO giftcode_uses (code, user_id) VALUES (?,?)", (code, uid))
            await update.message.reply_text(
                f"✅ *Code Redeemed!*\n💰 +`{float(gift['reward']):.2f}` coins added to your balance.",
                parse_mode="Markdown"
            )
    context.user_data.clear()

# ==================== LEADERBOARD ====================
async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = await run_db_query(
        "SELECT user_id, full_name, username, total_earned FROM users ORDER BY total_earned DESC LIMIT 10",
        fetch="all"
    )
    text = "🏆 *Top 10 Leaderboard*\n\n"
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for i, u in enumerate(users, 1):
        medal = medals.get(i, f"{i}.")
        name = f"@{u['username']}" if u['username'] else (u['full_name'] or str(u["user_id"]))
        text += f"{medal} {name} — `{u['total_earned']:.2f}` coins\n"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

# ==================== ADMIN PANEL ====================
async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data.clear()
    await update.message.reply_text(
        "🔧 *Admin Panel* — Choose an action:",
        parse_mode="Markdown",
        reply_markup=get_admin_keyboard()
    )

# --- Add Task ---
async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["add_task"] = {}
    context.user_data["add_task_step"] = "title"
    await update.message.reply_text("📝 Enter task title:", reply_markup=get_back_keyboard())

async def add_task_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("add_task_step")
    data = context.user_data["add_task"]
    text = update.message.text
    if step == "title":
        data["title"] = text
        context.user_data["add_task_step"] = "desc"
        await update.message.reply_text("📄 Enter description:")
    elif step == "desc":
        data["desc"] = text
        context.user_data["add_task_step"] = "link"
        await update.message.reply_text("🔗 Enter task link (or send 'skip'):")
    elif step == "link":
        data["link"] = None if text.lower() == "skip" else text
        context.user_data["add_task_step"] = "reward"
        await update.message.reply_text("💰 Enter reward amount (coins):")
    elif step == "reward":
        try:
            data["reward"] = float(text)
            await run_db_query(
                "INSERT INTO tasks (title, description, task_link, reward) VALUES (?,?,?,?)",
                (data["title"], data["desc"], data["link"], data["reward"])
            )
            await update.message.reply_text("✅ Task added successfully!", reply_markup=get_admin_keyboard())
            context.user_data.clear()
        except ValueError:
            await update.message.reply_text("❌ Invalid amount. Enter a number.")

# --- Remove Task ---
async def remove_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = await run_db_query("SELECT id, title FROM tasks", fetch="all")
    if not tasks:
        await update.message.reply_text("No tasks to remove.")
        return
    msg = "❌ *Remove Task* — Send the Task ID:\n\n"
    for t in tasks:
        msg += f"ID `{t['id']}`: {t['title']}\n"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_back_keyboard())
    context.user_data.clear()
    context.user_data["remove_task"] = True

async def remove_task_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tid = int(update.message.text)
        task = await run_db_query("SELECT title FROM tasks WHERE id=?", (tid,), "one")
        if not task:
            await update.message.reply_text("❌ Task not found.")
        else:
            await run_db_query("DELETE FROM tasks WHERE id=?", (tid,))
            await update.message.reply_text(f"✅ Task removed: *{task['title']}*", parse_mode="Markdown", reply_markup=get_admin_keyboard())
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.")
    context.user_data.clear()

# --- Pending Submissions ---
async def pending_submissions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subs = await run_db_query(
        "SELECT s.id, s.user_id, t.title, t.reward FROM submissions s "
        "JOIN tasks t ON s.task_id=t.id WHERE s.status='pending'",
        fetch="all"
    )
    if not subs:
        await update.message.reply_text("📭 No pending submissions.")
        return
    msg = "📥 *Pending Submissions:*\n\n"
    for s in subs:
        msg += f"ID `{s['id']}` | User `{s['user_id']}` | {s['title']} | {s['reward']} coins\n"
    msg += "\n✅ Approve: `approve ID`\n❌ Reject: `reject ID`"
    await update.message.reply_text(msg, parse_mode="Markdown")
    context.user_data.clear()
    context.user_data["submission_action"] = True

async def process_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.strip().lower().split()
    if len(parts) != 2 or parts[0] not in ("approve", "reject"):
        await update.message.reply_text("❌ Format: `approve ID` or `reject ID`", parse_mode="Markdown")
        return
    try:
        sid = int(parts[1])
        sub = await run_db_query("SELECT user_id, task_id, status FROM submissions WHERE id=?", (sid,), "one")
        if not sub:
            await update.message.reply_text("❌ Submission not found.")
        elif sub["status"] != "pending":
            await update.message.reply_text(f"⚠️ Already {sub['status']}.")
        elif parts[0] == "approve":
            task = await run_db_query("SELECT reward, title FROM tasks WHERE id=?", (sub["task_id"],), "one")
            if task:
                await update_balance(sub["user_id"], float(task["reward"]))
            await run_db_query("UPDATE submissions SET status='approved' WHERE id=?", (sid,))
            await update.message.reply_text("✅ Submission approved!")
            try:
                await context.bot.send_message(
                    sub["user_id"],
                    f"✅ *Your task submission was approved!*\n💰 +`{float(task['reward']):.2f}` coins added.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
        else:
            await run_db_query("UPDATE submissions SET status='rejected' WHERE id=?", (sid,))
            await update.message.reply_text("❌ Submission rejected.")
            try:
                await context.bot.send_message(sub["user_id"], "❌ Your task submission was rejected.")
            except Exception:
                pass
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.")
    context.user_data.clear()

# --- Withdraw Requests (Admin) ---
async def withdraw_requests_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reqs = await run_db_query("SELECT * FROM withdrawals WHERE status='pending'", fetch="all")
    if not reqs:
        await update.message.reply_text("📭 No pending withdrawal requests.")
        return
    msg = "💸 *Pending Withdrawals:*\n\n"
    for r in reqs:
        msg += (f"ID `{r['id']}` | User `{r['user_id']}` | "
                f"{r['amount']} coins | {r['payment_method']} | {r['account_info']}\n")
    msg += "\n✅ Approve: `approve_w ID`\n❌ Reject: `reject_w ID`"
    await update.message.reply_text(msg, parse_mode="Markdown")
    context.user_data.clear()
    context.user_data["withdraw_action"] = True

async def process_withdraw_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.strip().lower().split()
    if len(parts) != 2 or parts[0] not in ("approve_w", "reject_w"):
        await update.message.reply_text("❌ Format: `approve_w ID` or `reject_w ID`", parse_mode="Markdown")
        return
    try:
        wid = int(parts[1])
        req = await run_db_query("SELECT user_id, amount, status FROM withdrawals WHERE id=?", (wid,), "one")
        if not req:
            await update.message.reply_text("❌ Request not found.")
        elif req["status"] != "pending":
            await update.message.reply_text(f"⚠️ Already {req['status']}.")
        elif parts[0] == "approve_w":
            await run_db_query("UPDATE withdrawals SET status='approved' WHERE id=?", (wid,))
            await update.message.reply_text("✅ Withdrawal approved!")
            try:
                await context.bot.send_message(
                    req["user_id"],
                    f"✅ *Your withdrawal of `{req['amount']:.2f}` coins was approved!*\nPayment will be sent shortly.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
        else:
            # Refund balance on rejection
            await update_balance(req["user_id"], float(req["amount"]))
            await run_db_query("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,))
            await update.message.reply_text("❌ Withdrawal rejected. Balance refunded to user.")
            try:
                await context.bot.send_message(
                    req["user_id"],
                    f"❌ Your withdrawal request was rejected.\n"
                    f"💰 `{req['amount']:.2f}` coins have been refunded to your balance.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.")
    context.user_data.clear()

# --- Gift Codes ---
async def gift_codes_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["gift_code"] = {}
    context.user_data["gift_step"] = "reward"
    await update.message.reply_text("💰 Enter reward amount for the gift code:", reply_markup=get_back_keyboard())

async def process_gift_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("gift_step")
    data = context.user_data["gift_code"]
    text = update.message.text.strip()
    if step == "reward":
        try:
            data["reward"] = float(text)
            context.user_data["gift_step"] = "limit"
            await update.message.reply_text("🔢 Enter usage limit (0 = unlimited):")
        except ValueError:
            await update.message.reply_text("❌ Invalid amount.")
    elif step == "limit":
        try:
            data["limit"] = int(text)
            context.user_data["gift_step"] = "expiry"
            await update.message.reply_text("📅 Enter expiry date (YYYY-MM-DD) or 'skip':")
        except ValueError:
            await update.message.reply_text("❌ Invalid number.")
    elif step == "expiry":
        data["expiry"] = None if text.lower() == "skip" else text
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        await run_db_query(
            "INSERT INTO giftcodes (code, reward, usage_limit, expiry_date) VALUES (?,?,?,?)",
            (code, data["reward"], data["limit"], data["expiry"])
        )
        await update.message.reply_text(
            f"✅ *Gift Code Created!*\n\n"
            f"🎁 Code: `{code}`\n"
            f"💰 Reward: {data['reward']} coins\n"
            f"🔢 Usage Limit: {'Unlimited' if data['limit'] == 0 else data['limit']}\n"
            f"📅 Expiry: {data['expiry'] or 'Never'}",
            parse_mode="Markdown", reply_markup=get_admin_keyboard()
        )
        context.user_data.clear()

# --- Broadcast ---
async def broadcast_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["broadcast"] = True
    await update.message.reply_text("📢 Send the message to broadcast to all users:", reply_markup=get_back_keyboard())

async def process_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    users = await run_db_query("SELECT user_id FROM users WHERE is_banned=0", fetch="all")
    sent = 0
    failed = 0
    for u in users:
        try:
            await context.bot.send_message(u["user_id"], f"📢 *Announcement*\n\n{msg}", parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await update.message.reply_text(
        f"✅ Broadcast complete!\n📤 Sent: {sent}\n❌ Failed: {failed}",
        reply_markup=get_admin_keyboard()
    )
    context.user_data.clear()

# --- Analytics ---
async def analytics_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_users = (await run_db_query("SELECT COUNT(*) as c FROM users", fetch="one"))["c"]
    total_earned = (await run_db_query("SELECT SUM(total_earned) as s FROM users", fetch="one"))["s"] or 0
    total_tasks = (await run_db_query("SELECT COUNT(*) as c FROM tasks WHERE enabled=1", fetch="one"))["c"]
    pending_subs = (await run_db_query("SELECT COUNT(*) as c FROM submissions WHERE status='pending'", fetch="one"))["c"]
    pending_w = (await run_db_query("SELECT COUNT(*) as c FROM withdrawals WHERE status='pending'", fetch="one"))["c"]
    total_refs = (await run_db_query("SELECT COUNT(*) as c FROM referrals", fetch="one"))["c"]
    text = (
        f"📊 *Analytics*\n\n"
        f"👥 Total Users: `{total_users}`\n"
        f"📈 Total Coins Distributed: `{total_earned:.2f}`\n"
        f"📋 Active Tasks: `{total_tasks}`\n"
        f"📥 Pending Submissions: `{pending_subs}`\n"
        f"💸 Pending Withdrawals: `{pending_w}`\n"
        f"🎁 Total Referrals: `{total_refs}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_admin_keyboard())

# --- Manage User ---
async def manage_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["manage_user"] = "search"
    await update.message.reply_text(
        "👤 *Manage User*\n\nSend the user ID:",
        parse_mode="Markdown", reply_markup=get_back_keyboard()
    )

async def manage_user_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("manage_user")
    text = update.message.text.strip()

    if step == "search":
        try:
            uid = int(text)
            user = await run_db_query("SELECT * FROM users WHERE user_id=?", (uid,), "one")
            if not user:
                await update.message.reply_text("❌ User not found.")
                context.user_data.clear()
                return
            context.user_data["manage_uid"] = uid
            context.user_data["manage_user"] = "action"
            ban_status = "🚫 Banned" if user["is_banned"] else "✅ Active"
            await update.message.reply_text(
                f"👤 *User {uid}*\n"
                f"Name: {user['full_name'] or 'N/A'}\n"
                f"Balance: `{user['balance']:.2f}` coins\n"
                f"Status: {ban_status}\n\n"
                f"*Actions:*\n"
                f"• `add_coins AMOUNT`\n"
                f"• `remove_coins AMOUNT`\n"
                f"• `ban`\n"
                f"• `unban`",
                parse_mode="Markdown", reply_markup=get_back_keyboard()
            )
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
            context.user_data.clear()

    elif step == "action":
        uid = context.user_data.get("manage_uid")
        parts = text.lower().split()
        if parts[0] == "add_coins" and len(parts) == 2:
            try:
                amt = float(parts[1])
                await update_balance(uid, amt)
                await update.message.reply_text(f"✅ Added `{amt}` coins to user `{uid}`.", parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("❌ Invalid amount.")
        elif parts[0] == "remove_coins" and len(parts) == 2:
            try:
                amt = float(parts[1])
                await update_balance(uid, -amt)
                await update.message.reply_text(f"✅ Removed `{amt}` coins from user `{uid}`.", parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("❌ Invalid amount.")
        elif parts[0] == "ban":
            await run_db_query("UPDATE users SET is_banned=1 WHERE user_id=?", (uid,))
            await update.message.reply_text(f"✅ User `{uid}` banned.", parse_mode="Markdown")
        elif parts[0] == "unban":
            await run_db_query("UPDATE users SET is_banned=0 WHERE user_id=?", (uid,))
            await update.message.reply_text(f"✅ User `{uid}` unbanned.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Unknown action.")
        context.user_data.clear()

# --- Channels ---
async def add_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["add_channel"] = True
    await update.message.reply_text("Send channel username (e.g. @yourchannel):", reply_markup=get_back_keyboard())

async def process_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.replace("@", "").strip()
    await run_db_query("INSERT OR IGNORE INTO channels (channel_username) VALUES (?)", (username,))
    await update.message.reply_text(f"✅ Channel @{username} added!", reply_markup=get_admin_keyboard())
    context.user_data.clear()

async def remove_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channels = await run_db_query("SELECT channel_username FROM channels", fetch="all")
    if not channels:
        await update.message.reply_text("No channels to remove.")
        return
    context.user_data.clear()
    context.user_data["remove_channel"] = True
    msg = "Send channel username to remove:\n\n"
    for ch in channels:
        msg += f"• @{ch['channel_username']}\n"
    await update.message.reply_text(msg, reply_markup=get_back_keyboard())

async def process_remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.replace("@", "").strip()
    await run_db_query("DELETE FROM channels WHERE channel_username=?", (username,))
    await update.message.reply_text(f"✅ Channel @{username} removed!", reply_markup=get_admin_keyboard())
    context.user_data.clear()

# --- Withdraw Methods ---
async def add_method_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["add_method"] = {}
    context.user_data["add_method_step"] = "name"
    await update.message.reply_text("💳 Enter method name (e.g. bKash, PayPal, USDT):", reply_markup=get_back_keyboard())

async def add_method_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("add_method_step")
    data = context.user_data["add_method"]
    text = update.message.text.strip()
    if step == "name":
        data["name"] = text
        context.user_data["add_method_step"] = "min"
        await update.message.reply_text(f"Enter minimum withdrawal amount for *{text}*:", parse_mode="Markdown")
    elif step == "min":
        try:
            data["min"] = float(text)
            context.user_data["add_method_step"] = "max"
            await update.message.reply_text("Enter maximum withdrawal amount (0 = no limit):")
        except ValueError:
            await update.message.reply_text("❌ Invalid amount.")
    elif step == "max":
        try:
            data["max"] = float(text)
            context.user_data["add_method_step"] = "instructions"
            await update.message.reply_text("Enter instructions for users (or 'skip'):")
        except ValueError:
            await update.message.reply_text("❌ Invalid amount.")
    elif step == "instructions":
        data["inst"] = None if text.lower() == "skip" else text
        await run_db_query(
            "INSERT OR REPLACE INTO withdraw_methods (method_name, min_amount, max_amount, instructions) VALUES (?,?,?,?)",
            (data["name"], data["min"], data["max"], data["inst"])
        )
        await update.message.reply_text(
            f"✅ Method *{data['name']}* added!\n"
            f"Min: {data['min']} | Max: {data['max'] if data['max'] > 0 else 'No limit'}",
            parse_mode="Markdown", reply_markup=get_admin_keyboard()
        )
        context.user_data.clear()

async def remove_method_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    methods = await run_db_query("SELECT method_name FROM withdraw_methods", fetch="all")
    if not methods:
        await update.message.reply_text("No methods to remove.")
        return
    context.user_data.clear()
    context.user_data["remove_method"] = True
    msg = "🗑 Send method name to remove:\n\n"
    for m in methods:
        msg += f"• {m['method_name']}\n"
    await update.message.reply_text(msg, reply_markup=get_back_keyboard())

async def process_remove_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    method = await run_db_query("SELECT method_name FROM withdraw_methods WHERE method_name=?", (name,), "one")
    if not method:
        await update.message.reply_text("❌ Method not found.")
    else:
        await run_db_query("DELETE FROM withdraw_methods WHERE method_name=?", (name,))
        await update.message.reply_text(f"✅ Method *{name}* removed!", parse_mode="Markdown", reply_markup=get_admin_keyboard())
    context.user_data.clear()

async def method_limits_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    methods = await run_db_query("SELECT * FROM withdraw_methods", fetch="all")
    if not methods:
        await update.message.reply_text("No methods configured.")
        return
    context.user_data.clear()
    context.user_data["method_limits"] = "select"
    msg = "⚙️ *Method Limits* — Send method name to edit:\n\n"
    for m in methods:
        mx = f"Max: {m['max_amount']:.0f}" if float(m["max_amount"]) > 0 else "No max"
        status = "✅" if m["enabled"] else "❌"
        msg += f"{status} *{m['method_name']}* | Min: {m['min_amount']:.0f} | {mx}\n"
    msg += "\nSend a method name:"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_back_keyboard())

async def method_limits_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("method_limits")
    text = update.message.text.strip()

    if step == "select":
        method = await run_db_query("SELECT * FROM withdraw_methods WHERE method_name=?", (text,), "one")
        if not method:
            await update.message.reply_text("❌ Method not found.")
            context.user_data.clear()
            return
        context.user_data["edit_method_name"] = text
        context.user_data["method_limits"] = "action"
        await update.message.reply_text(
            f"⚙️ Editing *{text}*\n\n"
            f"Send action:\n"
            f"• `min AMOUNT` — Set min amount\n"
            f"• `max AMOUNT` — Set max (0 = no limit)\n"
            f"• `enable` — Enable method\n"
            f"• `disable` — Disable method",
            parse_mode="Markdown"
        )
    elif step == "action":
        name = context.user_data.get("edit_method_name")
        parts = text.lower().split()
        if parts[0] == "min" and len(parts) == 2:
            try:
                await run_db_query("UPDATE withdraw_methods SET min_amount=? WHERE method_name=?", (float(parts[1]), name))
                await update.message.reply_text(f"✅ Min for *{name}* set to `{parts[1]}`.", parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("❌ Invalid amount.")
        elif parts[0] == "max" and len(parts) == 2:
            try:
                await run_db_query("UPDATE withdraw_methods SET max_amount=? WHERE method_name=?", (float(parts[1]), name))
                await update.message.reply_text(f"✅ Max for *{name}* set to `{parts[1]}`.", parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("❌ Invalid amount.")
        elif parts[0] == "enable":
            await run_db_query("UPDATE withdraw_methods SET enabled=1 WHERE method_name=?", (name,))
            await update.message.reply_text(f"✅ *{name}* enabled.", parse_mode="Markdown")
        elif parts[0] == "disable":
            await run_db_query("UPDATE withdraw_methods SET enabled=0 WHERE method_name=?", (name,))
            await update.message.reply_text(f"✅ *{name}* disabled.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Unknown action.")
        context.user_data.clear()

# --- Toggle Withdraw ---
async def toggle_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = await get_setting("withdraw_enabled", "true")
    new_val = "false" if current == "true" else "true"
    await set_setting("withdraw_enabled", new_val)
    status = "✅ ENABLED" if new_val == "true" else "❌ DISABLED"
    await update.message.reply_text(f"Withdrawals are now: *{status}*", parse_mode="Markdown", reply_markup=get_admin_keyboard())

# --- Min Withdraw ---
async def set_min_withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["set_min"] = True
    current = await get_setting("min_withdraw", "10")
    await update.message.reply_text(
        f"💰 Current global min withdraw: `{current}` coins\n\nEnter new minimum:",
        parse_mode="Markdown", reply_markup=get_back_keyboard()
    )

async def process_set_min(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text)
        await set_setting("min_withdraw", str(amount))
        await update.message.reply_text(f"✅ Global min withdraw set to `{amount:.2f}` coins.", parse_mode="Markdown", reply_markup=get_admin_keyboard())
    except ValueError:
        await update.message.reply_text("❌ Invalid amount.")
    context.user_data.clear()

# --- Referral Bonus ---
async def set_referral_bonus_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["set_bonus"] = True
    current = await get_setting("referral_bonus", "5")
    await update.message.reply_text(
        f"👥 Current referral bonus: `{current}` coins\n\nEnter new bonus:",
        parse_mode="Markdown", reply_markup=get_back_keyboard()
    )

async def process_set_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text)
        await set_setting("referral_bonus", str(amount))
        await update.message.reply_text(f"✅ Referral bonus set to `{amount:.2f}` coins.", parse_mode="Markdown", reply_markup=get_admin_keyboard())
    except ValueError:
        await update.message.reply_text("❌ Invalid amount.")
    context.user_data.clear()

# --- Set Support ---
async def set_support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["set_support"] = True
    current = await get_setting("support_username", "")
    await update.message.reply_text(
        f"📞 Current support: @{current or 'Not set'}\n\nEnter support username (e.g. @support_user):",
        parse_mode="Markdown", reply_markup=get_back_keyboard()
    )

async def process_set_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.replace("@", "").strip()
    await set_setting("support_username", username)
    await update.message.reply_text(f"✅ Support username set to @{username}", reply_markup=get_admin_keyboard())
    context.user_data.clear()

# ==================== START COMMAND ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ref = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            ref = int(context.args[0].split("_")[1])
        except (ValueError, IndexError):
            ref = None

    if not await check_force_join(user.id, context):
        await show_force_join_message(update, context)
        return

    await register_user(user.id, user.username, user.full_name, ref)
    context.user_data.clear()

    balance = await get_user_balance(user.id)
    await update.message.reply_text(
        f"👋 *Welcome, {user.first_name}!*\n\n"
        f"💰 Your balance: `{balance:.2f}` coins\n\n"
        f"Complete tasks, refer friends, and earn coins!\n"
        f"Use the menu below to get started. 🚀",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(is_admin(user.id))
    )

# ==================== TASK COMMANDS ====================
async def handle_task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text = update.message.text
    if not text.startswith("/do_"):
        return
    try:
        task_id = int(text.split("_")[1])
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Invalid task command.")
        return

    if not await check_force_join(update.effective_user.id, context):
        await show_force_join_message(update, context)
        return

    existing = await run_db_query(
        "SELECT id FROM submissions WHERE user_id=? AND task_id=? AND status IN ('pending','approved')",
        (update.effective_user.id, task_id), "one"
    )
    if existing:
        await update.message.reply_text("⚠️ You already submitted this task!")
        return

    task = await run_db_query("SELECT title FROM tasks WHERE id=? AND enabled=1", (task_id,), "one")
    if not task:
        await update.message.reply_text("❌ Task not found.")
        return

    context.user_data["current_task"] = task_id
    context.user_data["state"] = "waiting_screenshot"
    await update.message.reply_text(
        f"📸 Task: *{task['title']}*\n\nPlease send a screenshot as proof of completion:",
        parse_mode="Markdown", reply_markup=get_back_keyboard()
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "waiting_screenshot":
        return
    task_id = context.user_data.get("current_task")
    if not task_id:
        return
    uid = update.effective_user.id
    photo = update.message.photo[-1].file_id
    await run_db_query(
        "INSERT INTO submissions (user_id, task_id, screenshot_file_id) VALUES (?,?,?)",
        (uid, task_id, photo)
    )
    sub_row = await run_db_query(
        "SELECT id FROM submissions WHERE user_id=? AND task_id=? ORDER BY id DESC",
        (uid, task_id), "one"
    )
    sub_id = sub_row["id"] if sub_row else "?"
    await update.message.reply_text(
        "✅ *Task submitted for review!*\nYou'll be notified once approved.",
        parse_mode="Markdown", reply_markup=get_main_keyboard(is_admin(uid))
    )
    context.user_data.clear()
    for admin_id in ADMIN_USER_IDS:
        try:
            task = await run_db_query("SELECT title FROM tasks WHERE id=?", (task_id,), "one")
            await context.bot.send_photo(
                admin_id, photo,
                caption=(
                    f"📝 *New Task Submission!*\n\n"
                    f"👤 User: `{uid}`\n"
                    f"📋 Task: {task['title'] if task else task_id}\n"
                    f"🆔 Sub ID: `{sub_id}`\n\n"
                    f"Reply with:\n✅ `approve {sub_id}`\n❌ `reject {sub_id}`"
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass

# ==================== MESSAGE HANDLER ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    uid = update.effective_user.id

    # Back / Home always work
    if text in ("🔙 Back", "🏠 Home"):
        await show_main_menu(update, context)
        return

    # Check ban
    user_row = await run_db_query("SELECT is_banned FROM users WHERE user_id=?", (uid,), "one")
    if user_row and user_row["is_banned"]:
        await update.message.reply_text("🚫 You are banned from using this bot.")
        return

    # Force join (skip for admins)
    if not is_admin(uid) and not await check_force_join(uid, context):
        await show_force_join_message(update, context)
        return

    # ---- Active state flows ----
    if context.user_data.get("withdraw_state"):
        await process_withdraw(update, context)
        return
    if context.user_data.get("redeem_state"):
        await process_redeem(update, context)
        return
    if context.user_data.get("add_task_step"):
        await add_task_process(update, context)
        return
    if context.user_data.get("remove_task"):
        await remove_task_process(update, context)
        return
    if context.user_data.get("submission_action"):
        await process_submission(update, context)
        return
    if context.user_data.get("withdraw_action"):
        await process_withdraw_admin(update, context)
        return
    if context.user_data.get("gift_step"):
        await process_gift_code(update, context)
        return
    if context.user_data.get("broadcast"):
        await process_broadcast(update, context)
        return
    if context.user_data.get("add_channel"):
        await process_add_channel(update, context)
        return
    if context.user_data.get("remove_channel"):
        await process_remove_channel(update, context)
        return
    if context.user_data.get("add_method_step"):
        await add_method_process(update, context)
        return
    if context.user_data.get("remove_method"):
        await process_remove_method(update, context)
        return
    if context.user_data.get("method_limits"):
        await method_limits_process(update, context)
        return
    if context.user_data.get("set_min"):
        await process_set_min(update, context)
        return
    if context.user_data.get("set_bonus"):
        await process_set_bonus(update, context)
        return
    if context.user_data.get("set_support"):
        await process_set_support(update, context)
        return
    if context.user_data.get("manage_user"):
        await manage_user_process(update, context)
        return
    if context.user_data.get("awaiting_task_id"):
        context.user_data.pop("awaiting_task_id", None)
        try:
            await show_task_detail(update, context, int(text))
        except ValueError:
            await update.message.reply_text("❌ Invalid task ID.")
        return

    # ---- Main menu buttons ----
    if text == "👤 Account":
        await show_account(update, context)
    elif text == "💰 Wallet":
        await show_wallet(update, context)
    elif text == "👥 Team":
        await show_team(update, context)
    elif text == "🎁 Invite":
        await show_invite(update, context)
    elif text == "📋 Tasks":
        await show_tasks(update, context)
    elif text == "🏆 Leaderboard":
        await show_leaderboard(update, context)
    elif text == "💳 Withdraw":
        await withdraw_start(update, context)
    elif text == "🎁 Redeem":
        await redeem_start(update, context)
    elif text == "📢 Channels":
        await show_channels(update, context)
    elif text == "🆘 Support":
        await show_support(update, context)

    # ---- Admin menu buttons ----
    elif text == "🔧 Admin Panel" and is_admin(uid):
        await show_admin_panel(update, context)
    elif is_admin(uid):
        if text == "➕ Add Task":
            await add_task_start(update, context)
        elif text == "❌ Remove Task":
            await remove_task_start(update, context)
        elif text == "📥 Pending Tasks":
            await pending_submissions(update, context)
        elif text == "💸 Withdraw Requests":
            await withdraw_requests_admin(update, context)
        elif text == "🎁 Create Gift Code":
            await gift_codes_admin(update, context)
        elif text == "📢 Broadcast":
            await broadcast_admin(update, context)
        elif text == "📊 Analytics":
            await analytics_admin(update, context)
        elif text == "👤 Manage User":
            await manage_user_start(update, context)
        elif text == "➕ Add Channel":
            await add_channel_start(update, context)
        elif text == "❌ Remove Channel":
            await remove_channel_start(update, context)
        elif text == "💳 Add Method":
            await add_method_start(update, context)
        elif text == "🗑 Remove Method":
            await remove_method_start(update, context)
        elif text == "⚙️ Method Limits":
            await method_limits_start(update, context)
        elif text == "🔘 Toggle Withdraw":
            await toggle_withdraw(update, context)
        elif text == "💰 Min Withdraw":
            await set_min_withdraw_start(update, context)
        elif text == "👥 Referral Bonus":
            await set_referral_bonus_start(update, context)
        elif text == "📞 Set Support":
            await set_support_start(update, context)
        else:
            await show_main_menu(update, context)
    else:
        await show_main_menu(update, context)

# ==================== MAIN ====================
async def main():
    await init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex(r"^/do_\d+$"), handle_task_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🤖 Bot started successfully!")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
