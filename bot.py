"""
Telegram Task & Reward Bot - Complete Working Code for Railway
✅ Only ReplyKeyboardMarkup (No InlineKeyboardMarkup)
✅ Universal Back/Home Navigation
✅ ALL Admin commands via buttons
✅ Ready for Railway Deployment
"""

import asyncio
import logging
import os
import sqlite3
import random
import string
from datetime import datetime, timedelta

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ==================== CONFIGURATION ====================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set in environment variables")

ADMIN_USER_IDS = [int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()]
DEFAULT_MIN_WITHDRAW = float(os.getenv("MIN_WITHDRAW_AMOUNT", "10.0"))
DEFAULT_REFERRAL_BONUS = float(os.getenv("REFERRAL_BONUS", "5.0"))
DEFAULT_WITHDRAW_ENABLED = os.getenv("WITHDRAW_ENABLED", "True").lower() == "true"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DB_PATH = "bot_data.db"

# ==================== DATABASE HELPER FUNCTIONS ====================
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
            elif fetch == "lastrowid":
                return cursor.lastrowid
            else:
                conn.commit()
                return None
    return await asyncio.to_thread(_query)

# ==================== SETTINGS MANAGEMENT ====================
async def get_setting(key: str, default: str = "0") -> str:
    row = await run_db_query("SELECT value FROM settings WHERE key = ?", (key,), "one")
    return row["value"] if row else default

