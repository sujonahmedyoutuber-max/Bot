"""
Telegram Task & Reward Bot - FULLY WORKING v4.0 (ONLY ReplyKeyboardMarkup)
✅ NO InlineKeyboardMarkup - শুধু ReplyKeyboardMarkup ব্যবহার করা হয়েছে
✅ Force join properly works with reply keyboard
✅ All buttons work perfectly
✅ Admin can configure everything via buttons
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
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ==================== CONFIGURATION ====================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set in .env file")

ADMIN_USER_IDS = [
    int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()
]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DB_PATH = "bot_data.db"

# ==================== STATE CONSTANTS ====================
STATE_NONE = None

# Withdraw states
WD_METHOD = "wd_method"
WD_AMOUNT = "wd_amount"
WD_ACCOUNT = "wd_account"

# Redeem state
REDEEM = "redeem"

# Task states
TASK_LIST = "task_list"
TASK_PHOTO = "task_photo"

# Force join state
FORCE_JOIN_WAIT = "force_join_wait"

# Admin states
ADMIN_ADD_TASK_TITLE = "admin_add_task_title"
ADMIN_ADD_TASK_DESC = "admin_add_task_desc"
ADMIN_ADD_TASK_LINK = "admin_add_task_link"
ADMIN_ADD_TASK_REWARD = "admin_add_task_reward"
ADMIN_REMOVE_TASK = "admin_remove_task"
ADMIN_PENDING_SUBS = "admin_pending_subs"
ADMIN_PENDING_WD = "admin_pending_wd"
ADMIN_GIFT_REWARD = "admin_gift_reward"
ADMIN_GIFT_LIMIT = "admin_gift_limit"
ADMIN_GIFT_EXPIRY = "admin_gift_expiry"
ADMIN_BROADCAST = "admin_broadcast"
ADMIN_MANAGE_USER_ID = "admin_manage_user_id"
ADMIN_MANAGE_USER_ACT = "admin_manage_user_act"
ADMIN_ADD_CHANNEL = "admin_add_channel"
ADMIN_REMOVE_CHANNEL = "admin_remove_channel"
ADMIN_ADD_METHOD_NAME = "admin_add_method_name"
ADMIN_ADD_METHOD_MIN = "admin_add_method_min"
ADMIN_ADD_METHOD_MAX = "admin_add_method_max"
ADMIN_ADD_METHOD_INST = "admin_add_method_inst"
ADMIN_REMOVE_METHOD = "admin_remove_method"
ADMIN_METHOD_LIMITS_SEL = "admin_method_limits_sel"
ADMIN_METHOD_LIMITS_ACT = "admin_method_limits_act"
ADMIN_SET_MIN_WD = "admin_set_min_wd"
ADMIN_SET_BONUS = "admin_set_bonus"
ADMIN_SET_SUPPORT = "admin_set_support"

# ==================== DATABASE ====================
def db_connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

_db_lock = asyncio.Lock()

async def run_db(query: str, params: tuple = (), fetch: str = "none"):
    async with _db_lock:
        def _q():
            with db_connect() as conn:
                cur = conn.cursor()
                cur.execute(query, params)
                if fetch == "one":
                    return cur.fetchone()
                elif fetch == "all":
                    return cur.fetchall()
                else:
                    conn.commit()
                    return None
        return await asyncio.to_thread(_q)

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
            channel_username TEXT UNIQUE NOT NULL,
            invite_link TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )""",
    ]
    for q in queries:
        await run_db(q)

    defaults = [
        ("min_withdraw", "10"),
        ("referral_bonus", "5"),
        ("withdraw_enabled", "true"),
        ("support_username", ""),
    ]
    for key, val in defaults:
        await run_db("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))

    default_methods = [
        ("bKash", 50, 5000, "Send your bKash number"),
        ("Nagad", 50, 5000, "Send your Nagad number"),
        ("Rocket", 50, 5000, "Send your Rocket number"),
        ("Bank", 100, 0, "Send your bank account details"),
    ]
    for name, mn, mx, inst in default_methods:
        await run_db(
            "INSERT OR IGNORE INTO withdraw_methods "
            "(method_name, min_amount, max_amount, instructions) VALUES (?,?,?,?)",
            (name, mn, mx, inst),
        )

# ==================== SETTINGS HELPERS ====================
async def get_setting(key: str, default: str = "") -> str:
    row = await run_db("SELECT value FROM settings WHERE key=?", (key,), "one")
    return row["value"] if row else default

async def set_setting(key: str, value: str):
    await run_db("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))

# ==================== USER HELPERS ====================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

async def get_user_balance(user_id: int) -> float:
    row = await run_db("SELECT balance FROM users WHERE user_id=?", (user_id,), "one")
    return float(row["balance"]) if row else 0.0

async def update_balance(user_id: int, amount: float):
    await run_db("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    if amount > 0:
        await run_db("UPDATE users SET total_earned = total_earned + ? WHERE user_id=?", (amount, user_id))

async def register_user(user_id: int, username: str = None, full_name: str = None, ref: int = None):
    existing = await run_db("SELECT user_id FROM users WHERE user_id=?", (user_id,), "one")
    if existing:
        await run_db("UPDATE users SET username=?, full_name=? WHERE user_id=?", (username, full_name, user_id))
        return

    if ref == user_id:
        ref = None

    await run_db(
        "INSERT INTO users (user_id, username, full_name, referred_by) VALUES (?,?,?,?)",
        (user_id, username, full_name, ref),
    )

    if ref:
        bonus = float(await get_setting("referral_bonus", "5"))
        await update_balance(ref, bonus)
        await run_db(
            "INSERT OR IGNORE INTO referrals (referrer_id, referred_id, bonus_paid) VALUES (?,?,1)",
            (ref, user_id),
        )

# ==================== FORCE JOIN ====================
async def check_force_join(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> tuple:
    channels = await run_db("SELECT channel_username FROM channels", fetch="all")
    if not channels:
        return True, []

    missing = []
    for row in channels:
        ch = row["channel_username"]
        try:
            chat_id = f"@{ch}" if not ch.startswith("@") else ch
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status in ("left", "kicked", "restricted"):
                missing.append(ch)
        except Exception:
            missing.append(ch)

    return len(missing) == 0, missing

async def show_force_join_message(update: Update, context: ContextTypes.DEFAULT_TYPE, missing: list):
    msg = "⛔ *আপনাকে নিচের চ্যানেলগুলোতে Join করতে হবে:*\n\n"
    for ch in missing:
        username = ch.lstrip("@")
        msg += f"📢 @{username}\n"
        msg += f"👉 [Join Link](https://t.me/{username})\n\n"
    
    msg += "\n*সব চ্যানেলে Join করার পর /start কমান্ড দিন*"
    
    await update.message.reply_text(
        msg,
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("🔄 Check Again")]],
            resize_keyboard=True
        )
    )

# ==================== KEYBOARDS ====================
def get_main_keyboard(admin: bool = False) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton("👤 Account"), KeyboardButton("💰 Wallet")],
        [KeyboardButton("👥 Team"), KeyboardButton("🎁 Invite")],
        [KeyboardButton("📋 Tasks"), KeyboardButton("🏆 Leaderboard")],
        [KeyboardButton("💳 Withdraw"), KeyboardButton("🎁 Redeem")],
        [KeyboardButton("📢 Channels"), KeyboardButton("🆘 Support")],
    ]
    if admin:
        buttons.append([KeyboardButton("🔧 Admin Panel")])
    buttons.append([KeyboardButton("🏠 Home")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_admin_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton("➕ Add Task"), KeyboardButton("❌ Remove Task")],
        [KeyboardButton("📥 Pending Tasks"), KeyboardButton("💸 Withdraw Requests")],
        [KeyboardButton("🎁 Create Gift Code"), KeyboardButton("📢 Broadcast")],
        [KeyboardButton("📊 Analytics"), KeyboardButton("👤 Manage User")],
        [KeyboardButton("➕ Add Channel"), KeyboardButton("❌ Remove Channel")],
        [KeyboardButton("💳 Add Method"), KeyboardButton("🗑 Remove Method")],
        [KeyboardButton("⚙️ Method Limits"), KeyboardButton("🔘 Toggle Withdraw")],
        [KeyboardButton("💰 Min Withdraw"), KeyboardButton("👥 Referral Bonus")],
        [KeyboardButton("📞 Set Support"), KeyboardButton("🏠 Home")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_back_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🔙 Back"), KeyboardButton("🏠 Home")]],
        resize_keyboard=True,
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

# ==================== STATE HELPER ====================
def set_state(context: ContextTypes.DEFAULT_TYPE, state: str, **kwargs):
    context.user_data["_state"] = state
    for k, v in kwargs.items():
        context.user_data[k] = v

def get_state(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("_state")

def clear_state(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

# ==================== MENUS ====================
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(context)
    await update.message.reply_text(
        "🏠 *Main Menu* — একটি অপশন বেছে নিন:",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(is_admin(update.effective_user.id)),
    )

async def show_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = await run_db("SELECT * FROM users WHERE user_id=?", (uid,), "one")
    if not user:
        await update.message.reply_text("Account পাওয়া যায়নি। /start পাঠান।")
        return
    refs = await run_db("SELECT COUNT(*) as c FROM referrals WHERE referrer_id=?", (uid,), "one")
    text = (
        f"👤 *আপনার Account*\n\n"
        f"🆔 ID: `{user['user_id']}`\n"
        f"👤 নাম: {user['full_name'] or 'N/A'}\n"
        f"📛 Username: @{user['username'] or 'N/A'}\n"
        f"💰 Balance: `{user['balance']:.2f}` coins\n"
        f"📈 Total Earned: `{user['total_earned']:.2f}` coins\n"
        f"👥 Referrals: `{refs['c'] if refs else 0}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    balance = await get_user_balance(uid)
    min_w = await get_setting("min_withdraw", "10")
    methods = await run_db("SELECT * FROM withdraw_methods WHERE enabled=1", fetch="all")
    m_text = ""
    for m in methods:
        mx = f" | Max: {m['max_amount']:.0f}" if float(m["max_amount"]) > 0 else ""
        m_text += f"  • *{m['method_name']}* — Min: {m['min_amount']:.0f}{mx} coins\n"
    text = (
        f"💰 *Wallet*\n\n"
        f"💵 Balance: `{balance:.2f}` coins\n"
        f"📉 Global Min Withdraw: `{min_w}` coins\n\n"
        f"💳 *Withdraw Methods:*\n{m_text if m_text else 'কোনো method নেই।'}"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

async def show_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    refs = await run_db("SELECT COUNT(*) as c FROM referrals WHERE referrer_id=?", (uid,), "one")
    bonus = await get_setting("referral_bonus", "5")
    text = f"👥 *আপনার Team*\n\nTotal Referrals: `{refs['c'] if refs else 0}`\nBonus per Referral: `{bonus}` coins"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

async def show_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bot_info = await context.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{uid}"
    bonus = await get_setting("referral_bonus", "5")
    text = f"🎁 *আপনার Invite Link*\n\n`{link}`\n\n✅ প্রতিটি বন্ধুর জন্য `{bonus}` coins আয় করুন!"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

async def show_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channels = await run_db("SELECT channel_username FROM channels", fetch="all")
    if not channels:
        text = "📢 কোনো required channel নেই।"
    else:
        text = "📢 *Required Channels:*\n\n"
        for ch in channels:
            text += f"• @{ch['channel_username']}\n"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    support_username = await get_setting("support_username", "")
    if support_username:
        text = f"🆘 *Support*\n\nযেকোনো সমস্যায় যোগাযোগ করুন:\n👤 @{support_username}"
    else:
        text = "🆘 *Support*\n\nযেকোনো সমস্যায় Admin-এর সাথে যোগাযোগ করুন।"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

# ==================== TASKS ====================
async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = await run_db("SELECT * FROM tasks WHERE enabled=1", fetch="all")
    if not tasks:
        await update.message.reply_text("📋 এখন কোনো task নেই।", reply_markup=get_back_keyboard())
        return
    msg = "📋 *Available Tasks:*\n\n"
    for t in tasks:
        msg += f"🔸 ID `{t['id']}` — *{t['title']}*\n💰 Reward: `{t['reward']:.2f}` coins\n\n"
    msg += "📌 Task ID পাঠান বিস্তারিত দেখতে।"
    set_state(context, TASK_LIST)
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_back_keyboard())

async def show_task_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int):
    uid = update.effective_user.id
    task = await run_db("SELECT * FROM tasks WHERE id=? AND enabled=1", (task_id,), "one")
    if not task:
        await update.message.reply_text("❌ Task পাওয়া যায়নি।")
        return
    already = await run_db(
        "SELECT id FROM submissions WHERE user_id=? AND task_id=? AND status IN ('pending','approved')",
        (uid, task_id), "one"
    )
    footer = "\n\n⚠️ আপনি এই task আগেই submit করেছেন।" if already else f"\n\n👉 Task শুরু করতে পাঠান: `/do_{task_id}`"
    text = f"📌 *{task['title']}*\n\n📝 Description: {task['description'] or 'N/A'}\n💰 Reward: `{task['reward']:.2f}` coins{footer}"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

# ==================== TASK COMMAND ====================
async def handle_task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text = update.message.text
    try:
        task_id = int(text.split("_")[1])
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Invalid task command.")
        return

    uid = update.effective_user.id
    joined, missing = await check_force_join(uid, context)
    if not joined:
        await show_force_join_message(update, context, missing)
        return

    existing = await run_db(
        "SELECT id FROM submissions WHERE user_id=? AND task_id=? AND status IN ('pending','approved')",
        (uid, task_id), "one"
    )
    if existing:
        await update.message.reply_text("⚠️ আপনি এই task আগেই submit করেছেন!")
        return

    task = await run_db("SELECT title FROM tasks WHERE id=? AND enabled=1", (task_id,), "one")
    if not task:
        await update.message.reply_text("❌ Task পাওয়া যায়নি।")
        return

    set_state(context, TASK_PHOTO, task_id=task_id)
    await update.message.reply_text(
        f"📸 Task: *{task['title']}*\n\nকাজ সম্পন্ন করার প্রমাণ হিসেবে একটি Screenshot পাঠান:",
        parse_mode="Markdown",
        reply_markup=get_back_keyboard(),
    )

# ==================== PHOTO HANDLER ====================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = get_state(context)

    if state != TASK_PHOTO:
        await update.message.reply_text("📋 প্রথমে একটি task বেছে নিন। /do_ID দিয়ে শুরু করুন।")
        return

    task_id = context.user_data.get("task_id")
    if not task_id:
        await update.message.reply_text("❌ Task ID পাওয়া যায়নি। আবার চেষ্টা করুন।")
        clear_state(context)
        return

    existing = await run_db(
        "SELECT id FROM submissions WHERE user_id=? AND task_id=? AND status IN ('pending','approved')",
        (uid, task_id), "one"
    )
    if existing:
        await update.message.reply_text("⚠️ আপনি এই task আগেই submit করেছেন!")
        clear_state(context)
        return

    photo = update.message.photo[-1].file_id
    await run_db(
        "INSERT INTO submissions (user_id, task_id, screenshot_file_id) VALUES (?,?,?)",
        (uid, task_id, photo),
    )
    sub_row = await run_db("SELECT id FROM submissions WHERE user_id=? AND task_id=? ORDER BY id DESC", (uid, task_id), "one")
    sub_id = sub_row["id"] if sub_row else "?"
    task = await run_db("SELECT title, reward FROM tasks WHERE id=?", (task_id,), "one")

    await update.message.reply_text(
        "✅ *Task submit হয়েছে! Review এর জন্য অপেক্ষা করুন।*",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(is_admin(uid)),
    )
    clear_state(context)

    for admin_id in ADMIN_USER_IDS:
        try:
            await context.bot.send_photo(
                admin_id, photo,
                caption=f"📝 *নতুন Task Submission!*\n\n👤 User: `{uid}`\n📋 Task: {task['title'] if task else task_id}\n💰 Reward: {task['reward'] if task else '?'} coins\n🆔 Sub ID: `{sub_id}`\n\nReply:\n✅ `approve {sub_id}`\n❌ `reject {sub_id}`",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")

# ==================== WITHDRAW ====================
async def withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if await get_setting("withdraw_enabled", "true") != "true":
        await update.message.reply_text("❌ Withdrawal এখন বন্ধ আছে।")
        return

    balance = await get_user_balance(uid)
    min_w = float(await get_setting("min_withdraw", "10"))
    if balance < min_w:
        await update.message.reply_text(f"❌ Minimum withdraw: `{min_w:.2f}` coins\nআপনার balance: `{balance:.2f}` coins", parse_mode="Markdown")
        return

    methods = await run_db("SELECT * FROM withdraw_methods WHERE enabled=1", fetch="all")
    if not methods:
        await update.message.reply_text("❌ কোনো withdraw method নেই।")
        return

    methods_dict = {m["method_name"]: dict(m) for m in methods}
    set_state(context, WD_METHOD, wd_methods=methods_dict)

    text = "💳 *একটি Withdraw Method বেছে নিন:*\n\n"
    for m in methods:
        mx = f" | Max: {m['max_amount']:.0f}" if float(m["max_amount"]) > 0 else ""
        text += f"• *{m['method_name']}* — Min: {m['min_amount']:.0f}{mx} coins\n"

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_method_keyboard(methods))

async def process_withdraw_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace("💳 ", "").strip()
    methods = context.user_data.get("wd_methods", {})
    if text not in methods:
        await update.message.reply_text("❌ Keyboard থেকে একটি valid method বেছে নিন।")
        return
    m = methods[text]
    set_state(context, WD_AMOUNT, wd_method=text, wd_method_data=m)
    balance = await get_user_balance(update.effective_user.id)
    mx_text = f" (Max: {m['max_amount']:.0f})" if float(m["max_amount"]) > 0 else ""
    await update.message.reply_text(
        f"💰 Withdraw করার পরিমাণ লিখুন:\nMin: `{m['min_amount']:.0f}` coins{mx_text}\nআপনার balance: `{balance:.2f}` coins",
        parse_mode="Markdown",
        reply_markup=get_back_keyboard(),
    )

async def process_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text)
        m = context.user_data.get("wd_method_data", {})
        balance = await get_user_balance(update.effective_user.id)
        min_a = float(m.get("min_amount", 10))
        max_a = float(m.get("max_amount", 0))
        if amount < min_a:
            await update.message.reply_text(f"❌ Minimum amount: `{min_a:.0f}` coins।", parse_mode="Markdown")
            return
        if max_a > 0 and amount > max_a:
            await update.message.reply_text(f"❌ Maximum amount: `{max_a:.0f}` coins।", parse_mode="Markdown")
            return
        if amount > balance:
            await update.message.reply_text(f"❌ যথেষ্ট balance নেই। আপনার balance: `{balance:.2f}` coins।", parse_mode="Markdown")
            return
        set_state(context, WD_ACCOUNT, wd_amount=amount)
        inst = m.get("instructions", "")
        inst_text = f"\n💡 {inst}" if inst else ""
        await update.message.reply_text(f"📝 আপনার account details লিখুন:{inst_text}", reply_markup=get_back_keyboard())
    except ValueError:
        await update.message.reply_text("❌ Invalid amount। একটি সংখ্যা লিখুন।")

async def process_withdraw_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    amount = context.user_data["wd_amount"]
    method = context.user_data["wd_method"]
    account_info = update.message.text

    await update_balance(uid, -amount)
    await run_db(
        "INSERT INTO withdrawals (user_id, amount, payment_method, account_info) VALUES (?,?,?,?)",
        (uid, amount, method, account_info),
    )
    await update.message.reply_text(
        f"✅ *Withdrawal Request Submit হয়েছে!*\n\n💰 Amount: `{amount:.2f}` coins\n💳 Method: {method}\n📝 Account: {account_info}",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(is_admin(uid)),
    )
    clear_state(context)

    for admin_id in ADMIN_USER_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"💸 *নতুন Withdrawal Request!*\n\n👤 User: `{uid}`\n💰 Amount: {amount:.2f} coins\n💳 Method: {method}\n📝 Account: {account_info}\n\nReply:\n✅ `approve_w ID`\n❌ `reject_w ID`",
                parse_mode="Markdown",
            )
        except Exception:
            pass

# ==================== REDEEM ====================
async def redeem_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_state(context, REDEEM)
    await update.message.reply_text("🎁 আপনার Gift Code লিখুন:", reply_markup=get_back_keyboard())

async def process_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    uid = update.effective_user.id
    gift = await run_db("SELECT * FROM giftcodes WHERE code=?", (code,), "one")
    if not gift:
        await update.message.reply_text("❌ Invalid gift code।")
    elif gift["expiry_date"] and datetime.now() > datetime.fromisoformat(gift["expiry_date"]):
        await update.message.reply_text("❌ এই code expired হয়ে গেছে।")
    elif int(gift["usage_limit"]) > 0 and int(gift["used_count"]) >= int(gift["usage_limit"]):
        await update.message.reply_text("❌ এই code এর usage limit শেষ হয়ে গেছে।")
    else:
        used = await run_db("SELECT 1 FROM giftcode_uses WHERE code=? AND user_id=?", (code, uid), "one")
        if used:
            await update.message.reply_text("❌ আপনি এই code আগেই ব্যবহার করেছেন।")
        else:
            await update_balance(uid, float(gift["reward"]))
            await run_db("UPDATE giftcodes SET used_count=used_count+1 WHERE code=?", (code,))
            await run_db("INSERT INTO giftcode_uses (code, user_id) VALUES (?,?)", (code, uid))
            await update.message.reply_text(f"✅ *Code Redeem হয়েছে!*\n💰 +`{float(gift['reward']):.2f}` coins আপনার account-এ যোগ হয়েছে।", parse_mode="Markdown")
    clear_state(context)

# ==================== LEADERBOARD ====================
async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = await run_db("SELECT user_id, full_name, username, total_earned FROM users ORDER BY total_earned DESC LIMIT 10", fetch="all")
    text = "🏆 *Top 10 Leaderboard*\n\n"
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for i, u in enumerate(users, 1):
        medal = medals.get(i, f"{i}.")
        name = f"@{u['username']}" if u["username"] else (u["full_name"] or str(u["user_id"]))
        text += f"{medal} {name} — `{u['total_earned']:.2f}` coins\n"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())

# ==================== ADMIN PANEL ====================
async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    clear_state(context)
    await update.message.reply_text("🔧 *Admin Panel* — একটি action বেছে নিন:", parse_mode="Markdown", reply_markup=get_admin_keyboard())

# --- Add Task ---
async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_state(context, ADMIN_ADD_TASK_TITLE, task_data={})
    await update.message.reply_text("📝 Task এর title লিখুন:", reply_markup=get_back_keyboard())

async def add_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["task_data"]["title"] = update.message.text
    set_state(context, ADMIN_ADD_TASK_DESC)
    await update.message.reply_text("📄 Task এর description লিখুন:")

async def add_task_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["task_data"]["desc"] = update.message.text
    set_state(context, ADMIN_ADD_TASK_LINK)
    await update.message.reply_text("🔗 Task এর link লিখুন (অথবা 'skip' পাঠান):")

async def add_task_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    context.user_data["task_data"]["link"] = None if text.lower() == "skip" else text
    set_state(context, ADMIN_ADD_TASK_REWARD)
    await update.message.reply_text("💰 Reward amount (coins) লিখুন:")

async def add_task_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        reward = float(update.message.text)
        data = context.user_data["task_data"]
        await run_db("INSERT INTO tasks (title, description, task_link, reward) VALUES (?,?,?,?)", (data["title"], data["desc"], data["link"], reward))
        await update.message.reply_text(f"✅ Task *{data['title']}* সফলভাবে যোগ হয়েছে!", parse_mode="Markdown", reply_markup=get_admin_keyboard())
        clear_state(context)
    except ValueError:
        await update.message.reply_text("❌ Invalid amount। একটি সংখ্যা লিখুন।")

# --- Remove Task ---
async def remove_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = await run_db("SELECT id, title FROM tasks", fetch="all")
    if not tasks:
        await update.message.reply_text("কোনো task নেই।")
        return
    msg = "❌ *Remove Task* — Task ID পাঠান:\n\n"
    for t in tasks:
        msg += f"ID `{t['id']}`: {t['title']}\n"
    set_state(context, ADMIN_REMOVE_TASK)
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_back_keyboard())

