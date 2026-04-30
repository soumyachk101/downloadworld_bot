import os
import re
import glob
import asyncio
import shutil
import sys
from dotenv import load_dotenv

# Fix for Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

import yt_dlp
import instaloader
from groq import AsyncGroq
from deep_translator import GoogleTranslator
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import timedelta

# ─── Load Env ────────────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
INSTA_USERNAME = os.getenv("INSTA_USERNAME")   # Add these in Railway/env
INSTA_PASSWORD = os.getenv("INSTA_PASSWORD")
YOUTUBE_COOKIES_FILE = os.getenv("YOUTUBE_COOKIES_FILE")
if YOUTUBE_COOKIES_FILE and not os.path.exists(YOUTUBE_COOKIES_FILE):
    print(f"⚠️  YOUTUBE_COOKIES_FILE is set but file not found: {YOUTUBE_COOKIES_FILE}")
    print("   YouTube downloads may fail due to bot detection.")
    YOUTUBE_COOKIES_FILE = None

YOUTUBE_EXTRACTOR_ARGS = os.getenv("YOUTUBE_EXTRACTOR_ARGS", "")
INSTAGRAM_COOKIES_FILE = os.getenv("INSTAGRAM_COOKIES_FILE")
if INSTAGRAM_COOKIES_FILE and not os.path.exists(INSTAGRAM_COOKIES_FILE):
    print(f"⚠️  INSTAGRAM_COOKIES_FILE is set but file not found: {INSTAGRAM_COOKIES_FILE}")
    INSTAGRAM_COOKIES_FILE = None

def _read_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return max(1, int(value))
    except ValueError:
        print(f"⚠️  Invalid {name}='{value}', using {default}")
        return default

TELEGRAM_STREAMING_LIMIT_MB = _read_int_env("TELEGRAM_STREAMING_LIMIT_MB", 50)
TELEGRAM_MAX_UPLOAD_MB = _read_int_env("TELEGRAM_MAX_UPLOAD_MB", 500)
if TELEGRAM_STREAMING_LIMIT_MB > TELEGRAM_MAX_UPLOAD_MB:
    print("⚠️  TELEGRAM_STREAMING_LIMIT_MB exceeds TELEGRAM_MAX_UPLOAD_MB; using streaming limit as max.")
    TELEGRAM_MAX_UPLOAD_MB = TELEGRAM_STREAMING_LIMIT_MB

TELEGRAM_STREAMING_LIMIT_BYTES = TELEGRAM_STREAMING_LIMIT_MB * 1024 * 1024
TELEGRAM_MAX_UPLOAD_BYTES = TELEGRAM_MAX_UPLOAD_MB * 1024 * 1024

LARGE_AUDIO_DOCUMENT_MSG = "📦 *Audio bada hai — file ke roop mein bhej raha...*"
LARGE_VIDEO_DOCUMENT_MSG = "📦 *Video bada hai — file ke roop mein bhej raha...*"

# ─── Groq Client ─────────────────────────────────────────────────────────────
groq_client = None
if GROQ_API_KEY:
    groq_client = AsyncGroq(api_key=GROQ_API_KEY)
else:
    print("Warning: GROQ_API_KEY missing. AI features disabled.")

# ─── Instaloader Setup ───────────────────────────────────────────────────────
L = instaloader.Instaloader(
    download_pictures=True,
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    sleep=True,             # auto-sleep between requests — helps avoid rate limits
    quiet=True,
)

