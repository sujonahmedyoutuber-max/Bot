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

load_dotenv()

# ==================== CONFIG ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_IDS = [int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()]
DEFAULT_MIN_WITHDRAW = float(os.getenv("MIN_WITHDRAW_AMOUNT", "10.0"))
DEFAULT_REFERRAL_BONUS = float(os.getenv("REFERRAL_BONUS", "5.0"))
DEFAULT_WITHDRAW_ENABLED = os.getenv("WITHDRAW_ENABLED", "True").lower() == "true"

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
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

# ==================== INIT DATABASE ====================
async def init_db():
    queries = [
        """CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
            balance REAL DEFAULT 0, total_earned REAL DEFAULT 0, referred_by INTEGER,
            redeemed_codes TEXT DEFAULT '', is_banned INTEGER DEFAULT 0,
            joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT,
            task_link TEXT, reward REAL NOT NULL, photo_url TEXT, enabled INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, task_id INTEGER,
            screenshot_file_id TEXT, note TEXT, status TEXT DEFAULT 'pending',
            admin_comment TEXT, submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, processed_at TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL,
            payment_method TEXT, account_info TEXT, status TEXT DEFAULT 'pending',
            reason TEXT, requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, processed_at TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS giftcodes (
            code TEXT PRIMARY KEY, reward REAL, usage_limit INTEGER DEFAULT 0,
            used_count INTEGER DEFAULT 0, expiry_date TIMESTAMP, created_by INTEGER
        )""",
        """CREATE TABLE IF NOT EXISTS referrals (
            referrer_id INTEGER, referred_id INTEGER UNIQUE, bonus_paid INTEGER DEFAULT 0,
            referred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT, channel_username TEXT UNIQUE NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""",
        """CREATE TABLE IF NOT EXISTS withdraw_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            method_name TEXT UNIQUE NOT NULL,
            required_info TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        )"""
    ]

    for query in queries:
        await run_db_query(query)

    await run_db_query("INSERT OR IGNORE INTO settings (key, value) VALUES ('min_withdraw', ?)", (str(DEFAULT_MIN_WITHDRAW),))
    await run_db_query("INSERT OR IGNORE INTO settings (key, value) VALUES ('referral_bonus', ?)", (str(DEFAULT_REFERRAL_BONUS),))
    await run_db_query("INSERT OR IGNORE INTO settings (key, value) VALUES ('withdraw_enabled', ?)", (str(DEFAULT_WITHDRAW_ENABLED).lower(),))

# ==================== HELPERS ====================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

async def update_last_active(user_id: int):
    await run_db_query("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE user_id = ?", (user_id,))

async def get_user_balance(user_id: int) -> float:
    row = await run_db_query("SELECT balance FROM users WHERE user_id = ?", (user_id,), "one")
    return row["balance"] if row else 0.0