async def remove_task_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tid = int(update.message.text)
        task = await run_db("SELECT title FROM tasks WHERE id=?", (tid,), "one")
        if not task:
            await update.message.reply_text("❌ Task পাওয়া যায়নি।")
        else:
            await run_db("DELETE FROM tasks WHERE id=?", (tid,))
            await update.message.reply_text(f"✅ Task সরানো হয়েছে: *{task['title']}*", parse_mode="Markdown", reply_markup=get_admin_keyboard())
        clear_state(context)
    except ValueError:
        await update.message.reply_text("❌ Invalid ID।")

# --- Pending Submissions ---
async def pending_submissions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subs = await run_db(
        "SELECT s.id, s.user_id, t.title, t.reward FROM submissions s JOIN tasks t ON s.task_id=t.id WHERE s.status='pending'", fetch="all"
    )
    if not subs:
        await update.message.reply_text("📭 কোনো pending submission নেই।")
        return
    msg = "📥 *Pending Submissions:*\n\n"
    for s in subs:
        msg += f"ID `{s['id']}` | User `{s['user_id']}` | {s['title']} | {s['reward']} coins\n"
    msg += "\n✅ Approve: `approve ID`\n❌ Reject: `reject ID`"
    set_state(context, ADMIN_PENDING_SUBS)
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_back_keyboard())