def setup_instaloader_session():
    """
    Login priority:
      1. Session file (platform-specific location)
      2. Instagram cookies file (INSTAGRAM_COOKIES_FILE)
      3. Username + Password from env
      4. Anonymous (public posts only, rate-limited heavily)
    """
    if not INSTA_USERNAME:
        print("Warning: INSTA_USERNAME missing — using anonymous session (rate-limits likely)")
        return

    # Get the correct session file path where instaloader actually saves it
    # On Windows: %LOCALAPPDATA%\Instaloader\session-USERNAME
    # On Unix/Linux/Mac: ~/.config/instaloader/session-USERNAME
    if sys.platform == "win32":
        base_dir = os.path.join(os.getenv('LOCALAPPDATA'), 'Instaloader')
    else:
        base_dir = os.path.expanduser('~/.config/instaloader')

    session_file = os.path.join(base_dir, f'session-{INSTA_USERNAME}')

    # 1. Try to load existing session file
    if os.path.exists(session_file):
        try:
            L.load_session_from_file(INSTA_USERNAME, session_file)
            print(f"✅ Instaloader: session loaded for @{INSTA_USERNAME}")
            return
        except Exception as e:
            print(f"⚠️  Session file load failed ({e}), trying next method...")

    # 2. Try cookies file if provided (Netscape format -> requests Session)
    if INSTAGRAM_COOKIES_FILE:
        try:
            import http.cookiejar
            import traceback
            import json
            from requests.cookies import RequestsCookieJar
            jar = RequestsCookieJar()

            with open(INSTAGRAM_COOKIES_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()

            if content.startswith('['):
                cookies_data = json.loads(content)
                for c in cookies_data:
                    name = c.get('name')
                    value = c.get('value')
                    if name and value:
                        domain = c.get('domain', '.instagram.com')
                        path = c.get('path', '/')
                        secure = c.get('secure', True)
                        jar.set(name, value, domain=domain, path=path, secure=secure)
            else:
                ncjar = http.cookiejar.MozillaCookieJar(INSTAGRAM_COOKIES_FILE)
                ncjar.load(ignore_discard=True, ignore_expires=True)
                for cookie in ncjar:
                    jar.set(cookie.name, cookie.value, domain=cookie.domain,
                            path=cookie.path, secure=cookie.secure)

            has_session = "sessionid" in set(jar.keys())
            if has_session:
                L.context._session.cookies = jar
                print(f"✅ Instaloader: logged in via cookies ({len(jar)} cookies loaded)")
                return
            else:
                print("⚠️  Instagram cookies file has no sessionid — not logged in")
        except Exception as e:
            print(f"⚠️  Failed to load Instagram cookies: {e}")
            traceback.print_exc()

    # 3. Try password login
    if INSTA_PASSWORD:
        try:
            L.login(INSTA_USERNAME, INSTA_PASSWORD)
            os.makedirs(os.path.dirname(session_file), exist_ok=True)
            L.save_session_to_file(session_file)
            print(f"✅ Instaloader: logged in as @{INSTA_USERNAME}, session saved.")
            return
        except Exception as e:
            error_msg = str(e).lower()
            if "checkpoint" in error_msg or "challenge" in error_msg:
                print(f"❌ Instagram checkpoint required!")
                print(f"   Your account needs additional verification.")
                print(f"   Steps to fix:")
                print(f"   1. Log into https://instagram.com in your browser")
                print(f"   2. Complete any security challenges")
                print(f"   3. Or use cookies file instead of password:")
                print(f"      Export Instagram cookies and set INSTAGRAM_COOKIES_FILE")
            else:
                print(f"❌ Instaloader login failed: {e}")

    # 4. Fallback to anonymous
    print("⚠️  Falling back to anonymous session (rate-limited).")

# ─── Stats Persistence ───────────────────────────────────────────────────────
import json
STATS_FILE = "bot_stats.json"

def load_stats():
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return {"total_downloads": 0, "users": {}}

def save_stats(stats):
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

def track_download(user_id):
    stats = load_stats()
    stats["total_downloads"] += 1
    uid = str(user_id)
    if uid not in stats["users"]:
        stats["users"][uid] = 0
    stats["users"][uid] += 1
    save_stats(stats)

# ─── Scheduler ───────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler()

# ─────────────────────────────────────────────────────────────────────────────
# HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    first_name = user.first_name if user and user.first_name else "Bhai"
    # Escape markdown special characters in first_name
    clean_name = re.sub(r'[*_`]', '', first_name)
    welcome_text = (
        f"╭━━━━━━━━━━━━━━━━━━━╮\n"
        f"  ⚡ *EVERYTHING DOWNLOADER* ⚡\n"
        f"╰━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"👋 *Hey {clean_name}!*\n"
        f"_Your one-stop media downloader_ 🎬\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌟 *WHAT I CAN DO*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎬  HD Video downloads\n"
        f"🎵  MP3 / Audio extraction\n"
        f"🔍  YouTube search engine\n"
        f"🤖  AI fun modes (Roast • Rap • Shayari)\n"
        f"🌐  Instant translation\n"
        f"⏰  Smart reminders\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ *QUICK START*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Paste any link → pick format\n"
        f"📌 `/search <query>` → find YT videos\n"
        f"📌 Tap buttons below to explore\n\n"
        f"_Supports: YouTube • Instagram • Facebook • Twitter • TikTok_"
    )
    keyboard = [
        [
            InlineKeyboardButton("🎬 Download", callback_data="show_help"),
            InlineKeyboardButton("🤖 AI Modes", callback_data="show_ai_modes"),
        ],
        [
            InlineKeyboardButton("📊 My Stats", callback_data="show_stats"),
            InlineKeyboardButton("📖 Help", callback_data="show_help"),
        ],
        [
            InlineKeyboardButton("⭐ Rate Bot", url="https://t.me/share/url?url=Check%20out%20Everything%20Downloader%20Bot!"),
        ],
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    else:
        await update.effective_message.reply_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "╭━━━━━━━━━━━━━━━━━━━╮\n"
        "  📖 *COMMAND CENTER* 📖\n"
        "╰━━━━━━━━━━━━━━━━━━━╯\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🎬 *DOWNLOADS*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "▸ `/mp4 <link>` — HD video\n"
        "▸ `/mp3 <link>` — High quality audio\n"
        "▸ `/thumb <link>` — Hi-res thumbnail 🖼️\n"
        "▸ `/subs <link> [lang]` — Subtitles (SRT) 📝\n"
        "▸ `/gif <link>` — Animated GIF (8 sec) 🎞️\n"
        "▸ `/search <query>` — Search YouTube\n"
        "▸ Or just paste any link 🪄\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 *AI FUN MODES*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🔥 Roast  •  ✍️ Shayari  •  🎤 Rap\n"
        "🔮 Fortune  •  📝 Story  •  🍕 Recipe\n"
        "💡 _Use /start to open AI Modes_\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🌐 *UTILITIES*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "▸ `/tr <text>` — Translate to Hindi\n"
        "▸ `/remind 10m <task>` — Set reminder\n"
        "▸ `/stats` — Your download stats\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 *PRO TIP*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 _Reply to any message with /tr to translate it!_"
    )
    keyboard = [
        [
            InlineKeyboardButton("🤖 AI Modes", callback_data="show_ai_modes"),
            InlineKeyboardButton("📊 Stats", callback_data="show_stats"),
        ],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="show_start")],
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(
            help_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    else:
        await update.effective_message.reply_text(
            help_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = load_stats()
    total = stats["total_downloads"]
    users_count = len(stats["users"])
    personal = stats["users"].get(str(update.effective_user.id), 0)
    
    msg = (
        "📊 *Bot Statistics*\n\n"
        f"🌍 *Global Downloads:* `{total}`\n"
        f"👥 *Total Users:* `{users_count}`\n\n"
        f"👤 *Your Downloads:* `{personal}`"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="show_start")]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(
            msg,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    else:
        await update.effective_message.reply_text(
            msg,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

def _is_expired_callback_query_error(error: object) -> bool:
    if not isinstance(error, BadRequest):
        return False
    message = getattr(error, "message", str(error)).lower()
    return (
        "query is too old" in message
        or "response timeout expired" in message
        or "query id is invalid" in message
    )

async def _safe_answer_callback(update: Update) -> bool:
    query = update.callback_query
    if not query:
        print("⚠️ Callback query missing in callback handler; skipping response.")
        return False
    try:
        await query.answer()
        return True
    except BadRequest as e:
        if _is_expired_callback_query_error(e):
            if update.effective_message:
                await update.effective_message.reply_text(
                    "⚠️ Button expired ho gaya. Link dobara bhejo fir se try karo. 🙏"
                )
            return False
        raise

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _safe_answer_callback(update):
        return
    query = update.callback_query

    mode_map = {
        "mode_roast":   ("roast",   "Naam bata jisko roast karna hai! 🔥"),
        "mode_shayari": ("shayari", "Kis topic pe shayari likhun? 📝"),
        "mode_rap":     ("rap",     "Rap ka topic bata, aag laga denge! 🔥🎤"),
        "mode_fortune": ("fortune", "Naam bata, tera bhavishya dekhta hoon! 🔮"),
        "mode_story":   ("story",   "Kis topic pe story likhun? 📝"),
        "mode_recipe":  ("recipe",  "Kaunsi recipe seekhni hai? Ingredients batao! 🍕"),
    }

    if query.data == "show_help":
        await help_command(update, context)
        return
    elif query.data == "show_start":
        await start(update, context)
        return
    elif query.data == "show_stats":
        await stats_command(update, context)
        return
    elif query.data == "show_ai_modes":
        ai_keyboard = [
            [InlineKeyboardButton("🔥 Roast",    callback_data="mode_roast"),
             InlineKeyboardButton("✍️ Shayari",  callback_data="mode_shayari")],
            [InlineKeyboardButton("🎤 Rap",      callback_data="mode_rap"),
             InlineKeyboardButton("🔮 Fortune", callback_data="mode_fortune")],
            [InlineKeyboardButton("📝 Story",    callback_data="mode_story"),
             InlineKeyboardButton("🍕 Recipe",   callback_data="mode_recipe")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="show_start")],
        ]
        ai_text = (
            "╭━━━━━━━━━━━━━━━━━━━╮\n"
            "  🤖 *AI FUN ZONE* 🤖\n"
            "╰━━━━━━━━━━━━━━━━━━━╯\n\n"
            "_Pick your flavor of madness_ 👇\n\n"
            "🔥 *Roast*  →  Savage burn for any name\n"
            "✍️ *Shayari*  →  Ghalib-style poetry\n"
            "🎤 *Rap*  →  Desi underground bars\n"
            "🔮 *Fortune*  →  Funny astrology\n"
            "📝 *Story*  →  Quick desi tale\n"
            "🍕 *Recipe*  →  Bhai-style cooking\n"
        )
        await query.edit_message_text(
            ai_text,
            reply_markup=InlineKeyboardMarkup(ai_keyboard),
            parse_mode="Markdown",
        )
        return

    if query.data in mode_map:
        mode, prompt_text = mode_map[query.data]
        context.user_data["mode"] = mode
        await query.edit_message_text(prompt_text)

# ─── AI ──────────────────────────────────────────────────────────────────────

async def ask_ai(prompt: str, system_prompt: str) -> str:
    if not groq_client:
        return "Bhai GROQ_API_KEY missing hai! Railway Dashboard mein add kar do. 🙏"
    try:
        chat_completion = await groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": prompt}
            ],
            model="llama-3.1-8b-instant",
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        print(f"Groq API Error: {e}")
        return "Bhai Groq AI mein thodi dikkat aa rahi hai. Baad mein try karna! 🙏"

async def handle_ai_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, user_text: str):
    prompts = {
        "roast":   {
            "system": "You are a savage, funny Indian roaster. Roast the person named in the prompt in exactly 4 lines using Hinglish. Be hilarious but don't cross community guidelines.",
            "user":   f"Roast this person: {user_text}"
        },
        "shayari": {
            "system": "You are a master Mirza Ghalib style poet but you write in Hinglish. Write a 4 line beautiful or funny shayari about the topic given.",
            "user":   f"Topic: {user_text}"
        },
        "rap":     {
            "system": "You are an Indian underground rapper like Divine or Emiway. Write an energetic desi Hindi rap with rhymes in exactly 8 lines using Hinglish about the given topic.",
            "user":   f"Topic: {user_text}"
        },
        "fortune": {
            "system": "You are a funny Indian jyotishi (astrologer). Tell a humorous 3-4 line fortune in Hinglish for the given name. Make it absurd and funny.",
            "user":   f"Name: {user_text}"
        },
        "story":   {
            "system": "You are a creative storyteller. Write a short, engaging 10-line story in Hinglish about the given topic. Make it interesting and desi.",
            "user":   f"Topic: {user_text}"
        },
        "recipe":  {
            "system": "You are a Desi Chef. Provide a simple and tasty recipe in Hinglish with clear steps based on the ingredients or dish name provided. Use a friendly, 'Bhai' style tone.",
            "user":   f"Recipe/Ingredients: {user_text}"
        },
    }

    if mode in prompts:
        msg = await update.message.reply_text("Typing... 🤖")
        response = await ask_ai(prompts[mode]["user"], prompts[mode]["system"])
        await msg.edit_text(response)
        context.user_data.pop("mode", None)