async def update_user_balance(user_id: int, amount_delta: float):
    await run_db_query("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount_delta, user_id))
    if amount_delta > 0:
        await run_db_query("UPDATE users SET total_earned = total_earned + ? WHERE user_id = ?", (amount_delta, user_id))

async def register_user(user_id: int, username: str = None, full_name: str = None, referred_by: int = None):
    if await run_db_query("SELECT user_id FROM users WHERE user_id = ?", (user_id,), "one"):
        return
    if referred_by == user_id:
        referred_by = None
    await run_db_query(
        "INSERT INTO users (user_id, username, full_name, referred_by) VALUES (?, ?, ?, ?)",
        (user_id, username, full_name, referred_by)
    )
    if referred_by:
        bonus = await get_referral_bonus()
        await update_user_balance(referred_by, bonus)
        await run_db_query("INSERT INTO referrals (referrer_id, referred_id, bonus_paid) VALUES (?, ?, 1)", (referred_by, user_id))

async def get_withdraw_methods():
    return await run_db_query("SELECT * FROM withdraw_methods WHERE is_active = 1", fetch="all")

# ==================== KEYBOARDS ====================
def get_main_keyboard(is_admin_user=False):
    kb = [
        ["👤 Account", "💰 Wallet"],
        ["👥 Team", "🎁 Invite"],
        ["📋 Tasks", "🏆 Leaderboard"],
        ["💳 Withdraw", "🎁 Redeem"],
        ["📢 Channel", "🆘 Support"]
    ]
    if is_admin_user:
        kb.append(["🔧 Admin Panel"])
    kb.append(["🔙 Back", "🏠 Home"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def get_admin_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ Add Task", "❌ Remove Task"],
        ["📥 Pending Tasks", "💸 Withdraw Requests"],
        ["🎁 Gift Codes", "📢 Broadcast"],
        ["👤 User Management", "📊 Analytics"],
        ["📢 Force Join", "⚙️ Bot Settings"],
        ["💳 Withdraw Methods"],
        ["🔙 Back", "🏠 Home"]
    ], resize_keyboard=True)

def get_settings_keyboard():
    return ReplyKeyboardMarkup([
        ["💰 Set Min Withdraw"], ["👥 Set Referral Bonus"],
        ["🔘 Toggle Withdraw System"], ["📊 View Current Settings"],
        ["🔙 Back", "🏠 Home"]
    ], resize_keyboard=True)

def get_force_join_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ Add Channel", "❌ Remove Channel"], ["📋 List Channels"],
        ["🔙 Back", "🏠 Home"]
    ], resize_keyboard=True)

def get_user_management_keyboard():
    return ReplyKeyboardMarkup([
        ["🔍 View User"],
        ["💰 Add Balance", "💸 Remove Balance"],
        ["🚫 Ban User", "✅ Unban User"],
        ["📊 User Stats"],
        ["🔙 Back", "🏠 Home"]
    ], resize_keyboard=True)

def get_withdraw_methods_keyboard(methods):
    kb = [[KeyboardButton(m["method_name"])] for m in methods]
    kb.append(["🔙 Back", "🏠 Home"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

# ==================== NAVIGATION ====================
async def push_history(context, menu):
    if "history" not in context.user_data:
        context.user_data["history"] = []
    context.user_data["history"].append(menu)

async def go_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = context.user_data.get("history", [])
    if len(history) > 1:
        history.pop()
        prev = history[-1]
        if prev == "main": await show_main_menu(update, context)
        elif prev == "admin_panel": await show_admin_panel(update, context)
        elif prev == "bot_settings": await bot_settings(update, context)
        elif prev == "force_join": await admin_force_join(update, context)
        elif prev == "user_management": await user_management(update, context)
        elif prev == "withdraw_methods": await admin_withdraw_methods(update, context)
    else:
        await reset_to_home(update, context)

async def reset_to_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await show_main_menu(update, context)

# ==================== MAIN MENU FUNCTIONS ====================
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update_last_active(update.effective_user.id)
    await push_history(context, "main")
    await update.message.reply_text("🏠 *Main Menu*", parse_mode="Markdown", reply_markup=get_main_keyboard(is_admin(update.effective_user.id)))

async def show_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await run_db_query("SELECT * FROM users WHERE user_id=?", (update.effective_user.id,), "one")
    text = f"👤 *Account Info*\nID: `{user['user_id']}`\nName: {user['full_name'] or 'N/A'}\nBalance: {user['balance']:.2f} coins"
    await push_history(context, "account")
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard(is_admin(update.effective_user.id)))

async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balance = await get_user_balance(update.effective_user.id)
    minw = await get_min_withdraw()
    text = f"💰 *Wallet*\nCurrent Balance: {balance:.2f} coins\nMinimum Withdraw: {minw:.2f} coins"
    await push_history(context, "wallet")
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard(is_admin(update.effective_user.id)))

