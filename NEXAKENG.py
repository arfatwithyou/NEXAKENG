import telebot
import subprocess
import os
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import json
import logging
import signal
import threading
import re
import sys
import atexit
import requests
import io
import random
import uuid

from flask import Flask, request, jsonify
from threading import Thread

# ===== FLASK KEEP-ALIVE SERVER =====
app = Flask('')

@app.route('/')
def home():
    return """
🗿 𝐀ʀᴄɪᴜs Hᴏsᴛɪɴɢ Bᴏᴛ

⠛⠛⣿⣿⣿⣿⣿⡷⢶⣦⣶⣶⣤⣤⣤⣀⠀⠀⠀
⠀⠀⠀⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⡀⠀
⠀⠀⠀⠉⠉⠉⠙⠻⣿⣿⠿⠿⠛⠛⠛⠻⣿⣿⣇⠀
 ⠀⢤⣀⣀⣀⠀⠀⢸⣷⡄⠀⣁⣀⣤⣴⣿⣿⣿⣆
⠀⠀⠀⠀⠹⠏⠀⠀⠀⣿⣧⠀⠹⣿⣿⣿⣿⣿⡿⣿

⠀⠀⠀⠀⠀⠀⠀⠀⠀⠛⠿⠇⢀⣼⣿⣿⠛⢯⡿⡟
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠦⠴⢿⢿⣿⡿⠷⠀⣿⠀
 ⠀⠀⠀⠀⠀⠀⠙⣷⣶⣶⣤⣤⣤⣤⣤⣶⣦⠃⠀
⠀⠀⠀⠀⠀⠀⢐⣿⣾⣿⣿⣿⣿⣿⣿⣿⣿⠀⠀
⠀⠀⠀⠀⠀⠀⠈⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠻⢿⣿⣿⣿⣿⠟⠁


"""

# ===== CENTRAL CLONE UPLOAD APPROVAL API =====
@app.route('/clone/upload', methods=['POST'])
def clone_upload_api():
    if IS_CLONE:
        return jsonify({'ok': False, 'error': 'clone_instance'}), 403
    try:
        clone_user_id = int(request.form.get('clone_user_id', '0'))
    except Exception:
        return jsonify({'ok': False, 'error': 'invalid_clone_user_id'}), 400
    if clone_user_id <= 0:
        return jsonify({'ok': False, 'error': 'invalid_clone_user_id'}), 400

    supplied_key = request.form.get('approval_key', '')
    expected_key = os.environ.get(f'CLONE_KEY_{clone_user_id}', '')
    if not expected_key or supplied_key != expected_key:
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401
    if clone_user_id not in user_clones:
        return jsonify({'ok': False, 'error': 'clone_not_registered'}), 404

    uploaded = request.files.get('file')
    if uploaded is None or not uploaded.filename:
        return jsonify({'ok': False, 'error': 'file_missing'}), 400

    file_name = os.path.basename(uploaded.filename)
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_ext not in ('.py', '.js', '.zip'):
        return jsonify({'ok': False, 'error': 'unsupported_type'}), 400
    raw = uploaded.read()
    if len(raw) > 100 * 1024 * 1024:
        return jsonify({'ok': False, 'error': 'file_too_large'}), 413

    pending_key = uuid.uuid4().hex[:16]
    local_path = os.path.join(CLONE_PENDING_DIR, f'{clone_user_id}_{pending_key}_{file_name}')
    with open(local_path, 'wb') as fh:
        fh.write(raw)

    user_name = request.form.get('user_name', 'Unknown')
    user_username = request.form.get('user_username', 'No username')
    file_type = 'py' if file_ext == '.py' else 'js' if file_ext == '.js' else 'zip'

    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''INSERT INTO clone_pending_uploads
            (clone_user_id, file_name, file_type, file_size, user_name,
             user_username, local_path, status, timestamp, extra_info)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)''',
            (clone_user_id, file_name, file_type, len(raw), user_name,
             user_username, local_path, datetime.now().isoformat(), 'Clone upload'))
        db_id = c.lastrowid
        conn.commit()
        conn.close()

    try:
        with open(local_path, 'rb') as fh:
            sent = bot.send_document(
                OWNER_ID, fh,
                caption=stylish_text(
                    f"📥 CLONE FILE APPROVAL\n\n"
                    f"🤖 Clone: @{user_clones[clone_user_id]['bot_username']}\n"
                    f"👤 User: {user_name} (@{user_username})\n"
                    f"🆔 Clone User ID: {clone_user_id}\n"
                    f"📄 File: {file_name}\n"
                    f"📏 Size: {len(raw)//1024} KB\n"
                    f"🆔 Request ID: {db_id}"
                )
            )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.row(types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_clone_{db_id}"),
                   types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_clone_{db_id}"))
        bot.edit_message_reply_markup(OWNER_ID, sent.message_id, reply_markup=markup)
    except Exception as e:
        logger.error(f"Failed to notify owner for clone upload {db_id}: {e}", exc_info=True)
        _finish_clone_pending(db_id, 'failed') if '_finish_clone_pending' in globals() else None
        return jsonify({'ok': False, 'error': 'owner_notification_failed'}), 500

    return jsonify({'ok': True, 'request_id': db_id}), 200


def run_flask():
  port = int(os.environ.get("PORT", 8080))
  app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("🌐 Flask Keep-Alive server started.")

# ===== BOT UPTIME TRACKING =====
BOT_START_TIME = datetime.now()

def get_uptime():
    uptime = datetime.now() - BOT_START_TIME
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"⏱️ {days}d {hours}h {minutes}m {seconds}s"

# ===== BOT CONFIGURATION =====
BOT_TOKEN = os.environ.get('BOT_TOKEN', '9199498122:AAGzdGe5Sd5D36vkl3E0UmvU6096NFWRZI0')
OWNER_ID = 8804527526
ADMIN_ID = OWNER_ID
YOUR_USERNAME = '@k1ttu_darling'
UPDATE_CHANNEL = 'https://t.me/shinzu_updates'

# ==================== GROQ AI CONFIGURATION ====================
GROQ_API_KEY = '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'  # Keep API key directly in main.py
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

AVAILABLE_MODELS = {
    'gpt-oss': 'openai/gpt-oss-120b'
}
DEFAULT_MODEL = 'gpt-oss'
global_model = DEFAULT_MODEL
# =====================================================================

# ===== CLONE / CENTRAL APPROVAL CONFIGURATION =====
IS_CLONE = False
CLONE_USER_ID = None
MASTER_APPROVAL_URL = os.environ.get('MASTER_APPROVAL_URL', 'http://127.0.0.1:8080/clone/upload')
CLONE_APPROVAL_KEY = os.environ.get('CLONE_APPROVAL_KEY', '')
CLONE_APPROVAL_DIR_NAME = 'approved_queue'
# =====================================================================

# ===== DIRECTORY SETUP =====
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

# ===== USER LIMITS =====
FREE_USER_LIMIT = 99
SUBSCRIBED_USER_LIMIT = 999
ADMIN_LIMIT = 9999
OWNER_LIMIT = float('inf')

# ===== CREATE DIRECTORIES =====
os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)
CLONE_PENDING_DIR = os.path.join(IROTECH_DIR, 'clone_pending')
os.makedirs(CLONE_PENDING_DIR, exist_ok=True)


# ===== USER PROFILE STORAGE (user.json) =====
USER_JSON_PATH = os.path.join(BASE_DIR, 'user.json')
USER_JSON_LOCK = threading.RLock()

def _load_user_json():
    """Read user profiles safely; recover to an empty mapping if the file is invalid."""
    if not os.path.exists(USER_JSON_PATH):
        return {}
    try:
        with open(USER_JSON_PATH, 'r', encoding='utf-8') as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Could not read user.json: %s", exc)
        return {}

def save_user_profile(user, chat_id=None):
    """Insert/update a Telegram user's public profile in user.json."""
    if user is None or not getattr(user, 'id', None):
        return False
    now = datetime.now().isoformat(timespec='seconds')
    uid = str(user.id)
    with USER_JSON_LOCK:
        profiles = _load_user_json()
        previous = profiles.get(uid, {})
        profiles[uid] = {
            'user_id': user.id,
            'username': getattr(user, 'username', None),
            'first_name': getattr(user, 'first_name', None),
            'last_name': getattr(user, 'last_name', None),
            'language_code': getattr(user, 'language_code', None),
            'is_premium': getattr(user, 'is_premium', False),
            'is_bot': getattr(user, 'is_bot', False),
            'chat_id': chat_id,
            'first_seen': previous.get('first_seen', now),
            'last_seen': now
        }
        temp_path = USER_JSON_PATH + '.tmp'
        try:
            with open(temp_path, 'w', encoding='utf-8') as fp:
                json.dump(profiles, fp, ensure_ascii=False, indent=2)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(temp_path, USER_JSON_PATH)
            return True
        except OSError as exc:
            logger.error("Could not save user profile to user.json: %s", exc)
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            return False

# Ensure user.json exists on first launch.
if not os.path.exists(USER_JSON_PATH):
    try:
        with open(USER_JSON_PATH, 'w', encoding='utf-8') as fp:
            json.dump({}, fp, indent=2)
    except OSError as exc:
        logging.error("Could not initialize user.json: %s", exc)

# ===== PERSISTENT UPTIME (survives restarts) =====
# --- Persistent uptime across restarts ---
PERSISTENT_START_FILE = os.path.join(IROTECH_DIR, 'bot_start_time.txt')

def get_persistent_start_time():
    if os.path.exists(PERSISTENT_START_FILE):
        try:
            with open(PERSISTENT_START_FILE, 'r') as f:
                timestamp = f.read().strip()
                return datetime.fromisoformat(timestamp)
        except Exception as e:
            logging.error(f"Failed to read persistent start time: {e}")
    now = datetime.now()
    try:
        with open(PERSISTENT_START_FILE, 'w') as f:
            f.write(now.isoformat())
    except Exception as e:
        logging.error(f"Failed to write persistent start time: {e}")
    return now

BOT_START_TIME = get_persistent_start_time()
BOT_START_TIME = get_persistent_start_time()

# ===== BOT INITIALIZATION =====
bot = telebot.TeleBot(BOT_TOKEN)

# ===== BOT PASSWORD PROTECTION =====
BOT_PASSWORD = 'KHUSHI'
authenticated_users = set()
password_attempts = {}
PASSWORD_MAX_ATTEMPTS = 3
PASSWORD_LOCK_SECONDS = 60

def _password_locked(user_id):
    data = password_attempts.get(user_id, {})
    locked_until = data.get('locked_until', 0)
    return locked_until > time.time()

def _password_remaining(user_id):
    data = password_attempts.get(user_id, {})
    return max(0, int(data.get('locked_until', 0) - time.time()))

def _password_check_message(message):
    """Return True when this message is allowed through the password gate."""
    user_id = getattr(getattr(message, 'from_user', None), 'id', None)
    if not user_id:
        return True
    if user_id in authenticated_users:
        return True
    if _password_locked(user_id):
        bot.reply_to(message, f"⛔ Too many wrong attempts. Try again in {_password_remaining(user_id)} seconds.")
        return False
    text = (getattr(message, 'text', None) or '').strip()
    if text.startswith('/start'):
        bot.reply_to(message, '🔐 Password Protected Bot\n\n🔑 Please send the password to continue.')
        return False
    if text == BOT_PASSWORD:
        authenticated_users.add(user_id)
        password_attempts.pop(user_id, None)
        bot.reply_to(message, '✅ Password accepted. Access granted.')
        return False
    attempts = password_attempts.get(user_id, {}).get('attempts', 0) + 1
    if attempts >= PASSWORD_MAX_ATTEMPTS:
        password_attempts[user_id] = {'attempts': attempts, 'locked_until': time.time() + PASSWORD_LOCK_SECONDS}
        bot.reply_to(message, '🚫 Wrong password 3 times. Access locked for 1 minute.')
    else:
        password_attempts[user_id] = {'attempts': attempts, 'locked_until': 0}
        bot.reply_to(message, f'❌ Wrong password. Attempts left: {PASSWORD_MAX_ATTEMPTS - attempts}.')
    return False

# --- Premium Emoji + Custom Emoji ID System (copied from Hostinger.py) ---
# Only premium emoji mapping/conversion is added here; bot credentials and bot logic are untouched.
import html as _html_mod

_PE_TAG_EXTRA_MAP = {
    '━': '5997027028262461019',
    '⏳': '6084396322444544568',
    '🆔': '5841276284155467413',
    '🆓': '6258212648186285804',
    '⏱': '6334603778326529773',
    '⏸': '5116447063432758252',
    '▶': '5188332188763970767',
    '🆕': '5201674410179721640',
}

PREMIUM_EMOJI_MAP = {
    '🆔': '5841276284155467413',
    '⌛': 'PASTE_ID_HERE',
    '↗': 'PASTE_ID_HERE',
    '⌨': 'PASTE_ID_HERE',
    '⏱': '6334603778326529773',
    '⏳': '6084396322444544568',
    '⏸': 'PASTE_ID_HERE',
    '⏹': 'PASTE_ID_HERE',
    '▶': 'PASTE_ID_HERE',
    '☠': 'PASTE_ID_HERE',
    '⚙': '5404603265499283735',
    '⚜': 'PASTE_ID_HERE',
    '⚠': '6242010768825391425',
    '⚡': '6267107057304868214',
    '⛔': '5445350865776941647',
    '✅': '6267008582294705964',
    '🛫': '6302960211636266397',
    '✨': '6269051556503490994',
    '✳': '6266973397922616654',
    '❌': '6267000941547885720',
    '❓': '5814613642577977311',
    '➕': '6258168070720720472',
    '➖': 'PASTE_ID_HERE',
    '➜': 'PASTE_ID_HERE',
    '⬆': '5422737811423918818',
    '⭐': '6266962677684246108',
    '🌍': '5224450179368767019',
    '🌐': '5201732344993576400',
    '🌙': '5449569374065152798',
    '🎉': '6129579803600231171',
    '🎯': '5256131095094652290',
    '🏃': '5397809391741181485',
    '🐍': '5203945198043821970',
    '👀': '5931248916929453952',
    '👇': '6147439566107186310',
    '👉': '6266829404849050968',
    '👋': '5422427671835474966',
    '👑': '6235252066554484059',
    '👤': '5258011929993026890',
    '👥': '6089314901812056837',
    '💀': '4987978342431982490',
    '💎': '6028530359975548369',
    '💡': '5893290369629556374',
    '💥': '6269369495752545904',
    '💳': '5258204546391351475',
    '💻': '5350554349074391003',
    '💽': '5321295593040010652',
    '💾': '5884064642438795702',
    '📁': '5452165780579843515',
    '📂': '5258514780469075716',
    '📄': '5257965810634202885',
    '📅': '5287606810168028257',
    '📈': '5429651785352501917',
    '📊': '6129801569941592173',
    '📋': '5422537279400868395',
    '📌': '5397782960512444700',
    '📎': '5260730055880876557',
    '📏': '5996812868308177363',
    '📜': '6235406870060735585',
    '📝': '5258500400918587241',
    '📞': '5258337316715373336',
    '📡': '5949501312461707000',
    '📢': '6269365454188319459',
    '📤': '6266969287638913443',
    '📥': '5443127283898405358',
    '📦': '5258134813302332906',
    '📱': '5334954057192719331',
    '🔄': '5203988328105405942',
    '🔍': '5429571366384842791',
    '🔐': '5291873529464122510',
    '🔑': '5201761348907725593',
    '🔒': '5291873529464122510',
    '🔓': '5291873529464122510',
    '🔗': '5422527267832103807',
    '🔙': '6267119710278522544',
    '🔢': '5226513232549664618',
    '🔧': '6266858606331696360',
    '🔪': '5226513232549664618',
    '🔰': '6266794310671275367',
    '🔴': '6235576525563895420',
    '🔵': '6337119366441604701',
    '🗄': '5839323457015256759',
    '🗑': '6129486856212979482',
    '🗿': '5796444736914988613',
    '😕': '5397885236568661305',
    '🚀': '6129639980387015660',
    '🚦': '6269130601081608904',
    '🚨': '5204082134486117389',
    '🚫': '5796394541632201711',
    '🛑': '5204315024792777263',
    '🛒': '5431492767249342908',
    '🛠': '5988023995125993550',
    '🛡': '6267032805910254913',
    '🟠': '6267186570034419608',
    '🟡': '6267229004311303657',
    '🟢': '5203931080486319136',
    '🟣': '5967333011652350314',
    '🤍': '6280597085725136228',
    '🤔': '6096082069463508815',
    '🤖': '6300683629861280494',
    '🧑': '6208575629542038441',
    '🧠': '5389014256496684805',
    '🧹': '6089385206131726181',
}

# Extra tag map kept empty — all IDs set to PASTE_ID_HERE for manual fill
_PE_TAG_EXTRA_MAP = {}
PREMIUM_EMOJI_MAP.update(_PE_TAG_EXTRA_MAP)

_PREMIUM_EMOJI_RE = re.compile(
    '(?:' + '|'.join(re.escape(e) for e in PREMIUM_EMOJI_MAP) + ')️?'
)
_MARKUP_RE = re.compile(
    r'```(?:[a-zA-Z0-9_+\-]*\n)?(?P<pre>.*?)```'
    r'|`(?P<code>[^`\n]+?)`'
    r'|\*(?P<bold>[^*\n]+?)\*',
    re.DOTALL,
)

def pe(emoji_char):
    """Convert emoji to tg-emoji HTML tag only if a real (non-placeholder) ID exists."""
    base = emoji_char.rstrip('️')
    cid = PREMIUM_EMOJI_MAP.get(emoji_char) or PREMIUM_EMOJI_MAP.get(base)
    if not cid or cid == 'PASTE_ID_HERE' or cid == 'PASTE_ID':
        return emoji_char  # leave unmapped / placeholder emojis unchanged
    return f'<tg-emoji emoji-id="{cid}">{base}</tg-emoji>'


def premiumify(text):
    if not isinstance(text, str):
        return text
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in PREMIUM_EMOJI_MAP:
            out.append(pe(ch))
            i += 1
            if i < len(text) and text[i] == '️':
                i += 1
            continue
        out.append(ch)
        i += 1
    return ''.join(out)

def _utf16_len(s):
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in s)

def _emoji_entities_in(segment, base_offset):
    entities = []
    cursor = base_offset
    pos = 0
    for m in _PREMIUM_EMOJI_RE.finditer(segment):
        cursor += _utf16_len(segment[pos:m.start()])
        matched = m.group(0)
        base_char = matched[0]
        cid = PREMIUM_EMOJI_MAP.get(base_char)
        if cid and cid not in ('PASTE_ID_HERE', 'PASTE_ID'):
            entities.append(types.MessageEntity(type='custom_emoji', offset=cursor, length=_utf16_len(matched), custom_emoji_id=cid))
        cursor += _utf16_len(matched)
        pos = m.end()
    return entities

def build_custom_emoji_entities(text):
    if not isinstance(text, str):
        return text, []
    out_parts, entities = [], []
    pos = 0
    utf16_pos = 0
    for m in _MARKUP_RE.finditer(text):
        plain = text[pos:m.start()]
        entities.extend(_emoji_entities_in(plain, utf16_pos))
        out_parts.append(plain)
        utf16_pos += _utf16_len(plain)
        if m.group('pre') is not None:
            content, etype = m.group('pre'), 'pre'
        elif m.group('code') is not None:
            content, etype = m.group('code'), 'code'
        else:
            content, etype = m.group('bold'), 'bold'
        entities.append(types.MessageEntity(type=etype, offset=utf16_pos, length=_utf16_len(content)))
        out_parts.append(content)
        utf16_pos += _utf16_len(content)
        pos = m.end()
    tail = text[pos:]
    entities.extend(_emoji_entities_in(tail, utf16_pos))
    out_parts.append(tail)
    return ''.join(out_parts), entities

def _apply_premium_emoji(kwargs, text):
    """Apply premium emoji without fragile Telegram entity offsets."""
    if not isinstance(text, str):
        return text
    if _PREMIUM_EMOJI_RE.search(text) is None:
        return text

    mode = kwargs.get('parse_mode')
    if mode == 'HTML':
        return premiumify(text)
    if mode == 'Markdown':
        converted = premiumify(text)
        converted = re.sub(r'\*([^*\n]+)\*', r'<b>\1</b>', converted)
        converted = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', converted)
        kwargs['parse_mode'] = 'HTML'
        kwargs.pop('entities', None)
        return converted
    # No parse mode: custom emoji needs HTML, otherwise leave text as styled plain text.
    kwargs['parse_mode'] = 'HTML'
    kwargs.pop('entities', None)
    return premiumify(text)



# ===== MESSAGE STYLE PATCH (MIXED FONT: FIRST LETTER BOLD + REST SMALL CAPS) =====
# Example: Create Clone -> 𝐂ʀᴇᴀᴛᴇ 𝐂ʟᴏɴᴇ
BOLD_CAPS = {
    'a':'𝐀','b':'𝐁','c':'𝐂','d':'𝐃','e':'𝐄','f':'𝐅','g':'𝐆','h':'𝐇',
    'i':'𝐈','j':'𝐉','k':'𝐊','l':'𝐋','m':'𝐌','n':'𝐍','o':'𝐎','p':'𝐏',
    'q':'𝐐','r':'𝐑','s':'𝐒','t':'𝐓','u':'𝐔','v':'𝐕','w':'𝐖','x':'𝐗',
    'y':'𝐘','z':'𝐙'
}
SMALL_CAPS = {
    'a':'ᴀ','b':'ʙ','c':'ᴄ','d':'ᴅ','e':'ᴇ','f':'ғ','g':'ɢ','h':'ʜ',
    'i':'ɪ','j':'ᴊ','k':'ᴋ','l':'ʟ','m':'ᴍ','n':'ɴ','o':'ᴏ','p':'ᴘ',
    'q':'ǫ','r':'ʀ','s':'ꜱ','t':'ᴛ','u':'ᴜ','v':'ᴠ','w':'ᴡ','x':'x',
    'y':'ʏ','z':'ᴢ'
}

# Keep URLs, @usernames, /commands, placeholders and code spans untouched.
_STYLE_PROTECT_RE = re.compile(
    r'(https?://[^\s<>]+|www\.[^\s<>]+|t\.me/[^\s<>]+'
    r'|@[A-Za-z0-9_]{4,}|/[A-Za-z][A-Za-z0-9_]*|\{[^}]*\}|%[sd])',
    re.IGNORECASE
)
# Protect full blockquote blocks so stylize never corrupts native quote HTML.
_STYLE_MARKUP_RE = re.compile(
    r'(<blockquote(?:\s+expandable)?>.*?</blockquote>|<[^>]+>|&(?:amp|lt|gt|quot|#39|nbsp);|```.*?```|`[^`\n]*`)',
    re.DOTALL | re.IGNORECASE
)

# Normalize existing mixed/small-cap glyphs before styling, making the function
# idempotent and preventing already-styled button labels from being corrupted.
_FONT_TO_ASCII = str.maketrans({
    **{c: c.lower() for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'},
    **{c: u for c, u in zip('𝐀𝐁𝐂𝐃𝐄𝐅𝐆𝐇𝐈𝐉𝐊𝐋𝐌𝐍𝐎𝐏𝐐𝐑𝐒𝐓𝐔𝐕𝐖𝐗𝐘𝐙', 'abcdefghijklmnopqrstuvwxyz')},
    **dict(zip('ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ', 'abcdefghijklmnopqrstuvwxyz')),
})

def _stylize_plain_text(text):
    """Use bold-serif initials and small-caps for the rest of each word."""
    text = text.translate(_FONT_TO_ASCII)
    out = []
    word_start = True
    for ch in text:
        if ch.isascii() and ch.isalpha():
            lower = ch.lower()
            if word_start:
                out.append(BOLD_CAPS.get(lower, ch.upper()))
            else:
                out.append(SMALL_CAPS.get(lower, lower))
            word_start = False
        else:
            out.append(ch)
            # A new word starts after whitespace or punctuation/symbols.
            word_start = not (ch.isascii() and (ch.isdigit() or ch == '_'))
    return ''.join(out)

def stylize_text(text):
    """Apply mixed font while preserving URLs, usernames, commands and markup."""
    if not isinstance(text, str) or not text:
        return text
    out = []
    for chunk in _STYLE_MARKUP_RE.split(text):
        if not chunk:
            continue
        if _STYLE_MARKUP_RE.fullmatch(chunk):
            out.append(chunk)
            continue
        parts = _STYLE_PROTECT_RE.split(chunk)
        out.extend(part if i % 2 else _stylize_plain_text(part)
                   for i, part in enumerate(parts))
    return ''.join(out)

def format_message(text):
    """Apply the same mixed font to a full message; no box is added."""
    return stylize_text(text)

def _is_native_blockquote(text):
    """True when text already contains Telegram HTML blockquote markup."""
    return isinstance(text, str) and ('<blockquote' in text.lower())

def _make_styled_sender(orig, arg_index, arg_name, box=False, auto_quote=True):
    """Style outgoing bot text/captions while preserving protected values.
    When auto_quote=True (default), every text body is wrapped in Telegram
    native <blockquote> so all bot outputs show the quote UI.
    """
    def _styled(*args, **kwargs):
        original_args, original_kwargs = args, dict(kwargs)
        kwargs = dict(kwargs)
        try:
            def _process(val):
                if not isinstance(val, str) or not val:
                    return val
                if _is_native_blockquote(val):
                    # Already final HTML quote — force HTML parse mode
                    if auto_quote:
                        kwargs['parse_mode'] = 'HTML'
                    return val
                if auto_quote:
                    # Native blockquote for ALL bot output messages
                    # make_blockquote applies mixed-font + HTML escape
                    kwargs['parse_mode'] = 'HTML'
                    return make_blockquote(val)
                # Non-quote path (e.g. callback toasts): just stylize
                return format_message(val) if box else stylize_text(val)

            if arg_name in kwargs and isinstance(kwargs[arg_name], str) and kwargs[arg_name]:
                kwargs[arg_name] = _process(kwargs[arg_name])
            elif len(args) > arg_index and isinstance(args[arg_index], str) and args[arg_index]:
                args_list = list(args)
                args_list[arg_index] = _process(args_list[arg_index])
                args = tuple(args_list)
            # Apply premium custom emoji conversion after font/quote styling.
            if arg_name in kwargs and isinstance(kwargs[arg_name], str) and kwargs[arg_name]:
                kwargs[arg_name] = _apply_premium_emoji(kwargs, kwargs[arg_name])
            elif len(args) > arg_index and isinstance(args[arg_index], str) and args[arg_index]:
                args_list = list(args)
                args_list[arg_index] = _apply_premium_emoji(kwargs, args_list[arg_index])
                args = tuple(args_list)
            return orig(*args, **kwargs)
        except Exception as e:
            # Never let styling break a Telegram operation.
            try:
                logger.debug(f"style patch skipped: {e}")
            except Exception:
                pass
            return orig(*original_args, **original_kwargs)
    return _styled

# Apply mixed font + native blockquote to all outgoing messages and captions.
bot.send_message = _make_styled_sender(bot.send_message, 1, 'text', auto_quote=True)
bot.edit_message_text = _make_styled_sender(bot.edit_message_text, 0, 'text', auto_quote=True)
bot.send_photo = _make_styled_sender(bot.send_photo, 2, 'caption', auto_quote=True)
bot.send_video = _make_styled_sender(bot.send_video, 2, 'caption', auto_quote=True)
bot.send_document = _make_styled_sender(bot.send_document, 2, 'caption', auto_quote=True)
bot.edit_message_caption = _make_styled_sender(bot.edit_message_caption, 2, 'caption', auto_quote=True)
# Callback toasts must stay plain (no blockquote).
bot.answer_callback_query = _make_styled_sender(bot.answer_callback_query, 1, 'text', auto_quote=False)
bot.reply_to = _make_styled_sender(bot.reply_to, 1, 'text', auto_quote=True)

# Apply mixed font to Reply Keyboard and Inline Keyboard button labels.
_orig_kb_btn_init = types.KeyboardButton.__init__
def _styled_kb_btn_init(self, *args, **kwargs):
    if args and isinstance(args[0], str):
        args = (stylize_text(args[0]),) + args[1:]
    elif isinstance(kwargs.get('text'), str):
        kwargs = dict(kwargs)
        kwargs['text'] = stylize_text(kwargs['text'])
    _orig_kb_btn_init(self, *args, **kwargs)
types.KeyboardButton.__init__ = _styled_kb_btn_init

_orig_inl_btn_init = types.InlineKeyboardButton.__init__
def _styled_inl_btn_init(self, *args, **kwargs):
    if args and isinstance(args[0], str):
        args = (stylize_text(args[0]),) + args[1:]
    elif isinstance(kwargs.get('text'), str):
        kwargs = dict(kwargs)
        kwargs['text'] = stylize_text(kwargs['text'])
    _orig_inl_btn_init(self, *args, **kwargs)
types.InlineKeyboardButton.__init__ = _styled_inl_btn_init
# ===== END MESSAGE STYLE PATCH =====

# ===== GLOBAL PASSWORD GATE (DISABLED) =====
# Password is only checked once on /start. Global interception disabled
# so users are not asked repeatedly after authenticating.
# (Previous global gate conflicted with the /start password handler.)

# ===== DATA STORAGE =====
bot_scripts = {}
clone_processes = {}
user_subscriptions = {}
user_files = {}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False
user_clones = {}
banned_users = set()          # Banned users (User Management)
user_limits = {}              # Custom per-user file limits (User Management)
mandatory_channels = {}       # Mandatory join channels (Channel Add)
github_data = {}              # GitHub deploy flow state
auto_recovery_last_restart = {}   # Auto-recovery crash tracking

# ===== LOGGING SETUP =====
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ===== KEYBOARD LAYOUTS =====
COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📢 Uᴘᴅᴀᴛᴇꜱ Cʜᴀɴɴᴇʟ", "🌍 Uᴘʟᴏᴀᴅ"],
    ["📂 Mʏ Fɪʟᴇs", "⚡ Bᴏᴛ Sᴘᴇᴇᴅ"],
    ["🚀 Sᴛᴀᴛᴜs", "🔄 Rᴇsᴛᴀʀᴛ"],
    ["⏹ Sᴛᴏᴘ", "⚙️ Rᴇᴄᴏᴍᴍᴇɴᴅᴇᴅ Iɴsᴛᴀʟʟ"],
    ["🤖 AI Aɢᴇɴᴛ", "🌐 Gɪᴛ Cʟᴏɴᴇ"],
    ["📞 Cᴏɴᴛᴀᴄᴛ Oᴡɴᴇʀ", "📱 WʜᴀᴛsAᴘᴘ Bᴏᴛ"],
    ["🟣 Dɪsᴄᴏʀᴅ Bᴏᴛ", "🚀 API"],
    ["🌍 Wᴇʙsɪᴛᴇ", "💻 Lᴏᴄᴀʟ Hᴏsᴛ"],
    ["💳 Bᴜʏ Pʟᴀɴ", "🛒 Sᴛᴏʀᴇ"],
    ["👤 Mʏ Aᴄᴄᴏᴜɴᴛ"],
]

ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    # --- Hosting panel ---
    ["📢 Uᴘᴅᴀᴛᴇꜱ Cʜᴀɴɴᴇʟ", "🌍 Uᴘʟᴏᴀᴅ"],
    ["📂 Mʏ Fɪʟᴇs", "⚡ Bᴏᴛ Sᴘᴇᴇᴅ"],
    ["🚀 Sᴛᴀᴛᴜs", "🔄 Rᴇsᴛᴀʀᴛ"],
    ["⏹ Sᴛᴏᴘ", "⚙️ Rᴇᴄᴏᴍᴍᴇɴᴅᴇᴅ Iɴsᴛᴀʟʟ"],
    ["🤖 AI Aɢᴇɴᴛ", "🌐 Gɪᴛ Cʟᴏɴᴇ"],
    ["📞 Cᴏɴᴛᴀᴄᴛ Oᴡɴᴇʀ", "📱 WʜᴀᴛsAᴘᴘ Bᴏᴛ"],
    ["🟣 Dɪsᴄᴏʀᴅ Bᴏᴛ", "🚀 API"],
    ["🌍 Wᴇʙsɪᴛᴇ", "💻 Lᴏᴄᴀʟ Hᴏsᴛ"],
    ["💳 Bᴜʏ Pʟᴀɴ", "🛒 Sᴛᴏʀᴇ"],
    # --- Admin-only ---
    ["📤 Uᴘʟᴏᴀᴅ Fɪʟᴇ", "📂 Cʜᴇᴄᴋ Fɪʟᴇs"],
    ["📊 Sᴛᴀᴛɪsᴛɪᴄs", "⌨️ Sᴇɴᴅ Cᴏᴍᴍᴀɴᴅ"],
    ["💳 Sᴜʙsᴄʀɪᴘᴛɪᴏɴs", "📢 Bʀᴏᴀᴅᴄᴀsᴛ"],
    ["🔒 Lᴏᴄᴋ Bᴏᴛ", "🟢 Rᴜɴ Aʟʟ Sᴄʀɪᴘᴛs"],
    ["👑 Admin Panel", "🤖 Cʀᴇᴀᴛᴇ Cʟᴏɴᴇ"],
    ["📢 Cʜᴀɴɴᴇʟ Aᴅᴅ", "🛠️ Mᴀɴᴜᴀʟ Iɴꜱᴛᴀʟʟ"],
    ["👥 Uꜱᴇʀ Mᴀɴᴀɢᴇᴍᴇɴᴛ", "⚙️ Sᴇᴛᴛɪɴɢs"],
    ["📦 Pᴋɢ Iɴꜱᴛᴀʟʟ", "🌐 Gɪᴛʜᴜʙ Rᴇᴘᴏ"],
    # --- Always last ---
    ["👤 Mʏ Aᴄᴄᴏᴜɴᴛ"],
]

# ===== DATABASE FUNCTIONS =====
def init_db():
    logger.info(f"🗄️ Iɴɪᴛɪᴀʟɪᴢɪɴɢ Dᴀᴛᴀʙᴀsᴇ: {DATABASE_PATH}")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                     (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_files
                     (user_id INTEGER, file_name TEXT, file_type TEXT,
                      PRIMARY KEY (user_id, file_name))''')
        c.execute('''CREATE TABLE IF NOT EXISTS active_users
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS clone_bots
                     (user_id INTEGER PRIMARY KEY, bot_username TEXT, token TEXT, create_time TEXT,
                      clone_secret TEXT)''')
        try:
            c.execute("ALTER TABLE clone_bots ADD COLUMN clone_secret TEXT")
        except sqlite3.OperationalError:
            pass
        c.execute('''CREATE TABLE IF NOT EXISTS clone_pending_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clone_user_id INTEGER,
            file_name TEXT,
            file_type TEXT,
            file_size INTEGER,
            user_name TEXT,
            user_username TEXT,
            local_path TEXT,
            status TEXT DEFAULT 'pending',
            timestamp TEXT,
            extra_info TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS pending_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, file_id TEXT, file_name TEXT, file_type TEXT,
            file_size INTEGER, user_name TEXT, user_username TEXT,
            timestamp TEXT, extra_info TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS banned_users
                     (user_id INTEGER PRIMARY KEY, reason TEXT, banned_by INTEGER, ban_date TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_limits
                     (user_id INTEGER PRIMARY KEY, file_limit INTEGER, set_by INTEGER, set_date TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS mandatory_channels
                     (channel_id TEXT PRIMARY KEY,
                      channel_username TEXT,
                      channel_name TEXT,
                      added_by INTEGER,
                      added_date TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS install_logs
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER,
                      module_name TEXT,
                      package_name TEXT,
                      status TEXT,
                      log TEXT,
                      install_date TEXT)''')
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
             c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        conn.commit()
        conn.close()
        logger.info("✅ Dᴀᴛᴀʙᴀsᴇ Iɴɪᴛɪᴀʟɪᴢᴇᴅ Sᴜᴄᴄᴇssғᴜʟʟʏ.")
    except Exception as e:
        logger.error(f"❌ Dᴀᴛᴀʙᴀsᴇ Eʀʀᴏʀ: {e}", exc_info=True)

# ===== LOAD DATA FROM DATABASE =====
def load_data():
    logger.info("📥 Lᴏᴀᴅɪɴɢ Dᴀᴛᴀ Fʀᴏᴍ Dᴀᴛᴀʙᴀsᴇ...")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()

        c.execute('SELECT user_id, expiry FROM subscriptions')
        for user_id, expiry in c.fetchall():
            try:
                user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
            except ValueError:
                logger.warning(f"⚠️ Invalid expiry date format for user {user_id}: {expiry}. Skipping.")

        c.execute('SELECT user_id, file_name, file_type FROM user_files')
        for user_id, file_name, file_type in c.fetchall():
            if user_id not in user_files:
                user_files[user_id] = []
            user_files[user_id].append((file_name, file_type))

        c.execute('SELECT user_id FROM active_users')
        active_users.update(user_id for (user_id,) in c.fetchall())

        c.execute('SELECT user_id FROM admins')
        admin_ids.update(user_id for (user_id,) in c.fetchall())

        # Load banned users
        c.execute('SELECT user_id FROM banned_users')
        banned_users.update(user_id for (user_id,) in c.fetchall())

        # Load custom user limits
        c.execute('SELECT user_id, file_limit FROM user_limits')
        for user_id, file_limit in c.fetchall():
            user_limits[user_id] = file_limit

        # Load mandatory channels
        c.execute('SELECT channel_id, channel_username, channel_name FROM mandatory_channels')
        for channel_id, channel_username, channel_name in c.fetchall():
            mandatory_channels[channel_id] = {
                'username': channel_username,
                'name': channel_name
            }

        c.execute('SELECT user_id, bot_username, token, create_time, clone_secret FROM clone_bots')
        for user_id, bot_username, token, create_time, clone_secret in c.fetchall():
            try:
                user_clones[user_id] = {
                    'bot_username': bot_username,
                    'token': token,
                    'create_time': datetime.fromisoformat(create_time),
                    'clone_secret': clone_secret or ''
                }
                if clone_secret:
                    os.environ[f'CLONE_KEY_{user_id}'] = clone_secret
                logger.info(f"✅ Lᴏᴀᴅᴇᴅ Cʟᴏɴᴇ Bᴏᴛ @{bot_username} Fᴏʀ Usᴇʀ {user_id}")
            except ValueError:
                logger.warning(f"⚠︎ Iɴᴠᴀʟɪᴅ Cʀʀᴀᴛᴇ_Tɪᴍᴇ Fᴏʀ Cʟᴏɴᴇ Bᴏᴛ Fᴏʀ Usᴇʀ: {user_id}")

        conn.close()
        logger.info(f"✅ Dᴀᴛᴀ Lᴏᴀᴅᴇᴅ: 👥 {len(active_users)} Usᴇʀ, 💳 {len(user_subscriptions)} Sᴜʙsᴄʀɪᴘᴛɪᴏɴs, 👑 {len(admin_ids)} Aᴅᴍɪɴs, 🤖 {len(user_clones)} Cʟᴏɴᴇs.")
    except Exception as e:
        logger.error(f"❌ Eʀʀᴏʀ Dᴀᴛᴀ: {e}", exc_info=True)

# ===== Cʀᴇᴀᴛᴇ Cʟᴏɴᴇ Bᴏᴛ FUNCTIONS =====
def save_clone_info(user_id, bot_username, token, clone_secret=''):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('''INSERT OR REPLACE INTO clone_bots
                         (user_id, bot_username, token, create_time, clone_secret)
                         VALUES (?, ?, ?, ?, ?)''',
                      (user_id, bot_username, token, datetime.now().isoformat(), clone_secret))
            conn.commit()
            user_clones[user_id] = {
                'bot_username': bot_username,
                'token': token,
                'create_time': datetime.now()
            }
            logger.info(f"✅ Sᴀᴠᴇᴅ Cʟᴏɴᴇ Bᴏᴛ @{bot_username} Fᴏʀ Usᴇʀ {user_id}")
        except sqlite3.Error as e:
            logger.error(f"❌ Eʀʀᴏʀ Sᴀᴠɪɴᴠ Cʟᴏɴᴇ Bᴏᴛ Fᴏʀ {user_id}: {e}")
        except Exception as e:
            logger.error(f"❌ Eʀʀᴏʀ Sᴀᴠɪɴᴠ Cʟᴏɴᴇ Bᴏᴛ {user_id}: {e}", exc_info=True)
        finally:
            conn.close()

# ===== REMOVE CLONE INFO =====
def remove_clone_info(user_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM clone_bots WHERE user_id = ?', (user_id,))
            conn.commit()
            if user_id in user_clones:
                del user_clones[user_id]
            logger.info(f"🗑️ Rᴇᴍᴏᴠᴇ Cʟᴏɴᴇ Bᴏᴛ Fᴏʀ Usᴇʀ {user_id} Fʀᴏᴍ DB")
        except sqlite3.Error as e:
            logger.error(f"❌ SQLite error removing clone bot for {user_id}: {e}")
        except Exception as e:
            logger.error(f"❌ Unexpected error removing clone bot for {user_id}: {e}", exc_info=True)
        finally:
            conn.close()

# ===== FORCE JOIN CONFIGURATION =====
FORCE_JOIN_CHANNEL = "@shinzu_updates"  # Permanent primary channel
FORCE_JOIN_EMOJIS = ["🔥", "💎", "✨", "👑", "🌟", "⚡", "💠", "🚀"]


def _get_force_join_channels():
    """Return the fixed channel plus all admin-added channels, without duplicates."""
    channels = [{
        "id": FORCE_JOIN_CHANNEL,
        "username": FORCE_JOIN_CHANNEL,
        "name": "Shinzu Updates",
    }]
    seen = {FORCE_JOIN_CHANNEL.lower().lstrip("@")} 

    for channel_id, info in list(mandatory_channels.items()):
        info = info or {}
        username = (info.get("username") or "").strip()
        normalized_username = username.lower().lstrip("@")
        normalized_id = str(channel_id).strip().lower()
        if normalized_username and normalized_username in seen:
            continue
        if normalized_id and normalized_id in seen:
            continue
        if normalized_username:
            seen.add(normalized_username)
        if normalized_id:
            seen.add(normalized_id)
        channels.append({
            "id": str(channel_id),
            "username": username,
            "name": info.get("name") or "Channel",
        })
    return channels


def _is_user_in_all_force_channels(user_id):
    """Return (joined_all, missing_channels); invalid channels are logged and skipped."""
    missing = []
    for channel in _get_force_join_channels():
        channel_id = channel["id"]
        target = channel.get("username") or channel_id
        # Invite URLs are for users to join; membership checks must use the numeric chat ID.
        lookup_target = channel_id if str(target).startswith(("https://", "http://")) else target
        try:
            member = bot.get_chat_member(lookup_target, user_id)
            if member.status not in ("member", "administrator", "creator"):
                missing.append(channel)
                logger.info("Force-join: user %s is not a member of %s", user_id, target)
        except Exception as exc:
            error_text = str(exc).lower()
            invalid_markers = ("chat not found", "username not found", "chat_id is empty", "peer_id_invalid")
            if any(marker in error_text for marker in invalid_markers):
                logger.error("Required force-join channel %s is unavailable; treating it as not joined: %s", target, exc)
            else:
                logger.error("Force-join membership check failed for user %s in %s: %s", user_id, target, exc)
            missing.append(channel)
    return (len(missing) == 0), missing


def _force_join_button(text, **kwargs):
    """Build a green inline button without the global text-font transformation."""
    original_init = globals().get("_orig_inl_btn_init")
    if original_init:
        button = types.InlineKeyboardButton.__new__(types.InlineKeyboardButton)
        try:
            original_init(button, text, style="success", **kwargs)
        except TypeError:
            original_init(button, text, **kwargs)
            try:
                button.style = "success"
            except Exception:
                pass
        return button
    return ikb(text, style="success", **kwargs)


def _build_force_join_markup(channels):
    """Screenshot-style layout: two green JOIN buttons per row, VERIFY full width."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    for index, channel in enumerate(channels):
        username = (channel.get("username") or "").strip().lstrip("@")
        channel_id = str(channel.get("id") or "")
        if username.startswith(("https://t.me/", "http://t.me/")):
            join_url = username
        elif username:
            join_url = f"https://t.me/{username.lstrip('@')}"
        elif channel_id.startswith("-100"):
            join_url = f"https://t.me/c/{channel_id[4:]}"
        else:
            join_url = f"https://t.me/{channel_id}"
        emoji = FORCE_JOIN_EMOJIS[index % len(FORCE_JOIN_EMOJIS)]
        markup.add(_force_join_button(f"{emoji} ᴊᴏɪɴ", url=join_url))
    markup.row(_force_join_button("✅ ᴠᴇʀɪꜰʏ", callback_data="force_join_check"))
    return markup


def _send_force_join_prompt(chat_id, missing_channels):
    """Send the small-caps force-join prompt with buttons for every required channel."""
    channels = _get_force_join_channels()
    text = (
        "🔒 ᴊᴏɪɴ ᴏᴜʀ ᴄʜᴀɴɴᴇʟꜱ\n\n"
        " ✨ sʜɪɴᴢᴜ_ᴜᴘᴅᴀᴛᴇs \n"
    )
    return bot.send_message(chat_id, text, reply_markup=_build_force_join_markup(channels))


# ===== MANDATORY CHANNELS FUNCTIONS (Channel Add) =====
# --- Mandatory Channels Functions ---
def is_user_member(user_id, channel_id):
    """Check if user is member of a channel"""
    try:
        chat_member = bot.get_chat_member(channel_id, user_id)
        return chat_member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Error checking channel membership for {user_id} in {channel_id}: {e}")
        return False

def check_mandatory_subscription(user_id):
    """Check if user is subscribed to all mandatory channels"""
    if not mandatory_channels:
        return True, []  # No mandatory channels exist
    
    not_joined = []
    for channel_id, channel_info in mandatory_channels.items():
        if not is_user_member(user_id, channel_id):
            not_joined.append((channel_id, channel_info))
    
    if not_joined:
        return False, not_joined
    return True, []

def save_mandatory_channel(channel_id, channel_username, channel_name, added_by):
    """Save mandatory channel to database."""
    channel_id = str(channel_id).strip()
    channel_username = (channel_username or "").strip()
    channel_name = (channel_name or "Channel").strip()
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            added_date = datetime.now().isoformat()
            c.execute('INSERT OR REPLACE INTO mandatory_channels (channel_id, channel_username, channel_name, added_by, added_date) VALUES (?, ?, ?, ?, ?)',
                      (channel_id, channel_username, channel_name, added_by, added_date))
            conn.commit()
            mandatory_channels[channel_id] = {
                'username': channel_username,
                'name': channel_name
            }
            logger.info(f"Saved mandatory channel: {channel_name} ({channel_id})")
            return True
        except sqlite3.Error as e:
            logger.error(f"❌ SQLite error saving channel: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error saving channel: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def remove_mandatory_channel_db(channel_id):
    """Remove mandatory channel from database."""
    channel_id = str(channel_id).strip()
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM mandatory_channels WHERE channel_id = ?', (channel_id,))
            conn.commit()
            if channel_id in mandatory_channels:
                del mandatory_channels[channel_id]
            logger.info(f"Removed mandatory channel: {channel_id}")
            return True
        except sqlite3.Error as e:
            logger.error(f"❌ SQLite error removing channel: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error removing channel: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def create_mandatory_channels_menu():
    """Reply keyboard for mandatory channel management."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2, is_persistent=True)
    markup.row(kb_button('➕ Add Channel'), kb_button('➖ Remove Channel'))
    markup.row(kb_button('📋 List Channels'))
    markup.row(kb_button('🔙 Back to Main'))
    return markup


def create_subscription_check_message(not_joined_channels):
    """Create subscription verification message"""
    message = "📢 **Iᴍᴘᴏʀᴛᴀɴᴛ: Jᴏɪɴ Oᴜʀ Cʜᴀɴɴᴇʟs Fɪʀsᴛ:**\n\n"
    
    markup = types.InlineKeyboardMarkup()
    
    for channel_id, channel_info in not_joined_channels:
        channel_username = channel_info.get('username', '')
        channel_name = channel_info.get('name', 'Channel')
        
        if channel_username.startswith(("https://t.me/", "http://t.me/")):
            channel_link = channel_username
        elif channel_username:
            channel_link = f"https://t.me/{channel_username.replace('@', '')}"
        else:
            channel_link = f"https://t.me/c/{channel_id.replace('-100', '')}"
        
        message += f"• {channel_name}\n"
        markup.add(types.InlineKeyboardButton(f"ᴊᴏɪɴ", url=channel_link))
    
    markup.add(types.InlineKeyboardButton("✅ ᴠᴇʀɪғʏ", callback_data='check_subscription_status'))
    
    return message, markup

# ===== USER MANAGEMENT DB FUNCTIONS (Ban / Custom Limits) =====
# --- Uꜱᴇʀ Mᴀɴᴀɢᴇᴍᴇɴᴛ Functions ---
def is_user_banned(user_id):
    """Check if user is banned"""
    return user_id in banned_users

def ban_user_db(user_id, reason, banned_by):
    """Ban a user"""
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            ban_date = datetime.now().isoformat()
            c.execute('INSERT OR REPLACE INTO banned_users (user_id, reason, banned_by, ban_date) VALUES (?, ?, ?, ?)',
                      (user_id, reason, banned_by, ban_date))
            conn.commit()
            banned_users.add(user_id)
            logger.warning(f"User {user_id} banned by {banned_by}. Reason: {reason}")
            return True
        except sqlite3.Error as e:
            logger.error(f"❌ SQLite error banning user {user_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error banning user {user_id}: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def unban_user_db(user_id):
    """Unban a user"""
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM banned_users WHERE user_id = ?', (user_id,))
            conn.commit()
            banned_users.discard(user_id)
            logger.info(f"User {user_id} unbanned")
            return True
        except sqlite3.Error as e:
            logger.error(f"❌ SQLite error unbanning user {user_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error unbanning user {user_id}: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def set_user_limit_db(user_id, limit, set_by):
    """Set custom file limit for a user"""
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            set_date = datetime.now().isoformat()
            c.execute('INSERT OR REPLACE INTO user_limits (user_id, file_limit, set_by, set_date) VALUES (?, ?, ?, ?)',
                      (user_id, limit, set_by, set_date))
            conn.commit()
            user_limits[user_id] = limit
            logger.info(f"Set file limit {limit} for user {user_id} by {set_by}")
            return True
        except sqlite3.Error as e:
            logger.error(f"❌ SQLite error setting limit for user {user_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error setting limit for user {user_id}: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def remove_user_limit_db(user_id):
    """Remove custom file limit for a user"""
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM user_limits WHERE user_id = ?', (user_id,))
            conn.commit()
            if user_id in user_limits:
                del user_limits[user_id]
            logger.info(f"Removed custom limit for user {user_id}")
            return True
        except sqlite3.Error as e:
            logger.error(f"❌ SQLite error removing limit for user {user_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error removing limit for user {user_id}: {e}", exc_info=True)
            return False
        finally:
            conn.close()

# ===== INITIALIZE DATABASE AND LOAD DATA =====
init_db()
load_data()

# ===== USER FOLDER MANAGEMENT =====
def get_user_folder(user_id):
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

# ===== USER FILE LIMIT =====
def get_user_file_limit(user_id):
    if user_id == OWNER_ID: return OWNER_LIMIT
    if user_id in user_limits: return user_limits[user_id]
    if user_id in admin_ids: return ADMIN_LIMIT
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT

# ===== USER FILE COUNT =====
def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))

# ===== CHECK IF BOT IS RUNNING =====
def is_bot_running(script_owner_id, file_name):
    script_key = f"{script_owner_id}_{file_name}"
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            is_running = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
            if not is_running:
                logger.warning(f"⚠️ Pʀᴏᴄᴇss {script_info['process'].pid} Fᴏʀ {script_key} Fᴏᴜɴᴅ Iɴ Mᴇᴍᴏʀʏ Bᴜᴛ Nᴏᴛ Rᴜɴɴɪɴɢ/Zᴏᴍʙɪʟ Cʟᴇᴀɴɪɴɢ Uᴘ.")
                if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                    try:
                        script_info['log_file'].close()
                    except Exception as log_e:
                        logger.error(f"❌ Error closing log file during zombie cleanup {script_key}: {log_e}")
                if script_key in bot_scripts:
                    del bot_scripts[script_key]
            return is_running
        except psutil.NoSuchProcess:
            logger.warning(f"⚠️ Pʀᴏᴄᴇss Fᴏʀ {script_key} Nᴏᴛ Fᴏᴜɴᴅ (NᴏsᴜᴄʜPʀᴏᴄᴇss). Cʟᴇᴀɴɪɴɢ Uᴘ.")
            if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                try:
                     script_info['log_file'].close()
                except Exception as log_e:
                     logger.error(f"❌ Error closing log file during cleanup of non-existent process {script_key}: {log_e}")
            if script_key in bot_scripts:
                 del bot_scripts[script_key]
            return False
        except Exception as e:
            logger.error(f"❌ Error checking process status for {script_key}: {e}", exc_info=True)
            return False
    return False

# ===== KILL PROCESS TREE =====
def kill_process_tree(process_info):
    pid = None
    log_file_closed = False
    script_key = process_info.get('script_key', 'N/A')

    try:
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close') and not process_info['log_file'].closed:
            try:
                process_info['log_file'].close()
                log_file_closed = True
                logger.info(f"📜 Cʟᴏsᴇᴅ Lᴏɢ Fɪʟᴇ Fᴏʀ {script_key} (Pɪᴅ: {process_info.get('process', {}).get('pid', 'N/A')})")
            except Exception as log_e:
                logger.error(f"❌ Eʀʀᴏʀ Cʟᴏsɪɴɢ Lᴏɢ Fɪʟᴇ Dᴜʀɪɴɢ Kɪʟʟ Cʟᴏsɪɴɢ Lᴏɢ Fɪʟᴇ Dᴜʀɪɴɢ Kɪʟʟ Fᴏʀ {script_key}: {log_e}")

        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
           pid = process.pid
           if pid:
                try:
                    parent = psutil.Process(pid)
                    children = parent.children(recursive=True)
                    logger.info(f"🔪 Aᴛᴛᴇᴍᴘᴛɪɴɢ Tᴏ Kɪʟʟ Pʀᴏᴄᴇss Tʀᴇᴇ Fᴏʀ {script_key} (Pɪᴅ: {pid}, Cʜɪʟᴅʀᴇɴ: {[c.pid for c in children]})")

                    for child in children:
                        try:
                            child.terminate()
                            logger.info(f"🔪 Tʀᴇᴍɪɴᴀᴛᴇᴅ Cʜɪʟᴅ Pʀᴏᴄᴇss {child.pid} Fᴏʀ {script_key}")
                        except psutil.NoSuchProcess:
                            logger.warning(f"⚠️ Cʜɪʟᴅ Pʀᴏᴄᴇss {child.pid} Fᴏʀ {script_key} Aʟʀᴇᴀᴅʏ Gᴏɴᴇ.")
                        except Exception as e:
                            logger.error(f"❌ Eʀʀᴏʀ Tʀᴇᴍɪɴᴀᴛᴇᴅ Cʜɪʟᴅ {child.pid} Fᴏʀ {script_key}: {e}. Tʀʏɪɴɢ Kɪʟʟ...")
                            try: child.kill(); logger.info(f"💀 Kɪʟʟᴇᴅ Cʜɪʟᴅ Pʀᴏᴄᴇss {child.pid} Fᴏʀ {script_key}")
                            except Exception as e2: logger.error(f"❌ Fᴀɪʟᴇᴅ Tᴏ Kɪʟʟ Cʜɪʟᴅ  to  {child.pid} for {script_key}: {e2}")

                    gone, alive = psutil.wait_procs(children, timeout=1)
                    for p in alive:
                        logger.warning(f"⚠️ Child process {p.pid} for {script_key} still alive. Killing.")
                        try: p.kill()
                        except Exception as e: logger.error(f"❌ Failed to kill child {p.pid} for {script_key} after wait: {e}")

                    try:
                        parent.terminate()
                        logger.info(f"🔪 Terminated parent process {pid} for {script_key}")
                        try: parent.wait(timeout=1)
                        except psutil.TimeoutExpired:
                            logger.warning(f"⚠️ Parent process {pid} for {script_key} did not terminate. Killing.")
                            parent.kill()
                            logger.info(f"💀 Killed parent process {pid} for {script_key}")
                    except psutil.NoSuchProcess:
                        logger.warning(f"⚠️ Parent process {pid} for {script_key} already gone.")
                    except Exception as e:
                        logger.error(f"❌ Error terminating parent {pid} for {script_key}: {e}. Trying kill...")
                        try: parent.kill(); logger.info(f"💀 Killed parent process {pid} for {script_key}")
                        except Exception as e2: logger.error(f"❌ Failed to kill parent {pid} for {script_key}: {e2}")

                except psutil.NoSuchProcess:
                    logger.warning(f"⚠️ Process {pid or 'N/A'} for {script_key} not found during kill. Already terminated?")
           else: logger.error(f"❌ Process PID is None for {script_key}.")
        elif log_file_closed: logger.warning(f"⚠️ Process object missing for {script_key}, but log file closed.")
        else: logger.error(f"❌ Process object missing for {script_key}, and no log file. Cannot kill.")
    except Exception as e:
        logger.error(f"❌ Unexpected error killing process tree for PID {pid or 'N/A'} ({script_key}): {e}", exc_info=True)

# ===== PYTHON MODULES MAPPING =====
TELEGRAM_MODULES = {
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'python_telegram_bot': 'python-telegram-bot',
    'aiogram': 'aiogram',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'telethon.sync': 'telethon',
    'from telethon.sync import telegramclient': 'telethon',
    'telepot': 'telepot',
    'pytg': 'pytg',
    'tgcrypto': 'tgcrypto',
    'telegram_upload': 'telegram-upload',
    'telegram_send': 'telegram-send',
    'telegram_text': 'telegram-text',
    'tl': 'telethon',
    'telegram_utils': 'telegram-utils',
    'telegram_logger': 'telegram-logger',
    'telegram_handlers': 'python-telegram-handlers',
    'telegram_redis': 'telegram-redis',
    'telegram_sqlalchemy': 'telegram-sqlalchemy',
    'telegram_payment': 'telegram-payment',
    'telegram_shop': 'telegram-shop-sdk',
    'pytest_telegram': 'pytest-telegram',
    'telegram_debug': 'telegram-debug',
    'telegram_scraper': 'telegram-scraper',
    'telegram_analytics': 'telegram-analytics',
    'telegram_nlp': 'telegram-nlp-toolkit',
    'telegram_ai': 'telegram-ai',
    'telegram_api': 'telegram-api-client',
    'telegram_web': 'telegram-web-integration',
    'telegram_games': 'telegram-games',
    'telegram_quiz': 'telegram-quiz-bot',
    'telegram_ffmpeg': 'telegram-ffmpeg',
    'telegram_media': 'telegram-media-utils',
    'telegram_2fa': 'telegram-twofa',
    'telegram_crypto': 'telegram-crypto-bot',
    'telegram_i18n': 'telegram-i18n',
    'telegram_translate': 'telegram-translate',
    'bs4': 'beautifulsoup4',
    'requests': 'requests',
    'pillow': 'Pillow',
    'cv2': 'opencv-python',
    'yaml': 'PyYAML',
    'dotenv': 'python-dotenv',
    'dateutil': 'python-dateutil',
    'pandas': 'pandas',
    'numpy': 'numpy',
    'flask': 'Flask',
    'django': 'Django',
    'sqlalchemy': 'SQLAlchemy',
    'asyncio': None,
    'json': None,
    'datetime': None,
    'os': None,
    'sys': None,
    're': None,
    'time': None,
    'math': None,
    'random': None,
    'logging': None,
    'threading': None,
    'subprocess': None,
    'zipfile': None,
    'tempfile': None,
    'shutil': None,
    'sqlite3': None,
    'psutil': 'psutil',
    'atexit': None
}

# ===== MODULE INSTALL (auto + manual, with install logs) =====
# --- Manual Modules Installation System ---
def save_install_log(user_id, module_name, package_name, status, log):
    """Save installation log to database"""
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            install_date = datetime.now().isoformat()
            c.execute('INSERT INTO install_logs (user_id, module_name, package_name, status, log, install_date) VALUES (?, ?, ?, ?, ?, ?)',
                      (user_id, module_name, package_name, status, log, install_date))
            conn.commit()
            logger.info(f"Saved install log for user {user_id}: {module_name} - {status}")
        except sqlite3.Error as e:
            logger.error(f"❌ SQLite error saving install log: {e}")
        except Exception as e:
            logger.error(f"❌ Unexpected error saving install log: {e}", exc_info=True)
        finally:
            conn.close()

def attempt_install_pip(module_name, message, manual_request=False):
    """Install Python package via pip"""
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name) 
    if package_name is None: 
        logger.info(f"Module '{module_name}' is core. Skipping pip install.")
        return False, "Core module - no installation needed"
    
    try:
        if manual_request:
            bot.reply_to(message, f"""
╔══════════════════╗
║🔄 𝐌𝐀𝐍𝐔𝐀𝐋 𝐈𝐍𝐒𝐓𝐀𝐋𝐋𝐀𝐓𝐈𝐎𝐍 
║𝐑𝐄𝐐𝐔𝐄𝐒𝐓𝐄𝐃 𝐅𝐎𝐑
║`{module_name}` -> `{package_name}`
╚══════════════════╝
""", parse_mode='Markdown')
        else:
            bot.reply_to(message, f"""
╔══════════════════╗
║🐍 𝐌𝐎𝐃𝐔𝐋𝐄 
║`{module_name}` 𝐍𝐎𝐓 𝐅𝐎𝐔𝐍𝐃
╚══════════════════╝""", parse_mode='Markdown')
        
        command = [sys.executable, '-m', 'pip', 'install', package_name]
        logger.info(f"Running install: {' '.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
        
        if result.returncode == 0:
            log_msg = f"Installed {package_name}. Output:\n{result.stdout}"
            logger.info(log_msg)
            success_msg = f"""
╔══════════════════╗
║✅ Pᴀᴄᴋᴀɢᴇ `{package_name}`
║Iɴsᴛᴀʟʟᴇᴅ Sᴜᴄᴄᴇssғᴜʟʟʏ
╚══════════════════╝"""
            bot.reply_to(message, success_msg, parse_mode='Markdown')
            save_install_log(message.from_user.id, module_name, package_name, "success", log_msg)
            return True, log_msg
        else:
            error_msg = f"❌ 𝐅𝐀𝐋𝐄𝐃 𝐓𝐎 𝐈𝐍𝐀𝐓𝐀𝐋𝐋`{package_name}` 𝐅𝐎𝐑 `{module_name}`.\n𝐋𝐎𝐆\n```\n{result.stderr or result.stdout}\n```"
            logger.error(error_msg)
            if len(error_msg) > 4000: error_msg = error_msg[:4000] + "\n... (Log truncated)"
            bot.reply_to(message, error_msg, parse_mode='Markdown')
            save_install_log(message.from_user.id, module_name, package_name, "failed", error_msg)
            return False, error_msg
    except Exception as e:
        error_msg = f"❌ 𝐄𝐑𝐑𝐎𝐑 𝐈𝐍𝐒𝐓𝐀𝐋𝐋𝐈𝐍𝐆 `{package_name}`: {str(e)}"
        logger.error(error_msg, exc_info=True)
        bot.reply_to(message, error_msg)
        save_install_log(message.from_user.id, module_name, package_name, "error", error_msg)
        return False, error_msg

def attempt_install_npm(module_name, user_folder, message, manual_request=False):
    """Install Node package via npm"""
    try:
        if manual_request:
            bot.reply_to(message, f"🔄 Manual Node package installation requested for `{module_name}`...", parse_mode='Markdown')
        else:
            bot.reply_to(message, f"🟠 Node package `{module_name}` not found. Installing locally...", parse_mode='Markdown')
        
        command = ['npm', 'install', module_name]
        logger.info(f"Running npm install: {' '.join(command)} in {user_folder}")
        result = subprocess.run(command, capture_output=True, text=True, check=False, cwd=user_folder, encoding='utf-8', errors='ignore')
        
        if result.returncode == 0:
            log_msg = f"Installed {module_name}. Output:\n{result.stdout}"
            logger.info(log_msg)
            success_msg = f"✅ Node package `{module_name}` installed locally."
            bot.reply_to(message, success_msg, parse_mode='Markdown')
            save_install_log(message.from_user.id, module_name, module_name, "success", log_msg)
            return True, log_msg
        else:
            error_msg = f"❌ Failed to install Node package `{module_name}`.\nLog:\n```\n{result.stderr or result.stdout}\n```"
            logger.error(error_msg)
            if len(error_msg) > 4000: error_msg = error_msg[:4000] + "\n... (Log truncated)"
            bot.reply_to(message, error_msg, parse_mode='Markdown')
            save_install_log(message.from_user.id, module_name, module_name, "failed", error_msg)
            return False, error_msg
    except FileNotFoundError:
         error_msg = "❌ Error: 'npm' not found. Ensure Node.js/npm are installed and in PATH."
         logger.error(error_msg)
         bot.reply_to(message, error_msg)
         save_install_log(message.from_user.id, module_name, module_name, "error", error_msg)
         return False, error_msg
    except Exception as e:
        error_msg = f"❌ Error installing Node package `{module_name}`: {str(e)}"
        logger.error(error_msg, exc_info=True)
        bot.reply_to(message, error_msg)
        save_install_log(message.from_user.id, module_name, module_name, "error", error_msg)
        return False, error_msg

# ===== RUN PYTHON SCRIPT =====
def run_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj_for_reply, f"❌ Failed to run '{file_name}' after {max_attempts} attempts. Check logs.")
        return

    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"🐍 Attempt {attempt} to run Python script: {script_path} (Key: {script_key}) for user {script_owner_id}")

    try:
        if not os.path.exists(script_path):
             bot.reply_to(message_obj_for_reply, f"⚠️ File not found.")
             logger.error(f"❌ Script not found: {script_path} for user {script_owner_id}")
             if script_owner_id in user_files:
                 user_files[script_owner_id] = [f for f in user_files.get(script_owner_id, []) if f[0] != file_name]
             remove_user_file_db(script_owner_id, file_name)
             return

        if attempt == 1:
            check_command = [sys.executable, script_path]
            logger.info(f"🔍 Running Python pre-check: {' '.join(check_command)}")
            check_proc = None
            try:
                check_proc = subprocess.Popen(check_command, cwd=user_folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
                stdout, stderr = check_proc.communicate(timeout=5)
                return_code = check_proc.returncode
                logger.info(f"ℹ️ Python Pre-check early. RC: {return_code}. Stderr: {stderr[:200]}...")
                if return_code != 0 and stderr:
                    match_py = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                    if match_py:
                        module_name = match_py.group(1).strip().strip("'\"")
                        logger.info(f"📦 Detected missing Python module: {module_name}")
                        _pip_ok, _pip_log = attempt_install_pip(module_name, message_obj_for_reply)
                        if _pip_ok:
                            logger.info(f"✅ Install OK for {module_name}. Retrying run_script...")
                            bot.reply_to(message_obj_for_reply, f"✅ Install successful. Retrying '{file_name}'...")
                            time.sleep(2)
                            threading.Thread(target=run_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt + 1)).start()
                            return
                        else:
                            bot.reply_to(message_obj_for_reply, f"❌ Install failed. Cannot run '{file_name}'.")
                            return
                    else:
                         error_summary = stderr[:500]
                         bot.reply_to(message_obj_for_reply, f"⚠️ Error in script pre-check for '{file_name}':\n```\n{error_summary}\n```\nFix the script.", parse_mode='Markdown')
                         return
            except subprocess.TimeoutExpired:
                logger.info("⏱️ Python Pre-check timed out (>5s), imports likely OK. Killing check process.")
                if check_proc and check_proc.poll() is None: check_proc.kill(); check_proc.communicate()
                logger.info("✅ Python Check process killed. Proceeding to long run.")
            except FileNotFoundError:
                 logger.error(f"❌ Python interpreter not found: {sys.executable}")
                 bot.reply_to(message_obj_for_reply, f"❌ Error: Python interpreter '{sys.executable}' not found.")
                 return
            except Exception as e:
                 logger.error(f"❌ Error in Python pre-check for {script_key}: {e}", exc_info=True)
                 bot.reply_to(message_obj_for_reply, f"⚠️ Unexpected error in script pre-check for '{file_name}': {e}")
                 return
            finally:
                 if check_proc and check_proc.poll() is None:
                     logger.warning(f"⚠️ Python Check process {check_proc.pid} still running. Killing.")
                     check_proc.kill(); check_proc.communicate()

        logger.info(f"🚀 Starting long-running Python process for {script_key}")
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = None; process = None
        try: log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
             logger.error(f"❌ Failed to open log file '{log_file_path}' for {script_key}: {e}", exc_info=True)
             bot.reply_to(message_obj_for_reply, f"❌ Failed to open log file '{log_file_path}': {e}")
             return
        try:
            startupinfo = None; creationflags = 0
            if os.name == 'nt':
                 startupinfo = subprocess.STARTUPINFO(); startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                 startupinfo.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen(
                [sys.executable, script_path], cwd=user_folder, stdout=log_file, stderr=log_file,
                stdin=subprocess.PIPE, startupinfo=startupinfo, creationflags=creationflags,
                encoding='utf-8', errors='ignore'
            )
            logger.info(f"✅ Started Python process {process.pid} for {script_key}")
            bot_scripts[script_key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj_for_reply.chat.id,
                'script_owner_id': script_owner_id,
                'start_time': datetime.now(), 'user_folder': user_folder, 'type': 'py', 'script_key': script_key
            }
            bot.reply_to(message_obj_for_reply, f"✅ Python script '{file_name}' started! (PID: {process.pid})")
        except FileNotFoundError:
             logger.error(f"❌ Python interpreter {sys.executable} not found for long run {script_key}")
             bot.reply_to(message_obj_for_reply, f"❌ Error: Python interpreter '{sys.executable}' not found.")
             if log_file and not log_file.closed: log_file.close()
             if script_key in bot_scripts: del bot_scripts[script_key]
        except Exception as e:
            if log_file and not log_file.closed: log_file.close()
            error_msg = f"❌ Error starting Python script '{file_name}': {str(e)}"
            logger.error(error_msg, exc_info=True)
            bot.reply_to(message_obj_for_reply, error_msg)
            if process and process.poll() is None:
                 logger.warning(f"⚠️ Killing potentially started Python process {process.pid} for {script_key}")
                 kill_process_tree({'process': process, 'log_file': log_file, 'script_key': script_key})
            if script_key in bot_scripts: del bot_scripts[script_key]
    except Exception as e:
        error_msg = f"❌ Unexpected error running Python script '{file_name}': {str(e)}"
        logger.error(error_msg, exc_info=True)
        bot.reply_to(message_obj_for_reply, error_msg)
        if script_key in bot_scripts:
             logger.warning(f"⚠️ Cleaning up {script_key} due to error in run_script.")
             kill_process_tree(bot_scripts[script_key])
             del bot_scripts[script_key]

# ===== RUN JAVASCRIPT SCRIPT =====
def run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj_for_reply, f"❌ Failed to run '{file_name}' after {max_attempts} attempts. Check logs.")
        return

    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"📜 Attempt {attempt} to run JS script: {script_path} (Key: {script_key}) for user {script_owner_id}")

    try:
        if not os.path.exists(script_path):
             bot.reply_to(message_obj_for_reply, f"⚠️ File not found.")
             logger.error(f"❌ JS Script not found: {script_path} for user {script_owner_id}")
             if script_owner_id in user_files:
                 user_files[script_owner_id] = [f for f in user_files.get(script_owner_id, []) if f[0] != file_name]
             remove_user_file_db(script_owner_id, file_name)
             return

        if attempt == 1:
            check_command = ['node', script_path]
            logger.info(f"🔍 Running JS pre-check: {' '.join(check_command)}")
            check_proc = None
            try:
                check_proc = subprocess.Popen(check_command, cwd=user_folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
                stdout, stderr = check_proc.communicate(timeout=5)
                return_code = check_proc.returncode
                logger.info(f"ℹ️ JS Pre-check early. RC: {return_code}. Stderr: {stderr[:200]}...")
                if return_code != 0 and stderr:
                    match_js = re.search(r"Cannot find module '(.+?)'", stderr)
                    if match_js:
                        module_name = match_js.group(1).strip().strip("'\"")
                        if not module_name.startswith('.') and not module_name.startswith('/'):
                             logger.info(f"📦 Detected missing Node module: {module_name}")
                             _npm_ok, _npm_log = attempt_install_npm(module_name, user_folder, message_obj_for_reply)
                             if _npm_ok:
                                 logger.info(f"✅ NPM Install OK for {module_name}. Retrying run_js_script...")
                                 bot.reply_to(message_obj_for_reply, f"✅ NPM Install successful. Retrying '{file_name}'...")
                                 time.sleep(2)
                                 threading.Thread(target=run_js_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt + 1)).start()
                                 return
                             else:
                                 bot.reply_to(message_obj_for_reply, f"❌ NPM Install failed. Cannot run '{file_name}'.")
                                 return
                        else: logger.info(f"ℹ️ Skipping npm install for relative/core: {module_name}")
                    error_summary = stderr[:500]
                    bot.reply_to(message_obj_for_reply, f"⚠️ Error in JS script pre-check for '{file_name}':\n```\n{error_summary}\n```\nFix script or install manually.", parse_mode='Markdown')
                    return
            except subprocess.TimeoutExpired:
                logger.info("⏱️ JS Pre-check timed out (>5s), imports likely OK. Killing check process.")
                if check_proc and check_proc.poll() is None: check_proc.kill(); check_proc.communicate()
                logger.info("✅ JS Check process killed. Proceeding to long run.")
            except FileNotFoundError:
                 error_msg = "❌ Error: 'node' not found. Ensure Node.js is installed for JS files."
                 logger.error(error_msg)
                 bot.reply_to(message_obj_for_reply, error_msg)
                 return
            except Exception as e:
                 logger.error(f"❌ Error in JS pre-check for {script_key}: {e}", exc_info=True)
                 bot.reply_to(message_obj_for_reply, f"⚠️ Unexpected error in JS pre-check for '{file_name}': {e}")
                 return
            finally:
                 if check_proc and check_proc.poll() is None:
                     logger.warning(f"⚠️ JS Check process {check_proc.pid} still running. Killing.")
                     check_proc.kill(); check_proc.communicate()

        logger.info(f"🚀 Starting long-running JS process for {script_key}")
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = None; process = None
        try: log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
            logger.error(f"❌ Failed to open log file '{log_file_path}' for JS script {script_key}: {e}", exc_info=True)
            bot.reply_to(message_obj_for_reply, f"❌ Failed to open log file '{log_file_path}': {e}")
            return
        try:
            startupinfo = None; creationflags = 0
            if os.name == 'nt':
                 startupinfo = subprocess.STARTUPINFO(); startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                 startupinfo.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen(
                ['node', script_path], cwd=user_folder, stdout=log_file, stderr=log_file,
                stdin=subprocess.PIPE, startupinfo=startupinfo, creationflags=creationflags,
                encoding='utf-8', errors='ignore'
            )
            logger.info(f"✅ Started JS process {process.pid} for {script_key}")
            bot_scripts[script_key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj_for_reply.chat.id,
                'script_owner_id': script_owner_id,
                'start_time': datetime.now(), 'user_folder': user_folder, 'type': 'js', 'script_key': script_key
            }
            bot.reply_to(message_obj_for_reply, f"✅ JS script '{file_name}' started! (PID: {process.pid})")
        except FileNotFoundError:
             error_msg = "❌ Error: 'node' not found for long run. Ensure Node.js is installed."
             logger.error(error_msg)
             if log_file and not log_file.closed: log_file.close()
             bot.reply_to(message_obj_for_reply, error_msg)
             if script_key in bot_scripts: del bot_scripts[script_key]
        except Exception as e:
            if log_file and not log_file.closed: log_file.close()
            error_msg = f"❌ Error starting JS script '{file_name}': {str(e)}"
            logger.error(error_msg, exc_info=True)
            bot.reply_to(message_obj_for_reply, error_msg)
            if process and process.poll() is None:
                 logger.warning(f"⚠️ Killing potentially started JS process {process.pid} for {script_key}")
                 kill_process_tree({'process': process, 'log_file': log_file, 'script_key': script_key})
            if script_key in bot_scripts: del bot_scripts[script_key]
    except Exception as e:
        error_msg = f"❌ Unexpected error running JS script '{file_name}': {str(e)}"
        logger.error(error_msg, exc_info=True)
        bot.reply_to(message_obj_for_reply, error_msg)
        if script_key in bot_scripts:
             logger.warning(f"⚠️ Cleaning up {script_key} due to error in run_js_script.")
             kill_process_tree(bot_scripts[script_key])
             del bot_scripts[script_key]

# ===== DATABASE LOCK =====
DB_LOCK = threading.Lock()

# ===== SAVE USER FILE =====
def save_user_file(user_id, file_name, file_type='py'):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type) VALUES (?, ?, ?)',
                      (user_id, file_name, file_type))
            conn.commit()
            if user_id not in user_files: user_files[user_id] = []
            user_files[user_id] = [(fn, ft) for fn, ft in user_files[user_id] if fn != file_name]
            user_files[user_id].append((file_name, file_type))
            logger.info(f"💾 Saved file '{file_name}' ({file_type}) for user {user_id}")
        except sqlite3.Error as e: logger.error(f"❌ SQLite error saving file for user {user_id}, {file_name}: {e}")
        except Exception as e: logger.error(f"❌ Unexpected error saving file for {user_id}, {file_name}: {e}", exc_info=True)
        finally: conn.close()

# ===== REMOVE USER FILE =====
def remove_user_file_db(user_id, file_name):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
            conn.commit()
            if user_id in user_files:
                user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
                if not user_files[user_id]: del user_files[user_id]
            logger.info(f"🗑️ Removed file '{file_name}' for user {user_id} from DB")
        except sqlite3.Error as e: logger.error(f"❌ SQLite error removing file for {user_id}, {file_name}: {e}")
        except Exception as e: logger.error(f"❌ Unexpected error removing file for {user_id}, {file_name}: {e}", exc_info=True)
        finally: conn.close()

# ===== ADD ACTIVE USER =====
def add_active_user(user_id):
    active_users.add(user_id)
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
            conn.commit()
            logger.info(f"👥 Added/Confirmed active user {user_id} in DB")
        except sqlite3.Error as e: logger.error(f"❌ SQLite error adding active user {user_id}: {e}")
        except Exception as e: logger.error(f"❌ Unexpected error adding active user {user_id}: {e}", exc_info=True)
        finally: conn.close()

# ===== SAVE SUBSCRIPTION =====
def save_subscription(user_id, expiry):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            expiry_str = expiry.isoformat()
            c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)', (user_id, expiry_str))
            conn.commit()
            user_subscriptions[user_id] = {'expiry': expiry}
            logger.info(f"💳 Saved subscription for {user_id}, expiry {expiry_str}")
        except sqlite3.Error as e: logger.error(f"❌ SQLite error saving subscription for {user_id}: {e}")
        except Exception as e: logger.error(f"❌ Unexpected error saving subscription for {user_id}: {e}", exc_info=True)
        finally: conn.close()

# ===== REMOVE SUBSCRIPTION =====
def remove_subscription_db(user_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
            conn.commit()
            if user_id in user_subscriptions: del user_subscriptions[user_id]
            logger.info(f"🗑️ Removed subscription for {user_id} from DB")
        except sqlite3.Error as e: logger.error(f"❌ SQLite error removing subscription for {user_id}: {e}")
        except Exception as e: logger.error(f"❌ Unexpected error removing subscription for {user_id}: {e}", exc_info=True)
        finally: conn.close()

# ===== ADD ADMIN =====
def add_admin_db(admin_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
            conn.commit()
            admin_ids.add(admin_id)
            logger.info(f"👑 Added admin {admin_id} to DB")
        except sqlite3.Error as e: logger.error(f"❌ SQLite error adding admin {admin_id}: {e}")
        except Exception as e: logger.error(f"❌ Unexpected error adding admin {admin_id}: {e}", exc_info=True)
        finally: conn.close()

# ===== REMOVE ADMIN =====
def remove_admin_db(admin_id):
    if admin_id == OWNER_ID:
        logger.warning("⚠️ Attempted to remove OWNER_ID from admins.")
        return False
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        removed = False
        try:
            c.execute('SELECT 1 FROM admins WHERE user_id = ?', (admin_id,))
            if c.fetchone():
                c.execute('DELETE FROM admins WHERE user_id = ?', (admin_id,))
                conn.commit()
                removed = c.rowcount > 0
                if removed: admin_ids.discard(admin_id); logger.info(f"🗑️ Removed admin {admin_id} from DB")
                else: logger.warning(f"⚠️ Admin {admin_id} found but delete affected 0 rows.")
            else:
                logger.warning(f"⚠️ Admin {admin_id} not found in DB.")
                admin_ids.discard(admin_id)
            return removed
        except sqlite3.Error as e: logger.error(f"❌ SQLite error removing admin {admin_id}: {e}"); return False
        except Exception as e: logger.error(f"❌ Unexpected error removing admin {admin_id}: {e}", exc_info=True); return False
        finally: conn.close()

# ===== CREATE MAIN MENU KEYBOARD =====
# ===== BUTTON COLOR STYLES (Bot API 9.4+, telebot 4.36+ required) =====
# "primary" = blue | "success" = green | "danger" = red
BUTTON_STYLES = {
    # USER MENU — Primary (blue)
    "📢 Uᴘᴅᴀᴛᴇꜱ Cʜᴀɴɴᴇʟ": "primary",
    "📂 Mʏ Fɪʟᴇs": "primary",
    "⚡ Bᴏᴛ Sᴘᴇᴇᴅ": "primary",
    "⚙️ Rᴇᴄᴏᴍᴍᴇɴᴅᴇᴅ Iɴsᴛᴀʟʟ": "primary",
    "🌐 Gɪᴛ Cʟᴏɴᴇ": "primary",
    "🟣 Dɪsᴄᴏʀᴅ Bᴏᴛ": "primary",
    "🚀 API": "primary",
    "💻 Lᴏᴄᴀʟ Hᴏsᴛ": "primary",
    "👤 Mʏ Aᴄᴄᴏᴜɴᴛ": "primary",
    # USER MENU — Success (green)
    "🌍 Uᴘʟᴏᴀᴅ": "success",
    "🚀 Sᴛᴀᴛᴜs": "success",
    "🔄 Rᴇsᴛᴀʀᴛ": "success",
    "🤖 AI Aɢᴇɴᴛ": "success",
    "📱 WʜᴀᴛsAᴘᴘ Bᴏᴛ": "success",
    "🌍 Wᴇʙsɪᴛᴇ": "success",
    "💳 Bᴜʏ Pʟᴀɴ": "success",
    "🛒 Sᴛᴏʀᴇ": "success",
    # USER MENU — Danger (red)
    "⏹ Sᴛᴏᴘ": "danger",
    "📞 Cᴏɴᴛᴀᴄᴛ Oᴡɴᴇʀ": "danger",
    # ADMIN (preserved)
    "📤 Uᴘʟᴏᴀᴅ Fɪʟᴇ": "success",
    "📂 Cʜᴇᴄᴋ Fɪʟᴇs": "primary",
    "📊 Sᴛᴀᴛɪsᴛɪᴄs": "primary",
    "⌨️ Sᴇɴᴅ Cᴏᴍᴍᴀɴᴅ": "primary",
    "🤖 Cʀᴇᴀᴛᴇ Cʟᴏɴᴇ": "primary",
    "📦 Mᴀɴᴜᴀʟ Iɴꜱᴛᴀʟʟ": "primary",
    "💳 Sᴜʙsᴄʀɪᴘᴛɪᴏɴs": "primary",
    "📢 Bʀᴏᴀᴅᴄᴀsᴛ": "primary",
    "🔒 Lᴏᴄᴋ Bᴏᴛ": "danger",
    "🟢 Rᴜɴ Aʟʟ Sᴄʀɪᴘᴛs": "success",
    "👑 Admin Panel": "primary",
    "📤 Host Upload": "success",
    "📁 Host Projects": "primary",
    "⚡ Host Recommended": "primary",
    "🔙 Host Back": "danger",
    "📢 Cʜᴀɴɴᴇʟ Aᴅᴅ": "primary",
    "🛠️ Mᴀɴᴜᴀʟ Iɴꜱᴛᴀʟʟ": "primary",
    "👥 Uꜱᴇʀ Mᴀɴᴀɢᴇᴍᴇɴᴛ": "danger",
    "⚙️ Sᴇᴛᴛɪɴɢs": "primary",
    "🔄 Rᴇᴛᴀʀᴛ": "primary",
    "🔄 Rᴇᴀᴛᴀʀᴛ": "primary",
    "📦 Pᴋɢ Iɴꜱᴛᴀʟʟ": "primary",
    "🌐 Gɪᴛʜᴜʙ Rᴇᴘᴏ": "primary",
    # Sub-menu reply keyboard buttons (hosting / pkg / admin)
    "📤 Upload": "success",
    "📁 Projects": "primary",
    "⚡ Recommended Install": "primary",
    "🌍 Upload": "success",
    "📂 My Projects": "primary",
    "⚙️ Recommended Install": "primary",
    "✅ Install Recommended": "success",
    "❌ Cancel": "danger",
    "🔙 Back": "danger",
    "➕ Add Admin": "success",
    "➖ Remove Admin": "danger",
    "📋 List Admins": "primary",
    "🤖 Clone Control": "primary",
    "📤 Upload File": "success",
    "📂 Manage Files": "primary",
    "🚫 Ban User": "danger",
    "✅ Unban User": "success",
    "📊 User Info": "primary",
    "👥 All Users": "primary",
    "🔧 Set User Limit": "primary",
    "🗑️ Remove User Limit": "danger",
    "📊 System Info": "primary",
    "📈 Bot Performance": "primary",
    "🧹 Cleanup Files": "danger",
    "📋 Installation Logs": "primary",
    "📦 Send Module Name": "primary",
}



def ikb(text, style=None, **kwargs):
    """InlineKeyboardButton with optional color style (primary/success/danger)."""
    try:
        if style:
            return types.InlineKeyboardButton(text, style=style, **kwargs)
        return types.InlineKeyboardButton(text, **kwargs)
    except TypeError:
        return types.InlineKeyboardButton(text, **kwargs)

def kb_button(text, style=None):
    """Reply-keyboard button with colour style (primary/success/danger)."""
    if style is None:
        style = BUTTON_STYLES.get(text)
    if style is None:
        # Flexible match: ignore mixed-font / case differences
        plain = text.translate(_FONT_TO_ASCII).lower() if text else ''
        for key, val in BUTTON_STYLES.items():
            if key.translate(_FONT_TO_ASCII).lower() == plain:
                style = val
                break
    try:
        if style:
            btn = types.KeyboardButton(text, style=style)
        else:
            btn = types.KeyboardButton(text)
    except TypeError:
        btn = types.KeyboardButton(text)
    # Ensure style survives even if constructor ignored the kwarg
    if style:
        try:
            btn.style = style
        except Exception:
            pass
    return btn


def ikb(text, style=None, **kwargs):
    """InlineKeyboardButton with optional colour style (primary/success/danger)."""
    try:
        if style:
            return types.InlineKeyboardButton(text, style=style, **kwargs)
        return types.InlineKeyboardButton(text, **kwargs)
    except TypeError:
        # Older pyTelegramBotAPI without style support
        return types.InlineKeyboardButton(text, **kwargs)

def create_reply_keyboard_main_menu(user_id):
    """Build main ReplyKeyboard. Always uses the latest layout lists."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2, is_persistent=True)
    is_admin = (user_id in admin_ids) or (user_id == OWNER_ID)
    layout_to_use = ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC if is_admin else COMMAND_BUTTONS_LAYOUT_USER_SPEC
    if IS_CLONE:
        layout_to_use = [row for row in layout_to_use if '👑 Admin Panel' not in row]
    for row_buttons_text in layout_to_use:
        btns = []
        for text in row_buttons_text:
            try:
                btns.append(kb_button(text))
            except Exception:
                btns.append(types.KeyboardButton(text))
        if len(btns) == 1:
            markup.row(btns[0])  # full-width (e.g. My Account)
        else:
            markup.row(*btns)
    return markup

# ===== CREATE CONTROL BUTTONS (with AI Fix) =====
def create_control_buttons(script_owner_id, file_name, is_running=True):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.row(
            types.InlineKeyboardButton("🔴 Stop", style="danger", callback_data=f'stop_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🔄 Restart", style="success", callback_data=f'restart_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("🗑️ Delete", style="danger", callback_data=f'delete_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("🤖 AI Fix", callback_data=f'aifix_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🔙 Back", callback_data='check_files')
        )
    else:
        markup.row(
            types.InlineKeyboardButton("🟢 Start", style="success", callback_data=f'start_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🗑️ Delete", style="danger", callback_data=f'delete_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("📜 View Logs", callback_data=f'logs_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🤖 AI Fix", callback_data=f'aifix_{script_owner_id}_{file_name}')
        )
        markup.row(types.InlineKeyboardButton("🔙 Back to Files", callback_data='check_files'))
    return markup

# ===== CREATE ADMIN PANEL =====
def create_admin_panel():
    """Reply keyboard for Admin Panel (not inline)."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.row(kb_button('➕ Add Admin'), kb_button('➖ Remove Admin'))
    markup.row(kb_button('📋 List Admins'), kb_button('🤖 Clone Control'))
    markup.row(kb_button('🔙 Back'))
    return markup

def create_admin_panel_inline():
    """Inline version kept for existing callback edits."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add', callback_data='add_admin'),
        types.InlineKeyboardButton('➖ Remove', callback_data='remove_admin')
    )
    markup.row(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
    markup.row(types.InlineKeyboardButton('🤖 Clone Control', callback_data='clone_control'))
    markup.row(
        types.InlineKeyboardButton('📤 Upload File', callback_data='ad:upload', style="success"),
        types.InlineKeyboardButton('📂 Manage Files', callback_data='ad:managefiles', style="primary")
    )
    markup.row(types.InlineKeyboardButton('🔙 Back', callback_data='back_to_main'))
    return markup

# ===== CLONE CONTROL PANEL =====
def _logic_clone_control(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    bot.reply_to(message, "🤖 Clone Control Panel\n\nManage cloned hosting bots below.",
                 reply_markup=create_clone_control_panel())


def create_clone_control_panel():
    markup = types.InlineKeyboardMarkup(row_width=1)
    if not user_clones:
        markup.add(types.InlineKeyboardButton("ℹ️ No clones found", callback_data="noop"))
    else:
        for clone_user_id, info in sorted(user_clones.items()):
            proc = clone_processes.get(clone_user_id)
            running = bool(proc and proc.poll() is None)
            icon = "🟢" if running else "🔴"
            markup.add(types.InlineKeyboardButton(
                f"{icon} @{info.get('bot_username','unknown')} • {clone_user_id}",
                callback_data=f"clone_manage_{clone_user_id}"))
    markup.add(types.InlineKeyboardButton("🔄 Refresh", callback_data="clone_control"))
    markup.add(types.InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel"))
    return markup


def clone_control_callback(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    bot.edit_message_text("🤖 Clone Control Panel\n\nManage cloned hosting bots below.",
                          call.message.chat.id, call.message.message_id,
                          reply_markup=create_clone_control_panel())


def clone_manage_callback(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        return
    try:
        clone_user_id = int(call.data.split('_')[-1])
    except ValueError:
        bot.answer_callback_query(call.id, "Invalid clone.", show_alert=True)
        return
    info = user_clones.get(clone_user_id)
    if not info:
        bot.answer_callback_query(call.id, "Clone not found.", show_alert=True)
        return
    proc = clone_processes.get(clone_user_id)
    running = bool(proc and proc.poll() is None)
    status = "🟢 Running" if running else "🔴 Stopped"
    markup = types.InlineKeyboardMarkup(row_width=2)
    if running:
        markup.add(types.InlineKeyboardButton("🛑 Stop", callback_data=f"clone_stop_{clone_user_id}"))
    else:
        markup.add(types.InlineKeyboardButton("▶️ Start", callback_data=f"clone_start_{clone_user_id}"))
    markup.add(types.InlineKeyboardButton("🚫 Ban + Stop", callback_data=f"clone_ban_{clone_user_id}"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="clone_control"))
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        f"🤖 Clone: @{info.get('bot_username','unknown')}\n🆔 User ID: {clone_user_id}\n📊 Status: {status}",
        call.message.chat.id, call.message.message_id, reply_markup=markup)


def stop_clone_process(clone_user_id):
    proc = clone_processes.get(clone_user_id)
    if not proc:
        return False
    try:
        parent = psutil.Process(proc.pid)
        for child in parent.children(recursive=True):
            try: child.terminate()
            except Exception: pass
        try:
            parent.terminate()
            parent.wait(timeout=3)
        except Exception:
            try: parent.kill()
            except Exception: pass
        clone_processes.pop(clone_user_id, None)
        return True
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        clone_processes.pop(clone_user_id, None)
        return False
    except Exception as e:
        logger.error(f"Failed to stop clone {clone_user_id}: {e}", exc_info=True)
        return False


def start_clone_process(clone_user_id):
    if clone_user_id not in user_clones:
        return False
    clone_dir = os.path.join(BASE_DIR, f"clone_{clone_user_id}")
    clone_file = os.path.join(clone_dir, 'bot.py')
    if not os.path.isfile(clone_file):
        return False
    proc = clone_processes.get(clone_user_id)
    if proc and proc.poll() is None:
        return True
    try:
        log_handle = open(os.path.join(clone_dir, 'clone.log'), 'a', encoding='utf-8')
        proc = subprocess.Popen([sys.executable, clone_file], cwd=clone_dir,
                                stdout=log_handle, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)
        log_handle.close()
        clone_processes[clone_user_id] = proc
        return True
    except Exception as e:
        logger.error(f"Failed to start clone {clone_user_id}: {e}", exc_info=True)
        return False


def clone_control_action_callback(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        return
    parts = call.data.split('_')
    action, clone_user_id = parts[1], int(parts[2])
    if action == 'stop':
        ok = stop_clone_process(clone_user_id)
        msg = "🛑 Clone stopped." if ok else "ℹ️ Clone is already stopped."
    elif action == 'start':
        ok = start_clone_process(clone_user_id)
        msg = "▶️ Clone started." if ok else "❌ Could not start clone."
    elif action == 'ban':
        ban_user_db(clone_user_id, 'Clone banned by master admin', call.from_user.id)
        stop_clone_process(clone_user_id)
        msg = "🚫 Clone user banned and clone stopped."
    else:
        msg = "❓ Unknown action."
    bot.answer_callback_query(call.id, msg, show_alert=True)
    clone_manage_callback(type('Call', (), {'from_user': call.from_user,
        'data': f'clone_manage_{clone_user_id}', 'message': call.message})())


# ===== ADMIN PANEL KEYBOARD (Upload / Manage Files) =====
# NOTE: style parameter Bot API 9.4+ ka feature hai (telebot 4.36+ chahiye).
# Valid values: "primary" (blue), "success" (green), "danger" (red).
def admin_panel_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("📤 Upload File", callback_data="ad:upload", style="success"),
        types.InlineKeyboardButton("📂 Manage Files", callback_data="ad:managefiles", style="primary")
    )
    markup.row(types.InlineKeyboardButton("🔙 Back to Main", callback_data="back_to_main"))
    return markup

def admin_reply_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton("📤 Upload File", style="success"),
               types.KeyboardButton("📂 Manage Files", style="primary"))
    return markup


# ===== CREATE USER MANAGEMENT MENU =====
def create_user_management_menu():
    """Reply keyboard for User Management (not inline)."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.row(kb_button('🚫 Ban User'), kb_button('✅ Unban User'))
    markup.row(kb_button('📊 User Info'), kb_button('👥 All Users'))
    markup.row(kb_button('🔧 Set User Limit'), kb_button('🗑️ Remove User Limit'))
    markup.row(kb_button('🔙 Back'))
    return markup

def create_user_management_menu_inline():
    """Inline version kept for existing callback edits."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('🚫 Ban User', callback_data='ban_user'),
        types.InlineKeyboardButton('✅ Unban User', callback_data='unban_user')
    )
    markup.row(
        types.InlineKeyboardButton('📊 User Info', callback_data='user_info'),
        types.InlineKeyboardButton('👥 All Users', callback_data='all_users')
    )
    markup.row(
        types.InlineKeyboardButton('🔧 Set User Limit', callback_data='set_user_limit'),
        types.InlineKeyboardButton('🗑️ Remove User Limit', callback_data='remove_user_limit')
    )
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

# ===== CREATE ADMIN SETTINGS MENU =====
def create_admin_settings_menu():
    """Reply keyboard for Admin Settings (not inline)."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.row(kb_button('📊 System Info'), kb_button('📈 Bot Performance'))
    markup.row(kb_button('🧹 Cleanup Files'), kb_button('📋 Installation Logs'))
    markup.row(kb_button('🔙 Back'))
    return markup

def create_admin_settings_menu_inline():
    """Inline version kept for existing callback edits."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('📊 System Info', callback_data='system_info'),
        types.InlineKeyboardButton('📈 Bot Performance', callback_data='bot_performance')
    )
    markup.row(
        types.InlineKeyboardButton('🧹 Cleanup Files', callback_data='cleanup_files'),
        types.InlineKeyboardButton('📋 Installation Logs', callback_data='install_logs')
    )
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

# ===== CREATE SUBSCRIPTION PANEL =====
def create_subscription_panel():
    """Reply keyboard for subscription management."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2, is_persistent=True)
    markup.row(kb_button('➕ Add Subscription'), kb_button('➖ Remove Subscription'))
    markup.row(kb_button('📋 List Subscriptions'), kb_button('🔍 Check Subscription'))
    markup.row(kb_button('🔙 Back to Main'))
    return markup


# ===== HANDLE ZIP FILE =====
def handle_zip_file(downloaded_file_content, file_name_zip, message):
    user_id = message.from_user.id
    user_folder = get_user_folder(user_id)
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
        logger.info(f"📦 Temp dir for zip: {temp_dir}")
        zip_path = os.path.join(temp_dir, file_name_zip)
        with open(zip_path, 'wb') as new_file: new_file.write(downloaded_file_content)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for member in zip_ref.infolist():
                member_path = os.path.abspath(os.path.join(temp_dir, member.filename))
                if not member_path.startswith(os.path.abspath(temp_dir)):
                    raise zipfile.BadZipFile(f"⚠️ Zip has unsafe path: {member.filename}")
            zip_ref.extractall(temp_dir)
            logger.info(f"📂 Extracted zip to {temp_dir}")

        extracted_items = os.listdir(temp_dir)
        py_files = [f for f in extracted_items if f.endswith('.py')]
        js_files = [f for f in extracted_items if f.endswith('.js')]
        req_file = 'requirements.txt' if 'requirements.txt' in extracted_items else None
        pkg_json = 'package.json' if 'package.json' in extracted_items else None

        if req_file:
            req_path = os.path.join(temp_dir, req_file)
            logger.info(f"📦 requirements.txt found, installing: {req_path}")
            bot.reply_to(message, f"⚠️ Installing Python deps from `{req_file}`...")
            try:
                command = [sys.executable, '-m', 'pip', 'install', '-r', req_path]
                result = subprocess.run(command, capture_output=True, text=True, check=True, encoding='utf-8', errors='ignore')
                logger.info(f"✅ pip install from requirements.txt OK. Output:\n{result.stdout}")
                bot.reply_to(message, f"✅ Python deps from `{req_file}` installed.")
            except subprocess.CalledProcessError as e:
                error_msg = f"❌ Failed to install Python deps from `{req_file}`.\nLog:\n```\n{e.stderr or e.stdout}\n```"
                logger.error(error_msg)
                if len(error_msg) > 4000: error_msg = error_msg[:4000] + "\n... (Log truncated)"
                bot.reply_to(message, error_msg, parse_mode='Markdown'); return
            except Exception as e:
                 error_msg = f"❌ Unexpected error installing Python deps: {e}"
                 logger.error(error_msg, exc_info=True); bot.reply_to(message, error_msg); return

        if pkg_json:
            logger.info(f"📦 package.json found, npm install in: {temp_dir}")
            bot.reply_to(message, f"⚠️ Installing Node deps from `{pkg_json}`...")
            try:
                command = ['npm', 'install']
                result = subprocess.run(command, capture_output=True, text=True, check=True, cwd=temp_dir, encoding='utf-8', errors='ignore')
                logger.info(f"✅ npm install OK. Output:\n{result.stdout}")
                bot.reply_to(message, f"✅ Node deps from `{pkg_json}` installed.")
            except FileNotFoundError:
                bot.reply_to(message, "❌ 'npm' not found. Cannot install Node deps."); return
            except subprocess.CalledProcessError as e:
                error_msg = f"❌ Failed to install Node deps from `{pkg_json}`.\nLog:\n```\n{e.stderr or e.stdout}\n```"
                logger.error(error_msg)
                if len(error_msg) > 4000: error_msg = error_msg[:4000] + "\n... (Log truncated)"
                bot.reply_to(message, error_msg, parse_mode='Markdown'); return
            except Exception as e:
                 error_msg = f"❌ Unexpected error installing Node deps: {e}"
                 logger.error(error_msg, exc_info=True); bot.reply_to(message, error_msg); return

        main_script_name = None; file_type = None
        preferred_py = ['main.py', 'bot.py', 'app.py']; preferred_js = ['index.js', 'main.js', 'bot.js', 'app.js']
        for p in preferred_py:
            if p in py_files: main_script_name = p; file_type = 'py'; break
        if not main_script_name:
             for p in preferred_js:
                 if p in js_files: main_script_name = p; file_type = 'js'; break
        if not main_script_name:
            if py_files: main_script_name = py_files[0]; file_type = 'py'
            elif js_files: main_script_name = js_files[0]; file_type = 'js'
        if not main_script_name:
            bot.reply_to(message, "❌ No `.py` or `.js` script found in archive!"); return

        logger.info(f"📁 Moving extracted files from {temp_dir} to {user_folder}")
        moved_count = 0
        for item_name in os.listdir(temp_dir):
            src_path = os.path.join(temp_dir, item_name)
            dest_path = os.path.join(user_folder, item_name)
            if os.path.isdir(dest_path): shutil.rmtree(dest_path)
            elif os.path.exists(dest_path): os.remove(dest_path)
            shutil.move(src_path, dest_path); moved_count +=1
        logger.info(f"✅ Moved {moved_count} items to {user_folder}")

        save_user_file(user_id, main_script_name, file_type)
        logger.info(f"✅ Saved main script '{main_script_name}' ({file_type}) for {user_id} from zip.")
        main_script_path = os.path.join(user_folder, main_script_name)
        bot.reply_to(message, f"✅ Files extracted. Starting main script: `{main_script_name}`...", parse_mode='Markdown')

        if file_type == 'py':
             threading.Thread(target=run_script, args=(main_script_path, user_id, user_folder, main_script_name, message)).start()
        elif file_type == 'js':
             threading.Thread(target=run_js_script, args=(main_script_path, user_id, user_folder, main_script_name, message)).start()

    except zipfile.BadZipFile as e:
        logger.error(f"❌ Bad zip file from {user_id}: {e}")
        bot.reply_to(message, f"❌ Error: Invalid/corrupted ZIP. {e}")
    except Exception as e:
        logger.error(f"❌ Error processing zip for {user_id}: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error processing zip: {str(e)}")
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try: shutil.rmtree(temp_dir); logger.info(f"🧹 Cleaned temp dir: {temp_dir}")
            except Exception as e: logger.error(f"❌ Failed to clean temp dir {temp_dir}: {e}", exc_info=True)

# ===== HANDLE JS FILE =====
def handle_js_file(file_path, script_owner_id, user_folder, file_name, message):
    try:
        save_user_file(script_owner_id, file_name, 'js')
        threading.Thread(target=run_js_script, args=(file_path, script_owner_id, user_folder, file_name, message)).start()
    except Exception as e:
        logger.error(f"❌ Error processing JS file {file_name} for {script_owner_id}: {e}", exc_info=True)
        bot.reply_to(message, f"⚠️ Error processing JS file: {str(e)}")

# ===== HANDLE PYTHON FILE =====
def handle_py_file(file_path, script_owner_id, user_folder, file_name, message):
    try:
        save_user_file(script_owner_id, file_name, 'py')
        threading.Thread(target=run_script, args=(file_path, script_owner_id, user_folder, file_name, message)).start()
    except Exception as e:
        logger.error(f"❌ Error processing Python file {file_name} for {script_owner_id}: {e}", exc_info=True)
        bot.reply_to(message, f"⚠️ Error processing Python file: {str(e)}")

# ===== CREATE BOT CLONE =====
def create_bot_clone(user_id, token, bot_username):
    try:
        clone_dir = os.path.join(BASE_DIR, f'clone_{user_id}')
        os.makedirs(clone_dir, exist_ok=True)
        os.makedirs(os.path.join(clone_dir, CLONE_APPROVAL_DIR_NAME), exist_ok=True)
        clone_file = os.path.join(clone_dir, 'bot.py')
        with open(__file__, 'r', encoding='utf-8') as f:
            script_content = f.read()

        clone_secret = uuid.uuid4().hex + uuid.uuid4().hex
        master_url = os.environ.get('MASTER_APPROVAL_URL',
                                    f"http://127.0.0.1:{os.environ.get('PORT', '8080')}/clone/upload")
        script_content = script_content.replace(BOT_TOKEN, token)
        script_content = script_content.replace(str(OWNER_ID), str(user_id))
        script_content = script_content.replace(str(ADMIN_ID), str(user_id))
        script_content = script_content.replace("IS_CLONE = False", "IS_CLONE = True", 1)
        script_content = script_content.replace("CLONE_USER_ID = None", f"CLONE_USER_ID = {user_id}", 1)
        script_content = script_content.replace(
            "MASTER_APPROVAL_URL = os.environ.get('MASTER_APPROVAL_URL', 'http://127.0.0.1:8080/clone/upload')",
            f"MASTER_APPROVAL_URL = {master_url!r}", 1)
        script_content = script_content.replace(
            "CLONE_APPROVAL_KEY = os.environ.get('CLONE_APPROVAL_KEY', '')",
            f"CLONE_APPROVAL_KEY = {clone_secret!r}", 1)

        with open(clone_file, 'w', encoding='utf-8') as f:
            f.write(script_content)
        os.environ[f'CLONE_KEY_{user_id}'] = clone_secret
        save_clone_info(user_id, bot_username, token, clone_secret)

        log_handle = open(os.path.join(clone_dir, 'clone.log'), 'a', encoding='utf-8')
        proc = subprocess.Popen([sys.executable, clone_file], cwd=clone_dir,
                                stdout=log_handle, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)
        log_handle.close()
        clone_processes[user_id] = proc
        time.sleep(0.8)
        if proc.poll() is not None:
            log_path = os.path.join(clone_dir, 'clone.log')
            try:
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as lf:
                    startup_log = lf.read()[-1500:]
            except Exception:
                startup_log = 'Unknown clone startup error.'
            logger.error(f"❌ Clone process exited immediately for user {user_id}: {startup_log}")
            return False
        logger.info(f"✅ Bot clone created for user {user_id}, @{bot_username}, PID={proc.pid}")
        return True
    except Exception as e:
        logger.error(f"❌ Error creating bot clone: {e}", exc_info=True)
        return False

# ===== LOGIC: SEND WELCOME =====
def _logic_send_command(message):
    """Open the Send Command flow for the user's own running scripts or all scripts for admins."""
    user_id = message.from_user.id
    if is_user_banned(user_id):
        bot.reply_to(message, stylish_text("❌ You are banned from using this bot."))
        return
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, stylish_text("⚠️ Bot locked by admin."))
        return
    if not check_subscription_and_continue(message):
        return
    running = []
    for script_key, info in list(bot_scripts.items()):
        owner_id = info.get('script_owner_id')
        file_name = info.get('file_name')
        if owner_id is None or not file_name:
            continue
        if (user_id == owner_id or user_id in admin_ids) and is_bot_running(owner_id, file_name):
            running.append((script_key, info))
    if not running:
        bot.reply_to(message, stylish_text("⚠️ No running scripts found."))
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for script_key, info in running:
        owner_id = info.get('script_owner_id')
        label = f"📄 {info.get('file_name', 'Unknown')}"
        if user_id in admin_ids and owner_id != user_id:
            label += f" (User: {owner_id})"
        markup.add(types.InlineKeyboardButton(stylish_text(label), callback_data=f"sendcmd_select_{script_key}"))
    markup.add(types.InlineKeyboardButton(stylish_text("🔙 Back"), callback_data="send_command_back"))
    bot.reply_to(message, stylish_text("⌨️ Select a running script to send a command:"), reply_markup=markup)

def process_send_command(message, script_key):
    user_id = message.from_user.id
    if not check_subscription_and_continue(message):
        return
    if script_key not in bot_scripts:
        bot.reply_to(message, stylish_text("❌ Script is no longer running."))
        return
    info = bot_scripts[script_key]
    owner_id = info.get('script_owner_id')
    if user_id != owner_id and user_id not in admin_ids:
        bot.reply_to(message, stylish_text("⚠️ You can only send commands to your own running scripts."))
        return
    command_text = (message.text or '').strip()
    if not command_text:
        bot.reply_to(message, stylish_text("⚠️ Empty command. Try again or send /cancel."))
        return
    if command_text.lower() == '/cancel':
        bot.reply_to(message, stylish_text("❌ Send Command cancelled."))
        return
    process = info.get('process')
    try:
        if not process or process.poll() is not None:
            bot.reply_to(message, stylish_text(f"❌ Script {info.get('file_name', '')} is not running."))
            return
        stdin = getattr(process, 'stdin', None)
        if stdin is None:
            bot.reply_to(message, stylish_text("❌ This script does not have an available input channel."))
            return
        stdin.write(command_text + '\n')
        stdin.flush()
        bot.reply_to(message, stylish_text(f"✅ Command sent to {info.get('file_name', '')}: {command_text}"))
        time.sleep(0.5)
        if process.poll() is not None:
            bot.reply_to(message, stylish_text(f"⚠️ Script {info.get('file_name', '')} stopped after receiving the command."))
    except Exception as e:
        logger.error(f"Send command error for {script_key}: {e}", exc_info=True)
        bot.reply_to(message, stylish_text("❌ Error sending command to the script."))

def sendcmd_select_callback(call):
    try:
        script_key = call.data.replace('sendcmd_select_', '', 1)
        if script_key not in bot_scripts:
            bot.answer_callback_query(call.id, stylish_text("❌ Script is no longer running."), show_alert=True)
            return
        info = bot_scripts[script_key]
        owner_id = info.get('script_owner_id')
        if call.from_user.id != owner_id and call.from_user.id not in admin_ids:
            bot.answer_callback_query(call.id, stylish_text("⚠️ Not authorized."), show_alert=True)
            return
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, stylish_text(f"⌨️ Enter command for {info.get('file_name', '')}:"))
        bot.register_next_step_handler(msg, lambda m: process_send_command(m, script_key))
    except Exception as e:
        logger.error(f"Send command selection error: {e}", exc_info=True)
        bot.answer_callback_query(call.id, stylish_text("❌ Error selecting script."), show_alert=True)

def send_command_callback(call):
    bot.answer_callback_query(call.id)
    try:
        _logic_send_command(call.message)
    except Exception as e:
        logger.error(f"Send command callback error: {e}", exc_info=True)

def send_command_back_callback(call):
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass

def _logic_send_welcome(message, user=None):
    # When called from an inline callback, use the clicking user rather than the bot-authored message sender.
    user = user or message.from_user
    user_id = user.id
    chat_id = message.chat.id
    user_name = user.first_name or "User"
    user_last_name = user.last_name or ""
    user_username = user.username

    # Keep a separate JSON record for each user, updated whenever they open the bot.
    save_user_profile(user, chat_id=chat_id)

    logger.info(f"👋 Welcome request from user_id: {user_id}, username: @{user_username}")

    # Check if user is banned
    if is_user_banned(user_id):
        bot.send_message(chat_id, "❌ You are banned from using this bot.")
        return

    # Force join: primary channel + every channel added through the admin Channel Add menu.
    if user_id not in admin_ids and user_id != OWNER_ID:
        joined_all, missing_channels = _is_user_in_all_force_channels(user_id)
        if not joined_all:
            _send_force_join_prompt(chat_id, missing_channels)
            return

    if bot_locked and user_id not in admin_ids:
        bot.send_message(chat_id, "⚠️ Bot locked by admin. Try later.")
        return

    user_bio = "Could not fetch bio"
    try: user_bio = bot.get_chat(user_id).bio or "No bio"
    except Exception: pass

    if user_id not in active_users:
        add_active_user(user_id)
        try:
            owner_notification = (f"👋 **New User Alert!**\n\n"
                                  f"👤 **Name:** {user_name} {user_last_name}\n"
                                  f"📱 **Username:** @{user_username or 'N/A'}\n"
                                  f"🆔 **User ID:** `{user_id}`\n"
                                  f"📝 **Bio:** {user_bio}")
            bot.send_message(OWNER_ID, owner_notification, parse_mode='Markdown')
        except Exception as e: logger.error(f"❌ Failed to notify owner about new user {user_id}: {e}")

    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
    expiry_info = ""
    
    if user_id == OWNER_ID:
        user_status = "👑 Owner"
    elif user_id in admin_ids:
        user_status = "⚜️ Admin"
    elif user_id in user_subscriptions:
        expiry_date = user_subscriptions[user_id].get('expiry')
        if expiry_date and expiry_date > datetime.now():
            user_status = "💎 Premium"
            days_left = (expiry_date - datetime.now()).days
            expiry_info = f"\n⌛️ Expires in: {days_left} days"
        else:
            user_status = "🆓 Free User"
            remove_subscription_db(user_id)
    else:
        user_status = "🆓 Free User"

    full_name = user_name
    if user_last_name:
        full_name += f" {user_last_name}"

    welcome_msg_text = f"""✨━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━✨
💎   Hᴏꜱᴛɪɴɢ  💎
⚡  Vᴇʀꜱɪᴏɴ 5.3  ⚡
✨━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━✨

👑 Wᴇʟᴄᴏᴍᴇ {full_name}
🆔 Uꜱᴇʀ Iᴅ: {user_id}

📂 Fɪʟᴇꜱ: {current_files}

⚡ Fᴇᴀᴛᴜʀᴇꜱ:
• 🔄 Aᴜᴛᴏ‑ʀᴇᴄᴏᴠᴇʀʏ Sʏꜱᴛᴇᴍ
• 🐍 Pʏᴛʜᴏɴ / 🟨 Jꜱ / 📦 Zɪᴘ Sᴜᴘᴘᴏʀᴛ
• 🌐 Wᴇʙꜱɪᴛᴇ Hᴏꜱᴛɪɴɢ (ʜᴛᴍʟ/ᴢɪᴘ)

💎 Uꜱᴇ ᴛʜᴇ ʙᴜᴛᴛᴏɴꜱ ʙᴇʟᴏᴡ ᴛᴏ ɴᴀᴠɪɢᴀᴛᴇ."""
    main_reply_markup = create_reply_keyboard_main_menu(user_id)
    try:
        # Send the welcome directly; no standalone hourglass message.
        try:
            photos = bot.get_user_profile_photos(user_id, limit=1)
            if photos.total_count > 0 and photos.photos and photos.photos[0]:
                profile_photo_id = photos.photos[0][-1].file_id
                bot.send_photo(
                    chat_id, profile_photo_id, caption=welcome_msg_text,
                    reply_markup=main_reply_markup
                )
            else:
                bot.send_message(chat_id, welcome_msg_text, reply_markup=main_reply_markup)
        except Exception as photo_error:
            logger.warning(f"Could not send welcome with profile photo for {user_id}: {photo_error}")
            bot.send_message(chat_id, welcome_msg_text, reply_markup=main_reply_markup)
    except Exception as e:
        logger.error(f"❌ Error sending welcome to {user_id}: {e}", exc_info=True)

# ===== LOGIC: Uᴘʟᴏᴀᴅ Fɪʟᴇ =====
def _logic_upload_file(message):
    if IS_CLONE:
        bot.reply_to(message, "📤 Send your Python (.py), JS (.js), or ZIP (.zip) file.\n\n🛡️ Every upload requires master-admin approval.")
        return
    user_id = message.from_user.id
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        subscription_message, sub_markup = create_subscription_check_message(not_joined)
        bot.reply_to(message, subscription_message, reply_markup=sub_markup, parse_mode='Markdown')
        return
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked by admin, cannot accept files.")
        return

    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, f"⚠️ File limit ({current_files}/{limit_str}) reached. Delete files first.")
        return
    bot.reply_to(message, "📤 Send your Python (`.py`), JS (`.js`), or ZIP (`.zip`) file.")

# ===== LOGIC: Cʜᴇᴄᴋ Fɪʟᴇs =====
def _logic_check_files(message):
    user_id = message.from_user.id
    user_files_list = user_files.get(user_id, [])
    if not user_files_list:
        bot.reply_to(message, make_blockquote("📂 Your files:\n\n(No files uploaded yet)"), parse_mode='HTML')
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for file_name, file_type in sorted(user_files_list):
        is_running = is_bot_running(user_id, file_name)
        status_icon = "🟢 Active" if is_running else "🔴 Stopped"
        btn_text = f"{file_name} ({file_type}) - {status_icon}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f'file_{user_id}_{file_name}'))
    bot.reply_to(message, make_blockquote("📂 Your files:\nClick to manage."), reply_markup=markup, parse_mode='HTML')

# ===== LOGIC: Bᴏᴛ Sᴘᴇᴇᴅ =====
def _logic_bot_speed(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    start_time_ping = time.time()
    wait_msg = bot.reply_to(message, "⏱️ Testing speed...")
    try:
        response_time = round((time.time() - start_time_ping) * 1000, 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        if user_id == OWNER_ID: user_level = "👑 Owner"
        elif user_id in admin_ids: user_level = "⚜️ Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now(): user_level = "💎 Premium"
        else: user_level = "🆓 Free User"
        speed_msg = (f"⚡ Bᴏᴛ Sᴘᴇᴇᴅ & Status:\n\n"
                     f"⏱️ API Response Time: {response_time} ms\n"
                     f"🚦 Bot Status: {status}\n"
                     f"👤 Your Level: {user_level}")
        # Native Telegram blockquote (vertical bar + quote UI)
        bot.edit_message_text(make_blockquote(speed_msg), chat_id, wait_msg.message_id, parse_mode='HTML')
    except Exception as e:
        logger.error(f"❌ Error during speed test (cmd): {e}", exc_info=True)
        try:
            bot.edit_message_text(make_blockquote("❌ Error during speed test."), chat_id, wait_msg.message_id, parse_mode='HTML')
        except Exception:
            bot.edit_message_text("❌ Error during speed test.", chat_id, wait_msg.message_id)
        
# ===== LOGIC: Cᴏɴᴛᴀᴄᴛ Oᴡɴᴇʀ =====
def _logic_contact_owner(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(ikb('📞 Cᴏɴᴛᴀᴄᴛ Oᴡɴᴇʀ', style='primary', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'))
    bot.reply_to(message, "📞 Click to Cᴏɴᴛᴀᴄᴛ Oᴡɴᴇʀ:", reply_markup=markup)

# ===== LOGIC: Sᴛᴀᴛɪsᴛɪᴄs =====
def _logic_statistics(message):
    user_id = message.from_user.id
    total_users = len(active_users)
    total_files_records = sum(len(files) for files in user_files.values())

    running_bots_count = 0

    for script_key_iter, script_info_iter in list(bot_scripts.items()):
        s_owner_id, _ = script_key_iter.split('_', 1)
        if is_bot_running(int(s_owner_id), script_info_iter['file_name']):
            running_bots_count += 1

    stats_msg = (f"📊 Bot Live Sᴛᴀᴛɪsᴛɪᴄs:\n\n"
                 f"👥 Total Users: {total_users}\n"
                 f"🚫 Banned Users: {len(banned_users)}\n"
                 f"📂 Total File Records: {total_files_records}\n"
                 f"🟢 Total Active Bots: {running_bots_count}\n"
                 f"🤖 Clone Bots: {len(user_clones)}")

    bot.reply_to(message, stats_msg)

def _subscription_reply_step(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    raw = (message.text or '').strip()
    plain = raw.translate(_FONT_TO_ASCII).lower()
    if 'back' in plain:
        bot.reply_to(message, "Back to main menu.", reply_markup=create_reply_keyboard_main_menu(message.from_user.id))
    elif 'add subscription' in plain:
        msg = bot.send_message(message.chat.id, "🔢 Enter User ID & days (example: 123456789 30) or /cancel")
        bot.register_next_step_handler(msg, process_add_subscription_details)
    elif 'remove subscription' in plain:
        msg = bot.send_message(message.chat.id, "🔢 Enter User ID to remove subscription, or /cancel")
        bot.register_next_step_handler(msg, process_remove_subscription_id)
    elif 'list subscriptions' in plain:
        if not user_subscriptions:
            bot.reply_to(message, "😕 No active subscriptions.", reply_markup=create_subscription_panel())
        else:
            lines = ["💳 Active subscriptions:"]
            for uid, info in list(user_subscriptions.items())[:40]:
                expiry = info.get('expiry')
                lines.append(f"• {uid} — {expiry:%Y-%m-%d}" if expiry else f"• {uid} — expiry unavailable")
            bot.reply_to(message, "\\n".join(lines), reply_markup=create_subscription_panel())
        bot.register_next_step_handler(message, _subscription_reply_step)
    elif 'check subscription' in plain:
        msg = bot.send_message(message.chat.id, "🔍 Enter User ID to check, or /cancel")
        bot.register_next_step_handler(msg, process_check_subscription_id)
    else:
        bot.reply_to(message, "Choose an option below.", reply_markup=create_subscription_panel())
        bot.register_next_step_handler(message, _subscription_reply_step)


# ===== LOGIC: SUBSCRIPTIONS =====
def _logic_subscriptions(message):
    user_id = message.from_user.id
    if user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    
    markup = create_subscription_panel()
    bot.reply_to(message, "💳 **Subscription Management**\n\nManage user subscriptions here.", reply_markup=markup, parse_mode='Markdown')

# ===== LOGIC: BROADCAST INIT =====
def _logic_broadcast_init(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    msg = bot.reply_to(message, "📢 Send message to broadcast to all active users.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

# ===== LOGIC: TOGGLE LOCK BOT =====
def _logic_toggle_lock_bot(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    global bot_locked
    bot_locked = not bot_locked
    status = "locked" if bot_locked else "unlocked"
    lock_emoji = "🔒" if bot_locked else "✅"
    logger.warning(f"🔒 Bot {status} by Admin {message.from_user.id} via command/button.")
    bot.reply_to(message, f"{lock_emoji} Bot has been {status}.")

# ===== LOGIC: ADMIN PANEL =====
def _admin_panel_reply_step(message):
    """Handle Admin Panel reply-keyboard choices."""
    user_id = message.from_user.id
    if user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.",
                     reply_markup=create_reply_keyboard_main_menu(user_id))
        return
    raw = (message.text or '').strip()
    plain = raw.translate(_FONT_TO_ASCII).lower() if raw else ''
    main_kb = create_reply_keyboard_main_menu(user_id)

    if 'back' in plain:
        bot.reply_to(message, "Back to main menu.", reply_markup=main_kb)
        return
    if 'add admin' in plain or plain.strip() in ('➕ add', 'add'):
        msg = bot.send_message(message.chat.id, "🔢 Enter User ID to promote to Admin.\n/cancel to abort.")
        bot.register_next_step_handler(msg, process_add_admin_id)
        return
    if 'remove admin' in plain or plain.strip() in ('➖ remove', 'remove'):
        msg = bot.send_message(message.chat.id, "🔢 Enter User ID of Admin to remove.\n/cancel to abort.")
        bot.register_next_step_handler(msg, process_remove_admin_id)
        return
    if 'list admin' in plain:
        admin_list_str = ""
        for aid in sorted(list(admin_ids)):
            if aid == OWNER_ID:
                admin_list_str += f"👑 `{aid}` (Owner)\n"
            else:
                admin_list_str += f"👤 `{aid}`\n"
        if not admin_list_str:
            admin_list_str = "😕 No Owner/Admins configured!"
        bot.reply_to(message, f"👑 Current Admins:\n\n{admin_list_str}",
                     reply_markup=create_admin_panel(), parse_mode='Markdown')
        bot.register_next_step_handler(message, _admin_panel_reply_step)
        return
    if 'clone control' in plain:
        _logic_clone_control(message)
        return
    if 'upload file' in plain:
        _logic_upload_file(message)
        bot.register_next_step_handler(message, _admin_panel_reply_step)
        return
    if 'manage file' in plain or 'check file' in plain:
        _logic_check_files(message)
        return
    bot.reply_to(message, "Unknown option. Use the buttons or Back.", reply_markup=create_admin_panel())
    bot.register_next_step_handler(message, _admin_panel_reply_step)

def _logic_admin_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    bot.reply_to(message, "👑 Admin Panel\nManage admins.", reply_markup=create_admin_panel())
    bot.register_next_step_handler(message, _admin_panel_reply_step)

# ===== LOGIC: RUN ALL SCRIPTS =====
def _logic_run_all_scripts(message):
    admin_user_id = message.from_user.id
    admin_chat_id = message.chat.id

    if admin_user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return

    bot.reply_to(message, "🚀 Starting process to Rᴜɴ Aʟʟ Sᴄʀɪᴘᴛs. This may take a while...")
    logger.info(f"👑 Admin {admin_user_id} initiated 'run all scripts' from chat {admin_chat_id}.")

    started_count = 0; attempted_users = 0; skipped_files = 0; error_files_details = []

    all_user_files_snapshot = dict(user_files)

    for target_user_id, files_for_user in all_user_files_snapshot.items():
        if not files_for_user: continue
        attempted_users += 1
        logger.info(f"📂 Processing scripts for user {target_user_id}...")
        user_folder = get_user_folder(target_user_id)

        for file_name, file_type in files_for_user:
            if not is_bot_running(target_user_id, file_name):
                file_path = os.path.join(user_folder, file_name)
                if os.path.exists(file_path):
                    logger.info(f"🚀 Admin {admin_user_id} attempting to start '{file_name}' ({file_type}) for user {target_user_id}.")
                    try:
                        if file_type == 'py':
                            threading.Thread(target=run_script, args=(file_path, target_user_id, user_folder, file_name, message)).start()
                            started_count += 1
                        elif file_type == 'js':
                            threading.Thread(target=run_js_script, args=(file_path, target_user_id, user_folder, file_name, message)).start()
                            started_count += 1
                        else:
                            logger.warning(f"⚠️ Unknown file type '{file_type}' for {file_name} (user {target_user_id}). Skipping.")
                            error_files_details.append(f"`{file_name}` (User {target_user_id}) - Unknown type")
                            skipped_files += 1
                        time.sleep(0.7)
                    except Exception as e:
                        logger.error(f"❌ Error queueing start for '{file_name}' (user {target_user_id}): {e}")
                        error_files_details.append(f"`{file_name}` (User {target_user_id}) - Start error")
                        skipped_files += 1
                else:
                    logger.warning(f"⚠️ File '{file_name}' for user {target_user_id} not found at '{file_path}'. Skipping.")
                    error_files_details.append(f"`{file_name}` (User {target_user_id}) - File not found")
                    skipped_files += 1

    summary_msg = (f"🚀 All Users' Scripts - Processing Complete:\n\n"
                   f"✅ Attempted to start: {started_count} scripts.\n"
                   f"👥 Users processed: {attempted_users}.\n")
    if skipped_files > 0:
        summary_msg += f"⚠️ Skipped/Error files: {skipped_files}\n"
        if error_files_details:
             summary_msg += "📋 Details (first 5):\n" + "\n".join([f"  - {err}" for err in error_files_details[:5]])
             if len(error_files_details) > 5: summary_msg += "\n  ... and more (check logs)."

    bot.reply_to(message, summary_msg, parse_mode='Markdown')
    logger.info(f"✅ Run all scripts finished. Admin: {admin_user_id}. Started: {started_count}. Skipped/Errors: {skipped_files}")

# ==== LOGIC: Cʀᴇᴀᴛᴇ Cʟᴏɴᴇ Bᴏᴛ =====
def _logic_clone_bot(message):
    user_id = message.from_user.id
    
    clone_text = f"🤖 Clone Bot Service\n\n"
    clone_text += f"📊 Total Clones: {len(user_clones)}\n\n"
    clone_text += f"🎯 Features in your clone:\n"
    clone_text += f"• 📁 Unlimited file hosting\n"
    clone_text += f"• 🛡️ Security scanning\n"
    clone_text += f"• 💾 File hosting\n"
    clone_text += f"• ⚡ Auto-restart\n\n"
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("🚀 Clone", callback_data="clone_create"),
        types.InlineKeyboardButton("🗑️ Remove", callback_data="clone_remove")
    )
    
    bot.reply_to(message, clone_text, reply_markup=markup, parse_mode="Markdown")

# ===== LOGIC: Mᴀɴᴜᴀʟ Iɴꜱᴛᴀʟʟ =====
def _logic_manual_install(message):
    """Handle Mᴀɴᴜᴀʟ Iɴꜱᴛᴀʟʟation request from user"""
    manual_install_module_init(message)

# ===== LOGIC: Uꜱᴇʀ Mᴀɴᴀɢᴇᴍᴇɴᴛ & Aᴅᴍɪɴ Sᴇᴛᴛɪɴɢꜱ =====
def _user_mgmt_reply_step(message):
    """Handle User Management reply-keyboard choices."""
    user_id = message.from_user.id
    if user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.",
                     reply_markup=create_reply_keyboard_main_menu(user_id))
        return
    raw = (message.text or '').strip()
    plain = raw.translate(_FONT_TO_ASCII).lower() if raw else ''
    main_kb = create_reply_keyboard_main_menu(user_id)

    if 'back' in plain:
        bot.reply_to(message, "Back to main menu.", reply_markup=main_kb)
        return
    if 'ban user' in plain and 'unban' not in plain:
        msg = bot.send_message(message.chat.id, "🚫 Enter User ID to ban and reason (e.g., `12345678 Spamming`)\n/cancel to cancel")
        bot.register_next_step_handler(msg, process_ban_user)
        return
    if 'unban' in plain:
        msg = bot.send_message(message.chat.id, "✅ Enter User ID to unban\n/cancel to cancel")
        bot.register_next_step_handler(msg, process_unban_user)
        return
    if 'user info' in plain:
        msg = bot.send_message(message.chat.id, "👤 Enter User ID to get info\n/cancel to cancel")
        bot.register_next_step_handler(msg, process_user_info)
        return
    if 'all users' in plain:
        # Reuse all_users display via a fake-like path
        try:
            users_list = list(active_users)
            if not users_list:
                bot.reply_to(message, "👥 No active users yet.", reply_markup=create_user_management_menu())
            else:
                lines = ["👥 Active Users:\n"]
                for i, uid in enumerate(users_list[:40], 1):
                    tag = "👑" if uid == OWNER_ID else ("🛡️" if uid in admin_ids else ("🚫" if uid in banned_users else "🆓"))
                    lines.append(f"{i}. `{uid}` {tag}")
                if len(users_list) > 40:
                    lines.append(f"\n... and {len(users_list) - 40} more")
                bot.reply_to(message, "\n".join(lines), reply_markup=create_user_management_menu(), parse_mode='Markdown')
            bot.register_next_step_handler(message, _user_mgmt_reply_step)
        except Exception as e:
            bot.reply_to(message, f"❌ Error: {e}", reply_markup=create_user_management_menu())
            bot.register_next_step_handler(message, _user_mgmt_reply_step)
        return
    if 'set user limit' in plain or 'set limit' in plain:
        msg = bot.send_message(message.chat.id, "🔧 Enter User ID and new limit (e.g., `12345678 50`)\n/cancel to cancel")
        bot.register_next_step_handler(msg, process_set_user_limit)
        return
    if 'remove user limit' in plain or 'remove limit' in plain:
        msg = bot.send_message(message.chat.id, "🗑️ Enter User ID to remove custom limit\n/cancel to cancel")
        bot.register_next_step_handler(msg, process_remove_user_limit)
        return
    bot.reply_to(message, "Unknown option. Use the buttons or Back.", reply_markup=create_user_management_menu())
    bot.register_next_step_handler(message, _user_mgmt_reply_step)

def _logic_user_management(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    bot.reply_to(message, "👥 User Management\nManage users, set limits, ban/unban.",
                 reply_markup=create_user_management_menu())
    bot.register_next_step_handler(message, _user_mgmt_reply_step)

def _admin_settings_reply_step(message):
    """Handle Admin Settings reply-keyboard choices."""
    user_id = message.from_user.id
    if user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.",
                     reply_markup=create_reply_keyboard_main_menu(user_id))
        return
    raw = (message.text or '').strip()
    plain = raw.translate(_FONT_TO_ASCII).lower() if raw else ''
    main_kb = create_reply_keyboard_main_menu(user_id)

    if 'back' in plain:
        bot.reply_to(message, "Back to main menu.", reply_markup=main_kb)
        return
    if 'system info' in plain:
        try:
            import platform as _plat
            parts = [
                "🤖 Bot Information:",
                f"• Python: {_plat.python_version()}",
                f"• Platform: {_plat.platform()}",
                f"\n📊 Bot Stats:",
                f"• Active Users: {len(active_users)}",
                f"• Running Scripts: {len(bot_scripts)}",
                f"• Total Files: {sum(len(f) for f in user_files.values())}",
                f"• Bot Status: {'🔒 Locked' if bot_locked else '🔓 Unlocked'}",
            ]
            try:
                cpu = psutil.cpu_percent(interval=0.5)
                memory = psutil.virtual_memory()
                parts += [f"\n💻 System:", f"• CPU: {cpu}%", f"• Memory: {memory.percent}%"]
            except Exception:
                parts.append("\n💻 System metrics unavailable on this host.")
            bot.reply_to(message, "\n".join(parts), reply_markup=create_admin_settings_menu())
        except Exception as e:
            bot.reply_to(message, f"❌ Error: {e}", reply_markup=create_admin_settings_menu())
        bot.register_next_step_handler(message, _admin_settings_reply_step)
        return
    if 'bot performance' in plain or 'performance' in plain:
        try:
            running = len(bot_scripts)
            total = sum(len(f) for f in user_files.values())
            parts = [
                "📈 Bot Performance:",
                f"• Running Scripts: {running}",
                f"• Total Scripts: {total}",
                f"• Active Users: {len(active_users)}",
                f"• Subscriptions: {len(user_subscriptions)}",
                f"• Banned Users: {len(banned_users)}",
            ]
            try:
                bp = psutil.Process()
                parts += [f"• Memory: {bp.memory_info().rss / 1024 / 1024:.1f} MB",
                          f"• CPU: {bp.cpu_percent(interval=0.3):.1f}%"]
            except Exception:
                pass
            bot.reply_to(message, "\n".join(parts), reply_markup=create_admin_settings_menu())
        except Exception as e:
            bot.reply_to(message, f"❌ Error: {e}", reply_markup=create_admin_settings_menu())
        bot.register_next_step_handler(message, _admin_settings_reply_step)
        return
    if 'cleanup' in plain:
        cleaned_dirs = cleaned_files = 0
        try:
            for user_dir in os.listdir(UPLOAD_BOTS_DIR):
                user_path = os.path.join(UPLOAD_BOTS_DIR, user_dir)
                if os.path.isdir(user_path):
                    if not os.listdir(user_path):
                        try:
                            os.rmdir(user_path); cleaned_dirs += 1
                        except Exception:
                            pass
                    else:
                        for fn in os.listdir(user_path):
                            if fn.endswith('.log'):
                                fp = os.path.join(user_path, fn)
                                try:
                                    if time.time() - os.path.getmtime(fp) > 7 * 24 * 3600:
                                        os.remove(fp); cleaned_files += 1
                                except Exception:
                                    pass
            bot.reply_to(message, f"🧹 Cleanup complete.\n• Empty dirs: {cleaned_dirs}\n• Old logs: {cleaned_files}",
                         reply_markup=create_admin_settings_menu())
        except Exception as e:
            bot.reply_to(message, f"❌ Cleanup error: {e}", reply_markup=create_admin_settings_menu())
        bot.register_next_step_handler(message, _admin_settings_reply_step)
        return
    if 'install' in plain and 'log' in plain:
        try:
            with DB_LOCK:
                conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
                c = conn.cursor()
                c.execute('SELECT user_id, module_name, package_name, status, install_date FROM install_logs ORDER BY install_date DESC LIMIT 20')
                logs = c.fetchall()
                conn.close()
            if not logs:
                bot.reply_to(message, "📋 No installation logs found.", reply_markup=create_admin_settings_menu())
            else:
                lines = ["📋 Recent Installation Logs:\n"]
                for uid, mod, pkg, st, dt in logs:
                    icon = "✅" if st == "success" else "❌"
                    lines.append(f"{icon} `{uid}`: {mod} -> {pkg}\n   📅 {str(dt)[:19]}")
                bot.reply_to(message, "\n".join(lines), reply_markup=create_admin_settings_menu(), parse_mode='Markdown')
        except Exception as e:
            bot.reply_to(message, f"❌ Error: {e}", reply_markup=create_admin_settings_menu())
        bot.register_next_step_handler(message, _admin_settings_reply_step)
        return
    bot.reply_to(message, "Unknown option. Use the buttons or Back.", reply_markup=create_admin_settings_menu())
    bot.register_next_step_handler(message, _admin_settings_reply_step)

def _logic_admin_settings(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    bot.reply_to(message, "⚙️ Admin Settings\nSystem information and management.",
                 reply_markup=create_admin_settings_menu())
    bot.register_next_step_handler(message, _admin_settings_reply_step)

# ===== LOGIC: Cʜᴀɴɴᴇʟ Mᴀɴᴀɢᴇᴍᴇɴᴛ & Aᴅᴍɪɴ Iɴꜱᴛᴀʟʟ =====
# --- New Admin Functions for Channel Management ---
def _show_mandatory_channels_menu(chat_id, text="📢 Manage Mandatory Channels\nChoose an option:"):
    menu_message = bot.send_message(chat_id, text, reply_markup=create_mandatory_channels_menu())
    bot.register_next_step_handler(menu_message, _mandatory_channels_reply_step)
    return menu_message


def _mandatory_channels_reply_step(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    raw = (message.text or "").strip()
    plain = raw.translate(_FONT_TO_ASCII).lower().strip()

    if plain in ("/cancel", "cancel"):
        _show_mandatory_channels_menu(message.chat.id, "Cancelled.\n📢 Channel management:")
    elif "back" in plain:
        bot.send_message(message.chat.id, "Back to main menu.",
                         reply_markup=create_reply_keyboard_main_menu(message.from_user.id))
    elif "add channel" in plain:
        prompt = bot.send_message(
            message.chat.id,
            "📢 Send channel ID (e.g. -1001234567890) or public @username.\n"
            "Make the bot an administrator in that channel first.\n/cancel to abort."
        )
        bot.register_next_step_handler(prompt, process_add_channel)
    elif "remove channel" in plain:
        if not mandatory_channels:
            _show_mandatory_channels_menu(message.chat.id, "No mandatory channels found.")
        else:
            channel_lines = [f"• {cid} — {info.get('name', 'Channel')}" for cid, info in mandatory_channels.items()]
            prompt = bot.send_message(
                message.chat.id,
                "🗑️ Send the channel ID to remove:\n" + "\n".join(channel_lines) + "\n/cancel"
            )
            bot.register_next_step_handler(prompt, _remove_channel_by_id_step)
    elif "list channels" in plain:
        lines = ["📢 Mandatory Channels:"]
        lines.extend(
            f"• {info.get('name', 'Channel')} — {info.get('username') or cid} ({cid})"
            for cid, info in mandatory_channels.items()
        )
        _show_mandatory_channels_menu(
            message.chat.id,
            "\n".join(lines) if mandatory_channels else "No mandatory channels found."
        )
    else:
        _show_mandatory_channels_menu(message.chat.id, "Choose an option using the buttons below.")


def _remove_channel_by_id_step(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Not authorized.")
        return
    value = (message.text or "").strip()
    if value.lower() == "/cancel":
        _show_mandatory_channels_menu(message.chat.id, "Removal cancelled.")
        return

    # Accept IDs as text so the DB key and dict key remain the same type.
    channel_id = value
    if channel_id not in mandatory_channels:
        prompt = bot.reply_to(message, "❌ Channel ID not found. Send an exact listed ID or /cancel.")
        bot.register_next_step_handler(prompt, _remove_channel_by_id_step)
        return

    channel_name = mandatory_channels[channel_id].get("name", "Channel")
    if remove_mandatory_channel_db(channel_id):
        _show_mandatory_channels_menu(message.chat.id, f"✅ Removed: {channel_name}")
    else:
        _show_mandatory_channels_menu(message.chat.id, "❌ Could not remove channel. Check bot logs.")


def _logic_manage_mandatory_channels(message):
    """Open mandatory channel management for admins."""
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    _show_mandatory_channels_menu(message.chat.id)

def _logic_admin_install(message):
    """Admin Mᴀɴᴜᴀʟ Iɴꜱᴛᴀʟʟation for users"""
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    msg = bot.reply_to(message, "🛠️ Admin Module Installation\nSend user ID and module name (e.g., `12345678 requests`)\n/cancel to cancel")
    bot.register_next_step_handler(msg, process_admin_install)

def process_admin_install(message):
    """Process admin installation request"""
    admin_id = message.from_user.id
    if admin_id not in admin_ids:
        bot.reply_to(message, "⚠️ Not authorized.")
        return
        
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Installation cancelled.")
        return
    
    try:
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "⚠️ Format: `user_id module_name`\nExample: `12345678 requests`")
            return
            
        user_id = int(parts[0])
        module_name = ' '.join(parts[1:])
        
        # Check if it's a Node.js module
        if module_name.lower().startswith('npm:'):
            module_name = module_name[4:].strip()
            user_folder = get_user_folder(user_id)
            success, log = attempt_install_npm(module_name, user_folder, message, manual_request=True)
        else:
            # Python module
            success, log = attempt_install_pip(module_name, message, manual_request=True)
        
        if success:
            logger.info(f"Admin {admin_id} installed module {module_name} for user {user_id}")
            # Notify user
            try:
                bot.send_message(user_id, f"📦 Admin installed module `{module_name}` for you.")
            except Exception as e:
                logger.error(f"Failed to notify user {user_id}: {e}")
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid user ID. Must be a number.")
    except Exception as e:
        logger.error(f"Error in admin install: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {str(e)}")

# ===== Mᴀɴᴜᴀʟ Iɴꜱᴛᴀʟʟ FLOW =====
def manual_install_module_init(message):
    """Initialize manual module installation"""
    user_id = message.from_user.id
    
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    # Check mandatory subscription first
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        subscription_message, markup = create_subscription_check_message(not_joined)
        bot.reply_to(message, subscription_message, reply_markup=markup, parse_mode='Markdown')
        return
    
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked by admin. Try later.")
        return
    
    msg = bot.reply_to(message, "📦 Send module name to install (e.g., `requests` or `pillow`)\nFor Node.js: `npm:module_name`\n/cancel to cancel")
    bot.register_next_step_handler(msg, process_manual_install_module)

def process_manual_install_module(message):
    """Process manual module installation"""
    user_id = message.from_user.id
    
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Installation cancelled.")
        return
    
    module_name = message.text.strip()
    
    # Check if it's a Node.js module
    if module_name.lower().startswith('npm:'):
        module_name = module_name[4:].strip()
        user_folder = get_user_folder(user_id)
        success, log = attempt_install_npm(module_name, user_folder, message, manual_request=True)
    else:
        # Python module
        success, log = attempt_install_pip(module_name, message, manual_request=True)
    
    if success:
        logger.info(f"User {user_id} manually installed module: {module_name}")

# ===== SUBSCRIPTION / BAN CHECK ADAPTER (for ported systems) =====
def check_subscription_and_continue(message=None, call=None):
    """Banned + mandatory-channel gate used by ported command systems."""
    try:
        if message is not None: user_id = message.from_user.id
        elif call is not None: user_id = call.from_user.id
        else: return True
    except Exception:
        return True
    if is_user_banned(user_id):
        if message is not None: bot.reply_to(message, "❌ You are banned from using this bot.")
        else:
            try: bot.answer_callback_query(call.id, "❌ You are banned.", show_alert=True)
            except Exception: pass
        return False
    if user_id in admin_ids: return True
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if is_subscribed: return True
    subscription_message, sub_markup = create_subscription_check_message(not_joined)
    if message is not None:
        bot.reply_to(message, subscription_message, reply_markup=sub_markup, parse_mode='Markdown')
    else:
        try: bot.answer_callback_query(call.id)
        except Exception: pass
        try: bot.send_message(call.message.chat.id, subscription_message, reply_markup=sub_markup, parse_mode='Markdown')
        except Exception: pass
    return False

# ===== STYLISH TEXT HELPER =====
# --- stylish_text ---
def stylish_text(text: str) -> str:
    """Small-caps style (same as stylize_text). Safe for captions & mixed content."""
    if not isinstance(text, str) or not text:
        return text
    # Already a native Telegram HTML blockquote — leave intact.
    if _is_native_blockquote(text):
        return text
    # strip only dangerous tags, keep content
    text = re.sub(r'</?code>', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    return stylize_text(text)


# ===== NATIVE TELEGRAM BLOCKQUOTE (HTML) =====
def make_blockquote(text: str, expandable: bool = False) -> str:
    """
    Build a Telegram native HTML blockquote string.
    Shows the real quote UI (vertical colored bar + quote styling).
    Content is mixed-font styled, then HTML-escaped. Multiline safe.
    Use with parse_mode='HTML'.
    """
    if text is None:
        text = ''
    if not isinstance(text, str):
        text = str(text)
    styled = stylize_text(text)
    escaped = _html_mod.escape(styled)
    open_tag = '<blockquote expandable>' if expandable else '<blockquote>'
    return f'{open_tag}{escaped}</blockquote>'


def send_blockquote(chat_id, text, expandable: bool = False, **kwargs):
    """Send a message using Telegram's native blockquote formatting."""
    kwargs = dict(kwargs)
    kwargs['parse_mode'] = 'HTML'
    body = make_blockquote(text, expandable=expandable)
    try:
        return bot.send_message(chat_id, body, **kwargs)
    except Exception as e:
        logger.error(f"send_blockquote failed for chat {chat_id}: {e}", exc_info=True)
        try:
            kwargs.pop('parse_mode', None)
            return bot.send_message(chat_id, stylish_text(text), **kwargs)
        except Exception as e2:
            logger.error(f"send_blockquote fallback also failed: {e2}", exc_info=True)
            raise


def edit_blockquote(chat_id, message_id, text, expandable: bool = False, **kwargs):
    """Edit an existing message into a native Telegram blockquote."""
    kwargs = dict(kwargs)
    kwargs['parse_mode'] = 'HTML'
    body = make_blockquote(text, expandable=expandable)
    try:
        return bot.edit_message_text(body, chat_id, message_id, **kwargs)
    except Exception as e:
        logger.error(f"edit_blockquote failed chat={chat_id} msg={message_id}: {e}", exc_info=True)
        try:
            kwargs.pop('parse_mode', None)
            return bot.edit_message_text(stylish_text(text), chat_id, message_id, **kwargs)
        except Exception as e2:
            logger.error(f"edit_blockquote fallback failed: {e2}", exc_info=True)
            raise

# ===== PENDING UPLOAD (APPROVAL SYSTEM) =====
def add_pending_upload(user_id, file_id, file_name, file_type, file_size, user_name, user_username, extra_info=""):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            timestamp = datetime.now().isoformat()
            c.execute('''INSERT INTO pending_uploads 
                         (user_id, file_id, file_name, file_type, file_size, user_name, user_username, timestamp, extra_info)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                      (user_id, file_id, file_name, file_type, file_size, user_name, user_username, timestamp, extra_info))
            conn.commit()
            return c.lastrowid
        except Exception as e:
            logger.error(f"Error adding pending upload: {e}")
            return None
        finally:
            conn.close()

def get_pending_upload(upload_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('SELECT id, user_id, file_id, file_name, file_type, file_size, user_name, user_username, extra_info FROM pending_uploads WHERE id = ?', (upload_id,))
            row = c.fetchone()
            if row:
                return {'id': row[0], 'user_id': row[1], 'file_id': row[2], 'file_name': row[3],
                        'file_type': row[4], 'file_size': row[5], 'user_name': row[6], 'user_username': row[7], 'extra_info': row[8]}
            return None
        except Exception as e:
            logger.error(f"Error getting pending upload: {e}")
            return None
        finally:
            conn.close()

def delete_pending_upload(upload_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM pending_uploads WHERE id = ?', (upload_id,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error deleting pending upload: {e}")
            return False
        finally:
            conn.close()

def process_approved_file(upload_id, admin_chat_id, user_message_obj=None):
    pending = get_pending_upload(upload_id)
    if not pending:
        bot.send_message(admin_chat_id, stylish_text(f"❌ Pending upload {upload_id} not found."))
        return False
    user_id = pending['user_id']
    file_id = pending['file_id']
    file_name = pending['file_name']
    file_ext = os.path.splitext(file_name)[1].lower()
    file_type = pending['file_type']
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.send_message(admin_chat_id, stylish_text(f"⚠️ User limit reached ({current_files}/{limit_str}). Cannot approve."))
        delete_pending_upload(upload_id)
        return False
    try:
        file_info = bot.get_file(file_id)
        downloaded = bot.download_file(file_info.file_path)
        user_folder = get_user_folder(user_id)
        if file_ext == '.zip':
            temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
            zip_path = os.path.join(temp_dir, file_name)
            with open(zip_path, 'wb') as f:
                f.write(downloaded)
            with zipfile.ZipFile(zip_path, 'r') as z:
                z.extractall(temp_dir)
            extracted = os.listdir(temp_dir)
            py_files = [f for f in extracted if f.endswith('.py')]
            js_files = [f for f in extracted if f.endswith('.js')]
            req_file = 'requirements.txt' if 'requirements.txt' in extracted else None
            pkg_json = 'package.json' if 'package.json' in extracted else None
            if req_file:
                try:
                    subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', os.path.join(temp_dir, req_file)], check=True, capture_output=True)
                    bot.send_message(admin_chat_id, stylish_text("✅ Python deps installed."))
                except Exception as e:
                    bot.send_message(admin_chat_id, stylish_text(f"❌ Python deps failed: {e}"))
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    delete_pending_upload(upload_id)
                    return False
            if pkg_json:
                try:
                    subprocess.run(['npm', 'install'], cwd=temp_dir, check=True, capture_output=True)
                    bot.send_message(admin_chat_id, stylish_text("✅ Node deps installed."))
                except Exception as e:
                    bot.send_message(admin_chat_id, stylish_text(f"❌ Node deps failed: {e}"))
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    delete_pending_upload(upload_id)
                    return False
            main_script = None
            for p in ['main.py', 'bot.py', 'app.py']:
                if p in py_files:
                    main_script = p
                    file_type = 'py'
                    break
            if not main_script:
                for p in ['index.js', 'main.js', 'bot.js', 'app.js']:
                    if p in js_files:
                        main_script = p
                        file_type = 'js'
                        break
            if not main_script and py_files:
                main_script = py_files[0]
                file_type = 'py'
            elif not main_script and js_files:
                main_script = js_files[0]
                file_type = 'js'
            if not main_script:
                bot.send_message(admin_chat_id, stylish_text("❌ No .py or .js script found in zip."))
                shutil.rmtree(temp_dir, ignore_errors=True)
                delete_pending_upload(upload_id)
                return False
            for item in os.listdir(temp_dir):
                src = os.path.join(temp_dir, item)
                dst = os.path.join(user_folder, item)
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                elif os.path.exists(dst):
                    os.remove(dst)
                shutil.move(src, dst)
            shutil.rmtree(temp_dir, ignore_errors=True)
            save_user_file(user_id, main_script, file_type)
            script_path = os.path.join(user_folder, main_script)
            if file_type == 'py':
                threading.Thread(target=run_script, args=(script_path, user_id, user_folder, main_script, user_message_obj)).start()
            else:
                threading.Thread(target=run_js_script, args=(script_path, user_id, user_folder, main_script, user_message_obj)).start()
            bot.send_message(admin_chat_id, stylish_text(f"✅ Approved and started: {main_script}"))
            return True
        else:
            file_path = os.path.join(user_folder, file_name)
            with open(file_path, 'wb') as f:
                f.write(downloaded)
            save_user_file(user_id, file_name, file_type)
            if file_type == 'py':
                threading.Thread(target=run_script, args=(file_path, user_id, user_folder, file_name, user_message_obj)).start()
            else:
                threading.Thread(target=run_js_script, args=(file_path, user_id, user_folder, file_name, user_message_obj)).start()
            bot.send_message(admin_chat_id, stylish_text(f"✅ Approved and started: {file_name}"))
            return True
    except Exception as e:
        logger.error(f"Error in process_approved_file: {e}", exc_info=True)
        bot.send_message(admin_chat_id, stylish_text(f"❌ Error: {e}"))
        return False
    finally:
        delete_pending_upload(upload_id)

# --- Approval / Rejection Callback ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_upload_') or call.data.startswith('reject_upload_'))
def handle_approval_callback(call):
    if not check_subscription_and_continue(None, call):
        return
    admin_id = call.from_user.id
    if admin_id not in admin_ids:
        bot.answer_callback_query(call.id, stylish_text("⚠️ Only admins can approve/reject."), show_alert=True)
        return
    upload_id = int(call.data.split('_')[-1])
    pending = get_pending_upload(upload_id)
    if not pending:
        bot.answer_callback_query(call.id, stylish_text("⚠️ This upload request no longer exists."), show_alert=True)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except: pass
        return
    user_id = pending['user_id']
    file_name = pending['file_name']
    if call.data.startswith('approve_upload_'):
        bot.answer_callback_query(call.id, stylish_text("✅ Approving and starting..."))
        success = process_approved_file(upload_id, admin_chat_id=call.message.chat.id, user_message_obj=call.message)
        if success:
            try:
                bot.send_message(user_id, stylish_text(f"✅ Your file {file_name} has been approved and is now running."))
            except Exception as e:
                logger.error(f"Could not notify user {user_id}: {e}")
            try:
                bot.edit_message_caption(
                    caption=stylish_text(call.message.caption + "\n\n✅ APPROVED"),
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=None
                )
            except: pass
        else:
            bot.send_message(call.message.chat.id, stylish_text(f"❌ Failed to process file for user {user_id}."))
    else:
        bot.answer_callback_query(call.id, stylish_text("❌ Rejected."))
        delete_pending_upload(upload_id)
        reject_msg = "AGLI BAR SE YE FILE RUN KARKE APNI MAA MAT CHUDWANA"
        try:
            bot.send_message(user_id, stylish_text(f"❌ Your file {file_name} was rejected by admin.\n\n{reject_msg}"))
        except Exception as e:
            logger.error(f"Could not notify user {user_id}: {e}")
        try:
            bot.edit_message_caption(
                caption=stylish_text(call.message.caption + "\n\n❌ REJECTED"),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=None
            )
        except: pass

# ===== GITHUB DEPLOY SYSTEM =====
# ======================= GITHUB DEPLOY =======================
def parse_github_url(url):
    url = re.sub(r'\.git$', '', url)
    if 'github.com' not in url:
        raise ValueError("Not a valid GitHub URL")
    parts = url.split('github.com/')[-1].split('/')
    if len(parts) < 2:
        raise ValueError("Invalid GitHub URL format")
    owner = parts[0]
    repo = parts[1]
    branch = 'main'
    if len(parts) >= 4 and parts[2] == 'tree':
        branch = parts[3]
    return owner, repo, branch

def download_github_repo(owner, repo, branch, token=None):
    url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{branch}"
    headers = {}
    if token:
        headers['Authorization'] = f'token {token}'
    resp = requests.get(url, headers=headers, stream=True)
    if resp.status_code == 404:
        raise Exception("Repository or branch not found")
    if resp.status_code == 401:
        raise Exception("Invalid or missing access token (private repo)")
    if resp.status_code != 200:
        raise Exception(f"GitHub API error: {resp.status_code}")
    content_length = resp.headers.get('content-length')
    if content_length and int(content_length) > 20 * 1024 * 1024:
        raise Exception("Repository ZIP exceeds 20MB limit")
    return resp.content

github_data = {}

def _logic_github_deploy(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if get_user_file_count(user_id) >= get_user_file_limit(user_id):
        bot.reply_to(message, stylish_text("⚠️ You have reached your file limit. Delete some files first."))
        return
    github_data[user_id] = {'step': 'url'}
    bot.reply_to(message, stylish_text("📦 Send me the GitHub repository URL.\nExample: https://github.com/user/repo\n\nSend /cancel to abort."))

@bot.message_handler(func=lambda m: m.from_user.id in github_data and github_data[m.from_user.id]['step'] == 'url')
def github_get_url(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if message.text and message.text.lower() == '/cancel':
        del github_data[user_id]
        bot.reply_to(message, stylish_text("❌ GitHub deploy cancelled."))
        return
    url = message.text.strip()
    try:
        owner, repo, branch = parse_github_url(url)
    except Exception as e:
        bot.reply_to(message, stylish_text(f"❌ Invalid GitHub URL: {e}"))
        return
    github_data[user_id]['url'] = url
    github_data[user_id]['owner'] = owner
    github_data[user_id]['repo'] = repo
    github_data[user_id]['branch'] = branch
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🔒 Private", callback_data=f"github_private_{user_id}"),
        types.InlineKeyboardButton("🌐 Public", callback_data=f"github_public_{user_id}")
    )
    bot.reply_to(message, stylish_text("Is this a private repository?"), reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('github_private_') or call.data.startswith('github_public_'))
def github_repo_type(call):
    user_id = int(call.data.split('_')[-1])
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "Not for you", show_alert=True)
        return
    if user_id not in github_data:
        bot.answer_callback_query(call.id, "Session expired", show_alert=True)
        return
    if call.data.startswith('github_private_'):
        github_data[user_id]['step'] = 'token'
        bot.edit_message_text("🔑 Send your GitHub personal access token (with `repo` scope).\nSend /cancel to abort.",
                              call.message.chat.id, call.message.message_id)
    else:
        github_data[user_id]['token'] = None
        _process_github_download(call.message.chat.id, user_id)
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: m.from_user.id in github_data and github_data[m.from_user.id].get('step') == 'token')
def github_get_token(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if message.text and message.text.lower() == '/cancel':
        del github_data[user_id]
        bot.reply_to(message, stylish_text("❌ GitHub deploy cancelled."))
        return
    token = message.text.strip()
    github_data[user_id]['token'] = token
    _process_github_download(message.chat.id, user_id)

def _process_github_download(chat_id, user_id):
    data = github_data.get(user_id)
    if not data:
        bot.send_message(chat_id, stylish_text("Session expired. Start again."))
        return
    url = data['url']
    owner = data['owner']
    repo = data['repo']
    branch = data['branch']
    token = data.get('token')
    
    msg = bot.send_message(chat_id, stylish_text("[▓░░░░░░░░░]"))
    time.sleep(1.5)
    bot.edit_message_text(stylish_text("[▓▓░░░░░░░░]"), chat_id, msg.message_id)
    time.sleep(1)
    bot.edit_message_text(stylish_text("[▓▓▓░░░░░░░]"), chat_id, msg.message_id)
    time.sleep(1)
    bot.edit_message_text(stylish_text("[▓▓▓▓░░░░░░]"), chat_id, msg.message_id)
    time.sleep(0.8)
    bot.edit_message_text(stylish_text("[▓▓▓▓▓░░░░░]"), chat_id, msg.message_id)
    time.sleep(0.8)
    bot.edit_message_text(stylish_text("[▓▓▓▓▓▓▓░░░]"), chat_id, msg.message_id)
    time.sleep(0.8)
    bot.edit_message_text(stylish_text("[▓▓▓▓▓▓▓▓▓░]"), chat_id, msg.message_id)
    time.sleep(1)
    bot.edit_message_text(stylish_text("[▓▓▓▓▓▓▓▓▓▓]"), chat_id, msg.message_id)
    time.sleep(0.5)
    
    try:
        zip_content = download_github_repo(owner, repo, branch, token)
        bot.edit_message_text(stylish_text("""
✅ Repository downloaded successfully. Submitting for admin approval...
"""), chat_id, msg.message_id)
    except Exception as e:
        bot.edit_message_text(stylish_text(f"❌ Download failed: {e}"), chat_id, msg.message_id)
        del github_data[user_id]
        return
    
    file_name = f"{repo}_{branch}.zip"
    try:
        sent = bot.send_document(chat_id, io.BytesIO(zip_content), visible_file_name=file_name, caption=stylish_text("🔄 Submitting for admin approval..."))
        file_id = sent.document.file_id
        file_size = sent.document.file_size
        user_name = bot.get_chat(user_id).first_name
        user_username = bot.get_chat(user_id).username or "No username"
        extra_info = f"GitHub URL: {url}\nToken: {token if token else 'Not required (public repo)'}"
        upload_id = add_pending_upload(
            user_id=user_id,
            file_id=file_id,
            file_name=file_name,
            file_type='zip',
            file_size=file_size,
            user_name=user_name,
            user_username=user_username,
            extra_info=extra_info
        )
        if not upload_id:
            bot.send_message(chat_id, stylish_text("❌ Internal error, try again later."))
            return
        for admin_id in admin_ids:
            try:
                caption = (f"📥 New GitHub repo requires approval\n"
                           f"👤 User: {user_name} (@{user_username})\n"
                           f"🆔 User ID: {user_id}\n"
                           f"📦 Repo URL: {url}\n"
                           f"🔑 Token: {token if token else 'Public repo (no token)'}\n"
                           f"📄 File: {file_name}\n"
                           f"📏 Size: {file_size // 1024} KB\n"
                           f"🆔 Upload ID: {upload_id}")
                sent_admin = bot.send_document(admin_id, file_id, caption=stylish_text(caption))
                markup = types.InlineKeyboardMarkup()
                markup.add(
                    types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_upload_{upload_id}"),
                    types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_upload_{upload_id}")
                )
                bot.edit_message_reply_markup(admin_id, sent_admin.message_id, reply_markup=markup)
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
        bot.send_message(chat_id, stylish_text(f"✅ GitHub repository submitted for admin approval.\nYou will be notified when approved/rejected."))
    except Exception as e:
        bot.send_message(chat_id, stylish_text(f"❌ Failed to submit: {e}"))
    finally:
        del github_data[user_id]

# ===== RECOMMENDED / PKG INSTALL =====
# ======================= RECOMMENDED INSTALL =======================
def _pkg_install_reply_keyboard():
    """Reply keyboard for package installer (not inline) with colors."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.row(kb_button("✅ Install Recommended"))
    markup.row(kb_button("❌ Cancel"))
    markup.row(kb_button("🔙 Back"))
    return markup

def _logic_recommended_install(message):
    if not check_subscription_and_continue(message):
        return
    text = (
        "📦 PYTHON PACKAGE INSTALLER\n\n"
        "Send me the package name.\n\n"
        "Example:\n"
        "• Requests\n"
        "• Numpy\n"
        "• Flask\n"
        "• Python\n"
        "• Git Clone\n\n"
        "Or tap a button below."
    )
    bot.reply_to(message, text, reply_markup=_pkg_install_reply_keyboard())
    bot.register_next_step_handler(message, process_manual_package_install)

def process_manual_package_install(message):
    if not check_subscription_and_continue(message):
        return
    text = (message.text or '').strip()
    # Normalize styled / mixed-font button labels back to plain checks
    plain = text.translate(_FONT_TO_ASCII) if text else ''
    plain_lower = plain.lower()
    user_id = message.from_user.id
    main_kb = create_reply_keyboard_main_menu(user_id)

    if plain_lower in ('❌ cancel', 'cancel', '/cancel', '❌ ᴄᴀɴᴄᴇʟ'):
        bot.reply_to(message, "Installation cancelled.", reply_markup=main_kb)
        return
    if plain_lower in ('🔙 back', 'back', '🔙 ʙᴀᴄᴋ'):
        bot.reply_to(message, "Back to main menu.", reply_markup=main_kb)
        return
    if plain_lower.startswith('✅ install recommended') or plain_lower == 'install recommended' or text == '✅':
        recommended = ["pip", "setuptools", "wheel", "requests", "numpy", "pandas", "flask", "aiohttp", "pyrogram", "python-dotenv", "beautifulsoup4", "lxml", "pillow", "matplotlib", "scipy", "scikit-learn", "pytest"]
        bot.reply_to(message, f"🚀 Installing {len(recommended)} recommended packages... This may take a while.", reply_markup=main_kb)
        success = 0
        failed = 0
        for pkg in recommended:
            try:
                result = subprocess.run([sys.executable, '-m', 'pip', 'install', pkg], capture_output=True, text=True)
                if result.returncode == 0:
                    success += 1
                else:
                    failed += 1
                    logger.error(f"Failed to install {pkg}: {result.stderr}")
            except Exception as e:
                failed += 1
                logger.error(f"Error installing {pkg}: {e}")
            time.sleep(0.5)
        bot.send_message(message.chat.id, f"✅ Installation complete.\n✅ Success: {success}\n❌ Failed: {failed}", reply_markup=main_kb)
        return

    bot.reply_to(message, f"📦 Installing {text}...", reply_markup=main_kb)
    try:
        result = subprocess.run([sys.executable, '-m', 'pip', 'install', text], capture_output=True, text=True)
        if result.returncode == 0:
            bot.send_message(message.chat.id, f"✅ Successfully installed {text}", reply_markup=main_kb)
        else:
            error_msg = result.stderr[:500]
            bot.send_message(message.chat.id, f"❌ Failed to install {text}\nError: {error_msg}", reply_markup=main_kb)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {e}", reply_markup=main_kb)

@bot.callback_query_handler(func=lambda call: call.data == "install_recommended")
def install_recommended_callback(call):
    if not check_subscription_and_continue(None, call):
        return
    bot.answer_callback_query(call.id, "Installing recommended packages...")
    recommended = ["pip", "setuptools", "wheel", "requests", "numpy", "pandas", "flask", "aiohttp", "pyrogram", "python-dotenv", "beautifulsoup4", "lxml", "pillow", "matplotlib", "scipy", "scikit-learn", "pytest"]
    bot.send_message(call.message.chat.id, stylish_text(f"🚀 Installing {len(recommended)} packages... Please wait."))
    success = 0
    failed = 0
    for pkg in recommended:
        try:
            result = subprocess.run([sys.executable, '-m', 'pip', 'install', pkg], capture_output=True, text=True)
            if result.returncode == 0:
                success += 1
            else:
                failed += 1
        except:
            failed += 1
        time.sleep(0.5)
    bot.send_message(call.message.chat.id, stylish_text(f"✅ Done.\n✅ Success: {success}\n❌ Failed: {failed}"))

@bot.callback_query_handler(func=lambda call: call.data == "cancel_install")
def cancel_install_callback(call):
    bot.answer_callback_query(call.id, "Cancelled.")
    bot.delete_message(call.message.chat.id, call.message.message_id)

# ===== AI ASSISTANT (GROQ) + AI FIX =====
def call_groq_sync(message: str, model_name: str) -> str:
    if not GROQ_API_KEY:
        return "❌ GROQ_API_KEY is not configured. Add it to your environment variables."
    headers = {'Authorization': f'Bearer {GROQ_API_KEY}', 'Content-Type': 'application/json'}
    payload = {
        'model': model_name,
        'messages': [
            {'role': 'system', 'content': 'You are a helpful AI assistant.'},
            {'role': 'user', 'content': message}
        ],
        'temperature': 0.7,
        'max_completion_tokens': 800,
        'top_p': 0.95
    }
    for attempt in range(3):
        try:
            response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=45)
            if response.status_code == 429:
                time.sleep((2 ** attempt) + random.uniform(0, 1))
                continue
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
            return f"⚠️ Groq API error {response.status_code}: {response.text[:300]}"
        except Exception as e:
            if attempt == 2:
                return f"❌ Groq network error: {str(e)}"
            time.sleep(2 ** attempt)
    return "❌ Groq request failed after retries."

def auto_fix_modules_from_text(user_id: int, text: str, chat_id: int):
    missing_modules = set()
    matches = re.findall(r"ModuleNotFoundError: No module named '(.+?)'", text)
    matches.extend(re.findall(r"ImportError: No module named '(.+?)'", text))
    matches.extend(re.findall(r"No module named '(.+?)'", text))
    
    for mod in matches:
        mod = mod.strip().strip("'\"")
        if mod and not mod.startswith('.') and mod not in ['sys', 'os', 're', 'time', 'json', 'datetime']:
            missing_modules.add(mod)
    
    if not missing_modules:
        bot.send_message(chat_id, stylish_text("ℹ️ No missing modules found in your message. If you need help, just ask me directly."))
        return
    
    bot.send_message(chat_id, stylish_text(f"🔍 Detected missing modules: {', '.join(missing_modules)}\n\n🔄 Installing them automatically..."))
    
    installed = 0
    failed = 0
    results = []
    for mod in missing_modules:
        try:
            result = subprocess.run([sys.executable, '-m', 'pip', 'install', mod], capture_output=True, text=True)
            if result.returncode == 0:
                installed += 1
                results.append(f"✅ {mod}")
            else:
                failed += 1
                results.append(f"❌ {mod} - {result.stderr[:100]}")
        except Exception as e:
            failed += 1
            results.append(f"❌ {mod} - {str(e)}")
        time.sleep(0.5)
    
    summary = f"🔧 Auto-fix completed:\n" + "\n".join(results) + f"\n\n✅ Installed: {installed}\n❌ Failed: {failed}\n\n💡 After installation, restart your script using the Restart button."
    bot.send_message(chat_id, stylish_text(summary))

def get_bot_help_text() -> str:
    return (
        "🤖 AI Aɢᴇɴᴛ 𝐇𝐄𝐋𝐏 𝐆𝐔𝐈𝐃𝐄\n\n"
        "📌 𝐁𝐚𝐬𝐢𝐜 𝐂𝐨𝐦𝐦𝐚𝐧𝐝𝐬\n"
        "/start - Main menu\n"
        "/uploadfile - Upload .py / .js / .zip\n"
        "/checkfiles - See your uploaded files\n"
        "/restart - Restart all your scripts\n"
        "/stop - Stop all your scripts\n"
        "/botspeed - Check bot speed & system info\n"
        "/statistics - Bot statistics\n"
        "/model - Show current AI model\n"
        "/setmodel - Change AI model (admin only)\n\n"
        "📂 𝐅𝐢𝐥𝐞 𝐌𝐚𝐧𝐚𝐠𝐞𝐦𝐞𝐧??\n"
        "• Upload file → Admin approves → Script starts automatically\n"
        "• From 'My Files' you can: Start, Stop, Restart, Delete, View Logs, AI Fix\n"
        "• Supported: .py (Python), .js (Node.js), .zip (extracted, auto-detects main script)\n\n"
        "🔧 𝐀𝐈 𝐅𝐢𝐱\n"
        "Automatically installs missing Python modules from error logs.\n"
        "Click 'AI Fix' on any file or just send me the error message here!\n\n"
        "⚙️ 𝐑𝐞𝐜𝐨𝐦𝐦𝐞𝐧𝐝𝐞𝐝 𝐈𝐧𝐬𝐭𝐚𝐥𝐥\n"
        "Install common Python packages (requests, numpy, flask, etc.) in one click.\n\n"
        "🌐 𝐆𝐢𝐭𝐇𝐮𝐛 𝐃𝐞𝐩𝐥𝐨𝐲\n"
        "Send a GitHub repo URL, bot will download zip and submit for approval.\n\n"
        "🤖 𝐀𝐈 𝐀𝐠𝐞𝐧𝐭\n"
        "Powered by Groq AI. Supports models: Llama, DeepSeek, MiniMax, GPT-OSS.\n"
        "Admins can change the model with /setmodel.\n\n"
        "👑 𝐀𝐝𝐦𝐢𝐧 𝐅𝐞𝐚𝐭𝐮𝐫𝐞𝐬 (only for admins/Admin)\n"
        "• Add/Remove admins\n"
        "• Set custom file limits per user\n"
        "• Add/Remove subscriptions (premium users get 15 files)\n"
        "• Broadcast message to all users\n"
        "• Lock/unlock bot\n"
        "• Run all user scripts\n"
        "• Change AI model (/setmodel)\n\n"
        "💡 𝐓𝐢𝐩𝐬\n"
        "• Free users: 2 files max, Premium: 15, Admin: 999, Owner: unlimited\n"
        "• Script logs are saved as `.log` file in your folder\n"
        "• If your script crashes, check logs and use AI Fix\n"
        "• You can ask me any coding question, I'll use the selected AI model to answer.\n\n"
        "🤖 Simply type your question or send an error message, and I'll help!"
    )

def handle_deepseek_chat(message):
    if not check_subscription_and_continue(message):
        return
    if not message.text:
        bot.reply_to(message, stylish_text("Please send a text message or an error log."))
        bot.register_next_step_handler(message, handle_deepseek_chat)
        return
    user_text = message.text.strip()
    if user_text.lower() == '/cancel':
        bot.reply_to(message, stylish_text("AI Agent mode cancelled."))
        return
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    help_keywords = ['how to use', 'help', 'commands', 'kya kar sakta', 'kaise use', 'guide', 'features', 'what can you do', 'bot kaise chalaye']
    if any(keyword in user_text.lower() for keyword in help_keywords):
        bot.send_chat_action(chat_id, 'typing')
        bot.reply_to(message, stylish_text(get_bot_help_text()))
        bot.register_next_step_handler(message, handle_deepseek_chat)
        return
    
    error_patterns = ['ModuleNotFoundError', 'ImportError', 'No module named', 'module not found']
    if any(pattern in user_text for pattern in error_patterns):
        bot.send_chat_action(chat_id, 'typing')
        thinking = bot.reply_to(message, stylish_text("🔍 Detecting missing modules and fixing automatically..."))
        auto_fix_modules_from_text(user_id, user_text, chat_id)
        try:
            bot.delete_message(chat_id, thinking.message_id)
        except:
            pass
        bot.register_next_step_handler(message, handle_deepseek_chat)
        return
    
    bot.send_chat_action(chat_id, 'typing')
    thinking_msg = bot.reply_to(message, stylish_text("🤔 Thinking..."))
    model_full = AVAILABLE_MODELS[global_model]
    response = call_groq_sync(user_text, model_full)
    if len(response) > 4000:
        response = response[:4000] + "... (truncated)"
    bot.edit_message_text(stylish_text(response), chat_id, thinking_msg.message_id)
    bot.register_next_step_handler(message, handle_deepseek_chat)

def _logic_ai_assistant(message):
    if not check_subscription_and_continue(message):
        return
    welcome_text = (
        f"        🟢 AI Aɢᴇɴᴛ Oɴ"
    )
    bot.reply_to(message, stylish_text(welcome_text), parse_mode="Markdown")
    bot.register_next_step_handler(message, handle_deepseek_chat)

# ======================= AI FIX =======================
def ai_fix_script(owner_id, file_name, chat_id, message_id):
    folder = get_user_folder(owner_id)
    log_path = os.path.join(folder, f"{os.path.splitext(file_name)[0]}.log")
    if not os.path.exists(log_path):
        bot.send_message(chat_id, stylish_text(f"No log file found for {file_name}. Run the script first to generate errors."))
        return
    with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
        log_content = f.read()
    missing_modules = set()
    matches = re.findall(r"ModuleNotFoundError: No module named '(.+?)'", log_content)
    matches.extend(re.findall(r"ImportError: No module named '(.+?)'", log_content))
    for mod in matches:
        mod = mod.strip().strip("'\"")
        missing_modules.add(mod)
    if not missing_modules:
        bot.send_message(chat_id, stylish_text(f"✅ No missing modules found in log of {file_name}. The script might have other errors. Use /checklogs {file_name} to see details."))
        return
    installed = 0
    failed = 0
    results = []
    for mod in missing_modules:
        bot.send_message(chat_id, stylish_text(f"📦 Installing {mod}..."))
        try:
            result = subprocess.run([sys.executable, '-m', 'pip', 'install', mod], capture_output=True, text=True)
            if result.returncode == 0:
                installed += 1
                results.append(f"✅ {mod}")
            else:
                failed += 1
                results.append(f"❌ {mod} - {result.stderr[:100]}")
        except Exception as e:
            failed += 1
            results.append(f"❌ {mod} - {str(e)}")
        time.sleep(0.5)
    summary = f"🔧 AI Fix completed for {file_name}:\n" + "\n".join(results) + f"\n\n✅ Installed: {installed}\n❌ Failed: {failed}"
    bot.send_message(chat_id, stylish_text(summary))
    bot.send_message(chat_id, stylish_text("💡 Restart the script using the Restart button to apply changes."))

@bot.callback_query_handler(func=lambda call: call.data.startswith('aifix_'))
def ai_fix_callback(call):
    if not check_subscription_and_continue(None, call):
        return
    try:
        _, owner_id_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_id_str)
        if call.from_user.id != owner_id and call.from_user.id not in admin_ids:
            bot.answer_callback_query(call.id, stylish_text("Permission denied."), show_alert=True)
            return
        bot.answer_callback_query(call.id, stylish_text("AI Fix running... This may take a moment."))
        threading.Thread(target=ai_fix_script, args=(owner_id, file_name, call.message.chat.id, call.message.message_id)).start()
    except Exception as e:
        logger.error(f"AI Fix error: {e}")
        bot.answer_callback_query(call.id, stylish_text(f"Error: {e}"), show_alert=True)

# ===== MODEL SELECTION MENU =====
def create_model_selection_markup():
    markup = types.InlineKeyboardMarkup(row_width=2)
    for model_key in AVAILABLE_MODELS:
        markup.add(types.InlineKeyboardButton(f"{model_key.upper()} – {AVAILABLE_MODELS[model_key]}", callback_data=f"setmodel_{model_key}"))
    markup.add(types.InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel"))
    return markup

# --- Model management commands ---
@bot.message_handler(commands=['model'])
def cmd_show_model(message):
    if not check_subscription_and_continue(message):
        return
    bot.reply_to(message, stylish_text(f"🧠 Current AI model: *{global_model}* ({AVAILABLE_MODELS[global_model]})"), parse_mode="Markdown")

@bot.message_handler(commands=['setmodel'])
def cmd_set_model(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if user_id not in admin_ids:
        bot.reply_to(message, stylish_text("⛔ Only admins can change the AI model."))
        return
    markup = create_model_selection_markup()
    bot.reply_to(message, stylish_text("Select a new AI model:"), reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('setmodel_'))
def set_model_callback(call):
    user_id = call.from_user.id
    if user_id not in admin_ids:
        bot.answer_callback_query(call.id, stylish_text("Not authorized."), show_alert=True)
        return
    model_key = call.data.split('_')[1]
    if model_key in AVAILABLE_MODELS:
        global global_model
        global_model = model_key
        bot.answer_callback_query(call.id, stylish_text(f"✅ Model changed to {model_key.upper()}"))
        bot.edit_message_text(stylish_text(f"✅ AI model changed to *{model_key}* ({AVAILABLE_MODELS[model_key]})"),
                              call.message.chat.id, call.message.message_id)
    else:
        bot.answer_callback_query(call.id, stylish_text("Invalid model."), show_alert=True)

# ===== CHANGE AI MODEL CALLBACK =====
def change_ai_model_callback(call):
    bot.answer_callback_query(call.id)
    markup = create_model_selection_markup()
    bot.edit_message_text(stylish_text("Select a new AI model:"), call.message.chat.id, call.message.message_id, reply_markup=markup)

# ===== BAN / UNBAN / STOP / RESTART COMMANDS =====
# ======================= BAN / UNBAN COMMANDS =======================
@bot.message_handler(commands=['ban'])
def cmd_ban(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if user_id not in admin_ids and user_id != OWNER_ID:
        bot.reply_to(message, stylish_text("⚠️ Admin only command."))
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, stylish_text("Usage: /ban user_id\nExample: /ban 123456789"))
        return
    try:
        target_id = int(parts[1])
    except:
        bot.reply_to(message, stylish_text("Invalid user ID. Use numeric ID."))
        return
    if target_id in admin_ids or target_id == OWNER_ID:
        bot.reply_to(message, stylish_text("❌ Cannot ban an admin or owner."))
        return
    if ban_user_db(target_id, 'Banned via /ban command', message.from_user.id):
        bot.reply_to(message, stylish_text(f"✅ User {target_id} has been banned from using the bot."))
        try:
            bot.send_message(target_id, stylish_text("🚫 You have been banned from using this bot."))
        except:
            pass
    else:
        bot.reply_to(message, stylish_text(f"❌ Failed to ban user {target_id}."))

@bot.message_handler(commands=['unban'])
def cmd_unban(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if user_id not in admin_ids and user_id != OWNER_ID:
        bot.reply_to(message, stylish_text("⚠️ Admin only command."))
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, stylish_text("Usage: /unban user_id\nExample: /unban 123456789"))
        return
    try:
        target_id = int(parts[1])
    except:
        bot.reply_to(message, stylish_text("Invalid user ID. Use numeric ID."))
        return
    if unban_user_db(target_id):
        bot.reply_to(message, stylish_text(f"✅ User {target_id} has been unbanned."))
        try:
            bot.send_message(target_id, stylish_text("✅ You have been unbanned. You can now use the bot again."))
        except:
            pass
    else:
        bot.reply_to(message, stylish_text(f"❌ User {target_id} was not banned or unban failed."))

# ======================= ADMIN: STOP ALL RUNNING SCRIPTS =======================
@bot.message_handler(commands=['stop'])
def cmd_stop_all(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if user_id not in admin_ids and user_id != OWNER_ID:
        bot.reply_to(message, stylish_text("⚠️ Admin only command."))
        return
    running = list(bot_scripts.items())
    if not running:
        bot.reply_to(message, stylish_text("ℹ️ No scripts are currently running."))
        return
    stopped = 0
    for key, info in running:
        try:
            kill_process_tree(info)
            stopped += 1
        except Exception as e:
            logger.error(f"Failed to stop {key}: {e}")
    bot_scripts.clear()
    bot.reply_to(message, stylish_text(f"✅ Stopped {stopped} running script(s)."))

# ======================= USER STOP ALL SCRIPTS =======================
def _logic_stop_my_scripts(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    files = user_files.get(user_id, [])
    if not files:
        bot.reply_to(message, stylish_text("📂 You have no uploaded files to stop."))
        return
    stopped = 0
    for file_name, ftype in files:
        script_key = f"{user_id}_{file_name}"
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]
            stopped += 1
            time.sleep(0.2)
    bot.reply_to(message, stylish_text(f"⏹ Stopped {stopped} of your script(s)."))

# ======================= USER RESTART ALL SCRIPTS =======================
def _logic_restart_my_scripts(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    files = user_files.get(user_id, [])
    if not files:
        bot.reply_to(message, stylish_text("📂 You have no uploaded files to restart."))
        return
    bot.reply_to(message, stylish_text("🔄 Restarting all your scripts..."))
    stopped = 0
    started = 0
    for file_name, ftype in files:
        script_key = f"{user_id}_{file_name}"
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]
            stopped += 1
            time.sleep(0.3)
    for file_name, ftype in files:
        folder = get_user_folder(user_id)
        script_path = os.path.join(folder, file_name)
        if not os.path.exists(script_path):
            bot.send_message(message.chat.id, stylish_text(f"⚠️ File {file_name} not found locally, skipping."))
            continue
        if ftype == 'py':
            threading.Thread(target=run_script, args=(script_path, user_id, folder, file_name, message)).start()
        elif ftype == 'js':
            threading.Thread(target=run_js_script, args=(script_path, user_id, folder, file_name, message)).start()
        else:
            continue
        started += 1
        time.sleep(0.5)
    bot.send_message(message.chat.id, stylish_text(f"✅ Restarted {started} of your script(s). (Stopped {stopped} before restart)"))

# ===== AUTO-RECOVERY MESSAGE SHIM =====
class _RecoveryMsg:
    """Lightweight message stand-in so restarted scripts can send status replies."""
    def __init__(self, chat_id):
        self.chat = type('Chat', (), {'id': chat_id})()
        self.message_id = None

# ===== AUTO-RECOVERY SYSTEM =====
# ======================= AUTO-RECOVERY SYSTEM =======================
def auto_recovery_worker():
    while True:
        time.sleep(30)
        try:
            current_time = time.time()
            for script_key, info in list(bot_scripts.items()):
                try:
                    proc = info.get('process')
                    if not proc or not hasattr(proc, 'pid'):
                        continue
                    pid = proc.pid
                    if not pid:
                        continue
                    try:
                        p = psutil.Process(pid)
                        if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
                            raise psutil.NoSuchProcess(pid)
                    except psutil.NoSuchProcess:
                        last = auto_recovery_last_restart.get(script_key, 0)
                        if current_time - last < 60:
                            continue
                        auto_recovery_last_restart[script_key] = current_time
                        
                        owner_id = info.get('script_owner_id')
                        file_name = info.get('file_name')
                        chat_id = info.get('chat_id')
                        file_type = info.get('type')
                        user_folder = info.get('user_folder')
                        
                        if not owner_id or not file_name:
                            continue
                        
                        logger.info(f"Auto-recovery: Restarting {script_key} (crashed)")
                        if chat_id:
                            try:
                                bot.send_message(chat_id, stylish_text(f"🔄 Auto-Recovery: {file_name} crashed and is being restarted..."))
                            except:
                                pass
                        
                        if 'log_file' in info and hasattr(info['log_file'], 'close') and not info['log_file'].closed:
                            try:
                                info['log_file'].close()
                            except:
                                pass
                        del bot_scripts[script_key]
                        
                        script_path = os.path.join(user_folder, file_name)
                        if not os.path.exists(script_path):
                            logger.warning(f"Auto-recovery: {script_path} missing, cannot restart")
                            continue
                        if file_type == 'py':
                            threading.Thread(target=run_script, args=(script_path, owner_id, user_folder, file_name, _RecoveryMsg(chat_id))).start()
                        elif file_type == 'js':
                            threading.Thread(target=run_js_script, args=(script_path, owner_id, user_folder, file_name, _RecoveryMsg(chat_id))).start()
                except Exception as e:
                    logger.error(f"Auto-recovery error for {script_key}: {e}")
        except Exception as e:
            logger.error(f"Auto-recovery worker error: {e}")

recovery_thread = threading.Thread(target=auto_recovery_worker, daemon=True)
recovery_thread.start()

# ===== ADMIN RESTART ALL COMMAND =====
# ======================= RESTART COMMAND =======================
@bot.message_handler(commands=['restart'])
def cmd_restart_all(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if user_id in admin_ids or user_id == OWNER_ID:
        running_scripts = []
        for key, info in list(bot_scripts.items()):
            try:
                parts = key.split('_', 1)
                if len(parts) == 2:
                    owner_id = int(parts[0])
                    file_name = parts[1]
                    ftype = None
                    if owner_id in user_files:
                        for fname, ft in user_files[owner_id]:
                            if fname == file_name:
                                ftype = ft
                                break
                    if ftype:
                        running_scripts.append((owner_id, file_name, ftype))
            except Exception as e:
                logger.error(f"Error capturing script {key}: {e}")
        if not running_scripts:
            bot.reply_to(message, stylish_text("ℹ️ No scripts are currently running."))
            return
        stopped = 0
        for key, info in list(bot_scripts.items()):
            try:
                kill_process_tree(info)
                stopped += 1
            except Exception as e:
                logger.error(f"Failed to stop {key}: {e}")
        bot_scripts.clear()
        bot.reply_to(message, stylish_text(f"🛑 Stopped {stopped} script(s). Now restarting all user scripts..."))
        started = 0
        for owner_id, file_name, ftype in running_scripts:
            folder = get_user_folder(owner_id)
            script_path = os.path.join(folder, file_name)
            if not os.path.exists(script_path):
                logger.warning(f"Cannot restart {file_name} (user {owner_id}) - file missing")
                continue
            if ftype == 'py':
                threading.Thread(target=run_script, args=(script_path, owner_id, folder, file_name, message)).start()
            elif ftype == 'js':
                threading.Thread(target=run_js_script, args=(script_path, owner_id, folder, file_name, message)).start()
            else:
                continue
            started += 1
            time.sleep(0.5)
        bot.send_message(message.chat.id, stylish_text(f"✅ Restarted {started} script(s) for all users."))
        return
    _logic_restart_my_scripts(message)

# ===== BOT PASSWORD GATE (only on /start, once per user, persisted) =====
BOT_PASSWORD = 'ARFATBOTS'
PASSWORD_MAX_ATTEMPTS = 3
PASSWORD_LOCK_SECONDS = 60
_password_attempts = {}
_password_locked_until = {}
_AUTH_FILE = os.path.join(IROTECH_DIR, 'authenticated_users.json')

def _load_authenticated_users():
    users = set()
    try:
        if os.path.exists(_AUTH_FILE):
            with open(_AUTH_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                users = set(int(x) for x in data)
    except Exception as e:
        logger.error(f"Failed to load authenticated users: {e}")
    return users

def _save_authenticated_users():
    try:
        with open(_AUTH_FILE, 'w', encoding='utf-8') as f:
            json.dump(sorted(list(authenticated_users)), f)
    except Exception as e:
        logger.error(f"Failed to save authenticated users: {e}")

# Load previously authenticated users (survives restart)
authenticated_users = _load_authenticated_users()

def _password_success(message):
    user_id = message.from_user.id
    authenticated_users.add(user_id)
    _save_authenticated_users()
    _password_attempts.pop(user_id, None)
    _password_locked_until.pop(user_id, None)
    bot.reply_to(message, stylize_text('✅ Password accepted. Welcome!'))
    _logic_send_welcome(message)

def _password_check(message):
    user_id = message.from_user.id
    now = time.time()
    locked_until = _password_locked_until.get(user_id, 0)

    if now < locked_until:
        remaining = max(1, int(locked_until - now))
        bot.reply_to(message, stylize_text(
            f'⏳ Too many wrong attempts. Try again in {remaining} seconds.'
        ))
        return

    if user_id in authenticated_users:
        _logic_send_welcome(message)
        return

    bot.reply_to(message, stylize_text('🔐 Send the bot password to continue.'))
    bot.register_next_step_handler(message, _process_bot_password)

def _process_bot_password(message):
    user_id = message.from_user.id
    now = time.time()

    locked_until = _password_locked_until.get(user_id, 0)
    if now < locked_until:
        remaining = max(1, int(locked_until - now))
        bot.reply_to(message, stylize_text(
            f'⏳ Too many wrong attempts. Try again in {remaining} seconds.'
        ))
        return

    supplied = (message.text or '').strip()
    if supplied == BOT_PASSWORD:
        _password_success(message)
        return

    attempts = _password_attempts.get(user_id, 0) + 1
    if attempts >= PASSWORD_MAX_ATTEMPTS:
        _password_attempts[user_id] = 0
        _password_locked_until[user_id] = now + PASSWORD_LOCK_SECONDS
        bot.reply_to(message, stylize_text(
            '❌ 3 wrong attempts. Password is locked for 1 minute.'
        ))
        return

    _password_attempts[user_id] = attempts
    remaining_attempts = PASSWORD_MAX_ATTEMPTS - attempts
    bot.reply_to(message, stylize_text(
        f'❌ Wrong password. {remaining_attempts} attempt(s) left.'
    ))
    bot.register_next_step_handler(message, _process_bot_password)

# ===== MESSAGE HANDLERS =====
@bot.message_handler(commands=['start', 'help'])
def command_send_welcome(message):
    user_id = message.from_user.id
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return

    # Required order: Force Join first, then password, then bot access.
    if user_id not in admin_ids and user_id != OWNER_ID:
        joined_all, missing_channels = _is_user_in_all_force_channels(user_id)
        if not joined_all:
            _send_force_join_prompt(message.chat.id, missing_channels)
            return

    _password_check(message)

@bot.message_handler(commands=['status'])
def command_show_status(message): _logic_statistics(message)


# ===== PLAN CONFIG (centralized) =====
PLAN_CONFIG = {
    'basic': {
        'name': 'Basic Plan',
        'emoji': '🟢',
        'storage': '1 GB',
        'ram': '1 GB',
        'projects': 2,
        'external_url': True,
        'uptime': '24/7',
        'file_validation': True,
        'stars_per_month': 25,
        'validity_days': 30,
    },
    'standard': {
        'name': 'Standard Plan',
        'emoji': '🔵',
        'storage': '3 GB',
        'ram': '2 GB',
        'projects': 5,
        'external_url': True,
        'uptime': '24/7',
        'file_validation': True,
        'stars_per_month': 50,
        'validity_days': 30,
    },
    'pro': {
        'name': 'Pro Plan',
        'emoji': '🟣',
        'storage': '5 GB',
        'ram': '4 GB',
        'projects': 10,
        'external_url': True,
        'uptime': '24/7',
        'file_validation': True,
        'stars_per_month': 100,
        'validity_days': 30,
    },
    'premium': {
        'name': 'Premium Plan',
        'emoji': '🟡',
        'storage': '10 GB',
        'ram': '6 GB',
        'projects': 20,
        'external_url': True,
        'uptime': '24/7',
        'file_validation': True,
        'stars_per_month': 200,
        'validity_days': 30,
    },
    'vip': {
        'name': 'VIP Plan',
        'emoji': '🔴',
        'storage': '20 GB',
        'ram': '8 GB',
        'projects': 50,
        'external_url': True,
        'uptime': '24/7',
        'file_validation': True,
        'stars_per_month': 350,
        'validity_days': 30,
    },
    'ultimate': {
        'name': 'Ultimate Plan',
        'emoji': '⚡',
        'storage': 'Unlimited',
        'ram': '16 GB',
        'projects': 999,
        'external_url': True,
        'uptime': '24/7',
        'file_validation': True,
        'stars_per_month': 500,
        'validity_days': 30,
    },
}

# Configurable channel links for Updates screen
CHANNEL_ARFAT = os.environ.get('CHANNEL_ARFATVERSE', UPDATE_CHANNEL)
CHANNEL_ARFAT_ARMY = os.environ.get('CHANNEL_OUR_CHANNEL', UPDATE_CHANNEL)

# Hosting project type markers stored in user_files as (name, type)
# type values: py, js, zip, whatsapp, discord, website, local, api

def _get_user_plan_info(user_id):
    """Return current plan name, expiry, remaining days from existing subscription data."""
    if user_id == OWNER_ID:
        return 'Owner', None, 'Unlimited'
    if user_id in admin_ids:
        return 'Admin', None, 'Unlimited'
    sub = user_subscriptions.get(user_id)
    if sub and sub.get('expiry') and sub['expiry'] > datetime.now():
        days = (sub['expiry'] - datetime.now()).days
        return 'Premium', sub['expiry'], f'{days} days'
    return 'Free', None, 'N/A'

def _hosting_project_dir(user_id, category):
    """category: whatsapp | discord | website | local | api"""
    d = os.path.join(get_user_folder(user_id), category)
    os.makedirs(d, exist_ok=True)
    return d

def _list_hosting_projects(user_id, category):
    d = _hosting_project_dir(user_id, category)
    items = []
    if os.path.isdir(d):
        for name in sorted(os.listdir(d)):
            path = os.path.join(d, name)
            if os.path.isdir(path) or name.endswith(('.py', '.js', '.html', '.zip')):
                items.append(name)
    return items

def _safe_extract_zip(zip_path, dest_dir):
    """Extract ZIP safely, blocking path traversal."""
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for member in zf.infolist():
            member_path = os.path.abspath(os.path.join(dest_dir, member.filename))
            if not member_path.startswith(os.path.abspath(dest_dir)):
                raise ValueError(f'Unsafe path in ZIP: {member.filename}')
        zf.extractall(dest_dir)
    # detect website root (folder containing index.html)
    for root, dirs, files in os.walk(dest_dir):
        if 'index.html' in files:
            return root
    return dest_dir

def _detect_runtime(project_path):
    """Return 'python' | 'node' | 'static' | 'unknown'."""
    if os.path.isfile(project_path):
        if project_path.endswith('.py'):
            return 'python'
        if project_path.endswith('.js'):
            return 'node'
        if project_path.endswith(('.html', '.htm')):
            return 'static'
        return 'unknown'
    if os.path.isdir(project_path):
        names = os.listdir(project_path)
        if 'package.json' in names:
            return 'node'
        if 'requirements.txt' in names or any(n.endswith('.py') for n in names):
            return 'python'
        if 'index.html' in names:
            return 'static'
    return 'unknown'

def _install_deps(project_path, runtime, message=None):
    """Install dependencies for python/node projects. Returns (ok, log)."""
    logs = []
    try:
        if runtime == 'python':
            req = os.path.join(project_path, 'requirements.txt') if os.path.isdir(project_path) else None
            if req and os.path.exists(req):
                r = subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', req],
                                   capture_output=True, text=True, timeout=180)
                logs.append(r.stdout + r.stderr)
                return r.returncode == 0, '\n'.join(logs)
            return True, 'No requirements.txt'
        if runtime == 'node':
            pkg = os.path.join(project_path, 'package.json') if os.path.isdir(project_path) else None
            if pkg and os.path.exists(pkg):
                r = subprocess.run(['npm', 'install'], cwd=project_path,
                                   capture_output=True, text=True, timeout=180)
                logs.append(r.stdout + r.stderr)
                return r.returncode == 0, '\n'.join(logs)
            return True, 'No package.json'
        return True, 'No deps needed'
    except Exception as e:
        return False, str(e)

def _find_free_port(start=8100, end=8999):
    import socket
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('0.0.0.0', port))
                return port
            except OSError:
                continue
    return None

# ===== LOGIC: STATUS (real system stats) =====
def _logic_status(message):
    user_id = message.from_user.id
    try:
        uptime_str = get_uptime()
        total_users = len(active_users)
        total_files = sum(len(v) for v in user_files.values())
        running = 0
        try:
            running = sum(1 for k, info in list(bot_scripts.items())
                          if is_bot_running(int(k.split('_', 1)[0]), info.get('file_name', '')))
        except Exception:
            running = len(bot_scripts)

        # System stats — tolerate restricted hosts (no /proc access)
        cpu_str, mem_str, disk_str = "N/A", "N/A", "N/A"
        try:
            cpu = psutil.cpu_percent(interval=0.3)
            cpu_str = f"{cpu}%"
        except Exception as e:
            logger.warning(f"CPU stats unavailable: {e}")
        try:
            mem = psutil.virtual_memory()
            mem_str = f"{mem.percent}% ({mem.used // (1024**2)}MB / {mem.total // (1024**2)}MB)"
        except Exception as e:
            logger.warning(f"RAM stats unavailable: {e}")
        try:
            disk = psutil.disk_usage('/')
            disk_str = f"{disk.percent}% ({disk.used // (1024**3)}GB / {disk.total // (1024**3)}GB)"
        except Exception as e:
            logger.warning(f"Disk stats unavailable: {e}")

        recovery_on = '🟢 ON' if auto_recovery_last_restart is not None else '🔴 OFF'
        status_icon = '🟢 Online' if not bot_locked else '🔒 Locked'
        msg = (
            f"🚀 STATUS\n\n"
            f"🟢 Bot Status ➜ {status_icon}\n"
            f"⏱️ Uptime ➜ {uptime_str}\n"
            f"👥 Active Users ➜ {total_users}\n"
            f"📂 Total Files ➜ {total_files}\n"
            f"🤖 Running Bots ➜ {running}\n"
            f"💾 CPU Usage ➜ {cpu_str}\n"
            f"🧠 RAM Usage ➜ {mem_str}\n"
            f"💽 Storage ➜ {disk_str}\n"
            f"🔄 Auto Recovery ➜ {recovery_on}"
        )
        bot.reply_to(message, msg)
    except Exception as e:
        logger.error(f"Status error: {e}", exc_info=True)
        # Minimal fallback so Status never fully fails
        try:
            bot.reply_to(
                message,
                f"🚀 STATUS\n\n"
                f"🟢 Bot Status ➜ {'🟢 Online' if not bot_locked else '🔒 Locked'}\n"
                f"⏱️ Uptime ➜ {get_uptime()}\n"
                f"👥 Active Users ➜ {len(active_users)}\n"
                f"📂 Total Files ➜ {sum(len(v) for v in user_files.values())}\n"
                f"🤖 Running Bots ➜ {len(bot_scripts)}\n"
                f"⚠️ System metrics unavailable on this host."
            )
        except Exception:
            bot.reply_to(message, f"❌ Failed to fetch status: {e}")

# ===== LOGIC: UPDATES CHANNEL (enhanced) =====
def _logic_updates_channel(message):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(ikb('OUR CHANNEL', style='primary', url=CHANNEL_ARFAT))
    markup.add(ikb('ARFATVERSE 🦍🔥', style='success', url=CHANNEL_ARFAT_ARMY))
    bot.reply_to(message, "📢 UPDATES CHANNEL\n\n👀 OUR CHANNELS:", reply_markup=markup)

# ===== LOGIC: MY ACCOUNT =====
def _logic_my_account(message):
    user = message.from_user
    user_id = user.id
    plan_name, expiry, remaining = _get_user_plan_info(user_id)
    file_count = get_user_file_count(user_id)
    file_limit = get_user_file_limit(user_id)
    limit_str = 'Unlimited' if file_limit == float('inf') else str(file_limit)
    name = (user.first_name or '') + ((' ' + user.last_name) if user.last_name else '')
    username = f"@{user.username}" if user.username else 'N/A'
    validity = expiry.strftime('%Y-%m-%d') if expiry else 'N/A'
    # storage estimate (sum file sizes in user folder)
    used_bytes = 0
    ufolder = get_user_folder(user_id)
    for root, dirs, files in os.walk(ufolder):
        for fn in files:
            try:
                used_bytes += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    used_mb = used_bytes / (1024 * 1024)
    status = 'Banned' if is_user_banned(user_id) else ('Verified' if user_id in active_users else 'Active')
    joined = 'N/A'
    try:
        with DB_LOCK:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_profiles'")
            if c.fetchone():
                c.execute('SELECT join_date FROM user_profiles WHERE user_id=?', (user_id,))
                row = c.fetchone()
                if row:
                    joined = row[0][:10]
            conn.close()
    except Exception:
        pass
    msg = (
        f"👤 MY ACCOUNT\n\n"
        f"🆔 USER ID ➜ `{user_id}`\n"
        f"👤 NAME ➜ {name}\n"
        f"🔗 USERNAME ➜ {username}\n\n"
        f"💎 CURRENT PLAN ➜ {plan_name}\n"
        f"📜 PLAN VALID ➜ {validity}\n"
        f"⌛ REMAINING ➜ {remaining}\n\n"
        f"💾 STORAGE ➜ {used_mb:.1f} MB used\n"
        f"🚀 PROJECTS ➜ {file_count}/{limit_str}\n"
        f"🟢 STATUS ➜ {status}\n\n"
        f"📁 TOTAL PROJECTS ➜ {file_count}\n"
        f"📅 JOINED ➜ {joined}\n\n"
        f"🟢 ACCOUNT STATUS ➜ VERIFIED"
    )
    bot.reply_to(message, msg, parse_mode='Markdown')

# ===== LOGIC: BUY PLAN =====
def _logic_buy_plan(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    keys = list(PLAN_CONFIG.keys())
    plan_styles = {
        'basic': 'success', 'standard': 'primary', 'pro': 'primary',
        'premium': 'primary', 'vip': 'danger', 'ultimate': 'success',
    }
    for i in range(0, len(keys), 2):
        row = []
        for k in keys[i:i+2]:
            p = PLAN_CONFIG[k]
            st = plan_styles.get(k, 'primary')
            row.append(ikb(f"{p['emoji']} {p['name']}", style=st, callback_data=f"plan_detail_{k}"))
        markup.row(*row)
    markup.row(
        ikb('↗️ Contact Owner', style='primary', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'),
        ikb('↩️ Back', style='danger', callback_data='back_to_main'),
    )
    bot.reply_to(message, "💳 BUY PLAN\n\nSelect a plan for details:", reply_markup=markup)

# ===== LOGIC: STORE =====
def _logic_store(message):
    parts = ["🛒 STORE\n"]
    for key, p in PLAN_CONFIG.items():
        parts.append(
            f"\n{p['emoji']} {p['name'].upper()}\n"
            f"💾 {p['storage']} STORAGE\n"
            f"🧠 {p['ram']} RAM\n"
            f"📁 {p['projects']} PROJECTS\n"
            f"🌐 EXTERNAL URL ➜ {'Yes' if p['external_url'] else 'No'}\n"
            f"🟢 {p['uptime']} UPTIME\n"
            f"🛡️ FILE VALIDATION ➜ {'Yes' if p['file_validation'] else 'No'}\n"
            f"⭐ {p['stars_per_month']} TELEGRAM STARS / MONTH\n"
            f"📜 VALIDITY ➜ {p['validity_days']} DAYS\n"
        )
    markup = types.InlineKeyboardMarkup()
    markup.add(ikb('💳 Buy Plan', style='success', callback_data='open_buy_plan'))
    markup.add(ikb('📞 Contact Owner', style='primary', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'))
    text = '\n'.join(parts)
    if len(text) > 4000:
        text = text[:3900] + '\n…'
    bot.reply_to(message, text, reply_markup=markup)

# ===== LOGIC: API INFO =====
def _logic_api_panel(message):
    port = int(os.environ.get('PORT', 8080))
    # Reuse existing Flask app — do not start a second server
    api_url = f"http://0.0.0.0:{port}"
    msg = (
        f"🚀 API\n\n"
        f"🟢 API Status ➜ Running (keep-alive)\n"
        f"🌐 API URL ➜ {api_url}\n"
        f"📡 Available endpoints:\n"
        f"  • GET  /  — Home / keep-alive\n"
        f"  • POST /clone/upload — Clone file approval\n\n"
        f"ℹ️ Uses the existing Flask keep-alive server.\n"
        f"No second server is started."
    )
    bot.reply_to(message, make_blockquote(msg), parse_mode='HTML')

# ===== GENERIC HOSTING PANEL HELPERS =====
_hosting_active_users = set()


def _hosting_reply_keyboard():
    """Reply keyboard for hosting panels; labels are distinct from main menu."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.row(kb_button("📤 Upload", style="success"), kb_button("📁 Projects", style="primary"))
    markup.row(kb_button("⚡ Recommended Install", style="primary"), kb_button("🔙 Back", style="danger"))
    return markup


def _hosting_menu_text(category, title, emoji, extra=""):
    return (
        f"{emoji} {title}\n\n"
        f"📤 Upload your project files.\n"
        f"⚡ They will be deployed on the server.\n"
        f"📋 Use My Projects to manage them."
        f"{extra}"
    )


def _handle_host_upload_and_resume(message, category):
    """Handle one upload step, then restore the hosting menu listener."""
    try:
        _handle_host_upload(message, category)
    finally:
        user_id = message.from_user.id
        text = (message.text or '').strip().lower()
        if text not in ('/cancel', '🔙 back', 'back'):
            bot.register_next_step_handler_by_chat_id(
                message.chat.id,
                lambda m: _hosting_reply_step(m, category)
            )
        else:
            _hosting_active_users.discard(user_id)


def _hosting_reply_step(message, category):
    """Handle hosting submenu choices without swallowing main-menu buttons."""
    user_id = message.from_user.id
    main_kb = create_reply_keyboard_main_menu(user_id)
    raw = (message.text or '').strip()
    plain = raw.translate(_FONT_TO_ASCII).lower() if raw else ''

    # A registered next-step handler runs before normal message handlers in TeleBot.
    # Route main-menu buttons here first, otherwise they look like unknown submenu text.
    main_logic = BUTTON_TEXT_TO_LOGIC.get(raw)
    if main_logic is None and raw:
        normalized_raw = raw.translate(_FONT_TO_ASCII).lower()
        for button_text, logic_func in BUTTON_TEXT_TO_LOGIC.items():
            if button_text.translate(_FONT_TO_ASCII).lower() == normalized_raw:
                main_logic = logic_func
                break
    if main_logic is not None:
        _hosting_active_users.discard(user_id)
        main_logic(message)
        return

    if plain in ('🔙 back', 'back') or plain.endswith(' back'):
        _hosting_active_users.discard(user_id)
        bot.reply_to(message, "Back to main menu.", reply_markup=main_kb)
        return

    if plain in ('⚡ host recommended', '⚙️ recommended install', 'recommended install') or plain.endswith('host recommended') or 'recommended install' in plain:
        _hosting_active_users.discard(user_id)
        _logic_recommended_install(message)
        return

    if plain in ('📤 upload', '🌍 upload', 'upload') or plain.endswith('host upload') or plain.endswith(' upload'):
        pending = getattr(bot, '_host_upload_pending', {})
        pending[user_id] = category
        bot._host_upload_pending = pending
        bot.reply_to(
            message,
            f"📤 Send your project file for {category} hosting.\n"
            f"Accepted: .py .js .zip .html\n"
            f"/cancel to abort.",
            reply_markup=_hosting_reply_keyboard()
        )
        bot.register_next_step_handler_by_chat_id(
            message.chat.id,
            lambda m: _handle_host_upload_and_resume(m, category)
        )
        return

    if plain in ('📁 projects', '📁 host projects', '📂 my projects', 'my projects', 'projects') or plain.endswith('host projects') or plain.endswith('my projects') or plain.endswith(' projects'):
        projects = (
            _list_hosting_projects(user_id, category)
            if '_list_hosting_projects' in globals() else []
        )
        if not projects:
            bot.reply_to(
                message,
                f"📂 No {category} projects found. Upload one first.",
                reply_markup=_hosting_reply_keyboard()
            )
            bot.register_next_step_handler_by_chat_id(
                message.chat.id, lambda m: _hosting_reply_step(m, category)
            )
            return
        lines = [f"📂 Your {category} projects:"] + [
            f"• {project}" for project in projects[:30]
        ]
        bot.reply_to(
            message,
            "\n".join(lines),
            reply_markup=_hosting_reply_keyboard()
        )
        bot.register_next_step_handler_by_chat_id(
            message.chat.id, lambda m: _hosting_reply_step(m, category)
        )
        return

    bot.reply_to(
        message,
        "Unknown option. Use the buttons or go Back.",
        reply_markup=_hosting_reply_keyboard()
    )
    bot.register_next_step_handler_by_chat_id(
        message.chat.id,
        lambda m: _hosting_reply_step(m, category)
    )


def _logic_whatsapp_bot(message):
    _hosting_active_users.add(message.from_user.id)
    text = _hosting_menu_text('whatsapp', 'WHATSAPP BOT HOSTING', '📱')
    bot.reply_to(message, text, reply_markup=_hosting_reply_keyboard())
    bot.register_next_step_handler_by_chat_id(
        message.chat.id, lambda m: _hosting_reply_step(m, 'whatsapp')
    )


def _logic_discord_bot(message):
    _hosting_active_users.add(message.from_user.id)
    text = _hosting_menu_text('discord', 'DISCORD BOT HOSTING', '🟣')
    bot.reply_to(message, text, reply_markup=_hosting_reply_keyboard())
    bot.register_next_step_handler_by_chat_id(
        message.chat.id, lambda m: _hosting_reply_step(m, 'discord')
    )


def _logic_website(message):
    _hosting_active_users.add(message.from_user.id)
    text = _hosting_menu_text(
        'website',
        'WEBSITE HOSTING',
        '🌍',
        "\n\nSupports HTML / CSS / JS / ZIP archives."
    )
    bot.reply_to(message, text, reply_markup=_hosting_reply_keyboard())
    bot.register_next_step_handler_by_chat_id(
        message.chat.id, lambda m: _hosting_reply_step(m, 'website')
    )


def _logic_local_host(message):
    _hosting_active_users.add(message.from_user.id)
    extra = (
        "\n\n💻 LOCAL HOST MANAGER\n"
        "📤 Upload Website, API, or Bot files.\n"
        "⚡ Deployed and run on the server.\n"
        "📋 Check logs for assigned port.\n"
        "🌐 http://<server_ip>:<port>"
    )
    text = _hosting_menu_text('local', 'LOCAL HOST MANAGER', '💻', extra)
    bot.reply_to(message, text, reply_markup=_hosting_reply_keyboard())
    bot.register_next_step_handler_by_chat_id(
        message.chat.id, lambda m: _hosting_reply_step(m, 'local')
    )

# Aliases for renamed menu buttons that map to existing logic
def _logic_upload_alias(message):
    _logic_upload_file(message)

def _logic_my_files_alias(message):
    _logic_check_files(message)

def _logic_git_clone_alias(message):
    _logic_github_deploy(message)

def _logic_recommended_alias(message):
    _logic_recommended_install(message)


# ===== BUTTON TEXT TO LOGIC MAPPING =====
BUTTON_TEXT_TO_LOGIC = {
    # --- New / updated USER menu ---
    "📢 Uᴘᴅᴀᴛᴇꜱ Cʜᴀɴɴᴇʟ": _logic_updates_channel,
    "🌍 Uᴘʟᴏᴀᴅ": _logic_upload_alias,
    "📂 Mʏ Fɪʟᴇs": _logic_my_files_alias,
    "⚡ Bᴏᴛ Sᴘᴇᴇᴅ": _logic_bot_speed,
    "🚀 Sᴛᴀᴛᴜs": _logic_status,
    "🔄 Rᴇsᴛᴀʀᴛ": _logic_restart_my_scripts,
    "⏹ Sᴛᴏᴘ": _logic_stop_my_scripts,
    "⚙️ Rᴇᴄᴏᴍᴍᴇɴᴅᴇᴅ Iɴsᴛᴀʟʟ": _logic_recommended_alias,
    "🤖 AI Aɢᴇɴᴛ": _logic_ai_assistant,
    "🌐 Gɪᴛ Cʟᴏɴᴇ": _logic_git_clone_alias,
    "📞 Cᴏɴᴛᴀᴄᴛ Oᴡɴᴇʀ": _logic_contact_owner,
    "📱 WʜᴀᴛsAᴘᴘ Bᴏᴛ": _logic_whatsapp_bot,
    "🟣 Dɪsᴄᴏʀᴅ Bᴏᴛ": _logic_discord_bot,
    "🚀 API": _logic_api_panel,
    "🌍 Wᴇʙsɪᴛᴇ": _logic_website,
    "💻 Lᴏᴄᴀʟ Hᴏsᴛ": _logic_local_host,
    "💳 Bᴜʏ Pʟᴀɴ": _logic_buy_plan,
    "🛒 Sᴛᴏʀᴇ": _logic_store,
    "👤 Mʏ Aᴄᴄᴏᴜɴᴛ": _logic_my_account,
    # --- ADMIN menu (preserved) ---
    "📤 Uᴘʟᴏᴀᴅ Fɪʟᴇ": _logic_upload_file,
    "📂 Cʜᴇᴄᴋ Fɪʟᴇs": _logic_check_files,
    "📊 Sᴛᴀᴛɪsᴛɪᴄs": _logic_statistics,
    "⌨️ Sᴇɴᴅ Cᴏᴍᴍᴀɴᴅ": _logic_send_command,
    "💳 Sᴜʙsᴄʀɪᴘᴛɪᴏɴs": _logic_subscriptions,
    "📢 Bʀᴏᴀᴅᴄᴀsᴛ": _logic_broadcast_init,
    "🔒 Lᴏᴄᴋ Bᴏᴛ": _logic_toggle_lock_bot,
    "🟢 Rᴜɴ Aʟʟ Sᴄʀɪᴘᴛs": _logic_run_all_scripts,
    "👑 Admin Panel": _logic_admin_panel,
    "🤖 Cʀᴇᴀᴛᴇ Cʟᴏɴᴇ": _logic_clone_bot,
    "👥 Uꜱᴇʀ Mᴀɴᴀɢᴇᴍᴇɴᴛ": _logic_user_management,
    "⚙️ Sᴇᴛᴛɪɴɢs": _logic_admin_settings,
    "⚙️ Sᴇᴛᴛɪɴɢꜱ": _logic_admin_settings,
    "📢 Cʜᴀɴɴᴇʟ Aᴅᴅ": _logic_manage_mandatory_channels,
    "🛠️ Mᴀɴᴜᴀʟ Iɴꜱᴛᴀʟʟ": _logic_manual_install,
    "📦 Mᴀɴᴜᴀʟ Iɴꜱᴛᴀʟʟ": _logic_manual_install,
    "📦 Pᴋɢ Iɴꜱᴛᴀʟʟ": _logic_recommended_install,
    "🌐 Gɪᴛʜᴜʙ Rᴇᴘᴏ": _logic_github_deploy,
    "🔄 Rᴇᴛᴀʀᴛ": _logic_restart_my_scripts,
    "🔄 Rᴇᴀᴛᴀʀᴛ": _logic_restart_my_scripts,
}

BUTTON_TEXT_TO_LOGIC = {stylize_text(k): v for k, v in BUTTON_TEXT_TO_LOGIC.items()}

@bot.message_handler(func=lambda message: bool(message.text) and (
    message.text in BUTTON_TEXT_TO_LOGIC or
    'upload file' in message.text.translate(_FONT_TO_ASCII).lower() or
    'check files' in message.text.translate(_FONT_TO_ASCII).lower() or
    'add channel' in message.text.translate(_FONT_TO_ASCII).lower() or
    'remove channel' in message.text.translate(_FONT_TO_ASCII).lower() or
    'list channels' in message.text.translate(_FONT_TO_ASCII).lower()
))
def handle_button_text(message):
    # Keep the two main-menu file actions reliable across Unicode font variants.
    normalized = message.text.translate(_FONT_TO_ASCII).lower().strip()
    if 'upload file' in normalized:
        _logic_upload_file(message)
        return
    if 'check files' in normalized or 'manage files' in normalized:
        _logic_check_files(message)
        return
    if any(label in normalized for label in ('add channel', 'remove channel', 'list channels')):
        _mandatory_channels_reply_step(message)
        return

    # Do not swallow main-menu buttons while a hosting panel is open.
    logic_func = BUTTON_TEXT_TO_LOGIC.get(message.text)
    if logic_func:
        logic_func(message)
    else:
        logger.warning("⚠️ Button text '%s' matched but no logic func.", message.text)

# ===== COMMAND HANDLERS =====
@bot.message_handler(commands=['updateschannel'])
def command_updates_channel(message): _logic_updates_channel(message)
@bot.message_handler(commands=['uploadfile'])
def command_upload_file(message): _logic_upload_file(message)
@bot.message_handler(commands=['checkfiles'])
def command_check_files(message): _logic_check_files(message)
@bot.message_handler(commands=['sendcommand', 'sendcmd'])
def command_send_command(message): _logic_send_command(message)
@bot.message_handler(commands=['botspeed'])
def command_bot_speed(message): _logic_bot_speed(message)
@bot.message_handler(commands=['contactowner'])
def command_contact_owner(message): _logic_contact_owner(message)
@bot.message_handler(commands=['statistics'])
def command_statistics(message): _logic_statistics(message)
@bot.message_handler(commands=['subscriptions'])
def command_subscriptions(message): _logic_subscriptions(message)
@bot.message_handler(commands=['broadcast'])
def command_broadcast(message): _logic_broadcast_init(message)
@bot.message_handler(commands=['lockbot'])
def command_lock_bot(message): _logic_toggle_lock_bot(message)
@bot.message_handler(commands=['adminpanel'])
def command_admin_panel(message): _logic_admin_panel(message)
@bot.message_handler(commands=['clonecontrol'])
def command_clone_control(message): _logic_clone_control(message)
@bot.message_handler(commands=['runallscripts'])
def command_run_all_scripts(message): _logic_run_all_scripts(message)
@bot.message_handler(commands=['clonebot'])
def command_clone_bot(message): _logic_clone_bot(message)
@bot.message_handler(commands=['usermanagement'])
def command_user_management(message): _logic_user_management(message)
@bot.message_handler(commands=['adminsettings'])
def command_admin_settings(message): _logic_admin_settings(message)
@bot.message_handler(commands=['managechannels'])
def command_manage_channels(message): _logic_manage_mandatory_channels(message)
@bot.message_handler(commands=['manualinstall'])
def command_manual_install(message): _logic_manual_install(message)
@bot.message_handler(commands=['admininstall'])
def command_admin_install(message): _logic_admin_install(message)

@bot.message_handler(commands=['ping'])
def ping(message):
    user_id = message.from_user.id
    
    # Check if user is banned
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    # Check mandatory subscription first
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        subscription_message, markup = create_subscription_check_message(not_joined)
        bot.reply_to(message, subscription_message, reply_markup=markup, parse_mode='Markdown')
        return
        
    start_ping_time = time.time() 
    msg = bot.reply_to(message, "Pong!")
    latency = round((time.time() - start_ping_time) * 1000, 2)
    bot.edit_message_text(f"Pong! Latency: {latency} ms", message.chat.id, msg.message_id)

@bot.message_handler(commands=['githubdeploy'])
def command_github_deploy(message): _logic_github_deploy(message)
@bot.message_handler(commands=['pkginstall'])
def command_pkg_install(message): _logic_recommended_install(message)
@bot.message_handler(commands=['aiagent'])
def command_ai_agent(message): _logic_ai_assistant(message)

# ===== CLONE APPROVAL UPLOAD / QUEUE HELPERS =====
def clone_submit_upload_for_approval(message):
    doc = message.document
    file_name = os.path.basename(doc.file_name or '')
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_ext not in ('.py', '.js', '.zip'):
        bot.reply_to(message, "⚠️ Unsupported type! Only `.py`, `.js`, `.zip` allowed.")
        return
    if doc.file_size > 20 * 1024 * 1024:
        bot.reply_to(message, "⚠️ File too large (Max: 20 MB).")
        return
    try:
        file_info = bot.get_file(doc.file_id)
        raw = bot.download_file(file_info.file_path)
        response = requests.post(
            MASTER_APPROVAL_URL,
            data={'clone_user_id': str(CLONE_USER_ID), 'approval_key': CLONE_APPROVAL_KEY,
                  'user_name': message.from_user.first_name or 'Unknown',
                  'user_username': message.from_user.username or 'No username'},
            files={'file': (file_name, io.BytesIO(raw), doc.mime_type or 'application/octet-stream')},
            timeout=60
        )
        try:
            result = response.json()
        except Exception:
            result = {'error': response.text[:200]}
        if response.ok and result.get('ok'):
            bot.reply_to(message, "⏳ File received.\n\n🛡️ Waiting for master-admin approval.\nThe file will NOT run until approved.")
        else:
            bot.reply_to(message, f"❌ Approval submission failed: {result.get('error', 'unknown error')}")
    except Exception as e:
        logger.error(f"Clone approval submission failed: {e}", exc_info=True)
        bot.reply_to(message, "❌ Could not submit the file for approval. Try again later.")


def clone_approval_worker():
    if not IS_CLONE:
        return
    queue_dir = os.path.join(BASE_DIR, CLONE_APPROVAL_DIR_NAME)
    os.makedirs(queue_dir, exist_ok=True)
    while True:
        try:
            for request_file in list(os.listdir(queue_dir)):
                if not request_file.endswith('.json'):
                    continue
                meta_path = os.path.join(queue_dir, request_file)
                try:
                    with open(meta_path, 'r', encoding='utf-8') as fh:
                        meta = json.load(fh)
                    source_path = meta.get('source_path')
                    file_name = os.path.basename(meta.get('file_name', ''))
                    file_type = meta.get('file_type', '')
                    if not source_path or not os.path.isfile(source_path) or not file_name:
                        os.remove(meta_path)
                        continue

                    user_folder = get_user_folder(CLONE_USER_ID)
                    file_ext = os.path.splitext(file_name)[1].lower()
                    if file_ext == '.zip':
                        temp_dir = tempfile.mkdtemp(prefix=f"approved_{CLONE_USER_ID}_")
                        try:
                            zip_path = os.path.join(temp_dir, file_name)
                            shutil.copy2(source_path, zip_path)
                            with zipfile.ZipFile(zip_path, 'r') as z:
                                z.extractall(temp_dir)
                            extracted = os.listdir(temp_dir)
                            py_files = [f for f in extracted if f.endswith('.py')]
                            js_files = [f for f in extracted if f.endswith('.js')]
                            req_file = 'requirements.txt' if 'requirements.txt' in extracted else None
                            pkg_json = 'package.json' if 'package.json' in extracted else None
                            if req_file:
                                subprocess.run([sys.executable, '-m', 'pip', 'install', '-r',
                                                os.path.join(temp_dir, req_file)], check=True, capture_output=True)
                            if pkg_json:
                                subprocess.run(['npm', 'install'], cwd=temp_dir, check=True, capture_output=True)
                            main_script = next((p for p in ['main.py', 'bot.py', 'app.py'] if p in py_files), None)
                            if not main_script:
                                main_script = next((p for p in ['index.js', 'main.js', 'bot.js', 'app.js'] if p in js_files), None)
                            if not main_script and py_files:
                                main_script = py_files[0]
                            if not main_script and js_files:
                                main_script = js_files[0]
                            if not main_script:
                                raise RuntimeError("No .py or .js script found in approved ZIP.")
                            for item in os.listdir(temp_dir):
                                if item == os.path.basename(zip_path):
                                    continue
                                src_item = os.path.join(temp_dir, item)
                                dst_item = os.path.join(user_folder, item)
                                if os.path.isdir(dst_item):
                                    shutil.rmtree(dst_item)
                                elif os.path.exists(dst_item):
                                    os.remove(dst_item)
                                shutil.move(src_item, dst_item)
                            ftype = 'py' if main_script.endswith('.py') else 'js'
                            save_user_file(CLONE_USER_ID, main_script, ftype)
                            script_path = os.path.join(user_folder, main_script)
                            runner = run_script if ftype == 'py' else run_js_script
                            threading.Thread(target=runner, args=(script_path, CLONE_USER_ID, user_folder, main_script, _RecoveryMsg(CLONE_USER_ID)), daemon=True).start()
                        finally:
                            shutil.rmtree(temp_dir, ignore_errors=True)
                    else:
                        file_path = os.path.join(user_folder, file_name)
                        shutil.copy2(source_path, file_path)
                        save_user_file(CLONE_USER_ID, file_name, file_type)
                        runner = run_script if file_type == 'py' else run_js_script
                        threading.Thread(target=runner, args=(file_path, CLONE_USER_ID, user_folder, file_name, _RecoveryMsg(CLONE_USER_ID)), daemon=True).start()
                    try:
                        os.remove(source_path)
                    except OSError:
                        pass
                    os.remove(meta_path)
                    bot.send_message(CLONE_USER_ID, f"✅ Approved: {file_name}\nYour file is now being hosted.")
                except Exception as e:
                    logger.error(f"Clone approval worker error: {e}", exc_info=True)
                    try:
                        os.remove(meta_path)
                    except OSError:
                        pass
        except Exception as e:
            logger.error(f"Clone queue worker error: {e}", exc_info=True)
        time.sleep(2)

# ===== HANDLE FILE UPLOAD =====
@bot.message_handler(content_types=['document'])
def handle_file_upload_doc(message):
    if IS_CLONE:
        clone_submit_upload_for_approval(message)
        return
    user_id = message.from_user.id
    chat_id = message.chat.id
    doc = message.document
    logger.info(f"📎 Doc from {user_id}: {doc.file_name} ({doc.mime_type}), Size: {doc.file_size}")

    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        subscription_message, sub_markup = create_subscription_check_message(not_joined)
        bot.reply_to(message, subscription_message, reply_markup=sub_markup, parse_mode='Markdown')
        return

    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked, cannot accept files.")
        return

    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, f"⚠️ File limit ({current_files}/{limit_str}) reached. Delete files via /checkfiles.")
        return

    file_name = doc.file_name
    if not file_name: bot.reply_to(message, "❌ No file name. Ensure file has a name."); return
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, "⚠️ Unsupported type! Only `.py`, `.js`, `.zip` allowed.")
        return
    max_file_size = 100 * 1024 * 1024
    if doc.file_size > max_file_size:
        bot.reply_to(message, f"⚠️ File too large (Max: {max_file_size // 1024 // 1024} MB)."); return

    try:
        user_name = message.from_user.first_name or 'Unknown'
        user_username = message.from_user.username or 'No username'
        file_type = 'py' if file_ext == '.py' else 'js' if file_ext == '.js' else 'zip'
        upload_id = add_pending_upload(
            user_id=user_id,
            file_id=doc.file_id,
            file_name=file_name,
            file_type=file_type,
            file_size=doc.file_size,
            user_name=user_name,
            user_username=user_username,
            extra_info='Main bot upload - awaiting admin approval'
        )
        if not upload_id:
            bot.reply_to(message, stylish_text('❌ Could not create the approval request. Please try again.'))
            return

        # Send the actual file to the owner/admin with approve/reject controls.
        try:
            for approval_admin_id in sorted(set(admin_ids) | {OWNER_ID}):
                try:
                    bot.forward_message(approval_admin_id, chat_id, message.message_id)
                except Exception as e:
                    logger.error(f'Failed to forward upload to admin {approval_admin_id}: {e}')
        except Exception as e:
            logger.error(f'❌ Failed to forward uploaded file to OWNER_ID {OWNER_ID}: {e}')

        approval_caption = stylish_text(
            f'📥 FILE APPROVAL\n\n'
            f'👤 User: {user_name} (@{user_username})\n'
            f'🆔 User ID: {user_id}\n'
            f'📄 File: {file_name}\n'
            f'📏 Size: {doc.file_size // 1024} KB\n'
            f'?? Request ID: {upload_id}'
        )
        approval_markup = types.InlineKeyboardMarkup(row_width=2)
        approval_markup.row(
            types.InlineKeyboardButton(stylish_text('✅ Approve'), callback_data=f'approve_upload_{upload_id}'),
            types.InlineKeyboardButton(stylish_text('❌ Reject'), callback_data=f'reject_upload_{upload_id}')
        )
        for approval_admin_id in sorted(set(admin_ids) | {OWNER_ID}):
            try:
                bot.send_message(approval_admin_id, approval_caption, reply_markup=approval_markup)
            except Exception as e:
                logger.error(f'Failed to notify admin {approval_admin_id}: {e}')
        bot.reply_to(message, stylish_text(f'⏳ {file_name} has been sent for admin approval. It will start only after approval.'))
        logger.info(f'🛡️ Main upload queued for approval: request={upload_id}, user={user_id}, file={file_name}')
        return
    except telebot.apihelper.ApiTelegramException as e:
         logger.error(f"❌ Telegram API Error handling file for {user_id}: {e}", exc_info=True)
         if "file is too big" in str(e).lower():
              bot.reply_to(message, f"⚠️ Telegram API Error: File too large to download (~20MB limit).")
         else: bot.reply_to(message, f"⚠️ Telegram API Error: {str(e)}. Try later.")
    except Exception as e:
        logger.error(f"❌ General error handling file for {user_id}: {e}", exc_info=True)
        bot.reply_to(message, f"⚠️ Unexpected error: {str(e)}")

# ===== CLONE APPROVAL CALLBACKS (MASTER) =====
def _get_clone_pending(upload_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM clone_pending_uploads WHERE id=?", (upload_id,)).fetchone()
        conn.close()
    return dict(row) if row else None


def _finish_clone_pending(upload_id, status):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        conn.execute("UPDATE clone_pending_uploads SET status=? WHERE id=?", (status, upload_id))
        conn.commit()
        conn.close()


@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_clone_') or call.data.startswith('reject_clone_'))
def handle_clone_approval_callback(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Only master admins can approve/reject.", show_alert=True)
        return
    try:
        upload_id = int(call.data.rsplit('_', 1)[1])
    except Exception:
        bot.answer_callback_query(call.id, "Invalid request.", show_alert=True)
        return
    pending = _get_clone_pending(upload_id)
    if not pending or pending.get('status') != 'pending':
        bot.answer_callback_query(call.id, "Request already handled.", show_alert=True)
        return

    clone_user_id = int(pending['clone_user_id'])
    file_name = pending['file_name']
    source_path = pending['local_path']

    if call.data.startswith('approve_clone_'):
        clone_dir = os.path.join(BASE_DIR, f"clone_{clone_user_id}")
        queue_dir = os.path.join(clone_dir, CLONE_APPROVAL_DIR_NAME)
        if not os.path.isfile(source_path) or clone_user_id not in user_clones or not os.path.isdir(clone_dir):
            _finish_clone_pending(upload_id, 'failed')
            bot.answer_callback_query(call.id, "File/clone unavailable.", show_alert=True)
            return
        os.makedirs(queue_dir, exist_ok=True)
        queue_file = os.path.join(queue_dir, f"{upload_id}.json")
        meta = {'source_path': source_path, 'file_name': file_name,
                'file_type': pending['file_type'], 'request_id': upload_id}
        tmp = queue_file + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(meta, fh)
        os.replace(tmp, queue_file)
        _finish_clone_pending(upload_id, 'approved')
        bot.answer_callback_query(call.id, "✅ Approved.")
        try:
            bot.edit_message_caption(caption=stylish_text((call.message.caption or '') + "\n\n✅ APPROVED"),
                                      chat_id=call.message.chat.id, message_id=call.message.message_id,
                                      reply_markup=None)
        except Exception: pass
        try: bot.send_message(clone_user_id, f"✅ Your clone file `{file_name}` was approved.", parse_mode='Markdown')
        except Exception: pass
    else:
        _finish_clone_pending(upload_id, 'rejected')
        try: os.remove(source_path)
        except OSError: pass
        bot.answer_callback_query(call.id, "❌ Rejected.")
        try:
            bot.edit_message_caption(caption=stylish_text((call.message.caption or '') + "\n\n❌ REJECTED"),
                                      chat_id=call.message.chat.id, message_id=call.message.message_id,
                                      reply_markup=None)
        except Exception: pass
        try: bot.send_message(clone_user_id, f"❌ Your clone file `{file_name}` was rejected.", parse_mode='Markdown')
        except Exception: pass


# ===== CALLBACK QUERY HANDLER =====

def _handle_host_upload(message, category):
    """Queue hosting uploads for explicit approval by any configured admin."""
    user_id = message.from_user.id
    if message.text and message.text.strip().lower() in ('/cancel', '🔙 back', 'back'):
        pending = getattr(bot, '_host_upload_pending', {})
        pending.pop(user_id, None)
        bot._host_upload_pending = pending
        if message.text.strip().lower() == '/cancel':
            bot.reply_to(message, "❌ Upload cancelled.", reply_markup=_hosting_reply_keyboard())
        else:
            bot.reply_to(message, "Back to main menu.", reply_markup=create_reply_keyboard_main_menu(user_id))
        return
    if not message.document:
        bot.reply_to(message, "⚠️ Please send a document (.py / .js / .zip / .html) or /cancel.")
        bot.register_next_step_handler(message, lambda m: _handle_host_upload_and_resume(m, category))
        return
    doc = message.document
    file_name = os.path.basename(doc.file_name or f"project_{int(time.time())}")
    ext = os.path.splitext(file_name)[1].lower()
    if ext not in ('.py', '.js', '.zip', '.html', '.htm'):
        bot.reply_to(message, "❌ Unsupported file type. Use .py, .js, .zip, .html or .htm.")
        return
    if doc.file_size > 100 * 1024 * 1024:
        bot.reply_to(message, "⚠️ File too large (maximum 100 MB).")
        return
    if get_user_file_count(user_id) >= get_user_file_limit(user_id):
        bot.reply_to(message, "⚠️ File limit reached. Upgrade your plan or delete old projects.")
        return
    upload_id = add_pending_upload(
        user_id=user_id, file_id=doc.file_id, file_name=file_name,
        file_type=f"host:{category}", file_size=doc.file_size,
        user_name=message.from_user.first_name or "Unknown",
        user_username=message.from_user.username or "No username",
        extra_info=f"Hosting upload awaiting approval: {category}"
    )
    if not upload_id:
        bot.reply_to(message, "❌ Could not create approval request. Please retry.")
        return
    caption = (
        f"📥 HOSTING APPROVAL\n\nCategory: {category}\n"
        f"User: {message.from_user.first_name or 'Unknown'} "
        f"(@{message.from_user.username or 'No username'})\n"
        f"User ID: {user_id}\nFile: {file_name}\n"
        f"Size: {doc.file_size // 1024} KB\nRequest ID: {upload_id}"
    )
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("✅ Approve", callback_data=f"hostapprove_{upload_id}"),
        types.InlineKeyboardButton("❌ Reject", callback_data=f"hostreject_{upload_id}")
    )
    delivered = 0
    for admin_id in sorted(set(admin_ids) | {OWNER_ID}):
        try:
            bot.send_document(admin_id, doc.file_id, caption=caption, reply_markup=markup)
            delivered += 1
        except Exception as e:
            logger.error(f"Could not send hosting approval {upload_id} to admin {admin_id}: {e}")
    if not delivered:
        delete_pending_upload(upload_id)
        bot.reply_to(message, "❌ Could not notify any admin; upload request cancelled.")
        return
    bot.reply_to(message, f"⏳ {file_name} was sent to admins for approval. It won't be stored or hosted until approved.")
    logger.info("Hosting upload queued: request=%s user=%s category=%s", upload_id, user_id, category)


@bot.callback_query_handler(func=lambda call: call.data.startswith('hostapprove_') or call.data.startswith('hostreject_'))
def handle_host_upload_approval(call):
    if call.from_user.id not in (set(admin_ids) | {OWNER_ID}):
        bot.answer_callback_query(call.id, "Only admins can approve/reject.", show_alert=True)
        return
    try:
        upload_id = int(call.data.rsplit('_', 1)[1])
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid request.", show_alert=True)
        return
    pending = get_pending_upload(upload_id)
    if not pending or not str(pending.get('file_type', '')).startswith('host:'):
        bot.answer_callback_query(call.id, "Request already handled or unavailable.", show_alert=True)
        return
    if call.data.startswith('hostreject_'):
        delete_pending_upload(upload_id)
        try:
            bot.send_message(pending['user_id'], f"❌ Hosting upload {pending['file_name']} was rejected by an admin.")
        except Exception:
            pass
        bot.answer_callback_query(call.id, "Upload rejected.")
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        return

    user_id = int(pending['user_id'])
    category = pending['file_type'].split(':', 1)[1]
    allowed_categories = {'whatsapp', 'discord', 'website', 'local', 'api'}
    if category not in allowed_categories:
        bot.answer_callback_query(call.id, "Invalid hosting category.", show_alert=True)
        return
    if get_user_file_count(user_id) >= get_user_file_limit(user_id):
        delete_pending_upload(upload_id)
        bot.answer_callback_query(call.id, "User file limit reached.", show_alert=True)
        return
    try:
        info = bot.get_file(pending['file_id'])
        raw = bot.download_file(info.file_path)
        file_name = os.path.basename(pending['file_name'])
        base = os.path.splitext(file_name)[0]
        dest_root = _hosting_project_dir(user_id, category)
        proj_dir = os.path.join(dest_root, base)
        os.makedirs(proj_dir, exist_ok=True)
        local_path = os.path.join(proj_dir, file_name)
        with open(local_path, 'wb') as fh:
            fh.write(raw)
        if file_name.lower().endswith('.zip'):
            _safe_extract_zip(local_path, proj_dir)
        entry = (f"{category}/{base}", category)
        user_files.setdefault(user_id, [])
        if entry not in user_files[user_id]:
            user_files[user_id].append(entry)
        with DB_LOCK:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            conn.execute('INSERT OR IGNORE INTO user_files (user_id, file_name, file_type) VALUES (?,?,?)',
                         (user_id, f"{category}/{base}", category))
            conn.commit()
            conn.close()
        delete_pending_upload(upload_id)
        try:
            bot.send_message(user_id, f"✅ Your {category} upload {file_name} was approved and saved.")
        except Exception:
            pass
        bot.answer_callback_query(call.id, "Approved.")
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.send_message(call.message.chat.id, f"✅ Approved hosting upload #{upload_id}: {file_name}")
    except Exception as e:
        logger.error("Hosting approval failed for %s: %s", upload_id, e, exc_info=True)
        bot.answer_callback_query(call.id, "Approval failed; request remains pending.", show_alert=True)

def _handle_host_action(call):
    """Start / stop / restart / logs / delete for hosting projects."""
    user_id = call.from_user.id
    data = call.data
    bot.answer_callback_query(call.id)
    # parse: host_{action}_{category}_{proj}
    for action in ('start', 'stop', 'restart', 'logs', 'delete'):
        prefix = f'host_{action}_'
        if data.startswith(prefix):
            rest = data[len(prefix):]
            # category_proj — category is first segment
            if '_' not in rest:
                bot.send_message(call.message.chat.id, "Invalid project.")
                return
            category, proj = rest.split('_', 1)
            break
    else:
        return
    proj_dir = os.path.join(_hosting_project_dir(user_id, category), proj)
    script_key = f"{user_id}_{category}_{proj}"
    if action == 'delete':
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]
        if os.path.exists(proj_dir):
            shutil.rmtree(proj_dir, ignore_errors=True)
        # remove from user_files
        if user_id in user_files:
            user_files[user_id] = [x for x in user_files[user_id] if x[0] != f"{category}/{proj}"]
        bot.send_message(call.message.chat.id, f"🗑️ Deleted {proj}")
        return
    if action == 'stop':
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]
            bot.send_message(call.message.chat.id, f"⏹ Stopped {proj}")
        else:
            bot.send_message(call.message.chat.id, f"ℹ️ {proj} is not running.")
        return
    if action == 'logs':
        log_path = None
        if script_key in bot_scripts:
            log_path = bot_scripts[script_key].get('log_path')
        if not log_path or not os.path.exists(log_path):
            # try default
            log_path = os.path.join(proj_dir, 'run.log')
        if log_path and os.path.exists(log_path):
            try:
                with open(log_path, 'r', errors='ignore') as fh:
                    tail = fh.read()[-3500:]
                bot.send_message(call.message.chat.id, f"📜 Logs for {proj}:\n```\n{tail or '(empty)'}\n```", parse_mode='Markdown')
            except Exception as e:
                bot.send_message(call.message.chat.id, f"❌ Log read error: {e}")
        else:
            bot.send_message(call.message.chat.id, f"📜 No logs yet for {proj}")
        return
    if action in ('start', 'restart'):
        if action == 'restart' and script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]
            time.sleep(0.5)
        if not os.path.exists(proj_dir):
            bot.send_message(call.message.chat.id, f"❌ Project path missing: {proj}")
            return
        runtime = _detect_runtime(proj_dir)
        ok, log = _install_deps(proj_dir, runtime)
        if not ok:
            bot.send_message(call.message.chat.id, f"⚠️ Dependency install issues:\n{log[:500]}")
        # find entry point
        entry = None
        if runtime == 'python':
            for cand in ('main.py', 'bot.py', 'app.py', 'index.py'):
                p = os.path.join(proj_dir, cand)
                if os.path.exists(p):
                    entry = p
                    break
            if not entry:
                pys = [f for f in os.listdir(proj_dir) if f.endswith('.py')]
                if pys:
                    entry = os.path.join(proj_dir, pys[0])
        elif runtime == 'node':
            for cand in ('index.js', 'bot.js', 'main.js', 'app.js'):
                p = os.path.join(proj_dir, cand)
                if os.path.exists(p):
                    entry = p
                    break
            if not entry:
                jss = [f for f in os.listdir(proj_dir) if f.endswith('.js')]
                if jss:
                    entry = os.path.join(proj_dir, jss[0])
        elif runtime == 'static':
            # serve via python http.server on free port
            port = _find_free_port()
            if not port:
                bot.send_message(call.message.chat.id, "❌ No free port available.")
                return
            log_file = open(os.path.join(proj_dir, 'run.log'), 'a')
            proc = subprocess.Popen(
                [sys.executable, '-m', 'http.server', str(port)],
                cwd=proj_dir, stdout=log_file, stderr=log_file
            )
            bot_scripts[script_key] = {
                'process': proc, 'file_name': f"{category}_{proj}",
                'log_file': log_file, 'log_path': os.path.join(proj_dir, 'run.log'),
                'script_key': script_key, 'port': port, 'start_time': datetime.now()
            }
            bot.send_message(call.message.chat.id, f"🟢 Static site started.\n🌐 Port: {port}\nhttp://0.0.0.0:{port}")
            return
        if not entry:
            bot.send_message(call.message.chat.id, f"❌ Could not detect entry point for {proj} (runtime={runtime})")
            return
        # launch via existing run helpers where possible
        try:
            log_path = os.path.join(proj_dir, 'run.log')
            log_file = open(log_path, 'a')
            if runtime == 'python':
                proc = subprocess.Popen([sys.executable, entry], cwd=proj_dir,
                                        stdout=log_file, stderr=log_file)
            else:
                proc = subprocess.Popen(['node', entry], cwd=proj_dir,
                                        stdout=log_file, stderr=log_file)
            bot_scripts[script_key] = {
                'process': proc, 'file_name': f"{category}_{proj}",
                'log_file': log_file, 'log_path': log_path,
                'script_key': script_key, 'start_time': datetime.now()
            }
            bot.send_message(call.message.chat.id, f"🟢 Started {proj} ({runtime})")
        except Exception as e:
            logger.error(f"Host start error: {e}", exc_info=True)
            bot.send_message(call.message.chat.id, f"❌ Start failed: {e}")


@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    data = call.data
    logger.info(f"📞 Callback: User={user_id}, Data='{data}'")

    # Check if user is banned
    if is_user_banned(user_id) and data not in ['back_to_main']:
        bot.answer_callback_query(call.id, "❌ You are banned from using this bot.", show_alert=True)
        return

    # Mandatory channels check (admins exempt)
    if data not in ['check_subscription_status', 'back_to_main', 'manual_install', 'force_join_check']:
        is_subscribed, not_joined = check_mandatory_subscription(user_id)
        if not is_subscribed and user_id not in admin_ids:
            subscription_message, sub_markup = create_subscription_check_message(not_joined)
            bot.answer_callback_query(call.id)
            try:
                bot.edit_message_text(subscription_message, call.message.chat.id, call.message.message_id, reply_markup=sub_markup, parse_mode='Markdown')
            except Exception:
                bot.send_message(call.message.chat.id, subscription_message, reply_markup=sub_markup, parse_mode='Markdown')
            return

    if bot_locked and user_id not in admin_ids and data not in ['back_to_main']:
        bot.answer_callback_query(call.id, "⚠️ Bot locked by admin.", show_alert=True)
        return
    try:
        if data == 'force_join_check':
            if user_id in admin_ids or user_id == OWNER_ID:
                joined_all, missing_channels = True, []
            else:
                joined_all, missing_channels = _is_user_in_all_force_channels(user_id)
            if not joined_all:
                bot.answer_callback_query(
                    call.id,
                    "❌ ᴘᴇʜʟᴇ ꜱᴀʙ ᴄʜᴀɴɴᴇʟ ᴊᴏɪɴ ᴋᴀʀᴏ!",
                    show_alert=True
                )
                return
            bot.answer_callback_query(call.id, "✅ ᴛʜᴀɴᴋꜱ ғᴏʀ ᴊᴏɪɴɪɴɢ!")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception as delete_error:
                logger.warning("Could not delete force-join prompt for %s: %s", user_id, delete_error)

            if user_id in authenticated_users:
                _logic_send_welcome(call.message, user=call.from_user)
            else:
                password_prompt = bot.send_message(
                    call.message.chat.id,
                    stylize_text('🔐 Send the bot password to continue.')
                )
                bot.register_next_step_handler(password_prompt, _process_bot_password)
        elif data == 'upload':
            upload_callback(call)
        elif data == 'check_files':
            check_files_callback(call)
        elif data == 'ad:upload':
            admin_required_callback(call, upload_callback)
        elif data == 'ad:managefiles':
            admin_required_callback(call, check_files_callback)
        elif data.startswith('file_'):
            file_control_callback(call)
        elif data.startswith('start_'):
            start_bot_callback(call)
        elif data.startswith('stop_'):
            stop_bot_callback(call)
        elif data.startswith('restart_'):
            restart_bot_callback(call)
        elif data.startswith('delete_'):
            delete_bot_callback(call)
        elif data.startswith('logs_'):
            logs_bot_callback(call)
        elif data == 'back_to_main':
            back_to_main_callback(call)
        elif data == 'send_command':
            send_command_callback(call)
        elif data == 'send_command_back':
            send_command_back_callback(call)
        elif data.startswith('sendcmd_select_'):
            sendcmd_select_callback(call)
        elif data.startswith('confirm_broadcast_'):
            handle_confirm_broadcast(call)
        elif data == 'cancel_broadcast':
            handle_cancel_broadcast(call)
        elif data == 'add_admin':
            owner_required_callback(call, add_admin_init_callback)
        elif data == 'remove_admin':
            owner_required_callback(call, remove_admin_init_callback)
        elif data == 'list_admins':
            admin_required_callback(call, list_admins_callback)
        elif data == 'add_subscription':
            admin_required_callback(call, add_subscription_init_callback)
        elif data == 'remove_subscription':
            admin_required_callback(call, remove_subscription_init_callback)
        elif data == 'list_subscriptions':
            admin_required_callback(call, list_subscriptions_callback)
        elif data == 'clone_create':
            clone_create_callback(call)
        elif data == 'clone_remove':
            clone_remove_callback(call)
        elif data == 'clone_remove_confirm':
            clone_remove_confirm_callback(call)
        elif data == 'manual_install': manual_install_callback(call)
        elif data == 'check_subscription': admin_required_callback(call, check_subscription_init_callback)
        elif data == 'user_management': admin_required_callback(call, user_management_callback)
        elif data == 'ban_user': admin_required_callback(call, ban_user_callback)
        elif data == 'unban_user': admin_required_callback(call, unban_user_callback)
        elif data == 'user_info': admin_required_callback(call, user_info_callback)
        elif data == 'all_users': admin_required_callback(call, all_users_callback)
        elif data == 'set_user_limit': admin_required_callback(call, set_user_limit_callback)
        elif data == 'remove_user_limit': admin_required_callback(call, remove_user_limit_callback)
        elif data == 'admin_settings': admin_required_callback(call, admin_settings_callback)
        elif data == 'system_info': admin_required_callback(call, system_info_callback)
        elif data == 'bot_performance': admin_required_callback(call, bot_performance_callback)
        elif data == 'cleanup_files': admin_required_callback(call, cleanup_files_callback)
        elif data == 'install_logs': admin_required_callback(call, install_logs_callback)
        elif data == 'admin_install': admin_required_callback(call, admin_install_callback)
        elif data == 'manage_mandatory_channels': admin_required_callback(call, manage_mandatory_channels_callback)
        elif data == 'add_mandatory_channel': admin_required_callback(call, add_mandatory_channel_callback)
        elif data == 'remove_mandatory_channel': admin_required_callback(call, remove_mandatory_channel_callback)
        elif data == 'list_mandatory_channels': admin_required_callback(call, list_mandatory_channels_callback)
        elif data.startswith('remove_channel_'): admin_required_callback(call, process_remove_channel)
        elif data.startswith('users_page_'): handle_users_page(call)
        elif data == 'check_subscription_status': check_subscription_status_callback(call)
        elif data == 'noop': bot.answer_callback_query(call.id)
        elif data == 'recommended_install': _logic_recommended_install(call.message); bot.answer_callback_query(call.id)
        elif data == 'ai_assistant': _logic_ai_assistant(call.message); bot.answer_callback_query(call.id)
        elif data == 'github_deploy': _logic_github_deploy(call.message); bot.answer_callback_query(call.id)
        elif data == 'change_ai_model': admin_required_callback(call, change_ai_model_callback)
        elif data == 'clone_control':
            clone_control_callback(call)
        elif data.startswith('clone_manage_'):
            clone_manage_callback(call)
        elif data.startswith('clone_stop_') or data.startswith('clone_start_') or data.startswith('clone_ban_'):
            clone_control_action_callback(call)
        # --- Hosting panel callbacks ---
        elif data.startswith('plan_detail_'):
            plan_key = data.replace('plan_detail_', '')
            p = PLAN_CONFIG.get(plan_key)
            if not p:
                bot.answer_callback_query(call.id, "Plan not found.", show_alert=True)
                return
            detail = (
                f"{p['emoji']} {p['name'].upper()}\n\n"
                f"💾 Storage ➜ {p['storage']}\n"
                f"🧠 RAM ➜ {p['ram']}\n"
                f"📁 Projects ➜ {p['projects']}\n"
                f"🌐 External URL ➜ {'Yes' if p['external_url'] else 'No'}\n"
                f"🟢 Uptime ➜ {p['uptime']}\n"
                f"🛡️ File Validation ➜ {'Yes' if p['file_validation'] else 'No'}\n"
                f"⭐ Stars / Month ➜ {p['stars_per_month']}\n"
                f"📜 Validity ➜ {p['validity_days']} days\n\n"
                f"To purchase, contact the owner."
            )
            mk = types.InlineKeyboardMarkup()
            mk.add(ikb('↗️ Contact Owner', style='primary', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'))
            mk.add(ikb('↩️ Back', style='danger', callback_data='open_buy_plan'))
            bot.answer_callback_query(call.id)
            try:
                bot.edit_message_text(detail, call.message.chat.id, call.message.message_id, reply_markup=mk)
            except Exception:
                bot.send_message(call.message.chat.id, detail, reply_markup=mk)
        elif data == 'open_buy_plan':
            bot.answer_callback_query(call.id)
            _logic_buy_plan(call.message)
        elif data.startswith('host_upload_'):
            category = data.replace('host_upload_', '')
            bot.answer_callback_query(call.id)
            pending = getattr(bot, '_host_upload_pending', {})
            pending[user_id] = category
            bot._host_upload_pending = pending
            bot.send_message(call.message.chat.id,
                f"📤 Send your project file for **{category}** hosting.\n"
                f"Accepted: `.py` `.js` `.zip` `.html`\n"
                f"/cancel to abort.",
                parse_mode='Markdown')
            bot.register_next_step_handler_by_chat_id(call.message.chat.id, lambda m: _handle_host_upload_and_resume(m, category))
        elif data.startswith('host_list_'):
            category = data.replace('host_list_', '')
            bot.answer_callback_query(call.id)
            projects = _list_hosting_projects(user_id, category)
            if not projects:
                bot.send_message(call.message.chat.id, f"📂 No {category} projects found. Upload one first.")
                return
            mk = types.InlineKeyboardMarkup(row_width=1)
            for proj in projects[:30]:
                mk.add(ikb(f"📁 {proj}", style='primary', callback_data=f"host_manage_{category}_{proj[:40]}"))
            mk.add(ikb('↩️ Back', style='danger', callback_data='back_to_main'))
            bot.send_message(call.message.chat.id, f"📂 Your {category} projects:", reply_markup=mk)
        elif data.startswith('host_manage_'):
            parts = data.split('_', 3)  # host_manage_{cat}_{name}
            if len(parts) < 4:
                bot.answer_callback_query(call.id, "Invalid.", show_alert=True)
                return
            category, proj = parts[2], parts[3]
            bot.answer_callback_query(call.id)
            script_key = f"{user_id}_{category}_{proj}"
            running = script_key in bot_scripts and is_bot_running(user_id, f"{category}_{proj}")
            mk = types.InlineKeyboardMarkup(row_width=2)
            if running:
                mk.row(
                    ikb('🔴 Stop', style='danger', callback_data=f'host_stop_{category}_{proj}'),
                    ikb('🔄 Restart', style='success', callback_data=f'host_restart_{category}_{proj}')
                )
            else:
                mk.row(ikb('🟢 Start', style='success', callback_data=f'host_start_{category}_{proj}'))
            mk.row(
                ikb('📜 Logs', style='primary', callback_data=f'host_logs_{category}_{proj}'),
                ikb('🗑️ Delete', style='danger', callback_data=f'host_delete_{category}_{proj}')
            )
            mk.row(ikb('↩️ Back', style='danger', callback_data=f'host_list_{category}'))
            status = '🟢 Running' if running else '🔴 Stopped'
            bot.send_message(call.message.chat.id, f"📁 {proj}\nStatus: {status}", reply_markup=mk)
        elif data.startswith('host_start_') or data.startswith('host_stop_') or data.startswith('host_restart_') or data.startswith('host_logs_') or data.startswith('host_delete_'):
            _handle_host_action(call)
        elif data == 'host_recommended':
            bot.answer_callback_query(call.id)
            _logic_recommended_install(call.message)
        else:
            bot.answer_callback_query(call.id, "❓ Unknown action.")
            logger.warning(f"⚠️ Unhandled callback data: {data} from user {user_id}")
    except Exception as e:
        logger.error(f"❌ Error handling callback '{data}' for {user_id}: {e}", exc_info=True)
        try:
            bot.answer_callback_query(call.id, "❌ Error processing request.", show_alert=True)
        except Exception as e_ans:
            logger.error(f"❌ Failed to answer callback after error: {e_ans}")

# ===== ADMIN REQUIRED CALLBACK =====
def admin_required_callback(call, func_to_run):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin permissions required.", show_alert=True)
        return
    func_to_run(call)

# ===== OWNER REQUIRED CALLBACK =====
def owner_required_callback(call, func_to_run):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "👑 Owner permissions required.", show_alert=True)
        return
    func_to_run(call)

# ===== UPLOAD CALLBACK =====
def upload_callback(call):
    user_id = call.from_user.id
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.answer_callback_query(call.id, f"⚠️ File limit ({current_files}/{limit_str}) reached.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "📤 Send your Python (`.py`), JS (`.js`), or ZIP (`.zip`) file.")

# ===== Cʜᴇᴄᴋ Fɪʟᴇs CALLBACK =====
def check_files_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    user_files_list = user_files.get(user_id, [])
    if not user_files_list:
        bot.answer_callback_query(call.id, "📂 No files uploaded.", show_alert=True)
        try:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_main'))
            bot.edit_message_text("📂 Your files:\n\n(No files uploaded)", chat_id, call.message.message_id, reply_markup=markup)
        except Exception as e: logger.error(f"❌ Error editing msg for empty file list: {e}")
        return
    bot.answer_callback_query(call.id)
    markup = types.InlineKeyboardMarkup(row_width=1)
    for file_name, file_type in sorted(user_files_list):
        is_running = is_bot_running(user_id, file_name)
        status_icon = "🟢 Active" if is_running else "🔴 Stopped"
        btn_text = f"{file_name} ({file_type}) - {status_icon}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f'file_{user_id}_{file_name}'))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_main'))
    try:
        bot.edit_message_text("📂 Your files:\nClick to manage.", chat_id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')
    except telebot.apihelper.ApiTelegramException as e:
         if "message is not modified" in str(e): logger.warning("ℹ️ Msg not modified (files).")
         else: logger.error(f"❌ Error editing msg for file list: {e}")
    except Exception as e: logger.error(f"❌ Unexpected error editing msg for file list: {e}", exc_info=True)

# ===== FILE CONTROL CALLBACK =====
def file_control_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id

        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            logger.warning(f"⚠️ User {requesting_user_id} tried to access file '{file_name}' of user {script_owner_id} without permission.")
            bot.answer_callback_query(call.id, "⛔ You can only manage your own files.", show_alert=True)
            check_files_callback(call)
            return

        user_files_list = user_files.get(script_owner_id, [])
        if not any(f[0] == file_name for f in user_files_list):
            bot.answer_callback_query(call.id, "❓ File not found.", show_alert=True)
            check_files_callback(call)
            return

        bot.answer_callback_query(call.id)
        is_running = is_bot_running(script_owner_id, file_name)
        status_text = '🟢 Active' if is_running else '🔴 Stopped'
        file_type = next((f[1] for f in user_files_list if f[0] == file_name), '?')
        try:
            bot.edit_message_text(
                f"⚙️ Controls for: `{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: {status_text}",
                call.message.chat.id, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, file_name, is_running),
                parse_mode='Markdown'
            )
        except telebot.apihelper.ApiTelegramException as e:
             if "message is not modified" in str(e): logger.warning(f"ℹ️ Msg not modified (controls for {file_name})")
             else: raise
    except (ValueError, IndexError) as ve:
        logger.error(f"❌ Error parsing file control callback: {ve}. Data: '{call.data}'")
        bot.answer_callback_query(call.id, "❌ Error: Invalid action data.", show_alert=True)
    except Exception as e:
        logger.error(f"❌ Error in file_control_callback for data '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ An error occurred.", show_alert=True)

# ===== START BOT CALLBACK =====
def start_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        logger.info(f"▶️ Start request: Requester={requesting_user_id}, Owner={script_owner_id}, File='{file_name}'")

        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⛔ Permission denied to start this script.", show_alert=True); return

        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "❓ File not found.", show_alert=True); check_files_callback(call); return

        file_type = file_info[1]
        user_folder = get_user_folder(script_owner_id)
        file_path = os.path.join(user_folder, file_name)

        if not os.path.exists(file_path):
            bot.answer_callback_query(call.id, f"⚠️ File not found.", show_alert=True)
            remove_user_file_db(script_owner_id, file_name); check_files_callback(call); return

        if is_bot_running(script_owner_id, file_name):
            bot.answer_callback_query(call.id, f"⚠️ Script '{file_name}' already running.", show_alert=True)
            try: bot.edit_message_reply_markup(chat_id_for_reply, call.message.message_id, reply_markup=create_control_buttons(script_owner_id, file_name, True))
            except Exception as e: logger.error(f"❌ Error updating buttons (already running): {e}")
            return

        bot.answer_callback_query(call.id, f"⏳ Attempting to start {file_name} for user {script_owner_id}...")

        if file_type == 'py':
            threading.Thread(target=run_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        elif file_type == 'js':
            threading.Thread(target=run_js_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        else:
             bot.send_message(chat_id_for_reply, f"❌ Error: Unknown file type '{file_type}' for '{file_name}'."); return

        time.sleep(1.5)
        is_now_running = is_bot_running(script_owner_id, file_name)
        status_text = '🟢 Active' if is_now_running else '🟡 Starting (or failed, check logs/replies)'
        try:
            bot.edit_message_text(
                f"⚙️ Controls for: `{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: {status_text}",
                chat_id_for_reply, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, file_name, is_now_running), parse_mode='Markdown'
            )
        except telebot.apihelper.ApiTelegramException as e:
             if "message is not modified" in str(e): logger.warning(f"ℹ️ Msg not modified after starting {file_name}")
             else: raise
    except (ValueError, IndexError) as e:
        logger.error(f"❌ Error parsing start callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "❌ Error: Invalid start command.", show_alert=True)
    except Exception as e:
        logger.error(f"❌ Error in start_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ Error starting script.", show_alert=True)
        try:
            _, script_owner_id_err_str, file_name_err = call.data.split('_', 2)
            script_owner_id_err = int(script_owner_id_err_str)
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_control_buttons(script_owner_id_err, file_name_err, False))
        except Exception as e_btn: logger.error(f"❌ Failed to update buttons after start error: {e_btn}")

# ===== STOP BOT CALLBACK =====
def stop_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        logger.info(f"⏹️ Stop request: Requester={requesting_user_id}, Owner={script_owner_id}, File='{file_name}'")
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⛔ Permission denied.", show_alert=True); return

        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "❓ File not found.", show_alert=True); check_files_callback(call); return

        file_type = file_info[1]
        script_key = f"{script_owner_id}_{file_name}"

        if not is_bot_running(script_owner_id, file_name):
            bot.answer_callback_query(call.id, f"🛑 Script '{file_name}' already stopped.", show_alert=True)
            try:
                 bot.edit_message_text(
                     f"⚙️ Controls for: `{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: 🔴 Stopped",
                     chat_id_for_reply, call.message.message_id,
                     reply_markup=create_control_buttons(script_owner_id, file_name, False), parse_mode='Markdown')
            except Exception as e: logger.error(f"❌ Error updating buttons (already stopped): {e}")
            return

        bot.answer_callback_query(call.id, f"⏳ Stopping {file_name} for user {script_owner_id}...")
        process_info = bot_scripts.get(script_key)
        if process_info:
            kill_process_tree(process_info)
            if script_key in bot_scripts: del bot_scripts[script_key]; logger.info(f"✅ Removed {script_key} from running after stop.")
        else: logger.warning(f"⚠️ Script {script_key} running by psutil but not in bot_scripts dict.")

        try:
            bot.edit_message_text(
                f"⚙️ Controls for: `{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: 🔴 Stopped",
                chat_id_for_reply, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, file_name, False), parse_mode='Markdown'
            )
        except telebot.apihelper.ApiTelegramException as e:
             if "message is not modified" in str(e): logger.warning(f"ℹ️ Msg not modified after stopping {file_name}")
             else: raise
    except (ValueError, IndexError) as e:
        logger.error(f"❌ Error parsing stop callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "❌ Error: Invalid stop command.", show_alert=True)
    except Exception as e:
        logger.error(f"❌ Error in stop_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ Error stopping script.", show_alert=True)

# ===== RESTART BOT CALLBACK =====
def restart_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        logger.info(f"🔄 Restart: Requester={requesting_user_id}, Owner={script_owner_id}, File='{file_name}'")
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⛔ Permission denied.", show_alert=True); return

        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "❓ File not found.", show_alert=True); check_files_callback(call); return

        file_type = file_info[1]; user_folder = get_user_folder(script_owner_id)
        file_path = os.path.join(user_folder, file_name); script_key = f"{script_owner_id}_{file_name}"

        if not os.path.exists(file_path):
            bot.answer_callback_query(call.id, f"⚠️ File not found.", show_alert=True)
            remove_user_file_db(script_owner_id, file_name)
            if script_key in bot_scripts: del bot_scripts[script_key]
            check_files_callback(call); return

        bot.answer_callback_query(call.id, f"🔄 Restarting {file_name} for user {script_owner_id}...")
        if is_bot_running(script_owner_id, file_name):
            logger.info(f"🔄 Restart: Stopping existing {script_key}...")
            process_info = bot_scripts.get(script_key)
            if process_info: kill_process_tree(process_info)
            if script_key in bot_scripts: del bot_scripts[script_key]
            time.sleep(1.5)

        logger.info(f"🚀 Restart: Starting script {script_key}...")
        if file_type == 'py':
            threading.Thread(target=run_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        elif file_type == 'js':
            threading.Thread(target=run_js_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        else:
             bot.send_message(chat_id_for_reply, f"❌ Unknown type '{file_type}' for '{file_name}'."); return

        time.sleep(1.5)
        is_now_running = is_bot_running(script_owner_id, file_name)
        status_text = '🟢 Active' if is_now_running else '🟡 Starting (or failed, check logs/replies)'
        try:
            bot.edit_message_text(
                f"⚙️ Controls for: `{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: {status_text}",
                chat_id_for_reply, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, file_name, is_now_running), parse_mode='Markdown'
            )
        except telebot.apihelper.ApiTelegramException as e:
             if "message is not modified" in str(e): logger.warning(f"ℹ️ Msg not modified (restart {file_name})")
             else: raise
    except (ValueError, IndexError) as e:
        logger.error(f"❌ Error parsing restart callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "❌ Error: Invalid restart command.", show_alert=True)
    except Exception as e:
        logger.error(f"❌ Error in restart_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ Error restarting.", show_alert=True)
        try:
            _, script_owner_id_err_str, file_name_err = call.data.split('_', 2)
            script_owner_id_err = int(script_owner_id_err_str)
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_control_buttons(script_owner_id_err, file_name_err, False))
        except Exception as e_btn: logger.error(f"❌ Failed to update buttons after restart error: {e_btn}")

# ===== DELETE BOT CALLBACK =====
def delete_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        logger.info(f"🗑️ Delete: Requester={requesting_user_id}, Owner={script_owner_id}, File='{file_name}'")
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⛔ Permission denied.", show_alert=True); return

        user_files_list = user_files.get(script_owner_id, [])
        if not any(f[0] == file_name for f in user_files_list):
            bot.answer_callback_query(call.id, "❓ File not found.", show_alert=True); check_files_callback(call); return

        bot.answer_callback_query(call.id, f"🗑️ Deleting {file_name} for user {script_owner_id}...")
        script_key = f"{script_owner_id}_{file_name}"
        if is_bot_running(script_owner_id, file_name):
            logger.info(f"🗑️ Delete: Stopping {script_key}...")
            process_info = bot_scripts.get(script_key)
            if process_info: kill_process_tree(process_info)
            if script_key in bot_scripts: del bot_scripts[script_key]
            time.sleep(0.5)

        user_folder = get_user_folder(script_owner_id)
        file_path = os.path.join(user_folder, file_name)
        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        deleted_disk = []
        if os.path.exists(file_path):
            try: os.remove(file_path); deleted_disk.append(file_name); logger.info(f"🗑️ Deleted file: {file_path}")
            except OSError as e: logger.error(f"❌ Error deleting {file_path}: {e}")
        if os.path.exists(log_path):
            try: os.remove(log_path); deleted_disk.append(os.path.basename(log_path)); logger.info(f"🗑️ Deleted log: {log_path}")
            except OSError as e: logger.error(f"❌ Error deleting log {log_path}: {e}")

        remove_user_file_db(script_owner_id, file_name)
        deleted_str = ", ".join(f"`{f}`" for f in deleted_disk) if deleted_disk else "associated files"
        try:
            bot.edit_message_text(
                f"🗑️ Record `{file_name}` (User `{script_owner_id}`) and {deleted_str} deleted!",
                chat_id_for_reply, call.message.message_id, reply_markup=None, parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"❌ Error editing msg after delete: {e}")
            bot.send_message(chat_id_for_reply, f"✅ Record `{file_name}` deleted.", parse_mode='Markdown')
    except (ValueError, IndexError) as e:
        logger.error(f"❌ Error parsing delete callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "❌ Error: Invalid delete command.", show_alert=True)
    except Exception as e:
        logger.error(f"❌ Error in delete_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ Error deleting.", show_alert=True)

# ===== LOGS BOT CALLBACK =====
def logs_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        logger.info(f"📜 Logs: Requester={requesting_user_id}, Owner={script_owner_id}, File='{file_name}'")
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⛔ Permission denied.", show_alert=True); return

        user_files_list = user_files.get(script_owner_id, [])
        if not any(f[0] == file_name for f in user_files_list):
            bot.answer_callback_query(call.id, "❓ File not found.", show_alert=True); check_files_callback(call); return

        user_folder = get_user_folder(script_owner_id)
        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        if not os.path.exists(log_path):
            bot.answer_callback_query(call.id, f"📜 No logs for '{file_name}'.", show_alert=True); return

        bot.answer_callback_query(call.id)
        try:
            log_content = ""; file_size = os.path.getsize(log_path)
            max_log_kb = 100; max_tg_msg = 4096
            if file_size == 0: log_content = "(Log empty)"
            elif file_size > max_log_kb * 1024:
                 with open(log_path, 'rb') as f: f.seek(-max_log_kb * 1024, os.SEEK_END); log_bytes = f.read()
                 log_content = log_bytes.decode('utf-8', errors='ignore')
                 log_content = f"(Last {max_log_kb} KB)\n...\n" + log_content
            else:
                 with open(log_path, 'r', encoding='utf-8', errors='ignore') as f: log_content = f.read()

            if len(log_content) > max_tg_msg:
                log_content = log_content[-max_tg_msg:]
                first_nl = log_content.find('\n')
                if first_nl != -1: log_content = "...\n" + log_content[first_nl+1:]
                else: log_content = "...\n" + log_content
            if not log_content.strip(): log_content = "(No visible content)"

            bot.send_message(chat_id_for_reply, f"📜 Logs for `{file_name}` (User `{script_owner_id}`):\n```\n{log_content}\n```", parse_mode='Markdown')
        except Exception as e:
            logger.error(f"❌ Error reading/sending log {log_path}: {e}", exc_info=True)
            bot.send_message(chat_id_for_reply, f"⚠️ Error reading log for `{file_name}`.")
    except (ValueError, IndexError) as e:
        logger.error(f"❌ Error parsing logs callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "❌ Error: Invalid logs command.", show_alert=True)
    except Exception as e:
        logger.error(f"❌ Error in logs_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ Error fetching logs.", show_alert=True)

# ===== BACK TO MAIN CALLBACK =====
def back_to_main_callback(call):
    """Return to the main keyboard without replaying the /start welcome message."""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    try:
        bot.send_message(chat_id, "Main menu.", reply_markup=create_reply_keyboard_main_menu(user_id))
    except Exception as e:
        logger.error(f"❌ Error returning to main menu: {e}", exc_info=True)

# ===== LOCK BOT CALLBACK =====
def lock_bot_callback(call):
    global bot_locked; bot_locked = True
    logger.warning(f"🔒 Bot locked by Admin {call.from_user.id}")
    bot.answer_callback_query(call.id, "🔒 Bot locked.")
    try: bot.edit_message_text("🔒 Bot has been locked.", call.message.chat.id, call.message.message_id)
    except Exception as e: logger.error(f"❌ Error updating menu (lock): {e}")

# ===== UNLOCK BOT CALLBACK =====
def unlock_bot_callback(call):
    global bot_locked; bot_locked = False
    logger.warning(f"✅ Bot unlocked by Admin {call.from_user.id}")
    bot.answer_callback_query(call.id, "✅ Bot unlocked.")
    try: bot.edit_message_text("✅ Bot has been unlocked.", call.message.chat.id, call.message.message_id)
    except Exception as e: logger.error(f"❌ Error updating menu (unlock): {e}")

# ===== BROADCAST INIT CALLBACK =====
def broadcast_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "📢 Send message to broadcast.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

# ===== PROCESS BROADCAST MESSAGE =====
def process_broadcast_message(message):
    user_id = message.from_user.id
    if user_id not in admin_ids: bot.reply_to(message, "⛔ Not authorized."); return
    if message.text and message.text.lower() == '/cancel': bot.reply_to(message, "❌ Broadcast cancelled."); return

    broadcast_content = message.text
    if not broadcast_content and not (message.photo or message.video or message.document or message.sticker or message.voice or message.audio):
         bot.reply_to(message, "⚠️ Cannot broadcast empty message. Send text or media, or /cancel.")
         msg = bot.send_message(message.chat.id, "📢 Send broadcast message or /cancel.")
         bot.register_next_step_handler(msg, process_broadcast_message)
         return

    target_count = len(active_users)
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_broadcast_{message.message_id}"),
               types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast"))

    preview_text = broadcast_content[:1000].strip() if broadcast_content else "(Media message)"
    bot.reply_to(message, f"📢 Confirm Broadcast:\n\n```\n{preview_text}\n```\n"
                          f"To **{target_count}** users. Sure?", reply_markup=markup, parse_mode='Markdown')

# ===== HANDLE CONFIRM BROADCAST =====
def handle_confirm_broadcast(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    if user_id not in admin_ids: bot.answer_callback_query(call.id, "⛔ Admin only.", show_alert=True); return
    try:
        original_message = call.message.reply_to_message
        if not original_message: raise ValueError("Could not retrieve original message.")

        broadcast_text = None
        broadcast_photo_id = None
        broadcast_video_id = None

        if original_message.text:
            broadcast_text = original_message.text
        elif original_message.photo:
            broadcast_photo_id = original_message.photo[-1].file_id
        elif original_message.video:
            broadcast_video_id = original_message.video.file_id
        else:
            raise ValueError("Message has no text or supported media for broadcast.")

        bot.answer_callback_query(call.id, "📢 Starting broadcast...")
        bot.edit_message_text(f"📢 Bʀᴏᴀᴅᴄᴀsᴛing to {len(active_users)} users...",
                              chat_id, call.message.message_id, reply_markup=None)
        thread = threading.Thread(target=execute_broadcast, args=(
            broadcast_text, broadcast_photo_id, broadcast_video_id,
            original_message.caption if (broadcast_photo_id or broadcast_video_id) else None,
            chat_id))
        thread.start()
    except ValueError as ve:
        logger.error(f"❌ Error retrieving msg for broadcast confirm: {ve}")
        bot.edit_message_text(f"❌ Error starting broadcast: {ve}", chat_id, call.message.message_id, reply_markup=None)
    except Exception as e:
        logger.error(f"❌ Error in handle_confirm_broadcast: {e}", exc_info=True)
        bot.edit_message_text("❌ Unexpected error during broadcast confirm.", chat_id, call.message.message_id, reply_markup=None)

# ===== HANDLE CANCEL BROADCAST =====
def handle_cancel_broadcast(call):
    bot.answer_callback_query(call.id, "📢 Bʀᴏᴀᴅᴄᴀsᴛ cancelled.")
    bot.delete_message(call.message.chat.id, call.message.message_id)
    if call.message.reply_to_message:
        try: bot.delete_message(call.message.chat.id, call.message.reply_to_message.message_id)
        except: pass

# ===== EXECUTE BROADCAST =====
def execute_broadcast(broadcast_text, photo_id, video_id, caption, admin_chat_id):
    sent_count = 0; failed_count = 0; blocked_count = 0
    start_exec_time = time.time()
    users_to_broadcast = list(active_users); total_users = len(users_to_broadcast)
    logger.info(f"📢 Executing broadcast to {total_users} users.")
    batch_size = 25; delay_batches = 1.5

    for i, user_id_bc in enumerate(users_to_broadcast):
        try:
            if broadcast_text:
                bot.send_message(user_id_bc, broadcast_text, parse_mode='Markdown')
            elif photo_id:
                bot.send_photo(user_id_bc, photo_id, caption=caption, parse_mode='Markdown' if caption else None)
            elif video_id:
                bot.send_video(user_id_bc, video_id, caption=caption, parse_mode='Markdown' if caption else None)
            sent_count += 1
        except telebot.apihelper.ApiTelegramException as e:
            err_desc = str(e).lower()
            if any(s in err_desc for s in ["bot was blocked", "user is deactivated", "chat not found", "kicked from", "restricted"]):
                logger.warning(f"⚠️ Broadcast failed to {user_id_bc}: User blocked/inactive.")
                blocked_count += 1
            elif "flood control" in err_desc or "too many requests" in err_desc:
                retry_after = 5; match = re.search(r"retry after (\d+)", err_desc)
                if match: retry_after = int(match.group(1)) + 1
                logger.warning(f"⏱️ Flood control. Sleeping {retry_after}s...")
                time.sleep(retry_after)
                try:
                    if broadcast_text: bot.send_message(user_id_bc, broadcast_text, parse_mode='Markdown')
                    elif photo_id: bot.send_photo(user_id_bc, photo_id, caption=caption, parse_mode='Markdown' if caption else None)
                    elif video_id: bot.send_video(user_id_bc, video_id, caption=caption, parse_mode='Markdown' if caption else None)
                    sent_count += 1
                except Exception as e_retry: logger.error(f"❌ Broadcast retry failed to {user_id_bc}: {e_retry}"); failed_count +=1
            else: logger.error(f"❌ Broadcast failed to {user_id_bc}: {e}"); failed_count += 1
        except Exception as e: logger.error(f"❌ Unexpected error broadcasting to {user_id_bc}: {e}"); failed_count += 1

        if (i + 1) % batch_size == 0 and i < total_users - 1:
            logger.info(f"📢 Bʀᴏᴀᴅᴄᴀsᴛ batch {i//batch_size + 1} sent. Sleeping {delay_batches}s...")
            time.sleep(delay_batches)
        elif i % 5 == 0: time.sleep(0.2)

    duration = round(time.time() - start_exec_time, 2)
    result_msg = (f"📢 **Broadcast Complete!**\n\n"
                  f"✅ Sent: {sent_count}\n"
                  f"❌ Failed: {failed_count}\n"
                  f"🚫 Blocked/Inactive: {blocked_count}\n"
                  f"🎯 Targets: {total_users}\n"
                  f"⏱️ Duration: {duration}s")
    logger.info(result_msg)
    try: bot.send_message(admin_chat_id, result_msg, parse_mode='Markdown')
    except Exception as e: logger.error(f"❌ Failed to send broadcast result to admin {admin_chat_id}: {e}")

# ===== ADMIN PANEL CALLBACK =====
def admin_panel_callback(call):
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text("👑 Admin Panel\nManage admins (Owner actions may be restricted).",
                              call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel_inline())
    except Exception as e: logger.error(f"❌ Error showing admin panel: {e}")

# ===== ADD ADMIN INIT CALLBACK =====
def add_admin_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "🔢 Enter User ID to promote to Admin.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_add_admin_id)

# ===== PROCESS ADD ADMIN ID =====
def process_add_admin_id(message):
    owner_id_check = message.from_user.id
    if owner_id_check != OWNER_ID: bot.reply_to(message, "👑 Owner only."); return
    if message.text and message.text.strip().lower() == '/cancel':
        bot.reply_to(message, "❌ Admin promotion cancelled.", reply_markup=create_admin_panel())
        bot.register_next_step_handler(message, _admin_panel_reply_step)
        return
    try:
        new_admin_id = int(message.text.strip())
        if new_admin_id <= 0: raise ValueError("ID must be positive")
        if new_admin_id == OWNER_ID: bot.reply_to(message, "👑 Owner is already Owner."); return
        if new_admin_id in admin_ids: bot.reply_to(message, f"👑 User `{new_admin_id}` already Admin."); return
        add_admin_db(new_admin_id)
        logger.warning(f"👑 Admin {new_admin_id} added by Owner {owner_id_check}.")
        bot.reply_to(message, f"✅ User `{new_admin_id}` promoted to Admin.", reply_markup=create_admin_panel())
        bot.register_next_step_handler(message, _admin_panel_reply_step)
        try: bot.send_message(new_admin_id, "👑 Congrats! You are now an Admin.")
        except Exception as e: logger.error(f"❌ Failed to notify new admin {new_admin_id}: {e}")
    except ValueError:
        bot.reply_to(message, "❌ Invalid ID. Send numerical ID or /cancel.")
        msg = bot.send_message(message.chat.id, "🔢 Enter User ID to promote or /cancel.")
        bot.register_next_step_handler(msg, process_add_admin_id)
    except Exception as e: logger.error(f"❌ Error processing add admin: {e}", exc_info=True); bot.reply_to(message, "❌ Error.")

# ===== REMOVE ADMIN INIT CALLBACK =====
def remove_admin_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "🔢 Enter User ID of Admin to remove.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_remove_admin_id)

# ===== PROCESS REMOVE ADMIN ID =====
def process_remove_admin_id(message):
    owner_id_check = message.from_user.id
    if owner_id_check != OWNER_ID: bot.reply_to(message, "👑 Owner only."); return
    if message.text and message.text.strip().lower() == '/cancel':
        bot.reply_to(message, "❌ Admin removal cancelled.", reply_markup=create_admin_panel())
        bot.register_next_step_handler(message, _admin_panel_reply_step)
        return
    try:
        admin_id_remove = int(message.text.strip())
        if admin_id_remove <= 0: raise ValueError("ID must be positive")
        if admin_id_remove == OWNER_ID: bot.reply_to(message, "👑 Owner cannot remove self."); return
        if admin_id_remove not in admin_ids: bot.reply_to(message, f"👑 User `{admin_id_remove}` not Admin."); return
        if remove_admin_db(admin_id_remove):
            logger.warning(f"👑 Admin {admin_id_remove} removed by Owner {owner_id_check}.")
            bot.reply_to(message, f"✅ Admin `{admin_id_remove}` removed.", reply_markup=create_admin_panel())
            bot.register_next_step_handler(message, _admin_panel_reply_step)
            try: bot.send_message(admin_id_remove, "👑 You are no longer an Admin.")
            except Exception as e: logger.error(f"❌ Failed to notify removed admin {admin_id_remove}: {e}")
        else: bot.reply_to(message, f"❌ Failed to remove admin `{admin_id_remove}`. Check logs.")
    except ValueError:
        bot.reply_to(message, "❌ Invalid ID. Send numerical ID or /cancel.")
        msg = bot.send_message(message.chat.id, "🔢 Enter Admin ID to remove or /cancel.")
        bot.register_next_step_handler(msg, process_remove_admin_id)
    except Exception as e: logger.error(f"❌ Error processing remove admin: {e}", exc_info=True); bot.reply_to(message, "❌ Error.")

# ===== LIST ADMINS CALLBACK =====
def list_admins_callback(call):
    bot.answer_callback_query(call.id)
    try:
        admin_list_str = ""
        for aid in sorted(list(admin_ids)):
            if aid == OWNER_ID:
                admin_list_str += f"👑 `{aid}` (Owner)\n"
            else:
                admin_list_str += f"👤 `{aid}`\n"
        
        if not admin_list_str: 
            admin_list_str = "😕 No Owner/Admins configured!"
        
        bot.edit_message_text(
            f"👑 **Current Admins:**\n\n{admin_list_str}", 
            call.message.chat.id,
            call.message.message_id, 
            reply_markup=create_admin_panel(), 
            parse_mode='Markdown'
        )
    except Exception as e: 
        logger.error(f"❌ Error listing admins: {e}")

# ===== ADD SUBSCRIPTION INIT CALLBACK =====
def add_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id, 
        "🔢 Enter User ID & days\n"
        "Example: `123456789 30`\n"
        "/cancel",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, process_add_subscription_details)
    
# ===== PROCESS ADD SUBSCRIPTION DETAILS =====
def process_add_subscription_details(message):
    admin_id_check = message.from_user.id
    if admin_id_check not in admin_ids: bot.reply_to(message, "⛔ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "❌ Sub add cancelled."); return
    try:
        parts = message.text.split();
        if len(parts) != 2: raise ValueError("Incorrect format")
        sub_user_id = int(parts[0].strip()); days = int(parts[1].strip())
        if sub_user_id <= 0 or days <= 0: raise ValueError("User ID/days must be positive")

        current_expiry = user_subscriptions.get(sub_user_id, {}).get('expiry')
        start_date_new_sub = datetime.now()
        if current_expiry and current_expiry > start_date_new_sub: start_date_new_sub = current_expiry
        new_expiry = start_date_new_sub + timedelta(days=days)
        save_subscription(sub_user_id, new_expiry)

        logger.info(f"💳 Sub for {sub_user_id} by admin {admin_id_check}. Expiry: {new_expiry:%Y-%m-%d}")
        bot.reply_to(message, f"✅ Sub for `{sub_user_id}` by {days} days.\nNew expiry: {new_expiry:%Y-%m-%d}")
        try: bot.send_message(sub_user_id, f"💎 Sub activated/extended by {days} days! Expires: {new_expiry:%Y-%m-%d}.")
        except Exception as e: logger.error(f"❌ Failed to notify {sub_user_id} of new sub: {e}")
    except ValueError as e:
        bot.reply_to(message, f"❌ Invalid: {e}. Format: `ID days` or /cancel.")
        msg = bot.send_message(message.chat.id, "🔢 Enter User ID & days, or /cancel.")
        bot.register_next_step_handler(msg, process_add_subscription_details)
    except Exception as e: logger.error(f"❌ Error processing add sub: {e}", exc_info=True); bot.reply_to(message, "❌ Error.")

# ===== REMOVE SUBSCRIPTION INIT CALLBACK =====
def remove_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id, 
        "🔢 Enter User ID to remove\n/cancel"
    )
    bot.register_next_step_handler(msg, process_remove_subscription_id)

# ===== PROCESS REMOVE SUBSCRIPTION ID =====
def process_remove_subscription_id(message):
    admin_id_check = message.from_user.id
    if admin_id_check not in admin_ids: bot.reply_to(message, "⛔ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "❌ Sub removal cancelled."); return
    try:
        sub_user_id_remove = int(message.text.strip())
        if sub_user_id_remove <= 0: raise ValueError("ID must be positive")
        if sub_user_id_remove not in user_subscriptions:
            bot.reply_to(message, f"ℹ️ User `{sub_user_id_remove}` no active sub in memory."); return
        remove_subscription_db(sub_user_id_remove)
        logger.warning(f"💳 Sub removed for {sub_user_id_remove} by admin {admin_id_check}.")
        bot.reply_to(message, f"✅ Sub for `{sub_user_id_remove}` removed.")
        try: bot.send_message(sub_user_id_remove, "❌ Your subscription removed by admin.")
        except Exception as e: logger.error(f"❌ Failed to notify {sub_user_id_remove} of sub removal: {e}")
    except ValueError:
        bot.reply_to(message, "❌ Invalid ID. Send numerical ID or /cancel.")
        msg = bot.send_message(message.chat.id, "🔢 Enter User ID to remove sub from, or /cancel.")
        bot.register_next_step_handler(msg, process_remove_subscription_id)
    except Exception as e: logger.error(f"❌ Error processing remove sub: {e}", exc_info=True); bot.reply_to(message, "❌ Error.")

# ===== LIST SUBSCRIPTIONS CALLBACK =====
def list_subscriptions_callback(call):
    bot.answer_callback_query(call.id)
    admin_id = call.from_user.id
    if admin_id not in admin_ids:
        bot.answer_callback_query(call.id, "⛔ Admin only.", show_alert=True)
        return
    
    if not user_subscriptions:
        bot.edit_message_text("😕 No active subscriptions found.", call.message.chat.id, call.message.message_id, reply_markup=create_subscription_panel())
        return
    
    subs_text = "**💳 Active Subscriptions:**\n\n"
    for user_id, sub_info in list(user_subscriptions.items())[:20]:
        expiry = sub_info.get('expiry')
        if expiry:
            days_left = (expiry - datetime.now()).days if expiry > datetime.now() else 0
            status = "🟢 Active" if expiry > datetime.now() else "🔴 Expired"
            subs_text += f"🆔 User {user_id}  \n"
            subs_text += f"🟢 Status: {status}  \n"
            subs_text += f"⏳ Expires: {expiry.strftime('%Y-%m-%d')} ({days_left} days remaining)\n\n"
    
    if len(user_subscriptions) > 20:
        subs_text += f"\n... and {len(user_subscriptions) - 20} more"
    
    bot.edit_message_text(subs_text, call.message.chat.id, call.message.message_id, reply_markup=create_subscription_panel(), parse_mode='Markdown')

# ===== MANUAL INSTALL CALLBACK =====
def manual_install_callback(call):
    user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    manual_install_module_init(call.message)

# ===== CHECK SUBSCRIPTION CALLBACKS =====
def check_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Enter User ID to check sub.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_check_subscription_id)

def process_check_subscription_id(message):
    admin_id_check = message.from_user.id
    if admin_id_check not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Sub check cancelled."); return
    try:
        sub_user_id_check = int(message.text.strip()) # Renamed
        if sub_user_id_check <= 0: raise ValueError("ID must be positive")
        if sub_user_id_check in user_subscriptions:
            expiry_dt = user_subscriptions[sub_user_id_check].get('expiry')
            if expiry_dt:
                if expiry_dt > datetime.now():
                    days_left = (expiry_dt - datetime.now()).days
                    bot.reply_to(message, f"✅ User `{sub_user_id_check}` active sub.\nExpires: {expiry_dt:%Y-%m-%d %H:%M:%S} ({days_left} days left).")
                else:
                    bot.reply_to(message, f"⚠️ User `{sub_user_id_check}` expired sub (On: {expiry_dt:%Y-%m-%d %H:%M:%S}).")
                    remove_subscription_db(sub_user_id_check) # Clean up
            else: bot.reply_to(message, f"⚠️ User `{sub_user_id_check}` in sub list, but expiry missing. Re-add if needed.")
        else: bot.reply_to(message, f"ℹ️ User `{sub_user_id_check}` no active sub record.")
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID. Send numerical ID or /cancel.")
        msg = bot.send_message(message.chat.id, "💳 Enter User ID to check, or /cancel.")
        bot.register_next_step_handler(msg, process_check_subscription_id)
    except Exception as e: logger.error(f"Error processing check sub: {e}", exc_info=True); bot.reply_to(message, "Error.")

# ===== USER MANAGEMENT CALLBACKS =====
# --- Uꜱᴇʀ Mᴀɴᴀɢᴇᴍᴇɴᴛ Callbacks ---
def user_management_callback(call):
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text("👥 Uꜱᴇʀ Mᴀɴᴀɢᴇᴍᴇɴᴛ\nSelect action:", call.message.chat.id, 
                              call.message.message_id, reply_markup=create_user_management_menu_inline())
    except Exception as e: logger.error(f"Error showing Uꜱᴇʀ Mᴀɴᴀɢᴇᴍᴇɴᴛ menu: {e}")

def ban_user_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "🚫 Enter User ID to ban and reason (e.g., `12345678 Spamming`)\n/cancel to cancel")
    bot.register_next_step_handler(msg, process_ban_user)

def process_ban_user(message):
    admin_id = message.from_user.id
    if admin_id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Ban cancelled.")
        return
    
    try:
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "⚠️ Format: `user_id reason`\nExample: `12345678 Spamming`")
            return
        
        user_id = int(parts[0])
        reason = ' '.join(parts[1:])
        
        if user_id <= 0: raise ValueError("ID must be positive")
        if user_id == OWNER_ID: bot.reply_to(message, "⚠️ Cannot ban owner."); return
        if user_id in admin_ids: bot.reply_to(message, "⚠️ Cannot ban admin."); return
        
        if ban_user_db(user_id, reason, admin_id):
            bot.reply_to(message, f"✅ User `{user_id}` banned.\nReason: {reason}")
            # Stop all scripts for banned user
            for file_name, _ in user_files.get(user_id, []):
                script_key = f"{user_id}_{file_name}"
                if script_key in bot_scripts:
                    kill_process_tree(bot_scripts[script_key])
                    del bot_scripts[script_key]
            
            try:
                bot.send_message(user_id, f"🚫 You have been banned from using this bot.\nReason: {reason}")
            except Exception as e:
                logger.error(f"Failed to notify banned user {user_id}: {e}")
        else:
            bot.reply_to(message, "❌ Failed to ban user.")
            
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid user ID. Must be a number.")
    except Exception as e:
        logger.error(f"Error banning user: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {str(e)}")

def unban_user_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "✅ Enter User ID to unban\n/cancel to cancel")
    bot.register_next_step_handler(msg, process_unban_user)

def process_unban_user(message):
    admin_id = message.from_user.id
    if admin_id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Unban cancelled.")
        return
    
    try:
        user_id = int(message.text.strip())
        if user_id <= 0: raise ValueError("ID must be positive")
        
        if user_id not in banned_users:
            bot.reply_to(message, f"ℹ️ User `{user_id}` is not banned.")
            return
        
        if unban_user_db(user_id):
            bot.reply_to(message, f"✅ User `{user_id}` unbanned.")
            try:
                bot.send_message(user_id, "✅ Your ban has been lifted. You can now use the bot again.")
            except Exception as e:
                logger.error(f"Failed to notify unbanned user {user_id}: {e}")
        else:
            bot.reply_to(message, "❌ Failed to unban user.")
            
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid user ID. Must be a number.")
    except Exception as e:
        logger.error(f"Error unbanning user: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {str(e)}")

def user_info_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👤 Enter User ID to get info\n/cancel to cancel")
    bot.register_next_step_handler(msg, process_user_info)

def process_user_info(message):
    admin_id = message.from_user.id
    if admin_id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Info request cancelled.")
        return
    
    try:
        user_id = int(message.text.strip())
        if user_id <= 0: raise ValueError("ID must be positive")
        
        # Gather user information
        info_parts = []
        
        # Basic info
        info_parts.append(f"👤 **User ID:** `{user_id}`")
        
        # Status
        if user_id == OWNER_ID:
            info_parts.append("👑 **Status:** Owner")
        elif user_id in admin_ids:
            info_parts.append("🛡️ **Status:** Admin")
        elif user_id in banned_users:
            info_parts.append("🚫 **Status:** Banned")
        elif user_id in user_subscriptions:
            expiry = user_subscriptions[user_id].get('expiry')
            if expiry and expiry > datetime.now():
                days_left = (expiry - datetime.now()).days
                info_parts.append(f"⭐ **Status:** Premium (Expires in {days_left} days)")
            else:
                info_parts.append("🆓 **Status:** Free User (Expired subscription)")
        else:
            info_parts.append("🆓 **Status:** Free User")
        
        # Files
        file_count = get_user_file_count(user_id)
        file_limit = get_user_file_limit(user_id)
        info_parts.append(f"📁 **Files:** {file_count}/{file_limit if file_limit != float('inf') else 'Unlimited'}")
        
        # Custom limit
        if user_id in user_limits:
            info_parts.append(f"⚙️ **Custom Limit:** {user_limits[user_id]}")
        
        # Active scripts
        running_scripts = 0
        for file_name, _ in user_files.get(user_id, []):
            if is_bot_running(user_id, file_name):
                running_scripts += 1
        info_parts.append(f"🤖 **Running Scripts:** {running_scripts}")
        
        # Last seen (if in active users)
        if user_id in active_users:
            info_parts.append("🟢 **Status:** Active")
        
        info_text = "\n".join(info_parts)
        bot.reply_to(message, info_text, parse_mode='Markdown')
        
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid user ID. Must be a number.")
    except Exception as e:
        logger.error(f"Error getting user info: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {str(e)}")

def all_users_callback(call):
    bot.answer_callback_query(call.id)
    try:
        if not active_users:
            bot.edit_message_text("👥 No active users yet.", call.message.chat.id, call.message.message_id)
            return
        
        users_list = list(active_users)
        chunk_size = 20
        total_pages = (len(users_list) + chunk_size - 1) // chunk_size
        
        # Create pagination
        current_page = 0
        display_users_list(call.message.chat.id, call.message.message_id, users_list, current_page, total_pages, chunk_size)
        
    except Exception as e:
        logger.error(f"Error displaying all users: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error displaying users.", show_alert=True)

def display_users_list(chat_id, message_id, users_list, page, total_pages, chunk_size):
    start_idx = page * chunk_size
    end_idx = min(start_idx + chunk_size, len(users_list))
    
    user_chunk = users_list[start_idx:end_idx]
    
    message_text = f"👥 **Active Users** (Page {page + 1}/{total_pages})\n\n"
    for i, user_id in enumerate(user_chunk, start=start_idx + 1):
        status = ""
        if user_id == OWNER_ID: status = "👑"
        elif user_id in admin_ids: status = "🛡️"
        elif user_id in banned_users: status = "🚫"
        elif user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now():
            status = "⭐"
        else: status = "🆓"
        
        message_text += f"{i}. `{user_id}` {status}\n"
    
    markup = types.InlineKeyboardMarkup(row_width=3)
    
    if total_pages > 1:
        page_buttons = []
        if page > 0:
            page_buttons.append(types.InlineKeyboardButton("⬅️ Previous", callback_data=f"users_page_{page-1}"))
        
        page_buttons.append(types.InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
        
        if page < total_pages - 1:
            page_buttons.append(types.InlineKeyboardButton("Next ▶️️", callback_data=f"users_page_{page+1}"))
        
        markup.row(*page_buttons)
    
    markup.row(types.InlineKeyboardButton("🔙 Back to Uꜱᴇʀ Mᴀɴᴀɢᴇᴍᴇɴᴛ", callback_data='user_management'))
    
    try:
        bot.edit_message_text(message_text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error editing users list: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('users_page_'))
def handle_users_page(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        return
    
    try:
        page = int(call.data.split('_')[2])
        users_list = list(active_users)
        chunk_size = 20
        total_pages = (len(users_list) + chunk_size - 1) // chunk_size
        
        if 0 <= page < total_pages:
            bot.answer_callback_query(call.id)
            display_users_list(call.message.chat.id, call.message.message_id, users_list, page, total_pages, chunk_size)
    except Exception as e:
        logger.error(f"Error handling users page: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error.", show_alert=True)

def set_user_limit_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "🔧 Enter User ID and new limit (e.g., `12345678 50`)\n/cancel to cancel")
    bot.register_next_step_handler(msg, process_set_user_limit)

def process_set_user_limit(message):
    admin_id = message.from_user.id
    if admin_id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Limit set cancelled.")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2: raise ValueError("Format: user_id limit")
        
        user_id = int(parts[0])
        limit = int(parts[1])
        
        if user_id <= 0 or limit <= 0: raise ValueError("ID and limit must be positive")
        
        if set_user_limit_db(user_id, limit, admin_id):
            bot.reply_to(message, f"✅ Set file limit {limit} for user `{user_id}`")
            try:
                bot.send_message(user_id, f"⚙️ Your file upload limit has been set to {limit}")
            except Exception as e:
                logger.error(f"Failed to notify user {user_id}: {e}")
        else:
            bot.reply_to(message, "❌ Failed to set limit.")
            
    except ValueError as e:
        bot.reply_to(message, f"⚠️ Invalid input: {e}\nFormat: `user_id limit`")
    except Exception as e:
        logger.error(f"Error setting user limit: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {str(e)}")

def remove_user_limit_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "🗑️ Enter User ID to remove custom limit\n/cancel to cancel")
    bot.register_next_step_handler(msg, process_remove_user_limit)

def process_remove_user_limit(message):
    admin_id = message.from_user.id
    if admin_id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Limit removal cancelled.")
        return
    
    try:
        user_id = int(message.text.strip())
        if user_id <= 0: raise ValueError("ID must be positive")
        
        if user_id not in user_limits:
            bot.reply_to(message, f"ℹ️ User `{user_id}` has no custom limit.")
            return
        
        if remove_user_limit_db(user_id):
            bot.reply_to(message, f"✅ Removed custom limit for user `{user_id}`")
            try:
                bot.send_message(user_id, "⚙️ Your custom file limit has been removed")
            except Exception as e:
                logger.error(f"Failed to notify user {user_id}: {e}")
        else:
            bot.reply_to(message, "❌ Failed to remove limit.")
            
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid user ID. Must be a number.")
    except Exception as e:
        logger.error(f"Error removing user limit: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {str(e)}")

# ===== ADMIN SETTINGS CALLBACKS =====
# --- Admin Settings Callbacks ---
def admin_settings_callback(call):
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text("⚙️ Admin Settings\nSelect action:", call.message.chat.id, 
                              call.message.message_id, reply_markup=create_admin_settings_menu_inline())
    except Exception as e: logger.error(f"Error showing admin settings: {e}")

def system_info_callback(call):
    bot.answer_callback_query(call.id)
    try:
        # Get system information
        import platform
        
        info_parts = []
        
        # Bot info
        info_parts.append("🤖 **Bot Information:**")
        info_parts.append(f"• Python: {platform.python_version()}")
        info_parts.append(f"• Platform: {platform.platform()}")
        info_parts.append(f"• Uptime: {time.strftime('%H:%M:%S', time.gmtime(time.time() - psutil.boot_time()))}")
        
        # System info
        info_parts.append("\n💻 **System Information:**")
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            info_parts.append(f"• CPU Usage: {cpu_percent}%")
            info_parts.append(f"• Memory: {memory.percent}% used ({memory.used//1024//1024}MB/{memory.total//1024//1024}MB)")
            info_parts.append(f"• Disk: {disk.percent}% used ({disk.used//1024//1024}MB/{disk.total//1024//1024}MB)")
        except Exception as e:
            info_parts.append(f"• System stats error: {str(e)}")
        
        # Bot stats
        info_parts.append("\n📊 **Bot Sᴛᴀᴛɪꜱᴛᴄꜱ:**")
        info_parts.append(f"• Active Users: {len(active_users)}")
        info_parts.append(f"• Running Scripts: {len(bot_scripts)}")
        info_parts.append(f"• Total Files: {sum(len(files) for files in user_files.values())}")
        info_parts.append(f"• Bot Status: {'🔒 Locked' if bot_locked else '🔓 Unlocked'}")
        
        info_text = "\n".join(info_parts)
        
        bot.edit_message_text(info_text, call.message.chat.id, call.message.message_id, 
                              reply_markup=create_admin_settings_menu_inline(), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error showing system info: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error showing system info.", show_alert=True)

def bot_performance_callback(call):
    bot.answer_callback_query(call.id)
    try:
        # Calculate performance metrics
        performance_parts = []
        
        # Script performance
        running_scripts = len(bot_scripts)
        total_files = sum(len(files) for files in user_files.values())
        
        performance_parts.append("📈 **Bot Performance Metrics:**")
        performance_parts.append(f"• Running Scripts: {running_scripts}")
        performance_parts.append(f"• Total Scripts: {total_files}")
        uptime_pct = (running_scripts / total_files * 100) if total_files > 0 else 0.0
        performance_parts.append(f"• Uptime Ratio: {running_scripts}/{total_files} ({uptime_pct:.1f}%)")
        
        # Resource usage
        try:
            bot_process = psutil.Process()
            memory_usage = bot_process.memory_info().rss / 1024 / 1024  # MB
            cpu_usage = bot_process.cpu_percent(interval=0.5)
            
            performance_parts.append(f"\n💾 **Resource Usage:**")
            performance_parts.append(f"• Memory: {memory_usage:.1f} MB")
            performance_parts.append(f"• CPU: {cpu_usage:.1f}%")
        except Exception as e:
            performance_parts.append(f"\n⚠️ Resource stats error: {str(e)}")
        
        # Database stats
        performance_parts.append(f"\n🗄️ **Database:**")
        performance_parts.append(f"• Active Users: {len(active_users)}")
        performance_parts.append(f"• Subscriptions: {len(user_subscriptions)}")
        performance_parts.append(f"• Banned Users: {len(banned_users)}")
        performance_parts.append(f"• Custom Limits: {len(user_limits)}")
        
        performance_text = "\n".join(performance_parts)
        
        bot.edit_message_text(performance_text, call.message.chat.id, call.message.message_id,
                              reply_markup=create_admin_settings_menu_inline(), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error showing performance: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error showing performance.", show_alert=True)

def cleanup_files_callback(call):
    bot.answer_callback_query(call.id, "🧹 Cleaning up temporary files...")
    
    try:
        # Clean up empty user directories
        cleaned_dirs = 0
        cleaned_files = 0
        
        for user_dir in os.listdir(UPLOAD_BOTS_DIR):
            user_path = os.path.join(UPLOAD_BOTS_DIR, user_dir)
            if os.path.isdir(user_path):
                # Check if directory is empty
                if not os.listdir(user_path):
                    try:
                        os.rmdir(user_path)
                        cleaned_dirs += 1
                    except Exception as e:
                        logger.error(f"Error removing empty dir {user_path}: {e}")
                
                # Clean old log files (older than 7 days)
                else:
                    for file_name in os.listdir(user_path):
                        if file_name.endswith('.log'):
                            file_path = os.path.join(user_path, file_name)
                            try:
                                file_age = time.time() - os.path.getmtime(file_path)
                                if file_age > 7 * 24 * 3600:  # 7 days
                                    os.remove(file_path)
                                    cleaned_files += 1
                            except Exception as e:
                                logger.error(f"Error cleaning log file {file_path}: {e}")
        
        result_msg = f"🧹 **Cleanup Complete:**\n• Removed empty directories: {cleaned_dirs}\n• Cleared old log files: {cleaned_files}"
        
        bot.edit_message_text(result_msg, call.message.chat.id, call.message.message_id,
                              reply_markup=create_admin_settings_menu_inline(), parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error during cleanup: {e}", exc_info=True)
        bot.edit_message_text(f"❌ Cleanup error: {str(e)}", call.message.chat.id, call.message.message_id)

def install_logs_callback(call):
    bot.answer_callback_query(call.id)
    try:
        with DB_LOCK:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('SELECT user_id, module_name, package_name, status, install_date FROM install_logs ORDER BY install_date DESC LIMIT 20')
            logs = c.fetchall()
            conn.close()
        
        if not logs:
            bot.edit_message_text("📋 **No installation logs found**", call.message.chat.id, 
                                  call.message.message_id, reply_markup=create_admin_settings_menu_inline())
            return
        
        log_text = "📋 **Recent Installation Logs (Last 20):**\n\n"
        for user_id, module_name, package_name, status, install_date in logs:
            status_icon = "✅" if status == "success" else "❌" if status == "failed" else "⚠️"
            log_text += f"{status_icon} `{user_id}`: {module_name} -> {package_name}\n"
            log_text += f"   📅 {install_date[:19]}\n\n"
        
        bot.edit_message_text(log_text, call.message.chat.id, call.message.message_id,
                              reply_markup=create_admin_settings_menu_inline(), parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error showing install logs: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error showing logs.", show_alert=True)

def admin_install_callback(call):
    bot.answer_callback_query(call.id)
    _logic_admin_install(call.message)

# ===== MANDATORY CHANNELS CALLBACKS =====
# --- Mandatory Channels Callbacks ---
def manage_mandatory_channels_callback(call):
    """Handle mandatory channels management request."""
    bot.answer_callback_query(call.id)
    _show_mandatory_channels_menu(call.message.chat.id)

def add_mandatory_channel_callback(call):
    """Add new mandatory channel"""
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "📢 Send channel ID or username (example: @channel_username or -1001234567890)\n/cancel to cancel")
    bot.register_next_step_handler(msg, process_add_channel)

def process_add_channel(message):
    """Validate and save a mandatory Telegram channel."""
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Not authorized.")
        return

    channel_identifier = (message.text or "").strip()
    if channel_identifier.lower() == "/cancel":
        _show_mandatory_channels_menu(message.chat.id, "Channel addition cancelled.")
        return
    if not channel_identifier:
        prompt = bot.reply_to(message, "❌ Send a valid channel ID or @username, or /cancel.")
        bot.register_next_step_handler(prompt, process_add_channel)
        return

    try:
        chat = bot.get_chat(channel_identifier)
        if getattr(chat, "type", None) != "channel":
            prompt = bot.reply_to(message, "❌ That ID/username is not a channel. Send a channel ID or @username, or /cancel.")
            bot.register_next_step_handler(prompt, process_add_channel)
            return

        channel_id = str(chat.id)
        channel_username = f"@{chat.username}" if getattr(chat, "username", None) else ""
        channel_name = getattr(chat, "title", None) or "Channel"

        bot_member = bot.get_chat_member(channel_id, bot.get_me().id)
        if bot_member.status not in ("administrator", "creator"):
            prompt = bot.reply_to(message, "❌ Bot is not an administrator in this channel. Promote it, then send the channel again or /cancel.")
            bot.register_next_step_handler(prompt, process_add_channel)
            return

        # Private channels have no public @username. Generate a real invite URL
        # so the Force Join button can actually take users into that channel.
        if not channel_username:
            try:
                channel_username = bot.export_chat_invite_link(channel_id)
            except Exception as invite_error:
                logger.error("Could not create invite link for private channel %s: %s", channel_id, invite_error)
                prompt = bot.reply_to(
                    message,
                    "❌ This private channel needs an invite link. Give the bot permission to invite users, then try again."
                )
                bot.register_next_step_handler(prompt, process_add_channel)
                return

        if save_mandatory_channel(channel_id, channel_username, channel_name, message.from_user.id):
            _show_mandatory_channels_menu(
                message.chat.id,
                f"✅ Mandatory channel added:\n{channel_name}\n{channel_username or channel_id}\n\nIt is now included in Force Join."
            )
        else:
            _show_mandatory_channels_menu(message.chat.id, "❌ Failed to save channel in the database.")
    except Exception as exc:
        logger.error("Error adding mandatory channel %s: %s", channel_identifier, exc, exc_info=True)
        prompt = bot.reply_to(
            message,
            "❌ Could not access this channel. Check the ID/username and ensure the bot is an admin, then try again or /cancel."
        )
        bot.register_next_step_handler(prompt, process_add_channel)

def remove_mandatory_channel_callback(call):
    """Remove mandatory channel"""
    if not mandatory_channels:
        bot.answer_callback_query(call.id, "❌ No mandatory channels.", show_alert=True)
        return
        
    bot.answer_callback_query(call.id)
    
    markup = types.InlineKeyboardMarkup()
    for channel_id, channel_info in mandatory_channels.items():
        channel_name = channel_info.get('name', 'Unknown')
        button_text = f"🗑️ {channel_name}"
        markup.add(types.InlineKeyboardButton(button_text, callback_data=f'remove_channel_{channel_id}'))
    
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='manage_mandatory_channels'))
    
    try:
        bot.edit_message_text("📢 Choose channel to delete:",
                              call.message.chat.id, call.message.message_id, 
                              reply_markup=markup)
    except Exception as e:
        logger.error(f"Error showing remove channel menu: {e}")

def process_remove_channel(call):
    """Process channel removal"""
    channel_id = call.data.replace('remove_channel_', '')
    
    if channel_id in mandatory_channels:
        channel_name = mandatory_channels[channel_id].get('name', 'Unknown')
        if remove_mandatory_channel_db(channel_id):
            bot.answer_callback_query(call.id, f"✅ Channel deleted: {channel_name}")
            try:
                _show_mandatory_channels_menu(
                    call.message.chat.id,
                    f"✅ Mandatory channel deleted: {channel_name}"
                )
            except Exception as e:
                logger.error(f"Error updating message after channel removal: {e}")
        else:
            bot.answer_callback_query(call.id, "❌ Failed to delete channel.", show_alert=True)
    else:
        bot.answer_callback_query(call.id, "❌ Channel not found.", show_alert=True)

def list_mandatory_channels_callback(call):
    """Show list of mandatory channels"""
    bot.answer_callback_query(call.id)
    
    if not mandatory_channels:
        message_text = "📢 **No mandatory channels currently**"
    else:
        message_text = "📢 **Mandatory Channels:**\n\n"
        for channel_id, channel_info in mandatory_channels.items():
            channel_name = channel_info.get('name', 'Unknown')
            channel_username = channel_info.get('username', 'No username')
            message_text += f"• **{channel_name}**\n  {channel_username or channel_id}\n\n"
    
    try:
        _show_mandatory_channels_menu(call.message.chat.id, message_text)
    except Exception as e:
        logger.error(f"Error listing channels: {e}", exc_info=True)

def check_subscription_status_callback(call):
    """Check subscription status"""
    user_id = call.from_user.id
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    
    if is_subscribed or user_id in admin_ids:
        bot.answer_callback_query(call.id, "✅ You are subscribed to all required channels!", show_alert=True)
        # Show main menu
        try:
            _logic_send_welcome(call.message)
        except:
            back_to_main_callback(call)
    else:
        bot.answer_callback_query(call.id, "❌ You haven't joined all required channels yet!", show_alert=True)
        # Update the subscription message
        subscription_message, markup = create_subscription_check_message(not_joined)
        try:
            bot.edit_message_text(subscription_message, call.message.chat.id, 
                                  call.message.message_id, reply_markup=markup, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error updating subscription message: {e}")

# ===== CLONE CREATE CALLBACK =====
def clone_create_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(types.InlineKeyboardButton("❌ Cancel", callback_data="back_to_main"))
    
    bot.edit_message_text(
        f"🚀 **Cʀᴇᴀᴛᴇ Cʟᴏɴᴇ Bᴏᴛ** \n\n"
        f"Send your bot token from 👇\n"
        f"@BotFather \n"
        f"Format: `1234567890:ABCdefGHi`\n"
        f"`jklMnOpqrstUvWxyz`",
        chat_id, message_id,
        reply_markup=markup, parse_mode="Markdown"
    )
    
    bot.register_next_step_handler_by_chat_id(
        chat_id,
        lambda msg: handle_token_input(msg, chat_id, message_id)
    )
    bot.answer_callback_query(call.id)

# ===== CLONE REMOVE CALLBACK =====
def clone_remove_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    if user_id not in user_clones:
        bot.answer_callback_query(call.id, f"⚠️ No Clone bot found.", show_alert=True)
        return
    
    bot_username = user_clones[user_id]['bot_username']
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("✅ Remove", callback_data="clone_remove_confirm"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="back_to_main")
    )
    
    bot.edit_message_text(
        f"🗑️ **Remove Clone Bot** \n\n"
        f"⚠️ Remove your clone bot? \n\n"
        f"🤖 Bot Name @{bot_username}\n"
        f"🚀 Will be removed",
        chat_id, message_id,
        reply_markup=markup, parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id)

# ===== CLONE REMOVE CONFIRM CALLBACK =====
def clone_remove_confirm_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    if user_id not in user_clones:
        bot.answer_callback_query(call.id, f"⚠️ No clone bot found.", show_alert=True)
        return
    
    bot_username = user_clones[user_id]['bot_username']
    
    stop_clone_process(user_id)
    clone_dir = os.path.join(BASE_DIR, f'clone_{user_id}')
    if os.path.exists(clone_dir):
        try:
            shutil.rmtree(clone_dir)
        except Exception as e:
            logger.error(f"❌ Error removing clone directory for {user_id}: {e}")
    
    remove_clone_info(user_id)
    
    bot.answer_callback_query(call.id)
    
    bot.edit_message_text(
        f"✅ **Clone Bot Removed!** \n\n"
        f"🤖 Bot @{bot_username}\n"
        f"🗑️ Successfully removed",
        chat_id, message_id,
        reply_markup=None, parse_mode="Markdown"
    )

# ===== HANDLE TOKEN INPUT =====
def handle_token_input(message, original_chat_id, original_message_id):
    user_id = message.from_user.id
    
    if message.text == '/cancel':
        _logic_clone_bot(message)
        return
    
    token = message.text.strip()
    
    if not token or len(token) < 35 or ':' not in token:
        error_msg = f"**❌ Invalid bot token!** \n\n"
        error_msg += f"Please send a valid bot token from @BotFather \n"
        error_msg += f"Format: `1234567890:ABCdefGHi`\n"
        error_msg += f"`jklMnOpqrstUvWxyz` \n\n"
        
        markup = types.InlineKeyboardMarkup()
        markup.row(types.InlineKeyboardButton("❌ Cancel", callback_data="back_to_main"))
        
        bot.reply_to(
            message,
            error_msg,
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return
    
    processing_msg = bot.reply_to(
        message, 
        "🔄 Creating your bot clone...\n\nThis may take a moment..."
    )
    
    try:
        test_bot = telebot.TeleBot(token)
        bot_info = test_bot.get_me()
        
        bot.edit_message_text(
            f"✅ Token validated!\n\nBot: @{bot_info.username}\nCreating clone...",
            processing_msg.chat.id,
            processing_msg.message_id
        )
        
        clone_success = create_bot_clone(user_id, token, bot_info.username)
        
        if clone_success:
            success_msg = f"🎉 Bot Clone Created!"
            success_msg += f"**🤖 Bot Name:** @{bot_info.username} \n"
            success_msg += f"**🚀 Status:** Running \n"
            success_msg += f"**🔗 Features:** All Universal File Host \n"
            success_msg += f"**🛡️ Protection:** Auto-restart On \n\n"
            success_msg += f"**✨ Unlimited clones available**"
            
            bot.edit_message_text(
                success_msg,
                processing_msg.chat.id,
                processing_msg.message_id,
                parse_mode="Markdown"
            )
        else:
            bot.edit_message_text(
                "❌ Failed to create bot clone. Please try again later.",
                processing_msg.chat.id,
                processing_msg.message_id
            )
    except Exception as e:
        error_msg = f"**❌ Bot Clone Failed** \n\n"
        error_msg += f"Error: `{str(e)}` \n\n"
        error_msg += f"💡 Make sure your token is valid and try again"
        
        bot.edit_message_text(
            error_msg,
            processing_msg.chat.id,
            processing_msg.message_id,
            parse_mode="Markdown"
        )

# ===== CLEANUP FUNCTION =====
def cleanup():
    logger.warning("🧹 Shutdown. Cleaning up processes...")
    script_keys_to_stop = list(bot_scripts.keys())
    if not script_keys_to_stop: logger.info("✅ No scripts running. Exiting."); return
    logger.info(f"🧹 Stopping {len(script_keys_to_stop)} scripts...")
    for key in script_keys_to_stop:
        if key in bot_scripts: logger.info(f"🧹 Stopping: {key}"); kill_process_tree(bot_scripts[key])
        else: logger.info(f"ℹ️ Script {key} already removed.")
    logger.warning("✅ Cleanup finished.")
atexit.register(cleanup)

# ===== MAIN ENTRY POINT =====
if __name__ == '__main__':
    logger.info("="*40 + "\n🚀 Bᴏᴛ Sᴛᴀʀᴛɪɴɢ Uᴘ...\n" + f"🐍 Pʏᴛʜᴏɴ: {sys.version.split()[0]}\n" +
                f"📁 Bᴀsᴇ Dɪʀ: {BASE_DIR}\n📂 Uᴘʟᴏᴀᴅ Dɪʀ: {UPLOAD_BOTS_DIR}\n" +
                f"🗄️ Dᴀᴛᴀ Dɪʀ: {IROTECH_DIR}\n👑 Oᴡɴᴇʀ ID: {OWNER_ID}\n👥 Aᴅᴍɪɴ: {admin_ids}\n")
    logger.info("🔄 Sᴛᴀʀᴛɪɴɢ Wᴏʀᴋᴇʀ...")
    recovery_thread = threading.Thread(target=auto_recovery_worker, daemon=True)
    recovery_thread.start()
    if not IS_CLONE:
        keep_alive()
    if IS_CLONE:
        threading.Thread(target=clone_approval_worker, daemon=True).start()
    logger.info("🔄 Starting polling...")
    while True:
        try:
            bot.infinity_polling(logger_level=logging.INFO, timeout=60, long_polling_timeout=30)
        except Exception as e:
            logger.critical(f"❌ Unrecoverable polling error: {e}", exc_info=True)
            logger.info("🔄 Restarting polling in 30s due to critical error..."); time.sleep(30)
        finally: logger.warning("🔄 Polling attempt finished. Will restart if in loop."); time.sleep(1)