async def process_submission_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.strip().lower().split()
    if len(parts) != 2 or parts[0] not in ("approve", "reject"):
        await update.message.reply_text("❌ Format: `approve ID` অথবা `reject ID`", parse_mode="Markdown")
        return
    try:
        sid = int(parts[1])
        sub = await run_db("SELECT user_id, task_id, status FROM submissions WHERE id=?", (sid,), "one")
        if not sub:
            await update.message.reply_text("❌ Submission পাওয়া যায়নি।")
        elif sub["status"] != "pending":
            await update.message.reply_text(f"⚠️ ইতিমধ্যে {sub['status']}।")
        elif parts[0] == "approve":
            task = await run_db("SELECT reward, title FROM tasks WHERE id=?", (sub["task_id"],), "one")
            if task:
                await update_balance(sub["user_id"], float(task["reward"]))
            await run_db("UPDATE submissions SET status='approved' WHERE id=?", (sid,))
            await update.message.reply_text("✅ Submission approved!")
            try:
                await context.bot.send_message(sub["user_id"], f"✅ *আপনার task submission approved হয়েছে!*\n💰 +`{float(task['reward']):.2f}` coins আপনার account-এ যোগ হয়েছে।", parse_mode="Markdown")
            except Exception:
                pass
        else:
            await run_db("UPDATE submissions SET status='rejected' WHERE id=?", (sid,))
            await update.message.reply_text("❌ Submission rejected।")
            try:
                await context.bot.send_message(sub["user_id"], "❌ আপনার task submission reject হয়েছে।\nভালোভাবে task সম্পন্ন করে আবার submit করুন।")
            except Exception:
                pass
    except ValueError:
        await update.message.reply_text("❌ Invalid ID।")
    clear_state(context)