async def set_setting(key: str, value: str):
    await run_db_query("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

async def get_min_withdraw() -> float:
    return float(await get_setting("min_withdraw", str(DEFAULT_MIN_WITHDRAW)))

async def get_referral_bonus() -> float:
    return float(await get_setting("referral_bonus", str(DEFAULT_REFERRAL_BONUS)))

async def is_withdraw_enabled() -> bool:
    return (await get_setting("withdraw_enabled", str(DEFAULT_WITHDRAW_ENABLED).lower())) == "true"

# ==================== DATABASE INITIALIZATION ====================
async def init_db():
    queries = [
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            balance REAL DEFAULT 0,
            total_earned REAL DEFAULT 0,
            referred_by INTEGER,
            redeemed_codes TEXT DEFAULT '',
            is_banned INTEGER DEFAULT 0,
            joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            task_link TEXT,
            reward REAL NOT NULL,
            photo_url TEXT,
            enabled INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            task_id INTEGER,
            screenshot_file_id TEXT,
            note TEXT,
            status TEXT DEFAULT 'pending',
            admin_comment TEXT,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id),
            FOREIGN KEY(task_id) REFERENCES tasks(id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            payment_method TEXT,
            account_info TEXT,
            status TEXT DEFAULT 'pending',
            reason TEXT,
            requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS giftcodes (
            code TEXT PRIMARY KEY,
            reward REAL,
            usage_limit INTEGER,
            used_count INTEGER DEFAULT 0,
            expiry_date TIMESTAMP,
            created_by INTEGER
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS referrals (
            referrer_id INTEGER,
            referred_id INTEGER UNIQUE,
            bonus_paid INTEGER DEFAULT 0,
            referred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(referrer_id) REFERENCES users(user_id),
            FOREIGN KEY(referred_id) REFERENCES users(user_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_username TEXT UNIQUE NOT NULL,
            invite_link TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_submissions_user ON submissions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status)",
        "CREATE INDEX IF NOT EXISTS idx_withdrawals_user ON withdrawals(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)",
    ]
    for query in queries:
        await run_db_query(query)
    
    await run_db_query("INSERT OR IGNORE INTO settings (key, value) VALUES ('min_withdraw', ?)", (str(DEFAULT_MIN_WITHDRAW),))
    await run_db_query("INSERT OR IGNORE INTO settings (key, value) VALUES ('referral_bonus', ?)", (str(DEFAULT_REFERRAL_BONUS),))
    await run_db_query("INSERT OR IGNORE INTO settings (key, value) VALUES ('withdraw_enabled', ?)", (str(DEFAULT_WITHDRAW_ENABLED).lower(),))

# ==================== HELPER FUNCTIONS ====================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

async def get_user_balance(user_id: int) -> float:
    row = await run_db_query("SELECT balance FROM users WHERE user_id = ?", (user_id,), "one")
    return row["balance"] if row else 0.0

async def update_user_balance(user_id: int, amount_delta: float):
    await run_db_query("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount_delta, user_id))
    if amount_delta > 0:
        await run_db_query("UPDATE users SET total_earned = total_earned + ? WHERE user_id = ?", (amount_delta, user_id))

async def register_user(user_id: int, username: str = None, full_name: str = None, referred_by: int = None):
    existing = await run_db_query("SELECT user_id FROM users WHERE user_id = ?", (user_id,), "one")
    if existing:
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
        await run_db_query(
            "INSERT INTO referrals (referrer_id, referred_id, bonus_paid) VALUES (?, ?, 1)",
            (referred_by, user_id)
        )

async def force_join_passed(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    channels = await run_db_query("SELECT channel_username FROM channels", fetch="all")
    if not channels:
        return True
    for row in channels:
        username = row["channel_username"]
        try:
            chat_member = await context.bot.get_chat_member(f"@{username}", user_id)
            if chat_member.status in ["left", "kicked"]:
                return False
        except Exception:
            return False
    return True

# ==================== KEYBOARDS ====================
def get_main_keyboard(is_admin_user: bool = False) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton("👤 Account"), KeyboardButton("💰 Wallet")],
        [KeyboardButton("👥 Team"), KeyboardButton("🎁 Invite")],
        [KeyboardButton("📋 Tasks"), KeyboardButton("🏆 Leaderboard")],
        [KeyboardButton("💳 Withdraw"), KeyboardButton("🎁 Redeem")],
        [KeyboardButton("📢 Channel"), KeyboardButton("🆘 Support")],
    ]
    if is_admin_user:
        buttons.append([KeyboardButton("🔧 Admin Panel")])
    buttons.append([KeyboardButton("🔙 Back"), KeyboardButton("🏠 Home")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_admin_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton("➕ Add Task"), KeyboardButton("❌ Remove Task")],
        [KeyboardButton("📥 Pending Tasks"), KeyboardButton("💸 Withdraw Requests")],
        [KeyboardButton("🎁 Gift Codes"), KeyboardButton("📢 Broadcast")],
        [KeyboardButton("👤 User Management"), KeyboardButton("📊 Analytics")],
        [KeyboardButton("📢 Force Join"), KeyboardButton("⚙️ Bot Settings")],
        [KeyboardButton("🔙 Back"), KeyboardButton("🏠 Home")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_settings_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton("💰 Set Min Withdraw")],
        [KeyboardButton("👥 Set Referral Bonus")],
        [KeyboardButton("🔘 Toggle Withdraw System")],
        [KeyboardButton("📊 View Current Settings")],
        [KeyboardButton("🔙 Back"), KeyboardButton("🏠 Home")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_back_home_keyboard() -> ReplyKeyboardMarkup:
    buttons = [[KeyboardButton("🔙 Back"), KeyboardButton("🏠 Home")]]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_force_join_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton("➕ Add Channel"), KeyboardButton("❌ Remove Channel")],
        [KeyboardButton("📋 List Channels")],
        [KeyboardButton("🔙 Back"), KeyboardButton("🏠 Home")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_user_management_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton("🔍 View User")],
        [KeyboardButton("💰 Add Balance"), KeyboardButton("💸 Remove Balance")],
        [KeyboardButton("🚫 Ban User"), KeyboardButton("✅ Unban User")],
        [KeyboardButton("📊 User Stats")],
        [KeyboardButton("🔙 Back"), KeyboardButton("🏠 Home")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# ==================== NAVIGATION HISTORY ====================
async def push_history(update: Update, context: ContextTypes.DEFAULT_TYPE, menu_name: str, data: dict = None):
    if "history" not in context.user_data:
        context.user_data["history"] = []
    context.user_data["history"].append({"menu": menu_name, "data": data or {}})

async def pop_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    history = context.user_data.get("history", [])
    if not history:
        return False
    history.pop()
    if not history:
        return False
    prev = history[-1]
    if prev["menu"] == "main":
        await show_main_menu(update, context)
    elif prev["menu"] == "admin_panel":
        await show_admin_panel(update, context)
    elif prev["menu"] == "bot_settings":
        await bot_settings(update, context)
    elif prev["menu"] == "force_join":
        await admin_force_join(update, context)
    elif prev["menu"] == "user_management":
        await user_management(update, context)
    else:
        await show_main_menu(update, context)
    return True

async def reset_to_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await show_main_menu(update, context)

async def handle_back_or_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    text = update.message.text
    if text == "🔙 Back":
        success = await pop_history(update, context)
        if not success:
            await update.message.reply_text("No previous page.", reply_markup=get_back_home_keyboard())
        return True
    elif text == "🏠 Home":
        await reset_to_home(update, context)
        return True
    return False

# ==================== MAIN MENU SCREENS ====================
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admin = is_admin(user_id)
    context.user_data.pop("state", None)
    context.user_data.pop("temp_data", None)
    await push_history(update, context, "main")
    await update.message.reply_text(
        "🏠 *Main Menu* - Choose an option:",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(admin)
    )

async def show_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await run_db_query("SELECT * FROM users WHERE user_id = ?", (user_id,), "one")
    if not user:
        await show_main_menu(update, context)
        return
    text = (f"👤 *Account Info*\n\n"
            f"ID: `{user['user_id']}`\n"
            f"Name: {user['full_name'] or 'N/A'}\n"
            f"Username: @{user['username'] or 'N/A'}\n"
            f"Balance: {user['balance']:.2f} coins\n"
            f"Total Earned: {user['total_earned']:.2f} coins")
    await push_history(update, context, "account")
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_home_keyboard())

async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = await get_user_balance(user_id)
    min_withdraw = await get_min_withdraw()
    pending = await run_db_query(
        "SELECT SUM(t.reward) as pending FROM submissions s JOIN tasks t ON s.task_id=t.id WHERE s.user_id=? AND s.status='pending'",
        (user_id,), "one"
    )
    pending_sum = pending["pending"] if pending and pending["pending"] else 0
    text = (f"💰 *Wallet*\n\n"
            f"Current Balance: {balance:.2f} coins\n"
            f"Pending Earnings: {pending_sum:.2f} coins\n"
            f"Minimum Withdraw: {min_withdraw:.2f} coins")
    await push_history(update, context, "wallet")
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_home_keyboard())

async def show_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    referrals = await run_db_query("SELECT COUNT(*) as cnt FROM referrals WHERE referrer_id = ?", (user_id,), "one")
    total = referrals["cnt"] if referrals else 0
    bonus = await get_referral_bonus()
    text = f"👥 *Team*\n\nYou have referred {total} users.\nBonus per referral: {bonus:.2f} coins"
    await push_history(update, context, "team")
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_home_keyboard())

async def show_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    bonus = await get_referral_bonus()
    link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    text = (f"🎁 *Invite Friends*\n\n"
            f"Share this link: {link}\n\n"
            f"Each friend gives you {bonus:.2f} coins bonus!")
    await push_history(update, context, "invite")
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_home_keyboard())

# ==================== TASKS SYSTEM ====================
async def show_tasks_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = await run_db_query("SELECT * FROM tasks WHERE enabled=1 ORDER BY id", fetch="all")
    if not tasks:
        await update.message.reply_text("No tasks available.", reply_markup=get_back_home_keyboard())
        return
    msg = "📋 *Available Tasks*\n\n"
    for idx, t in enumerate(tasks, 1):
        msg += f"{idx}. {t['title']} - Reward: {t['reward']:.2f} coins\n"
    msg += "\nSend the task number to view details."
    await push_history(update, context, "tasks")
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_back_home_keyboard())
    context.user_data["awaiting_task_selection"] = True
    context.user_data["tasks_list"] = tasks