async def show_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = await run_db_query("SELECT COUNT(*) as cnt FROM referrals WHERE referrer_id=?", (update.effective_user.id,), "one")
    bonus = await get_referral_bonus()
    text = f"👥 *Team*\nReferred Users: {count['cnt'] if count else 0}\nBonus per referral: {bonus:.2f} coins"
    await push_history(context, "team")
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard(is_admin(update.effective_user.id)))

async def show_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = await context.bot.get_me()
    link = f"https://t.me/{bot.username}?start=ref_{update.effective_user.id}"
    bonus = await get_referral_bonus()
    text = f"🎁 *Invite Friends*\nLink: {link}\nEach referral gives you {bonus:.2f} coins"
    await push_history(context, "invite")
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard(is_admin(update.effective_user.id)))

async def show_tasks_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = await run_db_query("SELECT * FROM tasks WHERE enabled=1", fetch="all")
    if not tasks:
        return await update.message.reply_text("No tasks available.")
    msg = "📋 *Available Tasks*\n\n"
    for i, t in enumerate(tasks, 1):
        msg += f"{i}. {t['title']} — {t['reward']:.2f} coins\n"
    msg += "\nSend the number to view details."
    context.user_data["awaiting_task_selection"] = True
    context.user_data["tasks_list"] = tasks
    await push_history(context, "tasks")
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard(is_admin(update.effective_user.id)))

# ==================== WITHDRAW SYSTEM (Admin Configurable) ====================
async def withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_withdraw_enabled():
        return await update.message.reply_text("❌ Withdraw is disabled by admin.")
    balance = await get_user_balance(update.effective_user.id)
    minw = await get_min_withdraw()
    if balance < minw:
        return await update.message.reply_text(f"❌ Minimum withdraw is {minw:.2f} coins.")

    methods = await get_withdraw_methods()
    if not methods:
        return await update.message.reply_text("❌ No payment methods added by admin yet.")

    context.user_data["state"] = "SELECT_WITHDRAW_METHOD"
    await update.message.reply_text("💳 Select Payment Method:", reply_markup=get_withdraw_methods_keyboard(methods))

async def handle_withdraw_method_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    method_name = update.message.text
    methods = await get_withdraw_methods()
    method = next((m for m in methods if m["method_name"] == method_name), None)
    if not method:
        return await update.message.reply_text("❌ Invalid method.")

    context.user_data["withdraw_method"] = method_name
    context.user_data["withdraw_required"] = method["required_info"]
    context.user_data["state"] = "WITHDRAW_AMOUNT"
    await update.message.reply_text(f"Enter amount to withdraw (Min: {await get_min_withdraw():.2f}):")

async def withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text)
        if amount < await get_min_withdraw():
            raise ValueError
        context.user_data["withdraw_amount"] = amount
        context.user_data["state"] = "WITHDRAW_ACCOUNT_INFO"
        await update.message.reply_text(f"Send your {context.user_data['withdraw_required']}:")
    except:
        await update.message.reply_text("❌ Invalid amount. Try again.")

async def withdraw_account_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account_info = update.message.text
    user_id = update.effective_user.id
    amount = context.user_data["withdraw_amount"]
    method = context.user_data["withdraw_method"]

    await run_db_query(
        "INSERT INTO withdrawals (user_id, amount, payment_method, account_info) VALUES (?,?,?,?)",
        (user_id, amount, method, account_info)
    )
    context.user_data.clear()
    await update.message.reply_text("✅ Withdrawal request submitted successfully!", reply_markup=get_main_keyboard(is_admin(user_id)))