# --- Withdraw Requests (Admin) ---
async def withdraw_requests_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reqs = await run_db("SELECT * FROM withdrawals WHERE status='pending'", fetch="all")
    if not reqs:
        await update.message.reply_text("📭 কোনো pending withdrawal request নেই।")
        return
    msg = "💸 *Pending Withdrawals:*\n\n"
    for r in reqs:
        msg += f"ID `{r['id']}` | User `{r['user_id']}` | {r['amount']} coins | {r['payment_method']} | `{r['account_info']}`\n"
    msg += "\n✅ Approve: `approve_w ID`\n❌ Reject: `reject_w ID`"
    set_state(context, ADMIN_PENDING_WD)
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_back_keyboard())

async def process_withdraw_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.strip().lower().split()
    if len(parts) != 2 or parts[0] not in ("approve_w", "reject_w"):
        await update.message.reply_text("❌ Format: `approve_w ID` অথবা `reject_w ID`", parse_mode="Markdown")
        return
    try:
        wid = int(parts[1])
        req = await run_db("SELECT user_id, amount, status FROM withdrawals WHERE id=?", (wid,), "one")
        if not req:
            await update.message.reply_text("❌ Request পাওয়া যায়নি।")
        elif req["status"] != "pending":
            await update.message.reply_text(f"⚠️ ইতিমধ্যে {req['status']}।")
        elif parts[0] == "approve_w":
            await run_db("UPDATE withdrawals SET status='approved' WHERE id=?", (wid,))
            await update.message.reply_text("✅ Withdrawal approved!")
            try:
                await context.bot.send_message(req["user_id"], f"✅ *আপনার `{req['amount']:.2f}` coins এর withdrawal approved!*\nPayment শীঘ্রই পাঠানো হবে।", parse_mode="Markdown")
            except Exception:
                pass
        else:
            await update_balance(req["user_id"], float(req["amount"]))
            await run_db("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,))
            await update.message.reply_text("❌ Withdrawal rejected। Balance refund করা হয়েছে।")
            try:
                await context.bot.send_message(req["user_id"], f"❌ আপনার withdrawal request reject হয়েছে।\n💰 `{req['amount']:.2f}` coins আপনার account-এ ফেরত দেওয়া হয়েছে।", parse_mode="Markdown")
            except Exception:
                pass
    except ValueError:
        await update.message.reply_text("❌ Invalid ID।")
    clear_state(context)

# --- Gift Codes ---
async def gift_codes_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_state(context, ADMIN_GIFT_REWARD, gift_data={})
    await update.message.reply_text("💰 Gift code এর reward amount লিখুন:", reply_markup=get_back_keyboard())

async def gift_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["gift_data"]["reward"] = float(update.message.text)
        set_state(context, ADMIN_GIFT_LIMIT)
        await update.message.reply_text("🔢 Usage limit লিখুন (0 = unlimited):")
    except ValueError:
        await update.message.reply_text("❌ Invalid amount।")

async def gift_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["gift_data"]["limit"] = int(update.message.text)
        set_state(context, ADMIN_GIFT_EXPIRY)
        await update.message.reply_text("📅 Expiry date লিখুন (YYYY-MM-DD) অথবা 'skip':")
    except ValueError:
        await update.message.reply_text("❌ Invalid number।")

async def gift_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = context.user_data["gift_data"]
    data["expiry"] = None if text.lower() == "skip" else text
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    await run_db("INSERT INTO giftcodes (code, reward, usage_limit, expiry_date) VALUES (?,?,?,?)", (code, data["reward"], data["limit"], data["expiry"]))
    await update.message.reply_text(f"✅ *Gift Code তৈরি হয়েছে!*\n\n🎁 Code: `{code}`\n💰 Reward: {data['reward']} coins\n🔢 Usage Limit: {'Unlimited' if data['limit'] == 0 else data['limit']}\n📅 Expiry: {data['expiry'] or 'কখনো শেষ হবে না'}", parse_mode="Markdown", reply_markup=get_admin_keyboard())
    clear_state(context)

# --- Broadcast ---
async def broadcast_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_state(context, ADMIN_BROADCAST)
    await update.message.reply_text("📢 সব users-দের কাছে পাঠানোর message লিখুন:", reply_markup=get_back_keyboard())

async def process_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    users = await run_db("SELECT user_id FROM users WHERE is_banned=0", fetch="all")
    sent = failed = 0
    for u in users:
        try:
            await context.bot.send_message(u["user_id"], f"📢 *ঘোষণা*\n\n{msg}", parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await update.message.reply_text(f"✅ Broadcast সম্পন্ন!\n📤 Sent: {sent}\n❌ Failed: {failed}", reply_markup=get_admin_keyboard())
    clear_state(context)

# --- Analytics ---
async def analytics_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_users = (await run_db("SELECT COUNT(*) as c FROM users", fetch="one"))["c"]
    total_earned = (await run_db("SELECT SUM(total_earned) as s FROM users", fetch="one"))["s"] or 0
    total_tasks = (await run_db("SELECT COUNT(*) as c FROM tasks WHERE enabled=1", fetch="one"))["c"]
    pending_subs = (await run_db("SELECT COUNT(*) as c FROM submissions WHERE status='pending'", fetch="one"))["c"]
    pending_w = (await run_db("SELECT COUNT(*) as c FROM withdrawals WHERE status='pending'", fetch="one"))["c"]
    total_refs = (await run_db("SELECT COUNT(*) as c FROM referrals", fetch="one"))["c"]
    text = f"📊 *Analytics*\n\n👥 Total Users: `{total_users}`\n📈 Total Coins Distributed: `{total_earned:.2f}`\n📋 Active Tasks: `{total_tasks}`\n📥 Pending Submissions: `{pending_subs}`\n💸 Pending Withdrawals: `{pending_w}`\n🎁 Total Referrals: `{total_refs}`"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_admin_keyboard())

# --- Manage User ---
async def manage_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_state(context, ADMIN_MANAGE_USER_ID)
    await update.message.reply_text("👤 *Manage User*\n\nUser ID পাঠান:", parse_mode="Markdown", reply_markup=get_back_keyboard())

async def manage_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text)
        user = await run_db("SELECT * FROM users WHERE user_id=?", (uid,), "one")
        if not user:
            await update.message.reply_text("❌ User পাওয়া যায়নি।")
            clear_state(context)
            return
        set_state(context, ADMIN_MANAGE_USER_ACT, manage_uid=uid)
        ban_status = "🚫 Banned" if user["is_banned"] else "✅ Active"
        await update.message.reply_text(
            f"👤 *User {uid}*\nনাম: {user['full_name'] or 'N/A'}\nBalance: `{user['balance']:.2f}` coins\nStatus: {ban_status}\n\n*Actions:*\n• `add_coins AMOUNT`\n• `remove_coins AMOUNT`\n• `ban`\n• `unban`",
            parse_mode="Markdown", reply_markup=get_back_keyboard()
        )
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID।")
        clear_state(context)