# ─── Downloads ───────────────────────────────────────────────────────────────

_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".m4v", ".mov", ".flv"}
_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".webm", ".opus", ".aac", ".wav", ".ogg", ".flac"}

def _find_largest_media_file(directory: str, extensions: set[str]) -> str | None:
    """Return the largest file in a directory that matches the given extensions."""
    if not directory or not os.path.isdir(directory):
        return None

    largest_path = None
    largest_size = -1
    with os.scandir(directory) as entries:
        for entry in entries:
            if not entry.is_file():
                continue
            if os.path.splitext(entry.name)[1].lower() not in extensions:
                continue
            size = entry.stat().st_size
            if size > largest_size:
                largest_size = size
                largest_path = entry.path
    return largest_path

def _find_largest_video_file(directory: str) -> str | None:
    """Return the largest video file in a directory by file size."""
    return _find_largest_media_file(directory, _VIDEO_EXTENSIONS)

def _find_largest_audio_file(directory: str) -> str | None:
    """Return the largest audio file in a directory by file size."""
    return _find_largest_media_file(directory, _AUDIO_EXTENSIONS)

def _resolve_downloaded_path(info: dict, output_path: str, audio_only: bool) -> str | None:
    """Resolve the downloaded file path using yt-dlp metadata and directory fallbacks.

    Expected info keys: filepath and _filename (strings, optional), requested_downloads
    (list of dicts with filepath/filename), and id (string) from yt-dlp extract_info.
    """
    candidates = []
    seen = set()

    if output_path and not os.path.isdir(output_path):
        output_path = None

    def add_candidate(path: str | None):
        if path and path not in seen:
            seen.add(path)
            candidates.append(path)

    if isinstance(info, dict):
        add_candidate(info.get("filepath"))
        add_candidate(info.get("_filename"))
        for req in info.get("requested_downloads") or []:
            add_candidate(req.get("filepath"))
            add_candidate(req.get("filename"))
        info_id = info.get("id")
        if info_id and output_path:
            extensions = _AUDIO_EXTENSIONS if audio_only else _VIDEO_EXTENSIONS
            with os.scandir(output_path) as entries:
                for entry in entries:
                    if not entry.is_file():
                        continue
                    if not entry.name.startswith(f"{info_id}."):
                        continue
                    if os.path.splitext(entry.name)[1].lower() in extensions:
                        add_candidate(entry.path)

    for path in candidates:
        if path and os.path.exists(path):
            return path
    fallback = _find_largest_audio_file(output_path) if audio_only else _find_largest_video_file(output_path)
    if not fallback:
        print("⚠️ Could not resolve downloaded file path from yt-dlp metadata.")
    return fallback