async def show_task_details(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int = None):
    if not task_id:
        return
    task = await run_db_query("SELECT * FROM tasks WHERE id=?", (task_id,), "one")
    if not task:
        await update.message.reply_text("Task not found.", reply_markup=get_back_home_keyboard())
        return
    text = (f"📌 *{task['title']}*\n\n"
            f"Description: {task['description']}\n"
            f"Reward: {task['reward']:.2f} coins\n"
            f"Photo: {task['photo_url'] or 'No photo'}\n\n"
            f"Click /start_task to begin this task.")
    await push_history(update, context, "task_details", {"task_id": task_id})
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_home_keyboard())
    context.user_data["current_task"] = task_id

async def start_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.user_data.get("current_task")
    if not task_id:
        await update.message.reply_text("No task selected. Use Tasks menu first.")
        return
    
    existing = await run_db_query(
        "SELECT id, status FROM submissions WHERE user_id=? AND task_id=? AND status IN ('pending','approved')",
        (update.effective_user.id, task_id), "one"
    )
    if existing:
        await update.message.reply_text(f"You already {existing['status']} this task.")
        return
    
    context.user_data["state"] = "WAITING_SCREENSHOT"
    context.user_data["screenshot_task_id"] = task_id
    await update.message.reply_text("Please send the screenshot of task completion.\nYou may also add a note after the image.", 
                                    reply_markup=get_back_home_keyboard())

async def handle_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "WAITING_SCREENSHOT":
        return
    photo_file = update.message.photo[-1].file_id
    task_id = context.user_data["screenshot_task_id"]
    context.user_data["temp_screenshot"] = photo_file
    context.user_data["temp_task_id"] = task_id
    context.user_data["state"] = "WAITING_NOTE"
    await update.message.reply_text("Screenshot received. Send a note (or type 'skip' to skip note):")

async def handle_submission_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "WAITING_NOTE":
        return
    note = update.message.text if update.message.text.lower() != "skip" else ""
    user_id = update.effective_user.id
    task_id = context.user_data["temp_task_id"]
    photo_id = context.user_data["temp_screenshot"]
    await run_db_query(
        "INSERT INTO submissions (user_id, task_id, screenshot_file_id, note) VALUES (?, ?, ?, ?)",
        (user_id, task_id, photo_id, note)
    )
    context.user_data.pop("state", None)
    context.user_data.pop("temp_screenshot", None)
    context.user_data.pop("temp_task_id", None)
    await update.message.reply_text("✅ Task submitted for review. Wait for admin approval.", reply_markup=get_back_home_keyboard())
    
    for admin_id in ADMIN_USER_IDS:
        try:
            await context.bot.send_message(admin_id, f"📝 New task submission from user {user_id}. Use Admin Panel to review.")
        except:
            pass

# ==================== WITHDRAW SYSTEM ====================
async def withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_withdraw_enabled():
        await update.message.reply_text("❌ Withdrawals are currently disabled by admin.")
        return
    
    balance = await get_user_balance(update.effective_user.id)
    min_withdraw = await get_min_withdraw()
    
    if balance < min_withdraw:
        await update.message.reply_text(f"❌ Insufficient balance. Minimum withdraw: {min_withdraw:.2f} coins\nYour balance: {balance:.2f} coins")
        return
    
    context.user_data["state"] = "WITHDRAW_AMOUNT"
    await update.message.reply_text(f"💰 Enter amount to withdraw (Min: {min_withdraw:.2f}):", reply_markup=get_back_home_keyboard())

async def withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text)
        min_withdraw = await get_min_withdraw()
        balance = await get_user_balance(update.effective_user.id)
        
        if amount < min_withdraw or amount > balance:
            raise ValueError
        
        context.user_data["withdraw_amount"] = amount
        context.user_data["state"] = "WITHDRAW_METHOD"
        await update.message.reply_text("💳 Send payment method (e.g., bkash, nagad, bank, crypto):")
    except:
        await update.message.reply_text("❌ Invalid amount. Try again or press Home.")

async def withdraw_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    method = update.message.text
    context.user_data["withdraw_method"] = method
    context.user_data["state"] = "WITHDRAW_ACCOUNT"
    await update.message.reply_text("📝 Send your account details (phone/address/ID):")

async def withdraw_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = update.message.text
    amount = context.user_data["withdraw_amount"]
    user_id = update.effective_user.id
    method = context.user_data["withdraw_method"]
    
    await run_db_query(
        "INSERT INTO withdrawals (user_id, amount, payment_method, account_info) VALUES (?,?,?,?)",
        (user_id, amount, method, account)
    )
    context.user_data.pop("state", None)
    await update.message.reply_text("✅ Withdrawal request submitted. Wait for admin approval.", reply_markup=get_back_home_keyboard())
    
    for admin_id in ADMIN_USER_IDS:
        await context.bot.send_message(admin_id, f"💸 New withdrawal request from user {user_id}\nAmount: {amount}\nMethod: {method}")

# ==================== REDEEM GIFT CODE ====================
async def redeem_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = "REDEEM_CODE"
    await update.message.reply_text("🎁 Send the gift code:", reply_markup=get_back_home_keyboard())