async def manage_user_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data.get("manage_uid")
    parts = update.message.text.lower().split()
    if parts[0] == "add_coins" and len(parts) == 2:
        try:
            amt = float(parts[1])
            await update_balance(uid, amt)
            await update.message.reply_text(f"✅ `{amt}` coins user `{uid}` এর account-এ যোগ হয়েছে।", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ Invalid amount।")
    elif parts[0] == "remove_coins" and len(parts) == 2:
        try:
            amt = float(parts[1])
            await update_balance(uid, -amt)
            await update.message.reply_text(f"✅ `{amt}` coins user `{uid}` এর account থেকে কাটা হয়েছে।", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ Invalid amount।")
    elif parts[0] == "ban":
        await run_db("UPDATE users SET is_banned=1 WHERE user_id=?", (uid,))
        await update.message.reply_text(f"✅ User `{uid}` banned।", parse_mode="Markdown")
    elif parts[0] == "unban":
        await run_db("UPDATE users SET is_banned=0 WHERE user_id=?", (uid,))
        await update.message.reply_text(f"✅ User `{uid}` unbanned।", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Unknown action।")
    clear_state(context)

# --- Channels ---
async def add_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_state(context, ADMIN_ADD_CHANNEL)
    await update.message.reply_text("Channel username পাঠান (যেমন: @yourchannel অথবা yourchannel):", reply_markup=get_back_keyboard())

async def add_channel_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.replace("@", "").strip()
    if not username:
        await update.message.reply_text("❌ Invalid username।")
        return
    await run_db("INSERT OR IGNORE INTO channels (channel_username) VALUES (?)", (username,))
    await update.message.reply_text(f"✅ Channel @{username} যোগ হয়েছে!\n\n⚠️ নিশ্চিত করুন Bot টি @{username} এর admin।", reply_markup=get_admin_keyboard())
    clear_state(context)

async def remove_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channels = await run_db("SELECT channel_username FROM channels", fetch="all")
    if not channels:
        await update.message.reply_text("কোনো channel নেই।")
        return
    set_state(context, ADMIN_REMOVE_CHANNEL)
    msg = "সরাতে চাওয়া channel username পাঠান:\n\n"
    for ch in channels:
        msg += f"• @{ch['channel_username']}\n"
    await update.message.reply_text(msg, reply_markup=get_back_keyboard())

async def remove_channel_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.replace("@", "").strip()
    await run_db("DELETE FROM channels WHERE channel_username=?", (username,))
    await update.message.reply_text(f"✅ Channel @{username} সরানো হয়েছে!", reply_markup=get_admin_keyboard())
    clear_state(context)

# --- Withdraw Methods ---
async def add_method_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_state(context, ADMIN_ADD_METHOD_NAME, method_data={})
    await update.message.reply_text("💳 Method এর নাম লিখুন (যেমন: bKash, PayPal, USDT):", reply_markup=get_back_keyboard())

async def add_method_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["method_data"]["name"] = update.message.text.strip()
    set_state(context, ADMIN_ADD_METHOD_MIN)
    await update.message.reply_text(f"*{update.message.text}* এর minimum withdrawal amount লিখুন:", parse_mode="Markdown")

async def add_method_min(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["method_data"]["min"] = float(update.message.text)
        set_state(context, ADMIN_ADD_METHOD_MAX)
        await update.message.reply_text("Maximum withdrawal amount লিখুন (0 = no limit):")
    except ValueError:
        await update.message.reply_text("❌ Invalid amount।")

async def add_method_max(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["method_data"]["max"] = float(update.message.text)
        set_state(context, ADMIN_ADD_METHOD_INST)
        await update.message.reply_text("Users-দের জন্য instructions লিখুন (অথবা 'skip'):")
    except ValueError:
        await update.message.reply_text("❌ Invalid amount।")

async def add_method_inst(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = context.user_data["method_data"]
    data["inst"] = None if text.lower() == "skip" else text
    await run_db("INSERT OR REPLACE INTO withdraw_methods (method_name, min_amount, max_amount, instructions) VALUES (?,?,?,?)", (data["name"], data["min"], data["max"], data["inst"]))
    await update.message.reply_text(f"✅ Method *{data['name']}* যোগ হয়েছে!\nMin: {data['min']} | Max: {data['max'] if data['max'] > 0 else 'No limit'}", parse_mode="Markdown", reply_markup=get_admin_keyboard())
    clear_state(context)

async def remove_method_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    methods = await run_db("SELECT method_name FROM withdraw_methods", fetch="all")
    if not methods:
        await update.message.reply_text("কোনো method নেই।")
        return
    set_state(context, ADMIN_REMOVE_METHOD)
    msg = "🗑 সরাতে চাওয়া method এর নাম পাঠান:\n\n"
    for m in methods:
        msg += f"• {m['method_name']}\n"
    await update.message.reply_text(msg, reply_markup=get_back_keyboard())

async def remove_method_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    method = await run_db("SELECT method_name FROM withdraw_methods WHERE method_name=?", (name,), "one")
    if not method:
        await update.message.reply_text("❌ Method পাওয়া যায়নি।")
    else:
        await run_db("DELETE FROM withdraw_methods WHERE method_name=?", (name,))
        await update.message.reply_text(f"✅ Method *{name}* সরানো হয়েছে!", parse_mode="Markdown", reply_markup=get_admin_keyboard())
    clear_state(context)

async def method_limits_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    methods = await run_db("SELECT * FROM withdraw_methods", fetch="all")
    if not methods:
        await update.message.reply_text("কোনো method নেই।")
        return
    set_state(context, ADMIN_METHOD_LIMITS_SEL)
    msg = "⚙️ *Method Limits* — edit করতে method এর নাম পাঠান:\n\n"
    for m in methods:
        mx = f"Max: {m['max_amount']:.0f}" if float(m["max_amount"]) > 0 else "No max"
        status = "✅" if m["enabled"] else "❌"
        msg += f"{status} *{m['method_name']}* | Min: {m['min_amount']:.0f} | {mx}\n"
    msg += "\nMethod এর নাম পাঠান:"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_back_keyboard())

async def method_limits_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    method = await run_db("SELECT * FROM withdraw_methods WHERE method_name=?", (name,), "one")
    if not method:
        await update.message.reply_text("❌ Method পাওয়া যায়নি।")
        clear_state(context)
        return
    set_state(context, ADMIN_METHOD_LIMITS_ACT, edit_method=name)
    await update.message.reply_text(
        f"⚙️ Editing *{name}*\n\nAction পাঠান:\n• `min AMOUNT` — Minimum set করুন\n• `max AMOUNT` — Maximum set করুন (0 = no limit)\n• `enable` — Enable করুন\n• `disable` — Disable করুন",
        parse_mode="Markdown"
    )

async def method_limits_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = context.user_data.get("edit_method")
    parts = update.message.text.lower().split()
    if parts[0] == "min" and len(parts) == 2:
        try:
            await run_db("UPDATE withdraw_methods SET min_amount=? WHERE method_name=?", (float(parts[1]), name))
            await update.message.reply_text(f"✅ *{name}* এর min `{parts[1]}` set হয়েছে।", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ Invalid amount।")
    elif parts[0] == "max" and len(parts) == 2:
        try:
            await run_db("UPDATE withdraw_methods SET max_amount=? WHERE method_name=?", (float(parts[1]), name))
            await update.message.reply_text(f"✅ *{name}* এর max `{parts[1]}` set হয়েছে।", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ Invalid amount।")
    elif parts[0] == "enable":
        await run_db("UPDATE withdraw_methods SET enabled=1 WHERE method_name=?", (name,))
        await update.message.reply_text(f"✅ *{name}* enabled।", parse_mode="Markdown")
    elif parts[0] == "disable":
        await run_db("UPDATE withdraw_methods SET enabled=0 WHERE method_name=?", (name,))
        await update.message.reply_text(f"✅ *{name}* disabled।", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Unknown action।")
    clear_state(context)

# --- Toggle Withdraw ---
async def toggle_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = await get_setting("withdraw_enabled", "true")
    new_val = "false" if current == "true" else "true"
    await set_setting("withdraw_enabled", new_val)
    status = "✅ ENABLED" if new_val == "true" else "❌ DISABLED"
    await update.message.reply_text(f"Withdrawals এখন: *{status}*", parse_mode="Markdown", reply_markup=get_admin_keyboard())

# --- Min Withdraw ---
async def set_min_withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = await get_setting("min_withdraw", "10")
    set_state(context, ADMIN_SET_MIN_WD)
    await update.message.reply_text(f"💰 Current global min withdraw: `{current}` coins\n\nনতুন minimum লিখুন:", parse_mode="Markdown", reply_markup=get_back_keyboard())

async def process_set_min(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text)
        await set_setting("min_withdraw", str(amount))
        await update.message.reply_text(f"✅ Global min withdraw `{amount:.2f}` coins set হয়েছে।", parse_mode="Markdown", reply_markup=get_admin_keyboard())
        clear_state(context)
    except ValueError:
        await update.message.reply_text("❌ Invalid amount।")

# --- Referral Bonus ---
async def set_referral_bonus_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = await get_setting("referral_bonus", "5")
    set_state(context, ADMIN_SET_BONUS)
    await update.message.reply_text(f"👥 Current referral bonus: `{current}` coins\n\nনতুন bonus লিখুন:", parse_mode="Markdown", reply_markup=get_back_keyboard())

async def process_set_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text)
        await set_setting("referral_bonus", str(amount))
        await update.message.reply_text(f"✅ Referral bonus `{amount:.2f}` coins set হয়েছে।", parse_mode="Markdown", reply_markup=get_admin_keyboard())
        clear_state(context)
    except ValueError:
        await update.message.reply_text("❌ Invalid amount।")

# --- Set Support ---
async def set_support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = await get_setting("support_username", "")
    set_state(context, ADMIN_SET_SUPPORT)
    await update.message.reply_text(f"📞 Current support: @{current or 'Set করা নেই'}\n\nSupport username লিখুন (যেমন: @support_user):", parse_mode="Markdown", reply_markup=get_back_keyboard())

async def process_set_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.replace("@", "").strip()
    await set_setting("support_username", username)
    await update.message.reply_text(f"✅ Support username @{username} set হয়েছে।", reply_markup=get_admin_keyboard())
    clear_state(context)

# ==================== /start COMMAND ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ref = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            ref = int(context.args[0].split("_")[1])
        except (ValueError, IndexError):
            ref = None

    joined, missing = await check_force_join(user.id, context)
    if not joined:
        context.user_data["_ref"] = ref
        await show_force_join_message(update, context, missing)
        return

    await register_user(user.id, user.username, user.full_name, ref)
    clear_state(context)

    user_row = await run_db("SELECT is_banned FROM users WHERE user_id=?", (user.id,), "one")
    if user_row and user_row["is_banned"]:
        await update.message.reply_text("🚫 আপনি এই bot থেকে বাদ দেওয়া হয়েছেন।")
        return

    balance = await get_user_balance(user.id)
    await update.message.reply_text(
        f"👋 *স্বাগতম, {user.first_name}!*\n\n💰 আপনার balance: `{balance:.2f}` coins\n\nটাস্ক করুন, বন্ধুদের Invite করুন এবং coins আয় করুন! 🚀",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(is_admin(user.id)),
    )

# ==================== MAIN MESSAGE HANDLER ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    uid = update.effective_user.id
    state = get_state(context)

    # Check Again for force join
    if text == "🔄 Check Again":
        joined, missing = await check_force_join(uid, context)
        if joined:
            ref = context.user_data.get("_ref")
            await register_user(uid, update.effective_user.username, update.effective_user.full_name, ref)
            clear_state(context)
            balance = await get_user_balance(uid)
            await update.message.reply_text(
                f"✅ *ধন্যবাদ! আপনি সব চ্যানেলে Join করেছেন!*\n\n👋 *স্বাগতম!*\n💰 আপনার balance: `{balance:.2f}` coins",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard(is_admin(uid)),
            )
        else:
            await show_force_join_message(update, context, missing)
        return

    # Back / Home
    if text in ("🔙 Back", "🏠 Home"):
        await show_main_menu(update, context)
        return

    # Check ban
    user_row = await run_db("SELECT is_banned FROM users WHERE user_id=?", (uid,), "one")
    if user_row and user_row["is_banned"]:
        await update.message.reply_text("🚫 আপনি এই bot থেকে বাদ দেওয়া হয়েছেন।")
        return

    # Force join check (skip for admins)
    if not is_admin(uid):
        joined, missing = await check_force_join(uid, context)
        if not joined:
            await show_force_join_message(update, context, missing)
            return

    # ========== STATE MACHINE ==========
    if state == WD_METHOD:
        await process_withdraw_method(update, context)
        return
    if state == WD_AMOUNT:
        await process_withdraw_amount(update, context)
        return
    if state == WD_ACCOUNT:
        await process_withdraw_account(update, context)
        return
    if state == REDEEM:
        await process_redeem(update, context)
        return
    if state == TASK_LIST:
        try:
            tid = int(text)
            clear_state(context)
            await show_task_detail(update, context, tid)
        except ValueError:
            await update.message.reply_text("❌ সঠিক Task ID লিখুন।")
        return
    if state == ADMIN_ADD_TASK_TITLE:
        await add_task_title(update, context)
        return
    if state == ADMIN_ADD_TASK_DESC:
        await add_task_desc(update, context)
        return
    if state == ADMIN_ADD_TASK_LINK:
        await add_task_link(update, context)
        return
    if state == ADMIN_ADD_TASK_REWARD:
        await add_task_reward(update, context)
        return
    if state == ADMIN_REMOVE_TASK:
        await remove_task_process(update, context)
        return
    if state == ADMIN_PENDING_SUBS:
        await process_submission_action(update, context)
        return
    if state == ADMIN_PENDING_WD:
        await process_withdraw_admin(update, context)
        return
    if state == ADMIN_GIFT_REWARD:
        await gift_reward(update, context)
        return
    if state == ADMIN_GIFT_LIMIT:
        await gift_limit(update, context)
        return
    if state == ADMIN_GIFT_EXPIRY:
        await gift_expiry(update, context)
        return
    if state == ADMIN_BROADCAST:
        await process_broadcast(update, context)
        return
    if state == ADMIN_MANAGE_USER_ID:
        await manage_user_id(update, context)
        return
    if state == ADMIN_MANAGE_USER_ACT:
        await manage_user_action(update, context)
        return
    if state == ADMIN_ADD_CHANNEL:
        await add_channel_process(update, context)
        return
    if state == ADMIN_REMOVE_CHANNEL:
        await remove_channel_process(update, context)
        return
    if state == ADMIN_ADD_METHOD_NAME:
        await add_method_name(update, context)
        return
    if state == ADMIN_ADD_METHOD_MIN:
        await add_method_min(update, context)
        return
    if state == ADMIN_ADD_METHOD_MAX:
        await add_method_max(update, context)
        return
    if state == ADMIN_ADD_METHOD_INST:
        await add_method_inst(update, context)
        return
    if state == ADMIN_REMOVE_METHOD:
        await remove_method_process(update, context)
        return
    if state == ADMIN_METHOD_LIMITS_SEL:
        await method_limits_select(update, context)
        return
    if state == ADMIN_METHOD_LIMITS_ACT:
        await method_limits_action(update, context)
        return
    if state == ADMIN_SET_MIN_WD:
        await process_set_min(update, context)
        return
    if state == ADMIN_SET_BONUS:
        await process_set_bonus(update, context)
        return
    if state == ADMIN_SET_SUPPORT:
        await process_set_support(update, context)
        return

    # ========== MENU BUTTONS ==========
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