def _ensure_netscape_cookies(path: str | None, default_domain: str = ".instagram.com") -> str | None:
    """yt-dlp expects Netscape cookie format. If the file is JSON (e.g. exported
    via Instagram cookie editor extensions), convert it to a sibling .netscape
    file and return that path. Returns the original path for Netscape files.
    Returns None if path is None or unreadable. default_domain is used when a
    cookie entry omits the domain field."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
    except Exception:
        return path

    if not content.startswith('['):
        return path  # already Netscape

    try:
        cookies_data = json.loads(content)
    except Exception:
        return path

    normalized_default_domain = default_domain
    if normalized_default_domain and not normalized_default_domain.startswith('.'):
        normalized_default_domain = f".{normalized_default_domain}"

    def normalize_domain(domain_value: str | None) -> str:
        if not domain_value:
            domain_value = normalized_default_domain or ""
        if not domain_value:
            return ""
        if not domain_value.startswith('.') and not domain_value.startswith('www'):
            domain_value = f".{domain_value}"
        return domain_value

    netscape_path = path + ".netscape"
    lines = ["# Netscape HTTP Cookie File", "# Auto-generated from JSON cookies", ""]
    for c in cookies_data:
        name = c.get('name')
        value = c.get('value')
        if not name or value is None:
            continue
        domain = normalize_domain(c.get('domain'))
        include_subdomains = "TRUE" if domain.startswith('.') else "FALSE"
        cookie_path = c.get('path', '/')
        secure = "TRUE" if c.get('secure', True) else "FALSE"
        expires = int(c.get('expirationDate', 0)) or 0
        lines.append("\t".join([domain, include_subdomains, cookie_path, secure, str(expires), name, value]))

    try:
        with open(netscape_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines) + "\n")
        return netscape_path
    except Exception as e:
        print(f"⚠️ Failed to write Netscape cookies: {e}")
        return path


def _parse_extractor_args(args_str: str) -> dict:
    """Parse YOUTUBE_EXTRACTOR_ARGS into yt-dlp format.

    Format: "youtubetab:skip=webpage" "youtube:player_skip=webpage,configs;visitor_data=VALUE"
    Returns: {'youtubetab': {'skip': 'webpage'}, 'youtube': {'player_skip': 'webpage,configs', 'visitor_data': 'VALUE'}}
    """
    if not args_str:
        return {}

    result = {}
    # Split by spaces, but respect potential quoted sections
    parts = args_str.strip().split()
    for part in parts:
        if ':' not in part:
            continue
        extractor, args_part = part.split(':', 1)
        args_dict = {}
        # Split multiple args by semicolon
        for arg in args_part.split(';'):
            if '=' in arg:
                key, value = arg.split('=', 1)
                args_dict[key] = value
        if args_dict:
            result[extractor] = args_dict
    return result


def download_video(url: str, output_path: str, audio_only: bool = False, cookies_file: str = None, progress_hook=None) -> str:
    """Blocking yt-dlp download — run via asyncio.to_thread."""
    
    def get_ffmpeg_path():
        path = shutil.which('ffmpeg')
        if path: return path
        for p in ['/opt/homebrew/bin/ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']:
            if os.path.exists(p): return p
        return None

    # Base options
    base_opts = {
        'outtmpl': f'{output_path}/%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [progress_hook] if progress_hook else [],
        'socket_timeout': 420, # Prevent "uncomplete" downloads
        'retries': 10,
    }
    
    ffmpeg_path = get_ffmpeg_path()
    if ffmpeg_path:
        base_opts['ffmpeg_location'] = ffmpeg_path
    
    if cookies_file:
        base_opts['cookiefile'] = cookies_file

    ffmpeg_available = bool(ffmpeg_path)

    def add_audio_postprocessor(opts):
        # Only add FFmpegExtractAudio when ffmpeg present — otherwise yt-dlp
        # downloads succeed but postprocess fails with
        # "Postprocessing: ffprobe and ffmpeg not found".
        if audio_only and ffmpeg_available:
            opts.setdefault('postprocessors', [])
            opts['postprocessors'].append({
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            })
        return opts

    if audio_only:
        # Without ffmpeg, prefer m4a/mp3 directly so yt-dlp returns a Telegram-
        # playable file without needing postprocess.
        hq_format = (
            'bestaudio/best'
            if ffmpeg_available
            else 'bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio/best'
        )
    else:
        hq_format = (
            'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
            if ffmpeg_available
            else 'best[ext=mp4]/best'
        )

    def make_opts(fmt, client=None, strip_cookies=False, extra_args=None):
        opts = base_opts.copy()
        opts['format'] = fmt
        if not audio_only and ffmpeg_available:
            opts['merge_output_format'] = 'mp4'
        if strip_cookies:
            opts.pop('cookiefile', None)
        args = {}
        if client:
            args['youtube'] = {'player_client': [client]}
        if extra_args:
            for k, v in extra_args.items():
                args.setdefault(k, {}).update(v)
        if args:
            opts['extractor_args'] = args
        return add_audio_postprocessor(opts)

    # Permissive format string for non-YT sites (Pinterest, Twitter etc.) where
    # ext-specific filters fail. Covers single-stream + split-stream cases.
    permissive_fmt = (
        'bestaudio/best' if audio_only
        else 'bestvideo+bestaudio/best/b/bv*+ba/bv/ba/worst'
    )

    tiers = [
        # 1. HQ + cookies + user extractor args
        (lambda: {**make_opts(hq_format),
                  **(({'extractor_args': _parse_extractor_args(YOUTUBE_EXTRACTOR_ARGS)}
                      if YOUTUBE_EXTRACTOR_ARGS else {}))},
         "HQ+cookies+extractor_args"),
        # 2. Android client + cookies — bypasses YT bot detection
        (lambda: make_opts(hq_format, client='android'),
         "HQ+android_client"),
        # 3. iOS client + cookies — second YT bypass
        (lambda: make_opts('bestaudio/best' if audio_only else 'best', client='ios'),
         "best+ios_client"),
        # 4. TV embedded client + no cookies — very permissive
        (lambda: make_opts('best', client='tv_embedded', strip_cookies=True),
         "best+tv_embedded+no_cookies"),
        # 5. Permissive format, no client — works for non-YT sites (Pinterest,
        # Twitter, Reddit) where ext filters fail.
        (lambda: make_opts(permissive_fmt),
         "permissive_format"),
        # 6. Last resort: android, no cookies, any format
        (lambda: make_opts('b', client='android', strip_cookies=True),
         "fallback+android+no_cookies"),
    ]

    last_err = None
    for opts_fn, label in tiers:
        try:
            opts = opts_fn()
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                resolved_path = _resolve_downloaded_path(info, output_path, audio_only)
                return resolved_path or ydl.prepare_filename(info)
        except Exception as e:
            print(f"⚠️ Tier [{label}] failed: {e}")
            last_err = e

    raise RuntimeError(f"All download tiers failed. Last error: {last_err}")


def download_instagram(url: str, output_path: str):
    """
    Blocking instaloader download with retry on rate-limit (401).
    Raises on final failure so the caller can surface a proper error message.
    """
    match = re.search(r'/(?:p|reel|tv)/([A-Za-z0-9_-]+)', url)
    if not match:
        raise ValueError("Invalid Instagram URL — shortcode not found")

    shortcode = match.group(1)
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            L.download_post(post, target=output_path)
            return  # success
        except instaloader.exceptions.LoginRequiredException:
            raise RuntimeError(
                "Yeh post private hai ya login chahiye! "
                "INSTA_USERNAME / INSTA_PASSWORD env mein add kar."
            )
        except Exception as e:
            err_str = str(e).lower()
            is_rate_limit = (
                "401" in err_str
                or "please wait" in err_str
                or "429" in err_str
                or "checkpoint" in err_str
            )
            if is_rate_limit and attempt < max_retries:
                wait = 60 * attempt          # 60s → 120s → 180s
                print(f"Instagram rate-limit (attempt {attempt}/{max_retries}), waiting {wait}s…")
                import time; time.sleep(wait)
            else:
                raise RuntimeError(f"Instagram download failed: {e}")


def cleanup(path: str):
    try:
        if os.path.exists(path):
            shutil.rmtree(path)
    except Exception as e:
        print(f"Cleanup Error: {e}")

def _is_request_entity_too_large(err: Exception) -> bool:
    return isinstance(err, BadRequest) and "Request Entity Too Large" in str(err)

async def _reply_document_with_timeouts(source_msg, file_path: str, caption: str):
    with open(file_path, 'rb') as doc:
        await source_msg.reply_document(
            InputFile(doc, filename=os.path.basename(file_path)),
            caption=caption,
            write_timeout=600,
            read_timeout=600,
            connect_timeout=600,
            pool_timeout=600,
        )

def _compress_video(input_path: str, output_path: str):
    """Compress video using ffmpeg to reduce file size while maintaining decent quality."""
    ffmpeg_bin = shutil.which('ffmpeg')
    if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
        for p in ['/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg', '/opt/homebrew/bin/ffmpeg']:
            if os.path.exists(p):
                ffmpeg_bin = p
                break
    
    if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
        print("❌ Compression aborted: FFmpeg not found.")
        return False

    if not os.path.exists(input_path):
        print(f"❌ Compression aborted: Input file not found: {input_path}")
        return False

    # Use libx264 with CRF 28 and ultrafast preset to save memory/time
    # Limit threads to 1 to prevent memory spikes in small containers
    cmd = [
        ffmpeg_bin, '-y', '-i', input_path,
        '-vcodec', 'libx264', '-crf', '28', '-preset', 'ultrafast',
        '-threads', '1', 
        '-vf', "scale='if(gt(iw,ih),min(1280,iw),-2)':'if(gt(iw,ih),-2,min(720,ih))'",
        '-acodec', 'aac', '-b:a', '128k',
        output_path
    ]
    
    import subprocess
    try:
        print(f"🎬 Starting compression: {input_path}")
        # Add a timeout of 300 seconds (5 minutes) to prevent hanging
        process = subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)
        return os.path.exists(output_path)
    except subprocess.TimeoutExpired:
        print(f"⚠️ Compression timed out after 300s: {input_path}")
        return False
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode(errors="ignore").strip() if e.stderr else "Unknown error"
        print(f"❌ FFmpeg compression failed: {err_msg}")
        return False
    except Exception as e:
        print(f"❌ Unexpected compression error: {e}")
        return False

# ─── Commands ─────────────────────────────────────────────────────────────────

# ─── Progress Hook Helper ───────────────────────────────────────────────────

def get_progress_bar(percentage):
    """Create a professional progress bar string."""
    filled_length = int(10 * percentage // 100)
    bar = "█" * filled_length + "░" * (10 - filled_length)
    return f"|{bar}| {percentage}%"

def progress_hook_factory(loop, bot, chat_id, message_id):
    """Creates a hook to update the progress in Telegram safely across threads."""
    last_update_time = 0

    def hook(d):
        nonlocal last_update_time
        if d['status'] == 'downloading':
            # Update only every 3 seconds to be safe
            import time
            current_time = time.time()
            if current_time - last_update_time > 3:
                last_update_time = current_time
                p = d.get('_percent_str', '0%').replace('%', '').strip()
                try:
                    percent = float(p)
                except:
                    percent = 0
                
                bar = get_progress_bar(percent)
                speed = d.get('_speed_str', 'N/A')
                eta = d.get('_eta_str', 'N/A')
                text = f"🚀 *Downloading...*\n\n{bar}\n\n⚡ Speed: `{speed}`\n⏳ ETA: `{eta}`"
                
                # Safely schedule the update in the main event loop
                asyncio.run_coroutine_threadsafe(
                    bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode="Markdown"),
                    loop
                )
        elif d['status'] == 'finished':
            asyncio.run_coroutine_threadsafe(
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="✅ Download Finished! Processing... 🛠️"),
                loop
            )

    return hook

# ─── Commands ─────────────────────────────────────────────────────────────────

async def mp3_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not context.args:
        await source_msg.reply_text(
            "❌ *Bhai link toh bhej!*\n\nExample: `/mp3 https://youtube.com/watch?v=xxx`",
            parse_mode="Markdown"
        )
        return

    url = context.args[0]
    status_msg = await source_msg.reply_text("⏳ *Initializing MP3 request...*", parse_mode="Markdown")
    download_dir = f"downloads_mp3_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        loop = asyncio.get_running_loop()
        hook = progress_hook_factory(loop, context.bot, update.effective_chat.id, status_msg.message_id)
        
        file_path = None
        is_instagram = "instagram.com" in url

        def _resolve_ffmpeg() -> str | None:
            path = shutil.which('ffmpeg')
            if path:
                return path
            for p in ['/opt/homebrew/bin/ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']:
                if os.path.exists(p):
                    return p
            return None

        async def _instaloader_to_mp3():
            await asyncio.to_thread(download_instagram, url, download_dir)
            mp4_files = glob.glob(f"{download_dir}/*.mp4")
            if not mp4_files:
                raise RuntimeError("Instaloader failed to find downloaded video for MP3 extraction.")
            video_path = mp4_files[0]
            mp3_path = os.path.splitext(video_path)[0] + ".mp3"
            ffmpeg_bin = _resolve_ffmpeg()
            if ffmpeg_bin:
                import subprocess
                cmd = [ffmpeg_bin, '-y', '-i', video_path, '-vn', '-acodec', 'libmp3lame', '-ab', '192k', mp3_path]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return mp3_path
            print("⚠️ FFmpeg missing — sending Instagram video as audio (no mp3 extraction).")
            return video_path

        if is_instagram:
            # YouTube player_client extractor args produce broken metadata for IG.
            # Route directly to Instaloader; fall back to yt-dlp with IG cookies on failure.
            try:
                file_path = await _instaloader_to_mp3()
            except Exception as e:
                print(f"Instaloader failed for Instagram MP3, trying yt-dlp. Error: {e}")
                ig_cookies = _ensure_netscape_cookies(INSTAGRAM_COOKIES_FILE, default_domain=".instagram.com")
                file_path = await asyncio.to_thread(download_video, url, download_dir, True, ig_cookies, hook)
        else:
            yt_cookies = _ensure_netscape_cookies(YOUTUBE_COOKIES_FILE, default_domain=".youtube.com")
            file_path = await asyncio.to_thread(download_video, url, download_dir, True, yt_cookies, hook)

        # yt-dlp converts to .mp3 after postprocessing — glob for it
        mp3_files = glob.glob(f"{download_dir}/*.mp3")
        ffmpeg_path_check = shutil.which('ffmpeg')
        if not ffmpeg_path_check:
            for p in ['/opt/homebrew/bin/ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']:
                if os.path.exists(p):
                    ffmpeg_path_check = p
                    break
        ffmpeg_available = bool(ffmpeg_path_check)

        if not mp3_files:
            audio_candidates = []
            if file_path and os.path.exists(file_path):
                audio_candidates.append(file_path)
            for candidate in glob.glob(f"{download_dir}/*"):
                if os.path.splitext(candidate)[1].lower() in {".m4a", ".webm", ".opus", ".aac", ".mp4", ".mkv", ".wav", ".ogg"}:
                    audio_candidates.append(candidate)

            unique_candidates = []
            seen = set()
            for candidate in audio_candidates:
                if candidate not in seen:
                    seen.add(candidate)
                    unique_candidates.append(candidate)
            audio_candidates = unique_candidates

            if ffmpeg_available and audio_candidates:
                source_audio = audio_candidates[0]
                mp3_path = os.path.splitext(source_audio)[0] + ".mp3"
                if not os.path.exists(mp3_path):
                    import subprocess
                    cmd = [ffmpeg_path_check, '-y', '-i', source_audio, '-vn', '-acodec', 'libmp3lame', '-ab', '192k', mp3_path]
                    try:
                        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    except subprocess.CalledProcessError as exc:
                        err = exc.stderr.decode(errors="ignore").strip() if exc.stderr else "Unknown error"
                        raise RuntimeError(f"FFmpeg conversion failed: {err}") from exc
                if os.path.exists(mp3_path):
                    mp3_files = [mp3_path]
            elif not ffmpeg_available and audio_candidates:
                # No ffmpeg — pick best raw audio (m4a > opus > others) and ship as-is.
                # Telegram plays m4a/ogg/opus natively in the audio player.
                priority = {".m4a": 0, ".ogg": 1, ".opus": 2, ".aac": 3, ".webm": 4, ".wav": 5, ".mp4": 6, ".mkv": 7}
                audio_candidates.sort(key=lambda p: priority.get(os.path.splitext(p)[1].lower(), 99))
                mp3_files = [audio_candidates[0]]

            if not mp3_files:
                await status_msg.edit_text("❌ *Bhai MP3 nahi bani. Link check kar!* 😔", parse_mode="Markdown")
                return

        file_path = mp3_files[0]
        file_size = os.path.getsize(file_path)
        if file_size <= TELEGRAM_MAX_UPLOAD_BYTES:
            send_as_document = file_size > TELEGRAM_STREAMING_LIMIT_BYTES
            try:
                if send_as_document:
                    await status_msg.edit_text(LARGE_AUDIO_DOCUMENT_MSG, parse_mode="Markdown")
                    await _reply_document_with_timeouts(source_msg, file_path, "🎵 Audio file (large)")
                else:
                    await status_msg.edit_text("📤 *Uploading Audio...*", parse_mode="Markdown")
                    with open(file_path, 'rb') as audio:
                        await source_msg.reply_audio(
                            audio,
                            caption="Enjoy your music! 🎵",
                            write_timeout=600,
                            read_timeout=600,
                            connect_timeout=600,
                            pool_timeout=600,
                        )
                track_download(user.id)
                await status_msg.delete()
            except Exception as upload_err:
                if not send_as_document and _is_request_entity_too_large(upload_err):
                    try:
                        await status_msg.edit_text(LARGE_AUDIO_DOCUMENT_MSG, parse_mode="Markdown")
                        await _reply_document_with_timeouts(source_msg, file_path, "🎵 Audio file (large)")
                        track_download(user.id)
                        await status_msg.delete()
                        return
                    except Exception as fallback_err:
                        print(f"❌ Upload failed: {upload_err}")
                        print(f"❌ Fallback upload failed: {fallback_err}")
                        await status_msg.edit_text(f"❌ *Bhai upload fail ho gaya:* `{fallback_err}`", parse_mode="Markdown")
                        return
                print(f"❌ Upload failed: {upload_err}")
                await status_msg.edit_text(f"❌ *Bhai upload fail ho gaya:* `{upload_err}`", parse_mode="Markdown")
        else:
            await status_msg.edit_text(
                f"❌ *Bhai audio {TELEGRAM_MAX_UPLOAD_MB}MB se badi hai!* 😔",
                parse_mode="Markdown",
            )

    except Exception as e:
        print(f"MP3 Error: {e}")
        await status_msg.edit_text("❌ *Bhai error aagaya MP3 banane mein.* 🙏", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def mp4_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not context.args:
        await source_msg.reply_text(
            "❌ *Bhai link toh bhej!*\n\nExample: `/mp4 https://youtube.com/watch?v=xxx`",
            parse_mode="Markdown"
        )
        return

    url = context.args[0]
    status_msg = await source_msg.reply_text("⏳ *Initializing Video request...*", parse_mode="Markdown")
    download_dir = f"downloads_mp4_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        loop = asyncio.get_running_loop()
        hook = progress_hook_factory(loop, context.bot, update.effective_chat.id, status_msg.message_id)
        
        file_path = None
        is_instagram = "instagram.com" in url
        cookies_for_url = (
            _ensure_netscape_cookies(INSTAGRAM_COOKIES_FILE, default_domain=".instagram.com")
            if is_instagram
            else _ensure_netscape_cookies(YOUTUBE_COOKIES_FILE, default_domain=".youtube.com")
        )

        if is_instagram:
            # Try Instaloader first — yt-dlp's IG extractor often hits 401 even with cookies.
            try:
                await asyncio.to_thread(download_instagram, url, download_dir)
                mp4_files = glob.glob(f"{download_dir}/*.mp4")
                if mp4_files:
                    file_path = mp4_files[0]
                else:
                    raise RuntimeError("Instaloader produced no MP4")
            except Exception as e:
                print(f"Instaloader failed for Instagram MP4, trying yt-dlp. Error: {e}")
                file_path = await asyncio.to_thread(download_video, url, download_dir, False, cookies_for_url, hook)
        else:
            file_path = await asyncio.to_thread(download_video, url, download_dir, False, cookies_for_url, hook)

        if not file_path or not os.path.exists(file_path):
            file_path = _find_largest_video_file(download_dir)

        if file_path and os.path.exists(file_path):
            # Compress video if requested or if it's large
            original_size = os.path.getsize(file_path)
            compressed_path = os.path.splitext(file_path)[0] + "_compressed.mp4"
            
            try:
                await status_msg.edit_text("⚙️ *Optimizing Video for Telegram...* 🛠️", parse_mode="Markdown")
                success = await asyncio.to_thread(_compress_video, file_path, compressed_path)
                
                if success and os.path.exists(compressed_path):
                    new_size = os.path.getsize(compressed_path)
                    if new_size < original_size:
                        file_path = compressed_path
                        print(f"✅ Compression: {original_size} -> {new_size}")
                    else:
                        print("ℹ️ Compressed file is larger; using original.")
                else:
                    print("⚠️ Compression failed or produced no file; using original.")
            except Exception as ce:
                print(f"⚠️ Compression step encountered an error: {ce}")
            
            file_size = os.path.getsize(file_path)
            if file_size <= TELEGRAM_MAX_UPLOAD_BYTES:
                send_as_document = file_size > TELEGRAM_STREAMING_LIMIT_BYTES
                try:
                    if send_as_document:
                        await status_msg.edit_text(LARGE_VIDEO_DOCUMENT_MSG, parse_mode="Markdown")
                        await _reply_document_with_timeouts(source_msg, file_path, "🎬 Video file (large)")
                    else:
                        await status_msg.edit_text("📤 *Uploading Video...* (This may take a while)", parse_mode="Markdown")
                        with open(file_path, 'rb') as video:
                            await source_msg.reply_video(
                                video, 
                                caption="Your video is ready! 🎬",
                                supports_streaming=True,
                                write_timeout=600,
                                read_timeout=600,
                                connect_timeout=600,
                                pool_timeout=600
                            )
                    track_download(user.id)
                    await status_msg.delete()
                except Exception as upload_err:
                    if not send_as_document and _is_request_entity_too_large(upload_err):
                        try:
                            await status_msg.edit_text(LARGE_VIDEO_DOCUMENT_MSG, parse_mode="Markdown")
                            await _reply_document_with_timeouts(source_msg, file_path, "🎬 Video file (large)")
                            track_download(user.id)
                            await status_msg.delete()
                            return
                        except Exception as fallback_err:
                            print(f"❌ Upload failed: {upload_err}")
                            print(f"❌ Fallback upload failed: {fallback_err}")
                            await status_msg.edit_text(f"❌ *Bhai upload fail ho gaya:* `{fallback_err}`", parse_mode="Markdown")
                            return
                    print(f"❌ Upload failed: {upload_err}")
                    await status_msg.edit_text(f"❌ *Bhai upload fail ho gaya:* `{upload_err}`", parse_mode="Markdown")
            else:
                await status_msg.edit_text(
                    f"❌ *Bhai video {TELEGRAM_MAX_UPLOAD_MB}MB se badi hai!* 😔",
                    parse_mode="Markdown",
                )
        else:
            await status_msg.edit_text("❌ *Bhai file nahi mili download ke baad.* 😔", parse_mode="Markdown")

    except Exception as e:
        print(f"MP4 Error: {e}")
        await status_msg.edit_text("❌ *Bhai error aagaya video download karne mein.* 🙏", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


# ─── /thumb — High-res thumbnail ─────────────────────────────────────────────

def _download_thumbnail(url: str, output_path: str) -> str:
    """Fetch metadata via yt-dlp, download largest thumbnail. Blocking."""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
    }
    if YOUTUBE_COOKIES_FILE:
        opts['cookiefile'] = YOUTUBE_COOKIES_FILE

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    thumbnails = info.get('thumbnails') or []
    if not thumbnails and info.get('thumbnail'):
        thumbnails = [{'url': info['thumbnail']}]
    if not thumbnails:
        raise RuntimeError("No thumbnail found in metadata.")

    # Pick highest resolution
    def _score(t):
        return (t.get('width') or 0) * (t.get('height') or 0) or t.get('preference', 0)
    thumb = max(thumbnails, key=_score)
    thumb_url = thumb.get('url')
    if not thumb_url:
        raise RuntimeError("Thumbnail entry had no URL.")

    import urllib.request
    ext = os.path.splitext(thumb_url.split('?')[0])[1].lower() or '.jpg'
    if ext not in {'.jpg', '.jpeg', '.png', '.webp'}:
        ext = '.jpg'
    safe_id = re.sub(r'[^A-Za-z0-9_-]', '_', str(info.get('id', 'thumb')))
    out_file = os.path.join(output_path, f"{safe_id}{ext}")

    req = urllib.request.Request(thumb_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=420) as resp, open(out_file, 'wb') as f:
        shutil.copyfileobj(resp, f)
    return out_file


async def thumb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not context.args:
        await source_msg.reply_text(
            "❌ *Bhai link toh bhej!*\n\nExample: `/thumb https://youtu.be/xxx`",
            parse_mode="Markdown",
        )
        return

    url = context.args[0]
    status_msg = await source_msg.reply_text("🖼️ *Fetching thumbnail...*", parse_mode="Markdown")
    download_dir = f"downloads_thumb_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        file_path = await asyncio.to_thread(_download_thumbnail, url, download_dir)
        if not file_path or not os.path.exists(file_path):
            raise RuntimeError("Thumbnail file missing after download.")

        await status_msg.edit_text("📤 *Uploading thumbnail...*", parse_mode="Markdown")
        with open(file_path, 'rb') as photo:
            await source_msg.reply_photo(photo, caption="🖼️ Hi-res thumbnail")
        track_download(user.id)
        await status_msg.delete()
    except Exception as e:
        print(f"Thumb Error: {e}")
        await status_msg.edit_text("❌ *Thumbnail nahi mili. Link check kar!* 😔", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


# ─── /subs — Subtitle / caption SRT download ─────────────────────────────────

def _download_subtitles(url: str, output_path: str, lang: str = 'en') -> str:
    """Download subtitles via yt-dlp. Prefers manual, falls back to auto-generated."""
    base = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'subtitleslangs': [lang, f'{lang}.*', 'en', 'en.*'],
        'subtitlesformat': 'srt/best',
        'outtmpl': f'{output_path}/%(id)s.%(ext)s',
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
    }
    if YOUTUBE_COOKIES_FILE:
        base['cookiefile'] = YOUTUBE_COOKIES_FILE

    # Pass 1: manual subs only
    opts = {**base, 'writesubtitles': True, 'writeautomaticsub': False}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    sub_files = glob.glob(f"{output_path}/*.srt") + glob.glob(f"{output_path}/*.vtt")
    if sub_files:
        return sub_files[0]

    # Pass 2: include auto-generated
    opts = {**base, 'writesubtitles': True, 'writeautomaticsub': True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    sub_files = glob.glob(f"{output_path}/*.srt") + glob.glob(f"{output_path}/*.vtt")
    if sub_files:
        return sub_files[0]
    raise RuntimeError("No subtitles available for this video.")


async def subs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not context.args:
        await source_msg.reply_text(
            "❌ *Bhai link toh bhej!*\n\n"
            "Example: `/subs https://youtu.be/xxx`\n"
            "Optional language: `/subs <link> hi`",
            parse_mode="Markdown",
        )
        return

    url = context.args[0]
    lang = context.args[1] if len(context.args) > 1 else 'en'
    status_msg = await source_msg.reply_text(f"📝 *Fetching subtitles ({lang})...*", parse_mode="Markdown")
    download_dir = f"downloads_subs_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        sub_path = await asyncio.to_thread(_download_subtitles, url, download_dir, lang)
        await status_msg.edit_text("📤 *Uploading subtitle file...*", parse_mode="Markdown")
        with open(sub_path, 'rb') as f:
            await source_msg.reply_document(f, caption=f"📝 Subtitles ({lang})")
        track_download(user.id)
        await status_msg.delete()
    except Exception as e:
        print(f"Subs Error: {e}")
        msg = "❌ *Is video pe subtitles nahi hain.* 😔" if "No subtitles" in str(e) else "❌ *Subtitle nahi mili.* 🙏"
        await status_msg.edit_text(msg, parse_mode="Markdown")
    finally:
        cleanup(download_dir)


# ─── /gif — Convert short clip to animated GIF ───────────────────────────────

def _video_to_gif(video_path: str, gif_path: str, max_seconds: int = 8, max_width: int = 480) -> None:
    """Convert video to optimized GIF (loop). Two-pass: palette + dither for size."""
    ffmpeg_bin = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
    palette = gif_path + ".palette.png"
    vf_palette = (
        f"fps=15,scale={max_width}:-1:flags=lanczos,palettegen=stats_mode=diff"
    )
    vf_use = (
        f"fps=15,scale={max_width}:-1:flags=lanczos[v];[v][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
    )
    import subprocess
    # Pass 1: palette
    subprocess.run(
        [ffmpeg_bin, '-y', '-t', str(max_seconds), '-i', video_path, '-vf', vf_palette, palette],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    # Pass 2: encode
    subprocess.run(
        [ffmpeg_bin, '-y', '-t', str(max_seconds), '-i', video_path, '-i', palette,
         '-lavfi', vf_use, '-loop', '0', gif_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        os.remove(palette)
    except OSError:
        pass


async def gif_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not context.args:
        await source_msg.reply_text(
            "❌ *Bhai link toh bhej!*\n\n"
            "Example: `/gif https://youtu.be/xxx`\n"
            "Default: first 8 sec → animated GIF",
            parse_mode="Markdown",
        )
        return

    if not shutil.which('ffmpeg'):
        await source_msg.reply_text("❌ *FFmpeg nahi mila — GIF nahi ban sakti.* 🙏", parse_mode="Markdown")
        return

    url = context.args[0]
    status_msg = await source_msg.reply_text("🎞️ *Downloading clip for GIF...*", parse_mode="Markdown")
    download_dir = f"downloads_gif_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        loop = asyncio.get_running_loop()
        hook = progress_hook_factory(loop, context.bot, update.effective_chat.id, status_msg.message_id)

        is_instagram = "instagram.com" in url
        cookies_for_url = (
            _ensure_netscape_cookies(INSTAGRAM_COOKIES_FILE) if is_instagram else YOUTUBE_COOKIES_FILE
        )
        video_path = await asyncio.to_thread(download_video, url, download_dir, False, cookies_for_url, hook)
        if not video_path or not os.path.exists(video_path):
            video_path = _find_largest_video_file(download_dir)
        if not video_path:
            raise RuntimeError("Video file missing after download.")

        await status_msg.edit_text("⚙️ *Converting to GIF (8 sec, 480p, 15fps)...*", parse_mode="Markdown")
        gif_path = os.path.splitext(video_path)[0] + ".gif"
        await asyncio.to_thread(_video_to_gif, video_path, gif_path, 8, 480)

        if not os.path.exists(gif_path):
            raise RuntimeError("GIF conversion produced no file.")
        if os.path.getsize(gif_path) > TELEGRAM_MAX_UPLOAD_BYTES:
            await status_msg.edit_text(
                f"❌ *GIF {TELEGRAM_MAX_UPLOAD_MB}MB se badi ban gayi. Shorter clip try kar.* 😔",
                parse_mode="Markdown",
            )
            return

        await status_msg.edit_text("📤 *Uploading GIF...*", parse_mode="Markdown")
        with open(gif_path, 'rb') as f:
            # send_animation gives Telegram's looping GIF player
            await context.bot.send_animation(
                chat_id=update.effective_chat.id,
                animation=f,
                reply_to_message_id=source_msg.message_id,
                caption="🎞️ Your GIF is ready!",
            )
        track_download(user.id)
        await status_msg.delete()
    except Exception as e:
        print(f"GIF Error: {e}")
        await status_msg.edit_text("❌ *Bhai GIF nahi bani. Link/length check kar.* 🙏", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🔍 *Kya search karna hai?*\n\nExample: `/search divine gully gang`", parse_mode="Markdown")
        return
    
    query = " ".join(context.args)
    status_msg = await update.message.reply_text(f"🔍 *Searching for:* `{query}`...", parse_mode="Markdown")
    
    try:
        # Search using yt-dlp — pass YouTube cookies + android client to bypass bot check.
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'best',
            'noplaylist': True,
            'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
        }
        if YOUTUBE_COOKIES_FILE:
            ydl_opts['cookiefile'] = YOUTUBE_COOKIES_FILE
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, f"ytsearch1:{query}", download=False)
            if not info or 'entries' not in info or not info['entries']:
                await status_msg.edit_text("❌ *Kuch nahi mila!* 😔")
                return
            
            entry = info['entries'][0]
            url = entry['webpage_url']
            title = entry.get('title', 'Video')
            duration = entry.get('duration_string', 'N/A')
            
            import uuid
            link_id = str(uuid.uuid4())[:8]
            context.user_data.setdefault("links", {})[link_id] = url

            keyboard = [
                [
                    InlineKeyboardButton("🎬 Video (MP4)", callback_data=f"dl_mp4:{link_id}"),
                    InlineKeyboardButton("🎵 Audio (MP3)", callback_data=f"dl_mp3:{link_id}")
                ]
            ]
            await status_msg.edit_text(
                f"✅ *Found:* `{title}`\n⏱ *Duration:* `{duration}`\n\nWhat would you like to download?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            
    except Exception as e:
        print(f"Search Error: {e}")
        await status_msg.edit_text("❌ *Search failed!* 🙏")


async def translate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_to_translate = ""
    if context.args:
        text_to_translate = " ".join(context.args)
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        text_to_translate = update.message.reply_to_message.text
    else:
        await update.message.reply_text(
            "Bhai kya translate karun? Text likho ya kisi message ko reply karo! 🙏"
        )
        return

    try:
        # GoogleTranslator auto-detects source language — no extra API key needed
        translated = GoogleTranslator(source='auto', target='hindi').translate(text_to_translate)
        await update.message.reply_text(f"🌐 Auto → Hindi:\n{translated}")
    except Exception as e:
        print(f"Translation Error: {e}")
        await update.message.reply_text("Bhai translation mein error aagaya! 🙏")


# ─── Reminder ─────────────────────────────────────────────────────────────────
# FIX: APScheduler jobs run outside PTB's context — pass `bot` directly, not `context`

async def _send_reminder_job(bot, chat_id: int, message: str):
    await bot.send_message(chat_id=chat_id, text=f"⏰ Yaad dilaya bhai: {message}")


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            "Bhai format galat hai! Example: /remind 10m Chai peeni hai"
        )
        return

    time_val = context.args[0]
    remind_text = " ".join(context.args[1:])

    seconds = 0
    try:
        if time_val.endswith('s'):
            seconds = int(time_val[:-1])
        elif time_val.endswith('m'):
            seconds = int(time_val[:-1]) * 60
        elif time_val.endswith('h'):
            seconds = int(time_val[:-1]) * 3600
        else:
            seconds = int(time_val)
    except ValueError:
        await update.message.reply_text("Bhai time sahi se bata! (30s, 10m, 2h) 🙏")
        return

    run_date = update.message.date + timedelta(seconds=seconds)
    scheduler.add_job(
        _send_reminder_job,
        'date',
        run_date=run_date,
        args=[context.bot, update.effective_chat.id, remind_text]  # bot, not context
    )
    await update.message.reply_text(f"Done bhai! {time_val} baad yaad dila dunga. 👍")


# ─── Main message handler ────────────────────────────────────────────────────

URL_PATTERN = re.compile(
    r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    print(f"📩 Received message from {update.effective_user.id}: {user_text[:20]}...")
    mode = context.user_data.get("mode")

    if mode:
        await handle_ai_mode(update, context, mode, user_text)
        return

    urls = re.findall(URL_PATTERN, user_text)
    if not urls:
        # If no URL and no mode, show a professional help prompt
        await update.message.reply_text(
            "👋 *Welcome to Everything Downloader!*\n\n"
            "To download something, please use the following commands:\n"
            "• `/mp4 <link>` - Download Video (MP4)\n"
            "• `/mp3 <link>` - Download Audio (MP3)\n\n"
            "Supported: YouTube, Instagram, FB, Twitter (X), etc.",
            parse_mode="Markdown"
        )
        return

    url = urls[0]
    import uuid
    link_id = str(uuid.uuid4())[:8]
    context.user_data.setdefault("links", {})[link_id] = url
    
    keyboard = [
        [
            InlineKeyboardButton("🎬 Video (MP4)", callback_data=f"dl_mp4:{link_id}"),
            InlineKeyboardButton("🎵 Audio (MP3)", callback_data=f"dl_mp3:{link_id}")
        ]
    ]
    await update.message.reply_text(
        "✨ *Link Detected!*\n\nWhat would you like to do with this link?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def dl_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _safe_answer_callback(update):
        return
    query = update.callback_query
    
    data = query.data
    url = None
    
    if ":" in data:
        action, link_id = data.split(":", 1)
        url = context.user_data.get("links", {}).get(link_id)
    else:
        url = context.user_data.get("current_url")
        action = data

    if not url:
        await query.edit_message_text("❌ *Error:* Link not found in memory. Please send the link again.", parse_mode="Markdown")
        return

    context.args = [url]
    
    if action == "dl_mp4":
        await mp4_command(update, context)
    elif action == "dl_mp3":
        await mp3_command(update, context)


# ─── Global Error Handler ─────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f"❌ Exception while handling an update: {context.error}")
    if _is_expired_callback_query_error(context.error):
        print(f"ℹ️ Ignoring expired callback query error for update: {update}")
        return
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            f"⚠️ *Bhai thoda error aagaya:* `{context.error}`",
            parse_mode="Markdown"
        )

# ─── App Bootstrap ────────────────────────────────────────────────────────────

async def post_init(application: Application):
    setup_instaloader_session()
    
    # Set bot commands in the menu
    commands = [
        ("start",     "Start the bot"),
        ("help",      "How to use the bot"),
        ("search",    "Search YouTube videos"),
        ("mp4",       "Download video via link"),
        ("mp3",       "Download audio via link"),
        ("thumb",     "Hi-res thumbnail download"),
        ("subs",      "Download subtitles (SRT)"),
        ("gif",       "Convert clip to animated GIF"),
        ("stats",     "View your download stats"),
        ("tr",        "Translate to Hindi"),
    ]
    await application.bot.set_my_commands(commands)
    
    # FFmpeg check
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        print(f"✅ FFmpeg found at: {ffmpeg_path}")
    else:
        print("❌ CRITICAL: FFmpeg NOT FOUND! High-quality downloads will fail.")
        print("   Fix: brew install ffmpeg")

    if not scheduler.running:
        scheduler.start()
    print("✅ Bot ready — scheduler started, instaloader configured.")


def main():
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing! Add it to environment variables.")
        return
    if not GROQ_API_KEY:
        print("⚠️  GROQ_API_KEY missing — AI features disabled, bot will still run.")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(420)
        .write_timeout(420)
        .connect_timeout(420)
        .pool_timeout(420)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("help",      help_command))
    app.add_handler(CommandHandler("stats",     stats_command))
    app.add_handler(CommandHandler("search",    search_command))
    app.add_handler(CommandHandler("mp3",       mp3_command))
    app.add_handler(CommandHandler("mp4",       mp4_command))
    app.add_handler(CommandHandler("thumb",     thumb_command))
    app.add_handler(CommandHandler("subs",      subs_command))
    app.add_handler(CommandHandler("gif",       gif_command))
    app.add_handler(CommandHandler("translate", translate_command))
    app.add_handler(CommandHandler("tr",        translate_command))
    app.add_handler(CommandHandler("remind",    remind_command))
    app.add_handler(CallbackQueryHandler(button_callback, pattern="^(mode_|show_)"))
    app.add_handler(CallbackQueryHandler(dl_callback,     pattern="^dl_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    print("🤖 Bot is starting up... waiting for messages.")
    app.run_polling()

if __name__ == "__main__":
    main()