async def process_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    gift = await run_db_query("SELECT * FROM giftcodes WHERE code=?", (code,), "one")
    
    if not gift:
        await update.message.reply_text("❌ Invalid code.")
        return
    
    if gift["expiry_date"] and datetime.now() > datetime.fromisoformat(gift["expiry_date"]):
        await update.message.reply_text("❌ Code expired.")
        return
    
    if gift["usage_limit"] and gift["used_count"] >= gift["usage_limit"]:
        await update.message.reply_text("❌ Code usage limit reached.")
        return
    
    user = await run_db_query("SELECT redeemed_codes FROM users WHERE user_id=?", (update.effective_user.id,), "one")
    if user and user["redeemed_codes"] and code in user["redeemed_codes"]:
        await update.message.reply_text("❌ You already used this code.")
        return
    
    await update_user_balance(update.effective_user.id, gift["reward"])
    await run_db_query("UPDATE giftcodes SET used_count = used_count+1 WHERE code=?", (code,))
    await run_db_query("UPDATE users SET redeemed_codes = COALESCE(redeemed_codes,'') || ? WHERE user_id=?", (f",{code}", update.effective_user.id))
    
    await update.message.reply_text(f"✅ Code redeemed! You received {gift['reward']:.2f} coins added to balance.")
    context.user_data.pop("state", None)

# ==================== LEADERBOARD ====================
async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await run_db_query("SELECT user_id, total_earned FROM users ORDER BY total_earned DESC LIMIT 10", fetch="all")
    text = f"🏆 *LEADERBOARD (Top Earners)*\n\n"
    for idx, row in enumerate(rows, 1):
        user = await run_db_query("SELECT username FROM users WHERE user_id=?", (row["user_id"],), "one")
        name = f"@{user['username']}" if user and user['username'] else str(row["user_id"])
        text += f"{idx}. {name} - {row['total_earned']:.2f} coins\n"
    
    await push_history(update, context, "leaderboard")
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_home_keyboard())

# ==================== CHANNEL & SUPPORT ====================
async def channel_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channels = await run_db_query("SELECT channel_username, invite_link FROM channels", fetch="all")
    if not channels:
        text = "📢 No required channels configured."
    else:
        text = "📢 *Required Channels:*\nPlease join these channels to use the bot:\n\n"
        for ch in channels:
            text += f"• @{ch['channel_username']}\n"
    await push_history(update, context, "channel")
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_home_keyboard())

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🆘 *Support*\n\nFor any issues or inquiries, contact admin.\n\nResponse time: 24-48 hours"
    await push_history(update, context, "support")
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_home_keyboard())

# ==================== ADMIN PANEL ====================
async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Access denied.")
        return
    await push_history(update, context, "admin_panel")
    await update.message.reply_text("🔧 *Admin Panel* - Choose an option:", parse_mode="Markdown", reply_markup=get_admin_keyboard())

# ==================== BOT SETTINGS (All via buttons) ====================
async def bot_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await push_history(update, context, "bot_settings")
    await update.message.reply_text("⚙️ *Bot Settings Menu*\nChoose an option below:", parse_mode="Markdown", reply_markup=get_settings_keyboard())

async def view_current_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    min_withdraw = await get_min_withdraw()
    referral_bonus = await get_referral_bonus()
    withdraw_enabled = await is_withdraw_enabled()
    status = "✅ Enabled" if withdraw_enabled else "❌ Disabled"
    
    text = (f"📊 *Current Bot Settings*\n\n"
            f"💰 Minimum Withdraw: `{min_withdraw:.2f}` coins\n"
            f"👥 Referral Bonus: `{referral_bonus:.2f}` coins\n"
            f"💸 Withdraw System: {status}")
    
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_settings_keyboard())

async def set_min_withdraw_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["setting_action"] = "set_min_withdraw"
    await update.message.reply_text("💰 Enter new minimum withdraw amount (in coins):", reply_markup=get_back_home_keyboard())

async def set_referral_bonus_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["setting_action"] = "set_referral_bonus"
    await update.message.reply_text("👥 Enter new referral bonus amount (in coins):", reply_markup=get_back_home_keyboard())

async def toggle_withdraw_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    current = await is_withdraw_enabled()
    new_value = "false" if current else "true"
    await set_setting("withdraw_enabled", new_value)
    
    status = "ENABLED ✅" if new_value == "true" else "DISABLED ❌"
    await update.message.reply_text(f"💸 Withdraw system has been {status}", reply_markup=get_settings_keyboard())

async def handle_setting_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get("setting_action")
    if not action:
        return
    
    try:
        value = float(update.message.text)
        if value <= 0:
            raise ValueError
        
        if action == "set_min_withdraw":
            await set_setting("min_withdraw", str(value))
            await update.message.reply_text(f"✅ Minimum withdraw amount set to {value:.2f} coins")
        elif action == "set_referral_bonus":
            await set_setting("referral_bonus", str(value))
            await update.message.reply_text(f"✅ Referral bonus set to {value:.2f} coins")
        
        context.user_data.pop("setting_action", None)
        await bot_settings(update, context)
    except:
        await update.message.reply_text("❌ Invalid amount. Please enter a positive number.")

# ==================== FORCE JOIN MANAGEMENT ====================
async def admin_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await push_history(update, context, "force_join")
    await update.message.reply_text("📢 *Force Join Management*\nManage required channels:", parse_mode="Markdown", reply_markup=get_force_join_keyboard())

async def add_channel_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["force_join_action"] = "add_channel"
    await update.message.reply_text("➕ Send the channel username (e.g., @mychannel):", reply_markup=get_back_home_keyboard())

async def remove_channel_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["force_join_action"] = "remove_channel"
    await update.message.reply_text("❌ Send the channel username to remove (e.g., @mychannel):", reply_markup=get_back_home_keyboard())

async def list_channels_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    channels = await run_db_query("SELECT channel_username FROM channels", fetch="all")
    if not channels:
        text = "📭 No channels in force join list."
    else:
        text = "📋 *Force Join Channels:*\n\n"
        for ch in channels:
            text += f"• @{ch['channel_username']}\n"
    
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_force_join_keyboard())