# ==================== ADMIN WITHDRAW METHODS ====================
async def admin_withdraw_methods(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await push_history(context, "withdraw_methods")
    await update.message.reply_text("💳 *Withdraw Methods Management*", parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([["➕ Add Method"], ["📋 List Methods"], ["❌ Remove Method"], ["🔙 Back", "🏠 Home"]], resize_keyboard=True))

async def add_withdraw_method_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["wm_step"] = 1
    await update.message.reply_text("Enter Method Name (e.g: bKash, Nagad, Bank):")

async def handle_wm_steps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("wm_step")
    if step == 1:
        context.user_data["wm_name"] = update.message.text
        context.user_data["wm_step"] = 2
        await update.message.reply_text("What information do you need from user?\nExample: Phone Number")
    elif step == 2:
        name = context.user_data["wm_name"]
        info = update.message.text
        await run_db_query("INSERT OR IGNORE INTO withdraw_methods (method_name, required_info) VALUES (?,?)", (name, info))
        await update.message.reply_text(f"✅ Method '{name}' added!")
        context.user_data.pop("wm_step", None)
        context.user_data.pop("wm_name", None)
        await admin_withdraw_methods(update, context)

# ==================== MESSAGE HANDLER (সবকিছু এখানে) ====================
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text
    user_id = update.effective_user.id
    state = context.user_data.get("state")

    await update_last_active(user_id)

    if text == "🔙 Back":
        await go_back(update, context)
        return
    if text == "🏠 Home":
        await reset_to_home(update, context)
        return

    # State Handling
    if state == "SELECT_WITHDRAW_METHOD":
        await handle_withdraw_method_selection(update, context)
        return
    if state == "WITHDRAW_AMOUNT":
        await withdraw_amount(update, context)
        return
    if state == "WITHDRAW_ACCOUNT_INFO":
        await withdraw_account_info(update, context)
        return
    if context.user_data.get("wm_step"):
        await handle_wm_steps(update, context)
        return

    # Main Buttons
    if text == "👤 Account": await show_account(update, context)
    elif text == "💰 Wallet": await show_wallet(update, context)
    elif text == "👥 Team": await show_team(update, context)
    elif text == "🎁 Invite": await show_invite(update, context)
    elif text == "📋 Tasks": await show_tasks_list(update, context)
    elif text == "🏆 Leaderboard": await show_leaderboard(update, context)
    elif text == "💳 Withdraw": await withdraw_start(update, context)
    elif text == "🎁 Redeem": 
        context.user_data["state"] = "REDEEM_CODE"
        await update.message.reply_text("Send Gift Code:")
    elif text == "🔧 Admin Panel" and is_admin(user_id):
        await show_admin_panel(update, context)

    # Admin Buttons
    elif is_admin(user_id):
        if text == "💳 Withdraw Methods":
            await admin_withdraw_methods(update, context)
        elif text == "➕ Add Method":
            await add_withdraw_method_start(update, context)
        elif text == "⚙️ Bot Settings":
            await bot_settings(update, context)
        # অন্যান্য অ্যাডমিন ফাংশন যোগ করতে পারবে

    else:
        await update.message.reply_text("Please use the buttons.", reply_markup=get_main_keyboard(is_admin(user_id)))

# ==================== OTHER FUNCTIONS (Placeholder) ====================
async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏆 Leaderboard coming soon...", reply_markup=get_main_keyboard(is_admin(update.effective_user.id)))

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await push_history(context, "admin_panel")
    await update.message.reply_text("🔧 Admin Panel", reply_markup=get_admin_keyboard())

async def bot_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await push_history(context, "bot_settings")
    await update.message.reply_text("⚙️ Bot Settings", reply_markup=get_settings_keyboard())

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") == "WAITING_SCREENSHOT":
        # তোমার আগের screenshot handler
        pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ref = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            ref = int(context.args[0].split("_")[1])
        except:
            pass
    await register_user(user.id, user.username, user.full_name, ref)
    await show_main_menu(update, context)

# ==================== RUN BOT ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    asyncio.get_event_loop().run_until_complete(init_db())

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & \~filters.COMMAND, message_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    logger.info("🤖 Bot Started Successfully with Full Withdraw System!")
    app.run_polling()

if __name__ == "__main__":
    main()