async def handle_force_join_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get("force_join_action")
    if not action:
        return
    
    username = update.message.text.replace("@", "").strip()
    
    if action == "add_channel":
        await run_db_query("INSERT OR IGNORE INTO channels (channel_username) VALUES (?)", (username,))
        await update.message.reply_text(f"✅ Channel @{username} added to force join list.")
    elif action == "remove_channel":
        await run_db_query("DELETE FROM channels WHERE channel_username=?", (username,))
        await update.message.reply_text(f"✅ Channel @{username} removed from force join list.")
    
    context.user_data.pop("force_join_action", None)
    await admin_force_join(update, context)

# ==================== USER MANAGEMENT ====================
async def user_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await push_history(update, context, "user_management")
    await update.message.reply_text("👤 *User Management*\nChoose an option:", parse_mode="Markdown", reply_markup=get_user_management_keyboard())

async def view_user_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["user_action"] = "view_user"
    await update.message.reply_text("🔍 Send the user ID to view details:", reply_markup=get_back_home_keyboard())

async def add_balance_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["user_action"] = "add_balance"
    context.user_data["user_action_step"] = "waiting_user_id"
    await update.message.reply_text("💰 Send the user ID to add balance:", reply_markup=get_back_home_keyboard())

async def remove_balance_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["user_action"] = "remove_balance"
    context.user_data["user_action_step"] = "waiting_user_id"
    await update.message.reply_text("💸 Send the user ID to remove balance:", reply_markup=get_back_home_keyboard())

async def ban_user_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["user_action"] = "ban_user"
    await update.message.reply_text("🚫 Send the user ID to ban:", reply_markup=get_back_home_keyboard())

async def unban_user_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["user_action"] = "unban_user"
    await update.message.reply_text("✅ Send the user ID to unban:", reply_markup=get_back_home_keyboard())

async def user_stats_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    total_users = (await run_db_query("SELECT COUNT(*) as c FROM users", fetch="one"))["c"]
    banned_users = (await run_db_query("SELECT COUNT(*) as c FROM users WHERE is_banned=1", fetch="one"))["c"]
    active_users = total_users - banned_users
    
    text = (f"📊 *User Statistics*\n\n"
            f"👥 Total Users: {total_users}\n"
            f"✅ Active Users: {active_users}\n"
            f"🚫 Banned Users: {banned_users}")
    
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_user_management_keyboard())

async def handle_user_action_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get("user_action")
    if not action:
        return
    
    try:
        user_id = int(update.message.text)
        
        if action == "view_user":
            user = await run_db_query("SELECT * FROM users WHERE user_id = ?", (user_id,), "one")
            if user:
                text = (f"👤 *User Details*\n\n"
                        f"ID: `{user['user_id']}`\n"
                        f"Name: {user['full_name'] or 'N/A'}\n"
                        f"Username: @{user['username'] or 'N/A'}\n"
                        f"Balance: {user['balance']:.2f} coins\n"
                        f"Total Earned: {user['total_earned']:.2f} coins\n"
                        f"Status: {'🚫 Banned' if user['is_banned'] else '✅ Active'}\n"
                        f"Joined: {user['joined_date']}")
                await update.message.reply_text(text, parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ User not found.")
        
        elif action == "ban_user":
            await run_db_query("UPDATE users SET is_banned=1 WHERE user_id=?", (user_id,))
            await update.message.reply_text(f"✅ User {user_id} has been banned.")
        
        elif action == "unban_user":
            await run_db_query("UPDATE users SET is_banned=0 WHERE user_id=?", (user_id,))
            await update.message.reply_text(f"✅ User {user_id} has been unbanned.")
        
        elif action in ["add_balance", "remove_balance"]:
            context.user_data["target_user_id"] = user_id
            context.user_data["user_action_step"] = "waiting_amount"
            await update.message.reply_text("💰 Send the amount:")
            return
        
        context.user_data.pop("user_action", None)
        context.user_data.pop("user_action_step", None)
        await user_management(update, context)
        
    except ValueError:
        if context.user_data.get("user_action_step") == "waiting_amount":
            try:
                amount = float(update.message.text)
                target_user_id = context.user_data.get("target_user_id")
                
                if context.user_data.get("user_action") == "add_balance":
                    await update_user_balance(target_user_id, amount)
                    await update.message.reply_text(f"✅ Added {amount:.2f} coins to user {target_user_id}")
                elif context.user_data.get("user_action") == "remove_balance":
                    await update_user_balance(target_user_id, -amount)
                    await update.message.reply_text(f"✅ Removed {amount:.2f} coins from user {target_user_id}")
                
                context.user_data.pop("user_action", None)
                context.user_data.pop("user_action_step", None)
                context.user_data.pop("target_user_id", None)
                await user_management(update, context)
            except:
                await update.message.reply_text("❌ Invalid amount.")
        else:
            await update.message.reply_text("❌ Invalid user ID. Please send a number.")

# ==================== ADD TASK ====================
async def admin_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["add_task"] = {}
    context.user_data["add_task_step"] = 1
    await update.message.reply_text("📝 Enter task title:", reply_markup=get_back_home_keyboard())

async def add_task_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("add_task_step", 1)
    data = context.user_data.get("add_task", {})
    
    if step == 1:
        data["title"] = update.message.text
        context.user_data["add_task_step"] = 2
        await update.message.reply_text("📝 Enter task description:")
    elif step == 2:
        data["description"] = update.message.text
        context.user_data["add_task_step"] = 3
        await update.message.reply_text("🔗 Enter task link (URL):")
    elif step == 3:
        data["task_link"] = update.message.text
        context.user_data["add_task_step"] = 4
        await update.message.reply_text("💰 Enter reward amount in coins:")
    elif step == 4:
        try:
            data["reward"] = float(update.message.text)
            context.user_data["add_task_step"] = 5
            await update.message.reply_text("🖼️ Enter photo URL (or type 'skip'):")
        except:
            await update.message.reply_text("❌ Invalid number. Try again:")
    elif step == 5:
        if update.message.text.lower() != "skip":
            data["photo_url"] = update.message.text
        else:
            data["photo_url"] = None
        
        await run_db_query(
            "INSERT INTO tasks (title, description, task_link, reward, photo_url) VALUES (?,?,?,?,?)",
            (data["title"], data["description"], data["task_link"], data["reward"], data["photo_url"])
        )
        await update.message.reply_text("✅ Task added successfully!", reply_markup=get_admin_keyboard())
        context.user_data.pop("add_task", None)
        context.user_data.pop("add_task_step", None)
        await show_admin_panel(update, context)

# ==================== REMOVE TASK ====================
async def admin_remove_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    tasks = await run_db_query("SELECT id, title FROM tasks ORDER BY id", fetch="all")
    if not tasks:
        await update.message.reply_text("📭 No tasks available to remove.")
        return
    
    msg = "📋 *Available Tasks:*\n\n"
    for t in tasks:
        msg += f"ID: `{t['id']}` - {t['title']}\n"
    msg += "\nSend the task ID to remove:"
    
    await update.message.reply_text(msg, parse_mode="Markdown")
    context.user_data["awaiting_remove_task"] = True

async def process_remove_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_remove_task"):
        try:
            tid = int(update.message.text)
            await run_db_query("DELETE FROM tasks WHERE id=?", (tid,))
            await update.message.reply_text("✅ Task removed successfully.", reply_markup=get_admin_keyboard())
        except:
            await update.message.reply_text("❌ Invalid task ID.")
        context.user_data.pop("awaiting_remove_task", None)
        await show_admin_panel(update, context)

# ==================== PENDING SUBMISSIONS ====================
async def pending_submissions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    subs = await run_db_query(
        "SELECT s.id, s.user_id, t.title, t.reward FROM submissions s JOIN tasks t ON s.task_id=t.id WHERE s.status='pending'", 
        fetch="all"
    )
    
    if not subs:
        await update.message.reply_text("📭 No pending submissions.", reply_markup=get_admin_keyboard())
        return
    
    msg = "📥 *Pending Submissions*\n\n"
    for s in subs:
        msg += f"ID: {s['id']} | User: {s['user_id']} | Task: {s['title']} | Reward: {s['reward']} coins\n"
    
    msg += "\nSend 'approve <id>' or 'reject <id> <reason>'"
    await update.message.reply_text(msg, parse_mode="Markdown")
    context.user_data["awaiting_submission_action"] = True

async def handle_submission_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_submission_action"):
        return
    
    text = update.message.text
    
    if text.startswith("approve"):
        parts = text.split()
        if len(parts) == 2:
            sid = int(parts[1])
            sub = await run_db_query("SELECT user_id, task_id FROM submissions WHERE id=?", (sid,), "one")
            if sub:
                task = await run_db_query("SELECT reward FROM tasks WHERE id=?", (sub["task_id"],), "one")
                await run_db_query("UPDATE submissions SET status='approved', processed_at=? WHERE id=?", (datetime.now(), sid))
                await update_user_balance(sub["user_id"], task["reward"])
                await update.message.reply_text(f"✅ Approved! Added {task['reward']:.2f} coins to user.", reply_markup=get_admin_keyboard())
                try:
                    await context.bot.send_message(sub["user_id"], f"✅ Your task submission has been approved! You received {task['reward']:.2f} coins.")
                except:
                    pass
    
    elif text.startswith("reject"):
        parts = text.split(maxsplit=2)
        if len(parts) >= 2:
            sid = int(parts[1])
            reason = parts[2] if len(parts) == 3 else "No reason provided"
            await run_db_query("UPDATE submissions SET status='rejected', admin_comment=?, processed_at=? WHERE id=?", (reason, datetime.now(), sid))
            sub = await run_db_query("SELECT user_id FROM submissions WHERE id=?", (sid,), "one")
            if sub:
                try:
                    await context.bot.send_message(sub["user_id"], f"❌ Your task submission was rejected.\nReason: {reason}")
                except:
                    pass
            await update.message.reply_text("❌ Submission rejected.", reply_markup=get_admin_keyboard())
    
    context.user_data.pop("awaiting_submission_action", None)
    await show_admin_panel(update, context)

# ==================== WITHDRAW REQUESTS ====================
async def withdraw_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    reqs = await run_db_query("SELECT * FROM withdrawals WHERE status='pending'", fetch="all")
    if not reqs:
        await update.message.reply_text("📭 No pending withdrawal requests.", reply_markup=get_admin_keyboard())
        return
    
    msg = "💸 *Withdrawal Requests*\n\n"
    for r in reqs:
        msg += f"ID: {r['id']} | User: {r['user_id']} | Amount: {r['amount']} | Method: {r['payment_method']}\n"
    
    msg += "\nSend 'approve_withdraw <id>' or 'reject_withdraw <id> <reason>'"
    await update.message.reply_text(msg, parse_mode="Markdown")
    context.user_data["awaiting_withdraw_action"] = True

async def handle_withdraw_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_withdraw_action"):
        return
    
    text = update.message.text
    
    if text.startswith("approve_withdraw"):
        parts = text.split()
        if len(parts) == 2:
            wid = int(parts[1])
            req = await run_db_query("SELECT user_id, amount FROM withdrawals WHERE id=?", (wid,), "one")
            if req:
                await run_db_query("UPDATE users SET balance = balance - ? WHERE user_id=?", (req["amount"], req["user_id"]))
                await run_db_query("UPDATE withdrawals SET status='approved', processed_at=? WHERE id=?", (datetime.now(), wid))
                await update.message.reply_text(f"✅ Withdrawal approved. {req['amount']:.2f} coins deducted.", reply_markup=get_admin_keyboard())
                try:
                    await context.bot.send_message(req["user_id"], f"✅ Your withdrawal of {req['amount']:.2f} coins has been approved and processed.")
                except:
                    pass
    
    elif text.startswith("reject_withdraw"):
        parts = text.split(maxsplit=2)
        if len(parts) >= 2:
            wid = int(parts[1])
            reason = parts[2] if len(parts) == 3 else "No reason"
            await run_db_query("UPDATE withdrawals SET status='rejected', reason=?, processed_at=? WHERE id=?", (reason, datetime.now(), wid))
            req = await run_db_query("SELECT user_id FROM withdrawals WHERE id=?", (wid,), "one")
            if req:
                try:
                    await context.bot.send_message(req["user_id"], f"❌ Your withdrawal request was rejected.\nReason: {reason}")
                except:
                    pass
            await update.message.reply_text("❌ Withdrawal rejected.", reply_markup=get_admin_keyboard())
    
    context.user_data.pop("awaiting_withdraw_action", None)
    await show_admin_panel(update, context)

# ==================== GIFT CODES ====================
async def admin_gift_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["gift_code_step"] = 1
    await update.message.reply_text("🎁 Enter reward amount for gift code:", reply_markup=get_back_home_keyboard())

async def gift_code_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("gift_code_step")
    
    if step == 1:
        try:
            reward = float(update.message.text)
            context.user_data["gift_reward"] = reward
            context.user_data["gift_code_step"] = 2
            await update.message.reply_text("📊 Enter usage limit (0 for unlimited):")
        except:
            await update.message.reply_text("❌ Invalid reward amount.")
    
    elif step == 2:
        limit = int(update.message.text)
        context.user_data["gift_limit"] = limit
        context.user_data["gift_code_step"] = 3
        await update.message.reply_text("📅 Enter expiry date (YYYY-MM-DD) or type 'skip' for no expiry:")
    
    elif step == 3:
        expiry = None
        if update.message.text.lower() != "skip":
            expiry = update.message.text
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
        await run_db_query(
            "INSERT INTO giftcodes (code, reward, usage_limit, expiry_date, created_by) VALUES (?,?,?,?,?)",
            (code, context.user_data["gift_reward"], context.user_data["gift_limit"], expiry, update.effective_user.id)
        )
        await update.message.reply_text(f"✅ Gift code created!\n\nCode: `{code}`\nReward: {context.user_data['gift_reward']:.2f} coins\nLimit: {context.user_data['gift_limit']}", parse_mode="Markdown", reply_markup=get_admin_keyboard())
        context.user_data.pop("gift_code_step", None)
        await show_admin_panel(update, context)

# ==================== BROADCAST ====================
async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["awaiting_broadcast"] = True
    await update.message.reply_text("📢 Send the message to broadcast to all users:", reply_markup=get_back_home_keyboard())

async def process_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_broadcast"):
        msg = update.message.text
        users = await run_db_query("SELECT user_id FROM users", fetch="all")
        sent = 0
        failed = 0
        
        await update.message.reply_text(f"📤 Broadcasting to {len(users)} users...")
        
        for u in users:
            try:
                await context.bot.send_message(u["user_id"], f"📢 *Announcement*\n\n{msg}", parse_mode="Markdown")
                sent += 1
                await asyncio.sleep(0.05)
            except:
                failed += 1
        
        await update.message.reply_text(f"✅ Broadcast completed!\nSent: {sent}\nFailed: {failed}", reply_markup=get_admin_keyboard())
        context.user_data.pop("awaiting_broadcast", None)
        await show_admin_panel(update, context)

# ==================== ANALYTICS ====================
async def admin_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    total_users = (await run_db_query("SELECT COUNT(*) as c FROM users", fetch="one"))["c"]
    active_today = (await run_db_query("SELECT COUNT(*) as c FROM users WHERE last_active > date('now','-1 day')", fetch="one"))["c"]
    total_earned = (await run_db_query("SELECT SUM(total_earned) as s FROM users", fetch="one"))["s"] or 0
    total_withdrawn = (await run_db_query("SELECT SUM(amount) as s FROM withdrawals WHERE status='approved'", fetch="one"))["s"] or 0
    pending_tasks = (await run_db_query("SELECT COUNT(*) as c FROM submissions WHERE status='pending'", fetch="one"))["c"]
    pending_withdraw = (await run_db_query("SELECT COUNT(*) as c FROM withdrawals WHERE status='pending'", fetch="one"))["c"]
    
    text = (f"📊 *Analytics*\n\n"
            f"👥 Total Users: {total_users}\n"
            f"🟢 Active (24h): {active_today}\n"
            f"💰 Total Earned: {total_earned:.2f} coins\n"
            f"💸 Total Withdrawn: {total_withdrawn:.2f} coins\n"
            f"⏳ Pending Tasks: {pending_tasks}\n"
            f"⏳ Pending Withdrawals: {pending_withdraw}")
    
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_admin_keyboard())

# ==================== START COMMAND ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    ref = None
    
    if context.args and context.args[0].startswith("ref_"):
        ref = int(context.args[0].split("_")[1])
    
    if not await force_join_passed(user_id, context):
        await update.message.reply_text("⚠️ Please join our required channels first to use the bot.")
        return
    
    await register_user(user_id, user.username, user.full_name, ref)
    await show_main_menu(update, context)

# ==================== MESSAGE HANDLER ====================
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await handle_back_or_home(update, context):
        return
    
    text = update.message.text
    user_id = update.effective_user.id
    state = context.user_data.get("state")
    
    # Handle setting value inputs
    if context.user_data.get("setting_action"):
        await handle_setting_value(update, context)
        return
    
    # Handle force join inputs
    if context.user_data.get("force_join_action"):
        await handle_force_join_value(update, context)
        return
    
    # Handle user management inputs
    if context.user_data.get("user_action"):
        await handle_user_action_value(update, context)
        return
    
    # State-based handlers
    if state == "WAITING_SCREENSHOT":
        await handle_screenshot(update, context)
        return
    if state == "WAITING_NOTE":
        await handle_submission_note(update, context)
        return
    if state == "WITHDRAW_AMOUNT":
        await withdraw_amount(update, context)
        return
    if state == "WITHDRAW_METHOD":
        await withdraw_method(update, context)
        return
    if state == "WITHDRAW_ACCOUNT":
        await withdraw_account(update, context)
        return
    if state == "REDEEM_CODE":
        await process_redeem(update, context)
        return
    if context.user_data.get("add_task_step"):
        await add_task_step(update, context)
        return
    if context.user_data.get("gift_code_step"):
        await gift_code_step(update, context)
        return
    
    # Menu commands
    if text == "👤 Account":
        await show_account(update, context)
    elif text == "💰 Wallet":
        await show_wallet(update, context)
    elif text == "👥 Team":
        await show_team(update, context)
    elif text == "🎁 Invite":
        await show_invite(update, context)
    elif text == "📋 Tasks":
        await show_tasks_list(update, context)
    elif text == "🏆 Leaderboard":
        await show_leaderboard(update, context)
    elif text == "💳 Withdraw":
        await withdraw_start(update, context)
    elif text == "🎁 Redeem":
        await redeem_code(update, context)
    elif text == "📢 Channel":
        await channel_info(update, context)
    elif text == "🆘 Support":
        await support(update, context)
    elif text == "🔧 Admin Panel" and is_admin(user_id):
        await show_admin_panel(update, context)
    
    # Admin panel sub-menus
    elif text == "⚙️ Bot Settings" and is_admin(user_id):
        await bot_settings(update, context)
    elif text == "📢 Force Join" and is_admin(user_id):
        await admin_force_join(update, context)
    elif text == "👤 User Management" and is_admin(user_id):
        await user_management(update, context)
    
    # Settings menu buttons
    elif text == "💰 Set Min Withdraw" and is_admin(user_id):
        await set_min_withdraw_button(update, context)
    elif text == "👥 Set Referral Bonus" and is_admin(user_id):
        await set_referral_bonus_button(update, context)
    elif text == "🔘 Toggle Withdraw System" and is_admin(user_id):
        await toggle_withdraw_button(update, context)
    elif text == "📊 View Current Settings" and is_admin(user_id):
        await view_current_settings(update, context)
    
    # Force join buttons
    elif text == "➕ Add Channel" and is_admin(user_id):
        await add_channel_button(update, context)
    elif text == "❌ Remove Channel" and is_admin(user_id):
        await remove_channel_button(update, context)
    elif text == "📋 List Channels" and is_admin(user_id):
        await list_channels_button(update, context)
    
    # User management buttons
    elif text == "🔍 View User" and is_admin(user_id):
        await view_user_button(update, context)
    elif text == "💰 Add Balance" and is_admin(user_id):
        await add_balance_button(update, context)
    elif text == "💸 Remove Balance" and is_admin(user_id):
        await remove_balance_button(update, context)
    elif text == "🚫 Ban User" and is_admin(user_id):
        await ban_user_button(update, context)
    elif text == "✅ Unban User" and is_admin(user_id):
        await unban_user_button(update, context)
    elif text == "📊 User Stats" and is_admin(user_id):
        await user_stats_button(update, context)
    
    # Admin main actions
    elif is_admin(user_id):
        if text == "➕ Add Task":
            await admin_add_task(update, context)
        elif text == "❌ Remove Task":
            await admin_remove_task(update, context)
        elif text == "📥 Pending Tasks":
            await pending_submissions(update, context)
        elif text == "💸 Withdraw Requests":
            await withdraw_requests(update, context)
        elif text == "🎁 Gift Codes":
            await admin_gift_codes(update, context)
        elif text == "📢 Broadcast":
            await admin_broadcast(update, context)
        elif text == "📊 Analytics":
            await admin_analytics(update, context)
        else:
            await update.message.reply_text("Use the menu buttons.", reply_markup=get_main_keyboard(is_admin(user_id)))
    
    # Task selection
    elif context.user_data.get("awaiting_task_selection"):
        try:
            idx = int(text) - 1
            tasks = context.user_data.get("tasks_list", [])
            if 0 <= idx < len(tasks):
                await show_task_details(update, context, tasks[idx]["id"])
            else:
                await update.message.reply_text("❌ Invalid task number.")
            context.user_data.pop("awaiting_task_selection", None)
        except:
            pass
    
    # Remove task
    elif context.user_data.get("awaiting_remove_task"):
        await process_remove_task(update, context)
    
    # Submission actions
    elif context.user_data.get("awaiting_submission_action"):
        await handle_submission_action(update, context)
    
    # Withdraw actions
    elif context.user_data.get("awaiting_withdraw_action"):
        await handle_withdraw_action(update, context)
    
    # Broadcast
    elif context.user_data.get("awaiting_broadcast"):
        await process_broadcast(update, context)
    
    else:
        await update.message.reply_text("Use the buttons below.", reply_markup=get_main_keyboard(is_admin(user_id)))

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") == "WAITING_SCREENSHOT":
        await handle_screenshot(update, context)
    else:
        await update.message.reply_text("Please use the Tasks menu to submit screenshots.")

# ==================== MAIN FUNCTION ====================
async def main():
    """Main function to run the bot"""
    await init_db()
    
    # Create application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("start_task", start_task))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    
    logger.info("🤖 Bot started successfully!")
    
    # Start polling (works on Railway)
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Keep running
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
