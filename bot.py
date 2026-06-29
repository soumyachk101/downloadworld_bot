import os
import re
import glob
import asyncio
import shutil
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

# Fix for Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji
from telegram.constants import ChatAction
# Fallback for deprecated UPLOAD_AUDIO in python-telegram-bot v20+
if not hasattr(ChatAction, "UPLOAD_AUDIO"):
    ChatAction.UPLOAD_AUDIO = ChatAction.UPLOAD_VOICE
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
import httpx
from deep_translator import GoogleTranslator
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import timedelta

# ─── Load Env ────────────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    print("Warning: GEMINI_API_KEY missing. AI features disabled.")

# ─── Stats Persistence ───────────────────────────────────────────────────────
import json
import tempfile
STATS_FILE = "bot_stats.json"
_stats_lock = threading.Lock()  # Use threading lock since stats are called from threads

def load_stats():
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return {"total_downloads": 0, "users": {}}

def save_stats(stats):
    """Atomically write stats to avoid corruption on concurrent writes."""
    tmp_path = STATS_FILE + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(stats, f)
        # Atomic rename (on most filesystems)
        if sys.platform == "win32":
            # Windows: remove existing file first if it exists
            if os.path.exists(STATS_FILE):
                os.remove(STATS_FILE)
        os.replace(tmp_path, STATS_FILE)
    except Exception as e:
        print(f"⚠️ Failed to save stats atomically: {e}")
        # Fallback to direct write
        try:
            with open(STATS_FILE, "w") as f:
                json.dump(stats, f)
        except Exception as e2:
            print(f"⚠️ Fallback stats save also failed: {e2}")

def track_download(user_id):
    """Thread-safe tracking of downloads with lock to prevent race conditions."""
    with _stats_lock:
        stats = load_stats()
        stats["total_downloads"] += 1
        uid = str(user_id)
        if uid not in stats["users"]:
            stats["users"][uid] = 0
        stats["users"][uid] += 1
        save_stats(stats)

def extract_contact_info(text: str):
    if not text:
        return [], []
    email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
    emails = email_pattern.findall(text)

    # Phone regex — supports:
    #   +CC AAA NNN NNNN     (e.g. +91 98765 43210)
    #   +CC AAA-NNN-NNNN     (e.g. +1-415-555-2671)
    #   +CC (AAA) NNN-NNNN   (e.g. +1 (415) 555-2671)
    #   NNNNN NNNNN         (e.g. 98765 43210)
    #   NNNNN-NNNNN         (e.g. 98765-43210)
    #   NNNNNNNNNN          (e.g. 9876543210)
    #   NNN-NNN-NNNN        (e.g. 415-555-2671)
    #   NNNN-NNN-NNN        (e.g. 1800-123-456)
    # A leading +country-code group is OPTIONAL, so an unprefixed 10-digit
    # number is still picked up. The lookarounds keep us from matching
    # inside @handles, dates, hashtag counts, and credit-card-shaped noise.
    phone_pattern = re.compile(
        r"(?<![@\w])"                          # don't start mid-word
        r"(\+\d{1,3}[\s.\-]?)?"               # optional +CC (captured for full match)
        r"(\(\d{2,5}\)|\d{2,5})[\s.\-]?"     # area code (parens or plain)
        r"(\d{3,5})"                           # first block
        r"([\s.\-]?\d{3,5})"                   # second block
        r"([\s.\-]?\d{2,5})?"                  # optional third block
        r"(?!\d)"                               # not followed by more digits
    )
    # Use finditer to get full match, not just captured groups
    phones_found = [m.group(0) for m in phone_pattern.finditer(text)]

    valid_phones = []
    for ph in phones_found:
        digits_only = re.sub(r'\D', '', ph)
        # Real phones: 8–15 digits, but reject 13+ digit sequences that
        # look like credit-card or order numbers (4-4-4-4 grouping, etc.).
        if not (8 <= len(digits_only) <= 15):
            continue
        if len(digits_only) >= 13:
            continue
        # Reject obvious dates: 2024-01-15, 12/05/2024, etc.
        if re.match(r'^(19|20)\d{2}[-/.\s]\d{1,2}[-/.\s]\d{1,2}', ph):
            continue
        clean_ph = re.sub(r'\s+', ' ', ph).strip()
        if clean_ph and clean_ph not in valid_phones:
            valid_phones.append(clean_ph)

    unique_emails = list(dict.fromkeys([e.lower().strip() for e in emails]))
    return unique_emails, valid_phones

def escape_ffmpeg_drawtext(text: str) -> str:
    safe_text = text.replace("'", "`").replace('"', '`').replace(':', '\\:')
    safe_text = safe_text.replace('\\', '\\\\')
    return safe_text

def escape_markdown(text: str) -> str:
    """Escape special Markdown characters for Telegram messages."""
    if not text:
        return ""
    # Escape Markdown special characters
    escape_chars = ['_', '*', '`', '[', ']', '(', ')', '~', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text

async def safe_reply_text(message, text, parse_mode="Markdown", **kwargs):
    """Safely send a message with Markdown, falling back to plain text if parsing fails."""
    try:
        return await message.reply_text(text, parse_mode=parse_mode, **kwargs)
    except BadRequest as e:
        if "can't parse" in str(e).lower() or "entities" in str(e).lower():
            print(f"⚠️ Markdown parse failed, retrying as plain text: {e}")
            return await message.reply_text(text, parse_mode=None, **kwargs)
        raise

async def safe_edit_text(message, text, parse_mode="Markdown", **kwargs):
    """Safely edit a message with Markdown, falling back to plain text if parsing fails."""
    try:
        return await message.edit_text(text, parse_mode=parse_mode, **kwargs)
    except BadRequest as e:
        if "can't parse" in str(e).lower() or "entities" in str(e).lower():
            print(f"⚠️ Markdown parse failed in edit, retrying as plain text: {e}")
            return await message.edit_text(text, parse_mode=None, **kwargs)
        raise

# ─── Scheduler ───────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler()

# ─────────────────────────────────────────────────────────────────────────────
# HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    first_name = user.first_name if user and user.first_name else "Hey"
    # Escape markdown special characters in first_name
    clean_name = re.sub(r'[*_`]', '', first_name)
    welcome_text = (
        f"✨ *━━━━━━━━━━━━━━━━━━━━━━━*\n"
        f"     ⚡ *DOWNLOAD WORLD v3.0* ⚡\n"
        f"✨ *━━━━━━━━━━━━━━━━━━━━━━━*\n\n"
        f"👋 *Hello {clean_name}! Welcome aboard!*\n"
        f"I am your all-in-one media companion and AI assistant. Simply send me any link or use a command to get started! 🪄\n\n"
        f"📥 *AUTOMATIC MEDIA DOWNLOADS:*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🚀 *Send any link* (YouTube, Instagram Reels, TikTok, Twitter, Facebook, etc.)\n"
        f"I will automatically fetch and deliver both the Video (MP4) and Audio (MP3) formats instantly! ⚡\n\n"
        f"🌟 *ADVANCED TOOLKIT HIGHLIGHTS:*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔊 `/effect` — Apply DSP voice effects to audio and video files!\n"
        f"📝 `/transcribe` — Convert voice notes and audio to text using AI!\n"
        f"📉 `/compress` — Advanced video compression with custom presets!\n"
        f"ℹ️ `/iginfo` — Extract Instagram profile details and contact info!\n"
        f"📞 `/extract` — Scan and extract phone numbers from any text!\n\n"
        f"_Tap the buttons below to explore the complete feature set!_ 👇"
    )
    import urllib.parse
    bot_username = context.bot.username
    if not bot_username:
        try:
            bot_me = await context.bot.get_me()
            bot_username = bot_me.username
        except Exception:
            bot_username = "everything_downloader_bot"

    share_text = "Check out Download World! Download videos, extract MP3s, edit media files, and apply photo filters instantly. 🚀"
    share_url = f"https://t.me/share/url?url=https://t.me/{bot_username}&text={urllib.parse.quote(share_text)}"

    keyboard = [
        [
            InlineKeyboardButton("🎬 Download Commands", callback_data="show_help"),
            InlineKeyboardButton("🤖 AI Fun Zone", callback_data="show_ai_modes"),
        ],
        [
            InlineKeyboardButton("📊 My Stats", callback_data="show_stats"),
            InlineKeyboardButton("⭐ Share / Rate Bot", url=share_url),
        ],
    ]
    if update.callback_query:
        try:
            await update.callback_query.edit_message_caption(
                caption=welcome_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )
        except BadRequest:
            await update.callback_query.edit_message_text(
                welcome_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )
    else:
        if os.path.exists("logo.jpg"):
            with open("logo.jpg", "rb") as logo_file:
                await update.effective_message.reply_photo(
                    photo=logo_file,
                    caption=welcome_text,
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
        "🎬 *BASIC DOWNLOADS*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "▸ `/mp4 <link>` — Download video (MP4)\n"
        "▸ `/mp3 <link>` — Download audio (MP3)\n"
        "▸ `/thumb <link>` — Hi-res thumbnail 🖼️\n"
        "▸ `/subs <link>` — Download subtitles (SRT) 📝\n"
        "▸ `/gif` — Convert replied video to GIF 🎞\n"
        "▸ `/search <query>` — Search YouTube\n"
        "▸ _Or just paste any link directly!_ 🪄\n\n"
        "🛠️ *ADVANCED MEDIA UTILITIES*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "▸ `/effect <type>` — Apply voice effects to audio/video 🔊\n"
        "  _Effects:_ `chipmunk`, `deep`, `echo`, `robot`, `bassboost`, `nightcore`\n"
        "▸ `/transcribe` — AI Whisper Speech-to-Text transcriber 📝\n"
        "▸ `/compress <mode>` — Smart video compression (`low`/`medium`/`high`)\n"
        "▸ `/watermark <text>` — Overlay custom text on photos/videos 🏷️\n"
        "▸ `/mute` — Strip audio streams from video files 🔇\n"
        "▸ `/sticker` — Convert photos/videos to Telegram stickers 🎨\n"
        "▸ `/caption` — AI-generated captions for images ✨\n"
        "▸ `/ocr` — Extract text from images 📄\n"
        "▸ `/tts <text>` — Text-to-speech conversion 🔊\n"
        "▸ `/notes` — Save and manage personal notes 📝\n\n"
        "🔍 *CONTACT DETAILS SCRAPERS*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "▸ `/iginfo <username>` — Scrape Instagram creator bio & contacts! ℹ️\n"
        "▸ `/extract` — Scrape emails, mobile numbers, handles & URLs from replied text! 📞\n\n"
        "🌐 *SOCIAL MEDIA DOWNLOADERS*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "▸ `/playlist <link> [mp3|mp4]` — Download a YouTube playlist (up to 25 items) 📜\n"
        "▸ `/hashtags <reel_url>` — Score an Instagram reel's hashtag reach 📊\n"
        "▸ `/reddit <sub|url>` — Pull top image/video from a subreddit 🔴\n"
        "▸ `/ycomments <video_url>` — Show top comments on a YouTube video 💬\n"
        "▸ `/ttslideshow <tiktok_url>` — Download every slide of a TikTok slideshow 🎞️\n"
        "▸ `/pinboard <board_url>` — Download all images from a Pinterest board 📌\n\n"
        "🌐 *UTILITIES & AI FUN*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "▸ `/tr <text>` — Translate to Hindi (or reply with `/tr`)\n"
        "▸ `/remind 10m <task>` — Set smart reminder (e.g. `2h` for hours)\n"
        "▸ `/stats` — Check bot statistics\n"
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
        "mode_roast":   ("roast",   "Tell me the name of the person you want to roast! 🔥"),
        "mode_shayari": ("shayari", "Kis topic pe shayari likhun? 📝"),
        "mode_rap":     ("rap",     "Rap ka topic bata, aag laga denge! 🔥🎤"),
        "mode_fortune": ("fortune", "Naam bata, tera bhavishya dekhta hoon! 🔮"),
        "mode_story":   ("story",   "Kis topic pe story likhun? 📝"),
        "mode_recipe":  ("recipe",  "Which recipe do you want to learn? Tell me the ingredients! 🍕"),
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
            "🍕 *Recipe*  →  Hey-style cooking\n"
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
    if not GEMINI_API_KEY:
        return "GEMINI_API_KEY is missing! Please configure it in your environment variables. 🙏"
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        },
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return "The Gemini AI is currently unavailable. Please try again later! 🙏"

async def handle_ai_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, user_text: str):
    prompts = {
        "roast":   {
            "system": "You are a savage, funny Indian roaster. Roast the person named in the prompt in exactly 4 lines in English. Be hilarious but don't cross community guidelines.",
            "user":   f"Roast this person: {user_text}"
        },
        "shayari": {
            "system": "You are a master Mirza Ghalib style poet but you write in Hinglish. Write a beautiful 4-line poem about the topic given.",
            "user":   f"Topic: {user_text}"
        },
        "rap":     {
            "system": "You are an underground rapper. Write an energetic rap with rhymes in exactly 8 lines about the given topic.",
            "user":   f"Topic: {user_text}"
        },
        "fortune": {
            "system": "You are a funny Indian jyotishi (astrologer). Tell a humorous 3-4 line fortune for the given name. Make it absurd and funny.",
            "user":   f"Name: {user_text}"
        },
        "story":   {
            "system": "You are a creative storyteller. Write a short, engaging 10-line story in Hinglish about the given topic. Make it interesting and desi.",
            "user":   f"Topic: {user_text}"
        },
        "recipe":  {
            "system": "You are a Desi Chef. Provide a simple and tasty recipe in Hinglish with clear steps based on the ingredients or dish name provided. Use a friendly, 'Hey' style tone.",
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


class CobaltDownloadError(Exception):
    pass

async def download_via_cobalt(url: str, output_dir: str, audio_only: bool = False) -> str:
    import httpx
    import os
    import shutil
    import subprocess
    import string
    import random
    from urllib.parse import urlparse
    import re

    api_url = os.getenv("COBALT_API_URL")
    api_key = os.getenv("COBALT_API_KEY")

    instances = [
        "https://api.cobalt.tools",
        "https://dog.kittycat.boo",
        "https://rue-cobalt.xenon.zone",
        "https://cobaltapi.cjs.nz",
        "https://cobalt.api.zwei.one",
        "https://co.e-z.host",
        "https://cobalt.urania.wang",
        "https://api.cobalt.bkc.hi.cn"
    ]
    if api_url:
        instances.insert(0, api_url)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Api-Key {api_key}"

    payload = {
        "url": url,
        "downloadMode": "audio" if audio_only else "auto",
        "videoQuality": "1080",
        "filenameStyle": "basic"
    }

    os.makedirs(output_dir, exist_ok=True)
    last_error = "Unknown Error"
    
    # 15s timeout on connect, 30s overall for Cobalt API call
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=15.0)) as client:
        for instance_url in instances:
            try:
                resp = await client.post(f"{instance_url}/", json=payload, headers=headers)
                
                if resp.status_code != 200:
                    try:
                        err_data = resp.json()
                        err_text = err_data.get("error", {}).get("code", resp.text)
                    except:
                        err_text = resp.text
                    last_error = f"Cobalt API Error {resp.status_code} on {instance_url}: {err_text}"
                    continue
                    
                data = resp.json()
                status = data.get("status")

                if status == "error":
                    code = data.get("error", {}).get("code", "UNKNOWN_ERROR")
                    last_error = f"Cobalt API Error on {instance_url}: {code}"
                    continue
                    
                if status in ["tunnel", "redirect"]:
                    target_url = data.get("url")
                    filename = data.get("filename", "")
                    if not target_url:
                        last_error = f"No URL in tunnel/redirect response from {instance_url}"
                        continue
                        
                    return await _download_file(target_url, output_dir, filename=filename)
                    
                elif status == "picker":
                    items = data.get("picker", [])
                    if not items:
                        last_error = f"Empty picker response from {instance_url}"
                        continue
                    # For now, just grab the first item.
                    target_url = items[0].get("url")
                    return await _download_file(target_url, output_dir)
                    
                elif status == "local-processing":
                    items = data.get("tunnel", [])
                    if not items:
                        last_error = f"No items for local-processing from {instance_url}"
                        continue
                        
                    files = []
                    for item in items:
                        file_path = await _download_file(item, output_dir)
                        files.append(file_path)
                    
                    if len(files) == 1:
                        return files[0]
                    
                    ffmpeg_bin = shutil.which("ffmpeg")
                    if not ffmpeg_bin:
                        raise CobaltDownloadError("ffmpeg required for local-processing but not found")
                        
                    out_ext = ".mp3" if audio_only else ".mp4"
                    out_name = f"merged_{''.join(random.choices(string.ascii_letters, k=6))}{out_ext}"
                    out_path = os.path.join(output_dir, out_name)
                    
                    cmd = [ffmpeg_bin, "-y"]
                    for f in files:
                        cmd.extend(["-i", f])
                    if audio_only:
                        cmd.extend(["-vn", "-acodec", "libmp3lame", "-ab", "192k"])
                    else:
                        cmd.extend(["-c", "copy"])
                    cmd.append(out_path)
                    
                    try:
                        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    except subprocess.CalledProcessError as e:
                        err = e.stderr.decode() if e.stderr else "unknown error"
                        raise CobaltDownloadError(f"ffmpeg merge failed: {err}")
                        
                    return out_path
                    
                else:
                    last_error = f"Unknown status '{status}' from {instance_url}"
                    continue
                    
            except httpx.RequestError as e:
                last_error = f"Connection failed to {instance_url}: {e}"
                continue
            except Exception as e:
                last_error = f"Unexpected error with {instance_url}: {e}"
                continue
                
    raise CobaltDownloadError(f"All Cobalt instances failed. Last error: {last_error}")

async def _download_file(url: str, output_dir: str, filename: str = "") -> str:
    import httpx
    import os
    import random
    import string
    from urllib.parse import urlparse
    import re
    
    if not filename:
        parsed = urlparse(url)
        filename = os.path.basename(parsed.path)
        if not filename:
            filename = f"file_{''.join(random.choices(string.ascii_letters, k=8))}"
            
    # Clean filename
    filename = re.sub(r'[^\w\-_\.]', '_', filename)
    out_path = os.path.join(output_dir, filename)
    
    async with httpx.AsyncClient() as client:
        async with client.stream("GET", url, timeout=120) as resp:
            resp.raise_for_status()
            with open(out_path, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
                    
    return out_path



def cleanup(path: str):
    try:
        if os.path.exists(path):
            shutil.rmtree(path)
    except Exception as e:
        print(f"Cleanup Error: {e}")

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

def progress_hook_factory(loop, bot, chat_id, message_id=None, action=ChatAction.TYPING):
    """Creates a hook to update the progress in Telegram safely across threads using ChatAction."""
    last_update_time = 0

    def hook(d):
        nonlocal last_update_time
        if d['status'] == 'downloading':
            # Update/Send action only every 4 seconds to avoid rate limits
            import time
            current_time = time.time()
            if current_time - last_update_time > 4:
                last_update_time = current_time
                if message_id is not None:
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
                else:
                    # Send chat action instead of editing status message
                    asyncio.run_coroutine_threadsafe(
                        bot.send_chat_action(chat_id=chat_id, action=action),
                        loop
                    )
        elif d['status'] == 'finished':
            if message_id is not None:
                asyncio.run_coroutine_threadsafe(
                    bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="✅ Download Finished! Processing... 🛠️"),
                    loop
                )

    return hook

# ─── Commands ─────────────────────────────────────────────────────────────────

async def mp3_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    url = None
    replied_video = None

    if context.args:
        url = context.args[0]
    elif source_msg.reply_to_message:
        reply = source_msg.reply_to_message
        if reply.video:
            replied_video = reply.video
        elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("video/"):
            replied_video = reply.document

    if not url and not replied_video:
        await source_msg.reply_text(
            "❌ *Hey use kaise karein?*\n\n"
            "Format:\n"
            "• `/mp3 <link>` — Download MP3 from a URL\n"
            "• Reply to any video file with `/mp3` to extract its audio directly!",
            parse_mode="Markdown"
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_mp3_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        file_path = None

        if replied_video:
            if replied_video.file_size > 20 * 1024 * 1024:
                await source_msg.reply_text("❌ *Hey video 20MB se badi hai!* Telegram bots can only download files up to 20MB. 😔", parse_mode="Markdown")
                cleanup(download_dir)
                return
                
            tg_file = await context.bot.get_file(replied_video.file_id)
            ext = ".mp4"
            if hasattr(replied_video, "file_name") and replied_video.file_name:
                ext = os.path.splitext(replied_video.file_name)[1] or ".mp4"
                
            input_file = os.path.join(download_dir, f"input{ext}")
            await tg_file.download_to_drive(input_file)
            
            ffmpeg_bin = shutil.which('ffmpeg')
            if ffmpeg_bin:
                mp3_path = os.path.join(download_dir, "extracted.mp3")
                import subprocess
                cmd = [ffmpeg_bin, '-y', '-i', input_file, '-vn', '-acodec', 'libmp3lame', '-ab', '192k', mp3_path]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                file_path = mp3_path
            else:
                raise RuntimeError("FFmpeg is missing on the system. Cannot extract audio.")
        else:
            file_path = await download_via_cobalt(url, download_dir, audio_only=True)

        # Make sure it's MP3 if downloaded from URL, although Cobalt handles this via local-processing if it needs merging.
        # Sometimes Cobalt just returns an audio file (.webm or .m4a) depending on format.
        if file_path and not file_path.endswith('.mp3'):
            ffmpeg_bin = shutil.which('ffmpeg')
            if ffmpeg_bin:
                mp3_path = os.path.splitext(file_path)[0] + ".mp3"
                import subprocess
                cmd = [ffmpeg_bin, '-y', '-i', file_path, '-vn', '-acodec', 'libmp3lame', '-ab', '192k', mp3_path]
                try:
                    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    file_path = mp3_path
                except subprocess.CalledProcessError:
                    pass

        if file_path and os.path.exists(file_path):
            if os.path.getsize(file_path) <= 500 * 1024 * 1024:
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_AUDIO)
                with open(file_path, 'rb') as audio:
                    await source_msg.reply_audio(audio, caption="Enjoy your music! 🎵")
                track_download(user.id)
            else:
                await source_msg.reply_text("❌ *Hey audio 500MB se badi hai!* 😔", parse_mode="Markdown")
        else:
            await source_msg.reply_text("❌ *Hey MP3 nahi bani. Link check kar!* 😔", parse_mode="Markdown")

    except CobaltDownloadError as ce:
        print(f"Cobalt MP3 Error: {ce}")
        await source_msg.reply_text("❌ *Couldn't fetch that link right now, try again in a bit!* 🙏", parse_mode="Markdown")
    except Exception as e:
        print(f"MP3 Error: {e}")
        await source_msg.reply_text("❌ *Hey error aagaya MP3 banane mein.* 🙏", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def mp4_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not context.args:
        await source_msg.reply_text(
            "❌ *Hey link toh bhej!*\n\nExample: `/mp4 https://youtube.com/watch?v=xxx`",
            parse_mode="Markdown"
        )
        return

    url = context.args[0]
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_mp4_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        file_path = await download_via_cobalt(url, download_dir)

        if file_path and os.path.exists(file_path):
            # Compress video if requested or if it's large
            original_size = os.path.getsize(file_path)
            compressed_path = os.path.splitext(file_path)[0] + "_compressed.mp4"
            
            try:
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
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
            
            if os.path.getsize(file_path) <= 500 * 1024 * 1024:
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)
                try:
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
                except Exception as upload_err:
                    print(f"❌ Upload failed: {upload_err}")
                    await source_msg.reply_text(f"❌ *Hey upload fail ho gaya:* `{upload_err}`", parse_mode="Markdown")
            else:
                await source_msg.reply_text("❌ *Hey video 500MB se badi hai!* 😔", parse_mode="Markdown")
        else:
            await source_msg.reply_text("❌ *Hey file nahi mili download ke baad.* 😔", parse_mode="Markdown")

    except CobaltDownloadError as ce:
        print(f"Cobalt MP4 Error: {ce}")
        await source_msg.reply_text("❌ *Couldn't fetch that link right now, try again in a bit!* 🙏", parse_mode="Markdown")
    except Exception as e:
        print(f"MP4 Error: {e}")
        await source_msg.reply_text("❌ *Hey error aagaya video download karne mein.* 🙏", parse_mode="Markdown")
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
            "❌ *Hey link toh bhej!*\n\nExample: `/thumb https://youtu.be/xxx`",
            parse_mode="Markdown",
        )
        return

    url = context.args[0]
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_thumb_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        file_path = await asyncio.to_thread(_download_thumbnail, url, download_dir)
        if not file_path or not os.path.exists(file_path):
            raise RuntimeError("Thumbnail file missing after download.")

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
        with open(file_path, 'rb') as photo:
            await source_msg.reply_photo(photo, caption="🖼️ Hi-res thumbnail")
        track_download(user.id)
    except Exception as e:
        print(f"Thumb Error: {e}")
        await source_msg.reply_text("❌ *Thumbnail nahi mili. Link check kar!* 😔", parse_mode="Markdown")
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
            "❌ *Hey link toh bhej!*\n\n"
            "Example: `/subs https://youtu.be/xxx`\n"
            "Optional language: `/subs <link> hi`",
            parse_mode="Markdown",
        )
        return

    url = context.args[0]
    lang = context.args[1] if len(context.args) > 1 else 'en'
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_subs_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        sub_path = await asyncio.to_thread(_download_subtitles, url, download_dir, lang)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_DOCUMENT)
        with open(sub_path, 'rb') as f:
            await source_msg.reply_document(f, caption=f"📝 Subtitles ({lang})")
        track_download(user.id)
    except Exception as e:
        print(f"Subs Error: {e}")
        msg = "❌ *Is video pe subtitles nahi hain.* 😔" if "No subtitles" in str(e) else "❌ *Subtitle nahi mili.* 🙏"
        await source_msg.reply_text(msg, parse_mode="Markdown")
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

    if not shutil.which('ffmpeg'):
        await source_msg.reply_text("❌ *FFmpeg nahi mila — GIF nahi ban sakti.* 🙏", parse_mode="Markdown")
        return

    url = None
    duration = 8
    replied_video = None
    
    if context.args:
        if context.args[0].startswith("http://") or context.args[0].startswith("https://"):
            url = context.args[0]
            if len(context.args) > 1:
                try:
                    duration = int(context.args[1])
                except ValueError:
                    pass
        else:
            try:
                duration = int(context.args[0])
            except ValueError:
                pass

    if not url:
        if source_msg.reply_to_message:
            reply = source_msg.reply_to_message
            if reply.video:
                replied_video = reply.video
            elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("video/"):
                replied_video = reply.document
                
        if not replied_video:
            await source_msg.reply_text(
                "❌ *Hey usage check karo!*\n\n"
                "• `/gif <link> [duration]`\n"
                "• Reply to any video with `/gif [duration]`\n\n"
                "Default duration: `8` seconds.",
                parse_mode="Markdown"
            )
            return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_gif_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        input_file = None
        if url:
            file_path = await download_via_cobalt(url, download_dir)
            if not file_path:
                raise RuntimeError("Failed to download video from the link.")
            input_file = file_path
        else:
            if replied_video.file_size > 20 * 1024 * 1024:
                await source_msg.reply_text("❌ *Hey video 20MB se badi hai!* Telegram custom downloads limit limits bots to 20MB. 😔", parse_mode="Markdown")
                return
            
            tg_file = await context.bot.get_file(replied_video.file_id)
            ext = ".mp4"
            if hasattr(replied_video, "file_name") and replied_video.file_name:
                ext = os.path.splitext(replied_video.file_name)[1] or ".mp4"
            
            input_file = os.path.join(download_dir, f"input{ext}")
            await tg_file.download_to_drive(input_file)

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        gif_path = os.path.splitext(input_file)[0] + ".gif"
        await asyncio.to_thread(_video_to_gif, input_file, gif_path, duration, 480)

        if not os.path.exists(gif_path):
            raise RuntimeError("GIF conversion produced no file.")
        if os.path.getsize(gif_path) > 50 * 1024 * 1024:
            await source_msg.reply_text("❌ *GIF 50MB se badi ban gayi. Shorter clip try kar.* 😔", parse_mode="Markdown")
            return

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)
        with open(gif_path, 'rb') as f:
            await context.bot.send_animation(
                chat_id=update.effective_chat.id,
                animation=f,
                reply_to_message_id=source_msg.message_id,
                caption=f"🎞️ Your GIF is ready! ({duration}s)",
            )
        track_download(user.id)
    except Exception as e:
        print(f"GIF Error: {e}")
        await source_msg.reply_text("❌ *Hey GIF nahi bani. Link/length check kar.* 🙏", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def trim_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    # Expected arguments:
    # 1. Start time (e.g. 00:10 or 10)
    # 2. End time (e.g. 00:30 or 30)
    # 3. URL (optional if replying to a video file)
    
    if len(context.args) < 2:
        await source_msg.reply_text(
            "❌ *Hey usage check karo!*\n\n"
            "Format:\n"
            "• `/trim <start_time> <end_time> <link>`\n"
            "• Reply to a video with `/trim <start_time> <end_time>`\n\n"
            "Examples:\n"
            "• `/trim 00:10 00:30 https://youtu.be/xxx`\n"
            "• `/trim 00:05 00:15` (as reply to video)",
            parse_mode="Markdown"
        )
        return

    start_time = context.args[0]
    end_time = context.args[1]
    
    url = context.args[2] if len(context.args) > 2 else None
    replied_video = None
    
    if not url:
        if source_msg.reply_to_message:
            reply = source_msg.reply_to_message
            if reply.video:
                replied_video = reply.video
            elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("video/"):
                replied_video = reply.document
                
        if not replied_video:
            await source_msg.reply_text("❌ *Hey koi link nahi mila aur na hi kisi video ko reply kiya!*", parse_mode="Markdown")
            return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_trim_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        input_file = None
        if url:
            file_path = await download_via_cobalt(url, download_dir)
            if not file_path:
                raise RuntimeError("Failed to download video from the link.")
            input_file = file_path
        else:
            # Replied to Telegram video
            if replied_video.file_size > 20 * 1024 * 1024:
                await source_msg.reply_text("❌ *Hey video 20MB se badi hai!* Telegram custom downloads limit limits bots to 20MB. 😔", parse_mode="Markdown")
                cleanup(download_dir)
                return
            
            tg_file = await context.bot.get_file(replied_video.file_id)
            ext = ".mp4"
            if hasattr(replied_video, "file_name") and replied_video.file_name:
                ext = os.path.splitext(replied_video.file_name)[1] or ".mp4"
            
            input_file = os.path.join(download_dir, f"input{ext}")
            await tg_file.download_to_drive(input_file)

        output_file = os.path.join(download_dir, "trimmed.mp4")
        ffmpeg_bin = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
        if not os.path.exists(ffmpeg_bin):
            for p in ['/opt/homebrew/bin/ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']:
                if os.path.exists(p):
                    ffmpeg_bin = p
                    break
                    
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            raise RuntimeError("FFmpeg not found. Cannot trim video.")

        import subprocess
        cmd = [ffmpeg_bin, '-y', '-ss', start_time, '-to', end_time, '-i', input_file, '-c', 'copy', output_file]
        
        # Run copy mode first
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        
        if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
            # Fall back to re-encoding if copy fails (e.g. keyframe issue)
            cmd_reencode = [ffmpeg_bin, '-y', '-ss', start_time, '-to', end_time, '-i', input_file, '-c:v', 'libx264', '-c:a', 'aac', output_file]
            subprocess.run(cmd_reencode, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)
            with open(output_file, 'rb') as video:
                await source_msg.reply_video(
                    video, 
                    caption=f"✂️ Video Trimmed! ({start_time} - {end_time}) 🎬",
                    supports_streaming=True
                )
            track_download(user.id)
        else:
            raise RuntimeError("FFmpeg failed to produce a valid trimmed file.")

    except Exception as e:
        print(f"Trim Error: {e}")
        await source_msg.reply_text(f"❌ *Hey trim nahi ho paya:* `{e}`", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def tag_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not source_msg.reply_to_message:
        await source_msg.reply_text(
            "❌ *Hey usage check karo!*\n\n"
            "Reply to any Audio/MP3 file with:\n"
            "`/tag Title | Artist | Album` (Album is optional)\n\n"
            "Example:\n"
            "`/tag Dil Se | A.R. Rahman | Dil Se OST`",
            parse_mode="Markdown"
        )
        return

    reply = source_msg.reply_to_message
    target_audio = None
    if reply.audio:
        target_audio = reply.audio
    elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("audio/"):
        target_audio = reply.document

    if not target_audio:
        await source_msg.reply_text("❌ *Hey kisi audio file ko reply karo!*", parse_mode="Markdown")
        return

    if not context.args:
        await source_msg.reply_text(
            "❌ *Hey tags specify karo!*\n\n"
            "Format: `/tag Title | Artist | Album` (use `|` as separator)",
            parse_mode="Markdown"
        )
        return

    arg_str = " ".join(context.args)
    parts = [p.strip() for p in arg_str.split("|")]
    
    title = parts[0] if len(parts) > 0 else ""
    artist = parts[1] if len(parts) > 1 else ""
    album = parts[2] if len(parts) > 2 else ""

    if not title:
        await source_msg.reply_text("❌ *Hey title specify karna zaroori hai!*", parse_mode="Markdown")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_tag_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        if target_audio.file_size > 20 * 1024 * 1024:
            await source_msg.reply_text("❌ *Hey audio file 20MB se badi hai!* Telegram custom downloads limit limits bots to 20MB. 😔", parse_mode="Markdown")
            cleanup(download_dir)
            return

        tg_file = await context.bot.get_file(target_audio.file_id)
        
        filename = "music.mp3"
        if hasattr(target_audio, "file_name") and target_audio.file_name:
            filename = target_audio.file_name
        elif hasattr(target_audio, "title") and target_audio.title:
            filename = f"{target_audio.title}.mp3"
            
        ext = os.path.splitext(filename)[1] or ".mp3"
        input_file = os.path.join(download_dir, f"input{ext}")
        await tg_file.download_to_drive(input_file)

        out_filename = filename
        if artist:
            out_filename = f"{artist} - {title}{ext}"
        else:
            out_filename = f"{title}{ext}"
            
        output_file = os.path.join(download_dir, out_filename)

        ffmpeg_bin = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            for p in ['/opt/homebrew/bin/ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']:
                if os.path.exists(p):
                    ffmpeg_bin = p
                    break
        
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            raise RuntimeError("FFmpeg not found. Cannot edit tags.")

        import subprocess
        cmd = [ffmpeg_bin, '-y', '-i', input_file]
        
        if title:
            cmd.extend(['-metadata', f'title={title}'])
        if artist:
            cmd.extend(['-metadata', f'artist={artist}'])
        if album:
            cmd.extend(['-metadata', f'album={album}'])
            
        cmd.extend(['-c:a', 'copy', output_file])

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_AUDIO)
            with open(output_file, 'rb') as audio:
                await source_msg.reply_audio(
                    audio, 
                    title=title,
                    performer=artist,
                    caption="✅ Audio Tags Updated! 🎵"
                )
            track_download(user.id)
        else:
            raise RuntimeError("FFmpeg failed to produce the tagged file.")

    except Exception as e:
        print(f"Tag Error: {e}")
        await source_msg.reply_text(f"❌ *Hey tags edit nahi ho paye:* `{e}`", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def iginfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not context.args:
        await source_msg.reply_text(
            "❌ *Hey username ya link toh batao!*\n\n"
            "Format:\n"
            "• `/iginfo <username>`\n"
            "• `/iginfo <profile_link>`\n\n"
            "Example: `/iginfo instagram`",
            parse_mode="Markdown"
        )
        return

    raw_input = context.args[0].strip()
    
    # If the user pasted a full profile link, extract the username
    if "instagram.com" in raw_input:
        match = re.search(r'instagram\.com/([A-Za-z0-9_\.]+)', raw_input)
        if match:
            username = match.group(1)
        else:
            username = raw_input
    else:
        username = raw_input

    username = username.replace("@", "").strip()
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_iginfo_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        # Load profile info via Instaloader. We run it in a worker thread with
        # a hard timeout so a stuck session can't hang the bot forever.
        profile = await asyncio.wait_for(
            asyncio.to_thread(instaloader.Profile.from_username, L.context, username),
            timeout=45.0,
        )

        full_name = profile.full_name or "N/A"
        bio = profile.biography or "No biography"
        followers = profile.followers
        following = profile.followees
        is_private = "Yes 🔒" if profile.is_private else "No 🔓"
        is_verified = "Yes ✅" if profile.is_verified else "No ❌"
        posts_count = profile.mediacount

        # Extract contact info from biography
        bio_emails, bio_phones = extract_contact_info(bio)
        
        # Check business attributes directly
        biz_phone = getattr(profile, "business_phone_number", None)
        biz_email = getattr(profile, "business_email", None)
        biz_cat = getattr(profile, "business_category_name", None)
        is_biz = getattr(profile, "is_business_account", False)

        # Merge results
        all_emails = []
        if biz_email:
            all_emails.append(biz_email.strip().lower())
        if bio_emails:
            for e in bio_emails:
                if e not in all_emails:
                    all_emails.append(e)

        all_phones = []
        if biz_phone:
            all_phones.append(biz_phone.strip())
        if bio_phones:
            for p in bio_phones:
                if p not in all_phones:
                    all_phones.append(p)

        profile_pic_url = profile.profile_pic_url
        profile_pic_path = os.path.join(download_dir, "profile_pic.jpg")

        # Download profile picture — if it fails we still send the text
        # details below instead of bailing out on the whole command.
        try:
            import urllib.request
            req = urllib.request.Request(profile_pic_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=20) as resp, open(profile_pic_path, 'wb') as f:
                shutil.copyfileobj(resp, f)
        except Exception as pic_err:
            print(f"Instagram profile-pic download failed: {pic_err}")
            try:
                os.remove(profile_pic_path)
            except OSError:
                pass

        # Escape full name to prevent Markdown parsing errors
        full_name_escaped = escape_markdown(full_name)
        caption = (
            f"📸 *Instagram Profile: @{profile.username}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *Name:* `{full_name_escaped}`\n"
            f"🔒 *Private:* {is_private}\n"
            f"✅ *Verified:* {is_verified}\n\n"
            f"👥 *Followers:* `{followers:,}`\n"
            f"👥 *Following:* `{following:,}`\n"
            f"📮 *Posts:* `{posts_count:,}`\n"
        )

        if is_biz:
            category_str = f" ({biz_cat})" if biz_cat else ""
            caption += f"💼 *Business Account:* Yes{category_str}\n"

        caption += f"━━━━━━━━━━━━━━━━━━━━━\n"

        # Add Contact Details if found
        if all_emails or all_phones:
            caption += f"📞 *Contact Details Found:*\n"
            if all_phones:
                phones_str = ", ".join([f"`{p}`" for p in all_phones])
                caption += f"• *Phone(s):* {phones_str}\n"
            if all_emails:
                emails_str = ", ".join([f"`{e}`" for e in all_emails])
                caption += f"• *Email(s):* {emails_str}\n"
            caption += f"━━━━━━━━━━━━━━━━━━━━━\n"

        # Escape bio text to prevent Markdown parsing errors
        bio_escaped = escape_markdown(bio)
        caption += f"📝 *Bio:* _{bio_escaped}_" 

        if os.path.exists(profile_pic_path) and os.path.getsize(profile_pic_path) > 0:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
            with open(profile_pic_path, 'rb') as photo:
                await source_msg.reply_photo(photo=photo, caption=caption, parse_mode="Markdown")
        else:
            await source_msg.reply_text(caption, parse_mode="Markdown")

        track_download(user.id)

    except instaloader.exceptions.ProfileNotExistsException:
        await source_msg.reply_text(
            f"❌ *Hey yeh Instagram profile exist nahi karti!*\n\n"
            f"Aapne diya: `{username}`. Please check if the username is correct.",
            parse_mode="Markdown"
        )
    except instaloader.exceptions.LoginRequiredException:
        await source_msg.reply_text(
            "🔒 *Instagram Login Required!*\n\n"
            "Hey, Instagram checks require active account session. Please add "
            "`INSTA_USERNAME` & `INSTA_PASSWORD` or `INSTAGRAM_COOKIES_FILE` in your "
            "`.env` (Railway Dashboard) to enable this feature!",
            parse_mode="Markdown"
        )
    except instaloader.exceptions.ConnectionException as conn_err:
        print(f"Instagram Connection Error: {conn_err}")
        await source_msg.reply_text(
            "⚠️ *Instagram Connection Limit / Rate Limit (429)!*\n\n"
            "Hey, Instagram has blocked anonymous requests temporarily. "
            "Please configure your login details or try again later.",
            parse_mode="Markdown"
        )
    except instaloader.exceptions.PrivateProfileNotFollowedException:
        await source_msg.reply_text(
            f"🔒 *@{username} is private.*\n\n"
            "Hey, private accounts ka data sirf unke approved followers ko "
            "milta hai. Public info (name, bio, follower count) ke liye "
            "follow request bhejo!",
            parse_mode="Markdown"
        )
    except (asyncio.TimeoutError, TimeoutError):
        await source_msg.reply_text(
            "⏱️ *Instagram ne respond nahi kiya 45s mein.*\n\n"
            "Hey, shayad rate-limit ya slow network hai. Thodi der baad "
            "dobara try karo ya cookies refresh karo.",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Instagram Info Error: {type(e).__name__}: {e}")
        await source_msg.reply_text(
            f"❌ *Hey details nahi nikal paye:* `{type(e).__name__}: {e}`",
            parse_mode="Markdown"
        )
    finally:
        cleanup(download_dir)


async def extract_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    
    text_to_parse = ""
    if context.args:
        text_to_parse = " ".join(context.args)
    elif source_msg.reply_to_message:
        reply = source_msg.reply_to_message
        if reply.text:
            text_to_parse = reply.text
        elif reply.caption:
            text_to_parse = reply.caption
            
    if not text_to_parse:
        await source_msg.reply_text(
            "❌ *Hey usage check karo!*\n\n"
            "Format:\n"
            "• `/extract <text>`\n"
            "• Reply to a text message with `/extract`",
            parse_mode="Markdown"
        )
        return

    # Extract info
    emails, phones = extract_contact_info(text_to_parse)
    
    # Social handles
    handles = re.findall(r'(?<!\w)@([a-zA-Z0-9_]{3,30})', text_to_parse)
    unique_handles = list(dict.fromkeys(handles))
    
    # URLs
    urls = re.findall(r'https?://[^\s]+', text_to_parse)
    clean_urls = []
    for u in urls:
        u_clean = u.rstrip('.,;)"\'?]')
        if u_clean not in clean_urls:
            clean_urls.append(u_clean)

    if not emails and not phones and not unique_handles and not clean_urls:
        await source_msg.reply_text("❌ *Hey is text mein koi details (phone, email, handles, URLs) nahi mile!*", parse_mode="Markdown")
        return

    result = "🔎 *Extracted Details:*\n━━━━━━━━━━━━━━━━━━━━━\n"
    if phones:
        result += "📞 *Mobile / Phone Numbers:*\n"
        result += "\n".join([f"• `{escape_markdown(p)}`" for p in phones]) + "\n\n"
    if emails:
        result += "✉️ *Email & Addresses:*\n"
        result += "\n".join([f"• `{escape_markdown(e)}`" for e in emails]) + "\n\n"
    if unique_handles:
        result += "👤 *Social Handles:*\n"
        result += "\n".join([f"• @{escape_markdown(h)}" for h in unique_handles]) + "\n\n"
    if clean_urls:
        result += "🌐 *URLs / Links:*\n"
        result += "\n".join([f"• {escape_markdown(u)}" for u in clean_urls]) + "\n\n"
        
    result += "━━━━━━━━━━━━━━━━━━━━━"
    
    # Try sending with Markdown, fallback to plain text if it fails
    try:
        await source_msg.reply_text(result, parse_mode="Markdown", disable_web_page_preview=True)
    except BadRequest as e:
        # If Markdown parsing fails, send as plain text
        print(f"Extract Markdown error: {e}, sending as plain text")
        plain_result = result.replace('*', '').replace('`', '').replace('_', '')
        await source_msg.reply_text(plain_result, disable_web_page_preview=True)


async def sticker_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Convert images or videos to Telegram stickers."""
    source_msg = update.effective_message
    user = update.effective_user

    if not source_msg.reply_to_message:
        await source_msg.reply_text(
            "❌ *Hey kisi photo ya video ko reply karo!*\n\n"
            "Reply to an image or video with `/sticker` to convert it to a sticker!",
            parse_mode="Markdown"
        )
        return

    reply = source_msg.reply_to_message
    download_dir = f"downloads_sticker_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        # Check for photo
        if reply.photo:
            photo = reply.photo[-1]  # Get highest resolution
            if photo.file_size > 10 * 1024 * 1024:
                await source_msg.reply_text("❌ *Hey photo 10MB se badi hai!* Stickers have size limits. 😔", parse_mode="Markdown")
                return

            file = await context.bot.get_file(photo.file_id)
            input_path = os.path.join(download_dir, "input.jpg")
            await file.download_to_drive(input_path)

            # Convert to WebP format for stickers (512x512)
            output_path = os.path.join(download_dir, "sticker.webp")
            cmd = f'ffmpeg -i "{input_path}" -vf "scale=512:512:force_original_aspect_ratio=decrease,pad=512:512:(ow-iw)/2:(oh-ih)/2" -vcodec libwebp -lossless 1 -loop 0 -preset default -an -vsync 0 "{output_path}" -y'

            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await process.communicate()

            if process.returncode != 0:
                err_msg = stderr.decode(errors="ignore").strip() if stderr else "Unknown error"
                print(f"Sticker (photo) ffmpeg error: {err_msg}")
                await source_msg.reply_text("❌ *Hey sticker convert nahi ho paya!* FFmpeg error.", parse_mode="Markdown")
                return

            if os.path.exists(output_path):
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
                with open(output_path, 'rb') as sticker:
                    await source_msg.reply_sticker(sticker=sticker)
                await source_msg.reply_text("✅ *Sticker created!* 🎨", parse_mode="Markdown")
            else:
                await source_msg.reply_text("❌ *Hey sticker convert nahi ho paya!* FFmpeg error.", parse_mode="Markdown")

        # Check for video
        elif reply.video or reply.animation:
            media = reply.video or reply.animation
            if media.file_size > 10 * 1024 * 1024:
                await source_msg.reply_text("❌ *Hey video 10MB se badi hai!* Stickers have size limits. 😔", parse_mode="Markdown")
                return

            file = await context.bot.get_file(media.file_id)
            input_path = os.path.join(download_dir, "input.mp4")
            await file.download_to_drive(input_path)

            # Convert to animated WebP sticker (512x512, max 3 seconds)
            output_path = os.path.join(download_dir, "sticker.webp")
            cmd = f'ffmpeg -i "{input_path}" -vf "scale=512:512:force_original_aspect_ratio=decrease,pad=512:512:(ow-iw)/2:(oh-ih)/2" -vcodec libwebp -lossless 1 -loop 0 -preset default -an -vsync 0 -t 3 "{output_path}" -y'

            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await process.communicate()

            if process.returncode != 0:
                err_msg = stderr.decode(errors="ignore").strip() if stderr else "Unknown error"
                print(f"Sticker (video) ffmpeg error: {err_msg}")
                await source_msg.reply_text("❌ *Hey sticker convert nahi ho paya!* FFmpeg error.", parse_mode="Markdown")
                return

            if os.path.exists(output_path):
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
                with open(output_path, 'rb') as sticker:
                    await source_msg.reply_sticker(sticker=sticker)
                await source_msg.reply_text("✅ *Animated sticker created!* 🎨", parse_mode="Markdown")
            else:
                await source_msg.reply_text("❌ *Hey sticker convert nahi ho paya!* FFmpeg error.", parse_mode="Markdown")
        else:
            await source_msg.reply_text("❌ *Hey sirf photo ya video ko sticker mein convert kar sakte ho!*", parse_mode="Markdown")

    except Exception as e:
        print(f"Sticker conversion error: {e}")
        await source_msg.reply_text(f"❌ *Hey sticker create nahi ho paya:* `{e}`", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def caption_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate AI-powered captions for images."""
    source_msg = update.effective_message
    user = update.effective_user

    if not GEMINI_API_KEY:
        await source_msg.reply_text("❌ *Hey GEMINI_API_KEY missing hai!* AI features disabled.", parse_mode="Markdown")
        return

    if not source_msg.reply_to_message or not source_msg.reply_to_message.photo:
        await source_msg.reply_text(
            "❌ *Hey kisi photo ko reply karo!*\n\n"
            "Reply to a photo with `/caption` to generate an AI caption!",
            parse_mode="Markdown"
        )
        return

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        # Get the photo
        reply = source_msg.reply_to_message
        photo = reply.photo[-1]
        
        if photo.file_size > 20 * 1024 * 1024:
            await source_msg.reply_text("❌ *Hey photo 20MB se badi hai!*", parse_mode="Markdown")
            return

        download_dir = f"downloads_caption_{user.id}_{source_msg.message_id}"
        os.makedirs(download_dir, exist_ok=True)
        
        file = await context.bot.get_file(photo.file_id)
        image_path = os.path.join(download_dir, "image.jpg")
        await file.download_to_drive(image_path)

        # Use Groq AI to generate caption
        import base64
        with open(image_path, 'rb') as img_file:
            image_base64 = base64.b64encode(img_file.read()).decode('utf-8')

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Generate a creative, engaging Instagram-style caption for this image. Include relevant emojis and hashtags. Keep it under 150 characters."
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}"
                        }
                    }
                ]
            }
        ]

        payload = {
                "contents": [{
                    "parts": [
                        {"text": "Extract all the text visible in this image accurately. Preserve formatting where possible. Only return the extracted text, no explanations."},
                        {"inlineData": {"mimeType": "image/jpeg", "data": image_base64}}
                    ]
                }]
            }
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=30.0)
                resp.raise_for_status()
                data = resp.json()
                extracted_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            result = f"📝 *Extracted Text (AI):*\n━━━━━━━━━━━━━━━━━━━━━\n\n{escape_markdown(extracted_text)}"
            await source_msg.reply_text(result, parse_mode="Markdown")

    except Exception as e:
        print(f"OCR error: {e}")
        await source_msg.reply_text(f"❌ *Failed to extract text:* `{e}`", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def tts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Convert text to speech using AI."""
    source_msg = update.effective_message
    user = update.effective_user

    if not GEMINI_API_KEY:
        await source_msg.reply_text("❌ *Hey GEMINI_API_KEY missing hai!* AI features disabled.", parse_mode="Markdown")
        return

    # Get text from command or replied message
    text_to_speak = ""
    if context.args:
        text_to_speak = " ".join(context.args)
    elif source_msg.reply_to_message:
        reply = source_msg.reply_to_message
        if reply.text:
            text_to_speak = reply.text
        elif reply.caption:
            text_to_speak = reply.caption

    if not text_to_speak:
        await source_msg.reply_text(
            "❌ *Hey text toh do!*\n\n"
            "Format:\n"
            "• `/tts <text>`\n"
            "• Reply to a text message with `/tts`",
            parse_mode="Markdown"
        )
        return

    if len(text_to_speak) > 1000:
        await source_msg.reply_text("❌ *The provided text exceeds the 1000 character limit!*", parse_mode="Markdown")
        return

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VOICE)

        download_dir = f"downloads_tts_{user.id}_{source_msg.message_id}"
        os.makedirs(download_dir, exist_ok=True)
        output_path = os.path.join(download_dir, "speech.mp3")

        import gtts
        tts = gtts.gTTS(text=text_to_speak, lang='en')
        tts.save(output_path)

        if os.path.exists(output_path):
            with open(output_path, 'rb') as audio_file:
                await source_msg.reply_voice(voice=audio_file, duration=0)
            await source_msg.reply_text("✅ *Speech generated!* 🔊", parse_mode="Markdown")
        else:
            await source_msg.reply_text("❌ *Hey speech generate nahi ho paya!*", parse_mode="Markdown")

    except Exception as e:
        print(f"TTS error: {e}")
        await source_msg.reply_text(f"❌ *Hey speech generate nahi ho paya:* `{e}`", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save and retrieve personal notes."""
    source_msg = update.effective_message
    user = update.effective_user
    user_id = str(user.id)

    # Load or create notes storage
    notes_file = "user_notes.json"
    try:
        if os.path.exists(notes_file):
            with open(notes_file, 'r', encoding='utf-8') as f:
                all_notes = json.load(f)
        else:
            all_notes = {}
    except:
        all_notes = {}

    if user_id not in all_notes:
        all_notes[user_id] = []

    # Check command arguments
    if not context.args:
        # Show all notes
        if not all_notes[user_id]:
            await source_msg.reply_text(
                "📝 *Hey tumhare paas koi notes nahi hai!*\n\n"
                "Use `/notes <text>` to save a new note.",
                parse_mode="Markdown"
            )
            return

        notes_list = "📝 *Your Notes:*\n━━━━━━━━━━━━━━━━━━━━━\n"
        for i, note in enumerate(all_notes[user_id][-10:], 1):  # Show last 10 notes
            timestamp = note.get('time', 'Unknown')
            text = note.get('text', '')
            notes_list += f"*{i}.* {escape_markdown(text)}\n   _{timestamp}_\n\n"
        
        if len(all_notes[user_id]) > 10:
            notes_list += f"\n_Showing last 10 of {len(all_notes[user_id])} notes_"
        
        await source_msg.reply_text(notes_list, parse_mode="Markdown")
        return

    # Check for delete command
    if context.args[0].lower() == "delete" and len(context.args) > 1:
        try:
            note_index = int(context.args[1]) - 1
            if 0 <= note_index < len(all_notes[user_id]):
                deleted_note = all_notes[user_id].pop(note_index)
                with open(notes_file, 'w', encoding='utf-8') as f:
                    json.dump(all_notes, f, ensure_ascii=False, indent=2)
                await source_msg.reply_text(
                    f"✅ *Note deleted!*\n\n_{escape_markdown(deleted_note['text'])}_",
                    parse_mode="Markdown"
                )
            else:
                await source_msg.reply_text("❌ *Hey invalid note number!*", parse_mode="Markdown")
        except ValueError:
            await source_msg.reply_text("❌ *Hey valid number do!* Example: `/notes delete 1`", parse_mode="Markdown")
        return

    # Check for clear command
    if context.args[0].lower() == "clear":
        all_notes[user_id] = []
        with open(notes_file, 'w', encoding='utf-8') as f:
            json.dump(all_notes, f, ensure_ascii=False, indent=2)
        await source_msg.reply_text("✅ *Saare notes clear kar diye!* 🗑️", parse_mode="Markdown")
        return

    # Save new note
    note_text = " ".join(context.args)
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    all_notes[user_id].append({
        'text': note_text,
        'time': timestamp
    })

    with open(notes_file, 'w', encoding='utf-8') as f:
        json.dump(all_notes, f, ensure_ascii=False, indent=2)

    await source_msg.reply_text(
        f"✅ *Note saved!*\n\n_{escape_markdown(note_text)}_\n\n"
        f"Use `/notes` to view all your notes.",
        parse_mode="Markdown"
    )


async def transcribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not GEMINI_API_KEY:
        await source_msg.reply_text("❌ *Hey GEMINI_API_KEY missing hai!* AI features disabled. Please add it to your environment variables. 🙏", parse_mode="Markdown")
        return

    if not source_msg.reply_to_message:
        await source_msg.reply_text(
            "❌ *Hey usage check karo!*\n\n"
            "Reply to any audio or voice note file with `/transcribe` to extract text!",
            parse_mode="Markdown"
        )
        return

    reply = source_msg.reply_to_message
    target_media = None
    media_type = None

    if reply.audio:
        target_media = reply.audio
        media_type = "audio"
    elif reply.voice:
        target_media = reply.voice
        media_type = "voice"
    elif reply.document:
        mime = reply.document.mime_type or ""
        if mime.startswith("audio/"):
            target_media = reply.document
            media_type = "audio"

    if not target_media:
        await source_msg.reply_text("❌ *Please reply to an audio file or voice note!*", parse_mode="Markdown")
        return

    if target_media.file_size > 20 * 1024 * 1024:
        await source_msg.reply_text("❌ *Hey file 20MB se badi hai!* Telegram custom downloads limit limits bots to 20MB. 😔", parse_mode="Markdown")
        return

    try:
        await context.bot.set_message_reaction(
            chat_id=update.effective_chat.id,
            message_id=source_msg.message_id,
            reaction=[ReactionTypeEmoji("👀")]
        )
    except Exception as re_err:
        print(f"Failed to set message reaction: {re_err}")

    status_msg = await source_msg.reply_text("🎙️ *Downloading audio...* Please wait.", parse_mode="Markdown")
    download_dir = f"downloads_transcribe_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        tg_file = await context.bot.get_file(target_media.file_id)
        filename = "input_audio"
        if hasattr(target_media, "file_name") and target_media.file_name:
            filename = target_media.file_name
        ext = os.path.splitext(filename)[1] or ".mp3"
        
        input_file = os.path.join(download_dir, f"input{ext}")
        await tg_file.download_to_drive(input_file)

        await status_msg.edit_text("🤖 *Transcribing using AI (Whisper)...* ⚡")

        import base64
        with open(input_file, "rb") as file_read:
            audio_base64 = base64.b64encode(file_read.read()).decode('utf-8')

        payload = {
            "contents": [{
                "parts": [
                    {"text": "Transcribe the following audio accurately. Only return the transcription, no explanations."},
                    {"inlineData": {"mimeType": "audio/mp3", "data": audio_base64}}
                ]
            }]
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=60.0)
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        if not text:
            await status_msg.edit_text("❌ *Kuch sunai nahi diya!* Transcription result was empty. 🤷‍♂️")
            return

        if len(text) <= 4000:
            await status_msg.edit_text(f"📝 *Transcription:*\n\n{text}", parse_mode="Markdown")
        else:
            await status_msg.edit_text("📝 *Transcription too long! Sending as text file...*")
            txt_path = os.path.join(download_dir, "transcription.txt")
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text)
            with open(txt_path, "rb") as f:
                await source_msg.reply_document(f, caption="📝 Transcription text file. 🎙️")
            await status_msg.delete()

        track_download(user.id)

    except Exception as e:
        print(f"Transcription Error: {e}")
        await status_msg.edit_text(f"❌ *Transcription failed:* `{e}`")
    finally:
        cleanup(download_dir)


async def qr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not context.args:
        await source_msg.reply_text(
            "❌ *Hey link ya text toh bhej!*\n\nExample: `/qr https://google.com`",
            parse_mode="Markdown"
        )
        return

    text = " ".join(context.args)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_qr_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        import urllib.parse
        import urllib.request
        
        encoded = urllib.parse.quote(text)
        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={encoded}"
        qr_path = os.path.join(download_dir, "qrcode.png")

        req = urllib.request.Request(qr_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as resp, open(qr_path, 'wb') as f:
            shutil.copyfileobj(resp, f)

        if os.path.exists(qr_path) and os.path.getsize(qr_path) > 0:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
            with open(qr_path, 'rb') as photo:
                await source_msg.reply_photo(photo=photo, caption=f"✅ *QR Code Generated!* 🔮\n\n`{text[:100]}`", parse_mode="Markdown")
            track_download(user.id)
        else:
            raise RuntimeError("Failed to generate QR code file.")

    except Exception as e:
        print(f"QR Error: {e}")
        await source_msg.reply_text(f"❌ *Hey QR Code nahi ban paya:* `{e}`", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def short_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not context.args:
        await source_msg.reply_text(
            "❌ *Hey link toh bhej!*\n\nExample: `/short https://github.com`",
            parse_mode="Markdown"
        )
        return

    url = context.args[0]
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        import urllib.request
        import urllib.parse
        import json
        
        short_url = None
        
        try:
            data = urllib.parse.urlencode({'url': url}).encode('utf-8')
            req = urllib.request.Request("https://cleanuri.com/api/v1/shorten", data=data, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                res = json.loads(resp.read().decode('utf-8'))
                short_url = res.get("result_url")
        except Exception as e:
            print(f"CleanUri failed: {e}. Trying is.gd...")

        if not short_url:
            encoded = urllib.parse.quote(url)
            req = urllib.request.Request(f"https://is.gd/create.php?format=json&url={encoded}", headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                res = json.loads(resp.read().decode('utf-8'))
                short_url = res.get("shorturl")

        if short_url:
            await source_msg.reply_text(
                f"🔗 *URL Shortened Successfully!* 🚀\n\n"
                f"📝 *Original:* {url}\n"
                f"⚡ *Shortened:* {short_url}",
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            track_download(user.id)
        else:
            raise RuntimeError("Both APIs failed to shorten the URL.")

    except Exception as e:
        print(f"Shortener Error: {e}")
        await source_msg.reply_text(f"❌ *Hey link short nahi ho paya:* `{e}`", parse_mode="Markdown")


async def voice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not source_msg.reply_to_message:
        await source_msg.reply_text(
            "❌ *Hey usage check karo!*\n\n"
            "Reply to any audio or video file with `/voice` to convert it to a native voice note!",
            parse_mode="Markdown"
        )
        return

    reply = source_msg.reply_to_message
    target_media = None
    media_type = None

    if reply.audio:
        target_media = reply.audio
        media_type = "audio"
    elif reply.voice:
        target_media = reply.voice
        media_type = "voice"
    elif reply.video:
        target_media = reply.video
        media_type = "video"
    elif reply.document:
        mime = reply.document.mime_type or ""
        if mime.startswith("audio/"):
            target_media = reply.document
            media_type = "audio"
        elif mime.startswith("video/"):
            target_media = reply.document
            media_type = "video"

    if not target_media:
        await source_msg.reply_text("❌ *Hey kisi audio ya video file ko reply karo!*", parse_mode="Markdown")
        return

    if target_media.file_size > 20 * 1024 * 1024:
        await source_msg.reply_text("❌ *Hey file 20MB se badi hai!* Telegram custom downloads limit limits bots to 20MB. 😔", parse_mode="Markdown")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_voice_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        tg_file = await context.bot.get_file(target_media.file_id)
        filename = "input_media"
        if hasattr(target_media, "file_name") and target_media.file_name:
            filename = target_media.file_name
        ext = os.path.splitext(filename)[1] or (".mp4" if media_type == "video" else ".mp3")
        
        input_file = os.path.join(download_dir, f"input{ext}")
        await tg_file.download_to_drive(input_file)

        output_ogg = os.path.join(download_dir, "voice.ogg")

        ffmpeg_bin = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            for p in ['/opt/homebrew/bin/ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']:
                if os.path.exists(p):
                    ffmpeg_bin = p
                    break
        
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            raise RuntimeError("FFmpeg not found. Cannot convert to voice note.")

        import subprocess
        cmd = [ffmpeg_bin, '-y', '-i', input_file, '-c:a', 'libopus', '-b:a', '32k', '-vbr', 'on', output_ogg]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        if os.path.exists(output_ogg) and os.path.getsize(output_ogg) > 0:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_AUDIO)
            with open(output_ogg, 'rb') as voice:
                await source_msg.reply_voice(voice, caption="🎙️ Voice note created successfully!")
            track_download(user.id)
        else:
            raise RuntimeError("FFmpeg conversion failed to produce a valid voice note.")

    except Exception as e:
        print(f"Voice Note Error: {e}")
        await source_msg.reply_text(f"❌ *Hey voice note nahi ban paya:* `{e}`", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def effect_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not source_msg.reply_to_message or not context.args:
        await source_msg.reply_text(
            "❌ *Hey usage check karo!*\n\n"
            "Reply to any audio, voice, or video with:\n"
            "`/effect <type>`\n\n"
            "Options:\n"
            "• `chipmunk` — High pitched voice\n"
            "• `deep` — Low pitched voice\n"
            "• `echo` — Delay/Echo effect\n"
            "• `robot` — Metallic vibrato\n"
            "• `bassboost` — Heavy bass\n"
            "• `nightcore` — High speed & high pitch",
            parse_mode="Markdown"
        )
        return

    effect_type = context.args[0].lower().strip()
    valid_effects = {
        "chipmunk": "asetrate=44100*1.5,aresample=44100",
        "deep": "asetrate=44100*0.75,aresample=44100",
        "echo": "aecho=0.8:0.88:60:0.4",
        "robot": "tremolo=f=10:d=0.7",
        "bassboost": "bass=g=15",
        "nightcore": "asetrate=44100*1.25,aresample=44100"
    }

    if effect_type not in valid_effects:
        await source_msg.reply_text(
            "❌ *Hey effect type galat hai!*\n\n"
            "Choose: `chipmunk`, `deep`, `echo`, `robot`, `bassboost`, or `nightcore`.",
            parse_mode="Markdown"
        )
        return

    reply = source_msg.reply_to_message
    target_media = None
    media_type = None

    if reply.audio:
        target_media = reply.audio
        media_type = "audio"
    elif reply.voice:
        target_media = reply.voice
        media_type = "voice"
    elif reply.video:
        target_media = reply.video
        media_type = "video"
    elif reply.document:
        mime = reply.document.mime_type or ""
        if mime.startswith("audio/"):
            target_media = reply.document
            media_type = "audio"
        elif mime.startswith("video/"):
            target_media = reply.document
            media_type = "video"

    if not target_media:
        await source_msg.reply_text("❌ *Hey kisi audio, voice note ya video ko reply karo!*", parse_mode="Markdown")
        return

    if target_media.file_size > 20 * 1024 * 1024:
        await source_msg.reply_text("❌ *Hey file 20MB se badi hai!* Telegram custom downloads limit limits bots to 20MB. 😔", parse_mode="Markdown")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_effect_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        tg_file = await context.bot.get_file(target_media.file_id)
        filename = "input_media"
        if hasattr(target_media, "file_name") and target_media.file_name:
            filename = target_media.file_name
        ext = os.path.splitext(filename)[1] or (".mp4" if media_type == "video" else ".mp3")
        
        input_file = os.path.join(download_dir, f"input{ext}")
        await tg_file.download_to_drive(input_file)

        output_file = os.path.join(download_dir, f"{effect_type}_{filename}")

        ffmpeg_bin = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            for p in ['/opt/homebrew/bin/ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']:
                if os.path.exists(p):
                    ffmpeg_bin = p
                    break
        
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            raise RuntimeError("FFmpeg not found. Cannot apply audio effect.")

        import subprocess
        filter_arg = valid_effects[effect_type]

        if media_type == "video":
            cmd = [
                ffmpeg_bin, '-y', '-i', input_file,
                '-c:v', 'copy',
                '-filter:a', filter_arg,
                '-c:a', 'aac', output_file
            ]
        else:
            cmd = [
                ffmpeg_bin, '-y', '-i', input_file,
                '-filter:a', filter_arg,
                output_file
            ]

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            if media_type == "video":
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)
                with open(output_file, 'rb') as f:
                    await source_msg.reply_video(f, caption=f"🎬 Audio effect *{effect_type.upper()}* applied! 🌟", supports_streaming=True, parse_mode="Markdown")
            else:
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_AUDIO)
                with open(output_file, 'rb') as f:
                    if media_type == "voice":
                        await source_msg.reply_voice(f, caption=f"🎙️ Audio effect *{effect_type.upper()}* applied! ✨", parse_mode="Markdown")
                    else:
                        await source_msg.reply_audio(f, caption=f"🎵 Audio effect *{effect_type.upper()}* applied! ✨", parse_mode="Markdown")
            track_download(user.id)
        else:
            raise RuntimeError("FFmpeg failed to produce the filtered media file.")

    except Exception as e:
        print(f"Effect Error: {e}")
        await source_msg.reply_text(f"❌ *Hey audio effect lag nahi paya:* `{e}`", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def speed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not source_msg.reply_to_message or not context.args:
        await source_msg.reply_text(
            "❌ *Hey usage check karo!*\n\n"
            "Reply to any audio or video file with:\n"
            "`/speed <factor>` (e.g. `/speed 1.5` or `/speed 0.75`)\n\n"
            "Factor must be between `0.5` and `2.0`.",
            parse_mode="Markdown"
        )
        return

    reply = source_msg.reply_to_message
    target_media = None
    media_type = None

    if reply.audio:
        target_media = reply.audio
        media_type = "audio"
    elif reply.voice:
        target_media = reply.voice
        media_type = "voice"
    elif reply.video:
        target_media = reply.video
        media_type = "video"
    elif reply.document:
        mime = reply.document.mime_type or ""
        if mime.startswith("audio/"):
            target_media = reply.document
            media_type = "audio"
        elif mime.startswith("video/"):
            target_media = reply.document
            media_type = "video"

    if not target_media:
        await source_msg.reply_text("❌ *Hey kisi audio ya video file ko reply karo!*", parse_mode="Markdown")
        return

    try:
        factor = float(context.args[0])
        if factor < 0.5 or factor > 2.0:
            raise ValueError()
    except ValueError:
        await source_msg.reply_text("❌ *Hey speed factor `0.5` aur `2.0` ke beech hona chahiye!*", parse_mode="Markdown")
        return

    if target_media.file_size > 20 * 1024 * 1024:
        await source_msg.reply_text("❌ *Hey file 20MB se badi hai!* Telegram custom downloads limit limits bots to 20MB. 😔", parse_mode="Markdown")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_speed_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        tg_file = await context.bot.get_file(target_media.file_id)
        filename = "input_media"
        if hasattr(target_media, "file_name") and target_media.file_name:
            filename = target_media.file_name
        ext = os.path.splitext(filename)[1] or (".mp4" if media_type == "video" else ".mp3")
        
        input_file = os.path.join(download_dir, f"input{ext}")
        await tg_file.download_to_drive(input_file)

        output_file = os.path.join(download_dir, f"speed_{factor}_{filename}")

        ffmpeg_bin = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            for p in ['/opt/homebrew/bin/ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']:
                if os.path.exists(p):
                    ffmpeg_bin = p
                    break
        
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            raise RuntimeError("FFmpeg not found. Cannot change speed.")

        import subprocess
        if media_type == "video":
            cmd = [
                ffmpeg_bin, '-y', '-i', input_file,
                '-filter_complex', f"[0:v]setpts={1.0/factor}*PTS[v];[0:a]atempo={factor}[a]",
                '-map', '[v]', '-map', '[a]',
                '-c:v', 'libx264', '-c:a', 'aac', output_file
            ]
        else:
            cmd = [
                ffmpeg_bin, '-y', '-i', input_file,
                '-filter:a', f"atempo={factor}",
                output_file
            ]

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            if media_type == "video":
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)
                with open(output_file, 'rb') as f:
                    await source_msg.reply_video(f, caption=f"⚡ Speed changed to {factor}x! 🎬", supports_streaming=True)
            else:
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_AUDIO)
                with open(output_file, 'rb') as f:
                    if media_type == "voice":
                        await source_msg.reply_voice(f, caption=f"⚡ Speed changed to {factor}x! 🎙️")
                    else:
                        await source_msg.reply_audio(f, caption=f"⚡ Speed changed to {factor}x! 🎵")
            track_download(user.id)
        else:
            raise RuntimeError("FFmpeg failed to produce the speed-modified file.")

    except Exception as e:
        print(f"Speed Error: {e}")
        await source_msg.reply_text(f"❌ *Hey speed change nahi ho payi:* `{e}`", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def reverse_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not source_msg.reply_to_message:
        await source_msg.reply_text(
            "❌ *Hey usage check karo!*\n\n"
            "Reply to any short video or audio file with `/reverse` to reverse it!",
            parse_mode="Markdown"
        )
        return

    reply = source_msg.reply_to_message
    target_media = None
    media_type = None

    if reply.audio:
        target_media = reply.audio
        media_type = "audio"
    elif reply.voice:
        target_media = reply.voice
        media_type = "voice"
    elif reply.video:
        target_media = reply.video
        media_type = "video"
    elif reply.document:
        mime = reply.document.mime_type or ""
        if mime.startswith("audio/"):
            target_media = reply.document
            media_type = "audio"
        elif mime.startswith("video/"):
            target_media = reply.document
            media_type = "video"

    if not target_media:
        await source_msg.reply_text("❌ *Hey kisi audio ya video file ko reply karo!*", parse_mode="Markdown")
        return

    if target_media.file_size > 20 * 1024 * 1024:
        await source_msg.reply_text("❌ *Hey file 20MB se badi hai!* Telegram custom downloads limit limits bots to 20MB. 😔", parse_mode="Markdown")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_reverse_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        tg_file = await context.bot.get_file(target_media.file_id)
        filename = "input_media"
        if hasattr(target_media, "file_name") and target_media.file_name:
            filename = target_media.file_name
        ext = os.path.splitext(filename)[1] or (".mp4" if media_type == "video" else ".mp3")
        
        input_file = os.path.join(download_dir, f"input{ext}")
        await tg_file.download_to_drive(input_file)

        output_file = os.path.join(download_dir, f"reversed_{filename}")

        ffmpeg_bin = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            for p in ['/opt/homebrew/bin/ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']:
                if os.path.exists(p):
                    ffmpeg_bin = p
                    break
        
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            raise RuntimeError("FFmpeg not found. Cannot reverse media.")

        import subprocess
        if media_type == "video":
            cmd = [
                ffmpeg_bin, '-y', '-i', input_file,
                '-filter_complex', "[0:v]reverse[v];[0:a]areverse[a]",
                '-map', '[v]', '-map', '[a]',
                '-c:v', 'libx264', '-c:a', 'aac', output_file
            ]
        else:
            cmd = [
                ffmpeg_bin, '-y', '-i', input_file,
                '-af', 'areverse',
                output_file
            ]

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            if media_type == "video":
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)
                with open(output_file, 'rb') as f:
                    await source_msg.reply_video(f, caption="🔄 Reversed Video! 🎬", supports_streaming=True)
            else:
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_AUDIO)
                with open(output_file, 'rb') as f:
                    if media_type == "voice":
                        await source_msg.reply_voice(f, caption="🔄 Reversed Voice! 🎙️")
                    else:
                        await source_msg.reply_audio(f, caption="🔄 Reversed Audio! 🎵")
            track_download(user.id)
        else:
            raise RuntimeError("FFmpeg failed to produce the reversed file.")

    except Exception as e:
        print(f"Reverse Error: {e}")
        await source_msg.reply_text(f"❌ *Hey reverse nahi ho paya:* `{e}`", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def boost_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not source_msg.reply_to_message or not context.args:
        await source_msg.reply_text(
            "❌ *Hey usage check karo!*\n\n"
            "Reply to any audio or video file with:\n"
            "`/boost <decibels>` (e.g. `/boost 6` to boost by 6dB, or `/boost 12` to boost by 12dB).",
            parse_mode="Markdown"
        )
        return

    reply = source_msg.reply_to_message
    target_media = None
    media_type = None

    if reply.audio:
        target_media = reply.audio
        media_type = "audio"
    elif reply.voice:
        target_media = reply.voice
        media_type = "voice"
    elif reply.video:
        target_media = reply.video
        media_type = "video"
    elif reply.document:
        mime = reply.document.mime_type or ""
        if mime.startswith("audio/"):
            target_media = reply.document
            media_type = "audio"
        elif mime.startswith("video/"):
            target_media = reply.document
            media_type = "video"

    if not target_media:
        await source_msg.reply_text("❌ *Hey kisi audio ya video file ko reply karo!*", parse_mode="Markdown")
        return

    try:
        db = float(context.args[0])
        if db <= 0 or db > 30:
            raise ValueError()
    except ValueError:
        await source_msg.reply_text("❌ *Hey boost value `1` aur `30` dB ke beech honi chahiye!*", parse_mode="Markdown")
        return

    if target_media.file_size > 20 * 1024 * 1024:
        await source_msg.reply_text("❌ *Hey file 20MB se badi hai!* Telegram custom downloads limit limits bots to 20MB. 😔", parse_mode="Markdown")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_boost_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        tg_file = await context.bot.get_file(target_media.file_id)
        filename = "input_media"
        if hasattr(target_media, "file_name") and target_media.file_name:
            filename = target_media.file_name
        ext = os.path.splitext(filename)[1] or (".mp4" if media_type == "video" else ".mp3")
        
        input_file = os.path.join(download_dir, f"input{ext}")
        await tg_file.download_to_drive(input_file)

        output_file = os.path.join(download_dir, f"boosted_{filename}")

        ffmpeg_bin = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            for p in ['/opt/homebrew/bin/ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']:
                if os.path.exists(p):
                    ffmpeg_bin = p
                    break
        
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            raise RuntimeError("FFmpeg not found. Cannot boost audio.")

        import subprocess
        if media_type == "video":
            cmd = [
                ffmpeg_bin, '-y', '-i', input_file,
                '-c:v', 'copy',
                '-filter:a', f"volume={db}dB",
                '-c:a', 'aac', output_file
            ]
        else:
            cmd = [
                ffmpeg_bin, '-y', '-i', input_file,
                '-filter:a', f"volume={db}dB",
                output_file
            ]

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            if media_type == "video":
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)
                with open(output_file, 'rb') as f:
                    await source_msg.reply_video(f, caption=f"🔊 Audio Boosted by +{db}dB! 🎬", supports_streaming=True)
            else:
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_AUDIO)
                with open(output_file, 'rb') as f:
                    if media_type == "voice":
                        await source_msg.reply_voice(f, caption=f"🔊 Audio Boosted by +{db}dB! 🎙️")
                    else:
                        await source_msg.reply_audio(f, caption=f"🔊 Audio Boosted by +{db}dB! 🎵")
            track_download(user.id)
        else:
            raise RuntimeError("FFmpeg failed to produce the boosted file.")

    except Exception as e:
        print(f"Boost Error: {e}")
        await source_msg.reply_text(f"❌ *Hey volume boost nahi ho paya:* `{e}`", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def compress_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    level = "medium"
    if context.args:
        level = context.args[0].lower().strip()

    if level not in ["low", "medium", "high"]:
        await source_msg.reply_text(
            "❌ *Hey compression level invalid!*\n\n"
            "Usage: `/compress <low/medium/high>` (as reply to video)\n\n"
            "• `low`: Maximum compression (480p resolution, lower file size)\n"
            "• `medium`: Balanced compression (720p resolution)\n"
            "• `high`: Light compression (No scale, high quality)",
            parse_mode="Markdown"
        )
        return

    if not source_msg.reply_to_message:
        await source_msg.reply_text(
            "❌ *Hey usage check karo!*\n\n"
            "Reply to any video file with:\n"
            "`/compress <low/medium/high>`",
            parse_mode="Markdown"
        )
        return

    reply = source_msg.reply_to_message
    target_media = None
    media_type = None

    if reply.video:
        target_media = reply.video
        media_type = "video"
    elif reply.document:
        mime = reply.document.mime_type or ""
        if mime.startswith("video/"):
            target_media = reply.document
            media_type = "video"

    if not target_media:
        await source_msg.reply_text("❌ *Hey kisi video file ko reply karo!*", parse_mode="Markdown")
        return

    if target_media.file_size > 20 * 1024 * 1024:
        await source_msg.reply_text("❌ *Hey video 20MB se badi hai!* Telegram custom downloads limit limits bots to 20MB. 😔", parse_mode="Markdown")
        return

    status_msg = await source_msg.reply_text(f"⏳ *Video compress ho raha hai ({level.upper()})...* Yeh thoda time le sakta hai. 🎬", parse_mode="Markdown")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_compress_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        tg_file = await context.bot.get_file(target_media.file_id)
        filename = "input_video"
        if hasattr(target_media, "file_name") and target_media.file_name:
            filename = target_media.file_name
        ext = os.path.splitext(filename)[1] or ".mp4"
        
        input_file = os.path.join(download_dir, f"input{ext}")
        await tg_file.download_to_drive(input_file)

        output_file = os.path.join(download_dir, f"compressed_{level}_{filename}")

        ffmpeg_bin = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            for p in ['/opt/homebrew/bin/ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']:
                if os.path.exists(p):
                    ffmpeg_bin = p
                    break
        
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            raise RuntimeError("FFmpeg not found. Cannot compress video.")

        import subprocess

        if level == "low":
            cmd = [
                ffmpeg_bin, '-y', '-i', input_file,
                '-vf', 'scale=-2:480',
                '-c:v', 'libx264', '-crf', '30', '-preset', 'superfast',
                '-c:a', 'aac', '-b:a', '64k',
                output_file
            ]
        elif level == "medium":
            cmd = [
                ffmpeg_bin, '-y', '-i', input_file,
                '-vf', 'scale=-2:720',
                '-c:v', 'libx264', '-crf', '26', '-preset', 'superfast',
                '-c:a', 'aac', '-b:a', '128k',
                output_file
            ]
        else:
            cmd = [
                ffmpeg_bin, '-y', '-i', input_file,
                '-c:v', 'libx264', '-crf', '22', '-preset', 'superfast',
                '-c:a', 'aac', '-b:a', '192k',
                output_file
            ]

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)
            old_size = target_media.file_size / (1024 * 1024)
            new_size = os.path.getsize(output_file) / (1024 * 1024)
            with open(output_file, 'rb') as f:
                await source_msg.reply_video(
                    f, 
                    caption=f"⚡ *Compression Complete ({level.upper()})!* 🎬\n\n📉 *Size:* `{old_size:.2f}MB` → `{new_size:.2f}MB`", 
                    supports_streaming=True, 
                    parse_mode="Markdown"
                )
            await status_msg.delete()
            track_download(user.id)
        else:
            raise RuntimeError("FFmpeg compression output file not found or empty.")

    except Exception as e:
        print(f"Compress Error: {e}")
        await status_msg.edit_text(f"❌ *Hey video compress nahi ho paya:* `{e}`")
    finally:
        cleanup(download_dir)


async def filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not source_msg.reply_to_message or not context.args:
        await source_msg.reply_text(
            "❌ *Hey usage check karo!*\n\n"
            "Reply to any photo with:\n"
            "`/filter <gray/blur/edge>`\n\n"
            "Options:\n"
            "• `gray` — Vintage Grayscale\n"
            "• `blur` — Gaussian Blur\n"
            "• `edge` — Edge Detection sketch",
            parse_mode="Markdown"
        )
        return

    reply = source_msg.reply_to_message
    
    target_photo = None
    if reply.photo:
        target_photo = reply.photo[-1]
    elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("image/"):
        target_photo = reply.document

    if not target_photo:
        await source_msg.reply_text("❌ *Hey kisi photo ko reply karo!*", parse_mode="Markdown")
        return

    filter_type = context.args[0].lower().strip()
    if filter_type not in ["gray", "blur", "edge"]:
        await source_msg.reply_text("❌ *Hey filter format invalid! Choose: gray, blur, or edge*", parse_mode="Markdown")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_filter_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        tg_file = await context.bot.get_file(target_photo.file_id)
        input_file = os.path.join(download_dir, "input.jpg")
        await tg_file.download_to_drive(input_file)

        output_file = os.path.join(download_dir, f"{filter_type}_filtered.jpg")

        ffmpeg_bin = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            for p in ['/opt/homebrew/bin/ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']:
                if os.path.exists(p):
                    ffmpeg_bin = p
                    break
        
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            raise RuntimeError("FFmpeg not found. Cannot apply filters.")

        vf_arg = None
        if filter_type == "gray":
            vf_arg = "format=gray"
        elif filter_type == "blur":
            vf_arg = "gblur=sigma=10"
        elif filter_type == "edge":
            vf_arg = "edgedetect"

        import subprocess
        cmd = [ffmpeg_bin, '-y', '-i', input_file, '-vf', vf_arg, output_file]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
            with open(output_file, 'rb') as photo:
                await source_msg.reply_photo(photo, caption=f"🎨 Filter Applied: *{filter_type.upper()}*! ✨", parse_mode="Markdown")
            track_download(user.id)
        else:
            raise RuntimeError("FFmpeg failed to apply image filter.")

    except Exception as e:
        print(f"Filter Error: {e}")
        await source_msg.reply_text(f"❌ *Hey filter lag nahi paya:* `{e}`", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def watermark_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not source_msg.reply_to_message or not context.args:
        await source_msg.reply_text(
            "❌ *Hey usage check karo!*\n\n"
            "Reply to any photo or video with:\n"
            "`/watermark <text>`\n\n"
            "Example: `/watermark MyBrandBot`",
            parse_mode="Markdown"
        )
        return

    watermark_text = " ".join(context.args)
    reply = source_msg.reply_to_message
    
    target_media = None
    media_type = None

    if reply.photo:
        target_media = reply.photo[-1]
        media_type = "photo"
    elif reply.video:
        target_media = reply.video
        media_type = "video"
    elif reply.document:
        mime = reply.document.mime_type or ""
        if mime.startswith("image/"):
            target_media = reply.document
            media_type = "photo"
        elif mime.startswith("video/"):
            target_media = reply.document
            media_type = "video"

    if not target_media:
        await source_msg.reply_text("❌ *Hey kisi photo ya video ko reply karo!*", parse_mode="Markdown")
        return

    if target_media.file_size > 20 * 1024 * 1024:
        await source_msg.reply_text("❌ *Hey file 20MB se badi hai!* Telegram custom downloads limit limits bots to 20MB. 😔", parse_mode="Markdown")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_watermark_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        tg_file = await context.bot.get_file(target_media.file_id)
        filename = "input_media"
        if hasattr(target_media, "file_name") and target_media.file_name:
            filename = target_media.file_name
        ext = os.path.splitext(filename)[1] or (".jpg" if media_type == "photo" else ".mp4")
        
        input_file = os.path.join(download_dir, f"input{ext}")
        await tg_file.download_to_drive(input_file)

        output_file = os.path.join(download_dir, f"watermarked_{filename}")

        ffmpeg_bin = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            for p in ['/opt/homebrew/bin/ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']:
                if os.path.exists(p):
                    ffmpeg_bin = p
                    break
        
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            raise RuntimeError("FFmpeg not found. Cannot apply watermark.")

        escaped_text = escape_ffmpeg_drawtext(watermark_text)

        import subprocess
        if media_type == "photo":
            vf_arg = f"drawtext=text='{escaped_text}':x=w-tw-15:y=h-th-15:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.5"
            cmd = [ffmpeg_bin, '-y', '-i', input_file, '-vf', vf_arg, output_file]
        else:
            vf_arg = f"drawtext=text='{escaped_text}':x=w-tw-20:y=h-th-20:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.4"
            cmd = [
                ffmpeg_bin, '-y', '-i', input_file,
                '-vf', vf_arg,
                '-c:v', 'libx264', '-preset', 'superfast',
                '-c:a', 'copy',
                output_file
            ]

        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if res.returncode != 0:
            err_msg = res.stderr.decode('utf-8', errors='ignore')
            print(f"FFmpeg Watermark Error output: {err_msg}")
            if "libfreetype" in err_msg or "drawtext" in err_msg:
                raise RuntimeError("Your FFmpeg installation does not support text overlay (drawtext/freetype).")
            else:
                raise RuntimeError(f"FFmpeg error: {err_msg[:100]}")

        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            if media_type == "photo":
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
                with open(output_file, 'rb') as photo:
                    await source_msg.reply_photo(photo, caption=f"🖼️ Watermark overlayed successfully! ✨", parse_mode="Markdown")
            else:
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)
                with open(output_file, 'rb') as video:
                    await source_msg.reply_video(video, caption=f"🎬 Watermark overlayed successfully! ✨", supports_streaming=True, parse_mode="Markdown")
            track_download(user.id)
        else:
            raise RuntimeError("FFmpeg failed to produce the watermarked file.")

    except Exception as e:
        print(f"Watermark Error: {e}")
        await source_msg.reply_text(f"❌ *Hey watermark overlay nahi ho paya:* `{e}`", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source_msg = update.effective_message
    user = update.effective_user

    if not source_msg.reply_to_message:
        await source_msg.reply_text(
            "❌ *Hey usage check karo!*\n\n"
            "Reply to any video file with `/mute` to remove its audio track.",
            parse_mode="Markdown"
        )
        return

    reply = source_msg.reply_to_message
    target_media = None
    media_type = None

    if reply.video:
        target_media = reply.video
        media_type = "video"
    elif reply.document:
        mime = reply.document.mime_type or ""
        if mime.startswith("video/"):
            target_media = reply.document
            media_type = "video"

    if not target_media:
        await source_msg.reply_text("❌ *Hey kisi video file ko reply karo!*", parse_mode="Markdown")
        return

    if target_media.file_size > 20 * 1024 * 1024:
        await source_msg.reply_text("❌ *Hey video 20MB se badi hai!* Telegram custom downloads limit limits bots to 20MB. 😔", parse_mode="Markdown")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    download_dir = f"downloads_mute_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        tg_file = await context.bot.get_file(target_media.file_id)
        filename = "input_video"
        if hasattr(target_media, "file_name") and target_media.file_name:
            filename = target_media.file_name
        ext = os.path.splitext(filename)[1] or ".mp4"
        
        input_file = os.path.join(download_dir, f"input{ext}")
        await tg_file.download_to_drive(input_file)

        output_file = os.path.join(download_dir, f"muted_{filename}")

        ffmpeg_bin = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            for p in ['/opt/homebrew/bin/ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']:
                if os.path.exists(p):
                    ffmpeg_bin = p
                    break
        
        if not ffmpeg_bin or not os.path.exists(ffmpeg_bin):
            raise RuntimeError("FFmpeg not found. Cannot mute video.")

        import subprocess
        cmd = [ffmpeg_bin, '-y', '-i', input_file, '-c:v', 'copy', '-an', output_file]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)
            with open(output_file, 'rb') as video:
                await source_msg.reply_video(video, caption="🔇 Video Muted successfully! 🎬", supports_streaming=True)
            track_download(user.id)
        else:
            raise RuntimeError("FFmpeg failed to mute the video.")

    except Exception as e:
        print(f"Mute Error: {e}")
        await source_msg.reply_text(f"❌ *Hey video mute nahi ho paya:* `{e}`", parse_mode="Markdown")
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
            "Hey kya translate karun? Text likho ya kisi message ko reply karo! 🙏"
        )
        return

    try:
        # GoogleTranslator auto-detects source language — no extra API key needed
        translated = GoogleTranslator(source='auto', target='hindi').translate(text_to_translate)
        await update.message.reply_text(f"🌐 Auto → Hindi:\n{translated}")
    except Exception as e:
        print(f"Translation Error: {e}")
        await update.message.reply_text("Hey translation mein error aagaya! 🙏")


# ─── Reminder ─────────────────────────────────────────────────────────────────
# FIX: APScheduler jobs run outside PTB's context — pass `bot` directly, not `context`

async def _send_reminder_job(bot, chat_id: int, message: str):
    await bot.send_message(chat_id=chat_id, text=f"⏰ Yaad dilaya bhai: {message}")


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            "Hey format galat hai! Example: /remind 10m Chai peeni hai"
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
        await update.message.reply_text("Hey time sahi se bata! (30s, 10m, 2h) 🙏")
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
        await update.message.reply_text(
            "🔮 *Oops! Koi link nahi mila...*\n\n"
            "Hey, download karne ke liye directly koi link paste karo ya in commands ko check karo:\n"
            "🎬 `/mp4 <link>` — Download Video\n"
            "🎵 `/mp3 <link>` — Download Audio\n"
            "🔍 `/search <query>` — Search YouTube\n\n"
            "💡 *Tip:* Send `/start` to open the main dashboard menu! 🚀",
            parse_mode="Markdown"
        )
        return

    url = urls[0]
    
    # React to the link message with an emoji
    try:
        await context.bot.set_message_reaction(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
            reaction=[ReactionTypeEmoji("👀")]
        )
    except Exception as re_err:
        print(f"Failed to set message reaction: {re_err}")

    context.args = [url]
    
    # Automatically download and send both video (MP4) and audio (MP3)
    try:
        await mp4_command(update, context)
    except Exception as e:
        print(f"Error in automatic MP4 download: {e}")
        
    try:
        await mp3_command(update, context)
    except Exception as e:
        print(f"Error in automatic MP3 download: {e}")

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


# ─── 6 new social-media features ──────────────────────────────────────────────
# All six are network-only (no ffmpeg, no Groq, no extra deps). They piggyback
# on yt-dlp where possible and fall back to public HTTP endpoints otherwise.

# ─── /playlist — Download every video in a YouTube playlist as MP3 or MP4 ─────

async def playlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: `/playlist <youtube_playlist_url> [mp3|mp4]` (default mp4, max 25 items)."""
    source_msg = update.effective_message
    user = update.effective_user

    if not context.args:
        await source_msg.reply_text(
            "❌ *Hey playlist link toh de!*\n\n"
            "Usage: `/playlist <link> [mp3|mp4]` (max 25 items, default mp4)",
            parse_mode="Markdown",
        )
        return

    url = context.args[0]
    audio_only = False
    if len(context.args) > 1 and context.args[1].lower().strip() in ("mp3", "audio"):
        audio_only = True
    elif len(context.args) > 1 and context.args[1].lower().strip() in ("mp4", "video"):
        audio_only = False

    status_msg = await source_msg.reply_text(
        f"📜 *Playlist scan ho rahi hai...* (max 25 items, mode=`{'MP3' if audio_only else 'MP4'}`)",
        parse_mode="Markdown",
    )
    download_dir = f"downloads_playlist_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        # 1. Flatten the playlist — extract_info with noplaylist=False returns
        # a 'entries' list. We resolve each entry's webpage_url so yt-dlp can
        # then download them one at a time through the same tiered pipeline.
        flat_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "playlistend": 25,
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
        }
        if YOUTUBE_COOKIES_FILE:
            flat_opts["cookiefile"] = YOUTUBE_COOKIES_FILE
        with yt_dlp.YoutubeDL(flat_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = (info or {}).get("entries") or []
        if not entries:
            await status_msg.edit_text("❌ *Hey playlist empty hai ya URL galat hai!*")
            cleanup(download_dir)
            return
        # Keep the count modest so we don't blow the 50 MB Telegram upload
        # limit or hit yt-dlp rate limits.
        entries = entries[:25]

        sent = 0
        failed = 0
        for idx, entry in enumerate(entries, 1):
            entry_url = entry.get("url") or entry.get("webpage_url")
            if not entry_url:
                failed += 1
                continue
            entry_title = (entry.get("title") or f"item {idx}")[:60]
            try:
                await status_msg.edit_text(
                    f"📥 *Downloading {idx}/{len(entries)}*\n`{entry_title}`",
                    parse_mode="Markdown",
                )
                item_dir = os.path.join(download_dir, f"item_{idx}")
                os.makedirs(item_dir, exist_ok=True)
                fpath = await download_via_cobalt(entry_url, item_dir, audio_only)
                if not fpath or not os.path.exists(fpath):
                    failed += 1
                    continue
                # Skip anything over Telegram's 50 MB bot upload limit
                if os.path.getsize(fpath) > 50 * 1024 * 1024:
                    failed += 1
                    continue
                if audio_only:
                    with open(fpath, "rb") as f:
                        await source_msg.reply_audio(f, caption=f"🎵 {entry_title}  ({idx}/{len(entries)})")
                else:
                    with open(fpath, "rb") as f:
                        await source_msg.reply_video(
                            f,
                            caption=f"🎬 {entry_title}  ({idx}/{len(entries)})",
                            supports_streaming=True,
                        )
                sent += 1
                track_download(user.id)
            except Exception as inner_e:
                print(f"Playlist item {idx} failed: {inner_e}")
                failed += 1
                continue

        await status_msg.edit_text(
            f"✅ *Playlist complete!*\n\n"
            f"📤 Sent: *{sent}*\n❌ Failed: *{failed}* (out of {len(entries)})",
            parse_mode="Markdown",
        )
    except Exception as e:
        print(f"Playlist error: {e}")
        await status_msg.edit_text(f"❌ *Hey playlist download nahi ho payi:* `{e}`", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


# ─── /hashtags — Score an Instagram reel link by reach potential ──────────────

_HASHTAG_BANK = {
    "fyp": 1.0, "viral": 1.0, "reels": 0.9, "reel": 0.9, "trending": 0.9,
    "explore": 0.8, "explorepage": 0.9, "instagood": 0.6, "instadaily": 0.6,
    "love": 0.5, "follow": 0.5, "followme": 0.5, "followforfollow": 0.4,
    "funny": 0.7, "comedy": 0.6, "meme": 0.6, "memes": 0.6, "lol": 0.5,
    "music": 0.6, "dance": 0.6, "song": 0.5, "songs": 0.5, "lyrics": 0.5,
    "food": 0.5, "foodie": 0.5, "recipe": 0.5, "cooking": 0.5,
    "fitness": 0.5, "workout": 0.5, "gym": 0.5, "motivation": 0.6,
    "travel": 0.5, "wanderlust": 0.5, "nature": 0.5, "photography": 0.6,
    "fashion": 0.6, "style": 0.6, "ootd": 0.6, "beauty": 0.5,
    "tech": 0.5, "gaming": 0.5, "gamer": 0.5,
    "cricket": 0.6, "football": 0.6, "ipl": 0.7, "bollywood": 0.6,
    "india": 0.5, "desi": 0.5, "hindi": 0.5, "punjabi": 0.5,
}


def _score_hashtags(hashtags):
    """Cheap reach-potential score: weighted count of niche tags minus generic ones."""
    if not hashtags:
        return 0, [], []
    weighted = []
    generic = []
    for tag in hashtags:
        weight = _HASHTAG_BANK.get(tag.lower())
        if weight is None:
            weighted.append(tag)
        elif weight >= 0.5:
            weighted.append(tag)
        else:
            generic.append(tag)
    # 30 hashtag cap, sweet spot 8-15. Beyond that, IG throttles.
    if len(hashtags) > 30:
        length_penalty = -0.5
    elif 8 <= len(hashtags) <= 15:
        length_penalty = 0.5
    else:
        length_penalty = 0.0
    niche_ratio = len(weighted) / max(1, len(hashtags))
    score = round(5.0 + 4.0 * niche_ratio + length_penalty, 1)
    return min(10.0, max(0.0, score)), weighted, generic


async def hashtags_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: `/hashtags <instagram_reel_url>` or `/hashtags <hashtag1 hashtag2 ...>`."""
    source_msg = update.effective_message
    user = update.effective_user

    if not context.args:
        await source_msg.reply_text(
            "❌ *Hey input toh de!*\n\n"
            "Format:\n"
            "• `/hashtags <instagram_reel_url>` — score the post's hashtags\n"
            "• `/hashtags #fyp #viral #reels` — score a hashtag set you plan to use",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    is_url = raw.startswith("http://") or raw.startswith("https://")

    if is_url and "instagram.com" in raw:
        # Pull the caption off the post via instaloader, then extract its hashtags.
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        try:
            m = re.search(r'/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)', raw)
            if not m:
                await source_msg.reply_text("❌ *Hey yeh Instagram shortcode nahi hai!*", parse_mode="Markdown")
                return
            shortcode = m.group(1)
            post = await asyncio.to_thread(instaloader.Post.from_shortcode, L.context, shortcode)
            caption = post.caption or ""
            hashtags = re.findall(r"#(\w+)", caption)
            if not hashtags:
                await source_msg.reply_text("❌ *Is reel pe koi hashtag nahi hai.* 🤷", parse_mode="Markdown")
                return
            source_label = f"the reel `{shortcode}`"
        except instaloader.exceptions.LoginRequiredException:
            await source_msg.reply_text(
                "🔒 *Login chahiye!* INSTA_USERNAME / INSTA_PASSWORD env mein add kar.",
                parse_mode="Markdown",
            )
            return
        except instaloader.exceptions.ProfileNotExistsException:
            await source_msg.reply_text("❌ *Reel exist nahi karti ya private hai.*", parse_mode="Markdown")
            return
        except Exception as e:
            await source_msg.reply_text(f"❌ *Hashtag pull nahi ho paya:* `{e}`", parse_mode="Markdown")
            return
    else:
        # Treat as a raw hashtag list
        hashtags = re.findall(r"#?(\w+)", raw)
        source_label = "your input"

    if not hashtags:
        await source_msg.reply_text("❌ *Koi hashtag nahi mila!*", parse_mode="Markdown")
        return

    score, weighted, generic = _score_hashtags(hashtags)
    if score >= 8:
        verdict = "🔥 *Excellent!* Strong niche mix. Reel should pop in Explore."
    elif score >= 6:
        verdict = "✅ *Solid set.* Reasonable reach potential."
    elif score >= 4:
        verdict = "⚠️ *Meh.* Too many generic tags dilute reach. Swap a few for niche."
    else:
        verdict = "❌ *Weak.* Mostly overused tags — IG throttles these."

    tag_list = ", ".join(f"`#{h}`" for h in hashtags[:30])
    weighted_list = ", ".join(f"`#{h}`" for h in weighted) or "_none_"
    generic_list = ", ".join(f"`#{h}`" for h in generic) or "_none_"

    msg = (
        f"📊 *Hashtag score for* {source_label}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *Score:* *{score}/10*  {verdict}\n\n"
        f"📦 *Total tags:* {len(hashtags)} (IG sweet spot 8–15)\n\n"
        f"🌟 *Niche / high-value ({len(weighted)}):*\n{weighted_list}\n\n"
        f"🔁 *Generic ({len(generic)}):*\n{generic_list}\n\n"
        f"🏷 *All tags:*\n{tag_list}"
    )
    await source_msg.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)
    track_download(user.id)


# ─── /reddit — Pull the top image / video off a subreddit or Reddit post ──────

async def reddit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: `/reddit <subreddit | reddit_post_url>` (default sort: top/week)."""
    source_msg = update.effective_message
    user = update.effective_user

    if not context.args:
        await source_msg.reply_text(
            "❌ *Hey input toh de!*\n\n"
            "• `/reddit pics` — top from r/pics this week\n"
            "• `/reddit https://reddit.com/r/aww/comments/xxx/...`",
            parse_mode="Markdown",
        )
        return

    target = context.args[0].strip()
    # Build a Reddit JSON URL. The .json suffix returns a parseable payload
    # without needing PRAW or an API key.
    if "reddit.com" in target:
        url = target if target.endswith(".json") else re.sub(r"/?$", ".json", target)
    else:
        sub = target.lstrip("r/").strip("/")
        url = f"https://www.reddit.com/r/{sub}/top.json?t=week&limit=10"

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        import urllib.request
        import json as _json
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "TelegramBot:DownloadWorld:v1 (by /u/anonymous)"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
        listing = (payload.get("data") or {}).get("children") or []
        if not listing:
            await source_msg.reply_text("❌ *Kuch nahi mila!* Subreddit private ya empty ho sakta hai.", parse_mode="Markdown")
            return

        # Find the first post whose URL points to a downloadable image or video
        chosen = None
        for child in listing:
            d = (child or {}).get("data") or {}
            u = (d.get("url_overridden_by_dest") or d.get("url") or "").lower()
            if any(u.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".gifv")):
                chosen = d
                break
        if not chosen:
            chosen = (listing[0].get("data") or {})

        title = chosen.get("title", "Reddit post")[:200]
        media_url = chosen.get("url_overridden_by_dest") or chosen.get("url")
        permalink = "https://reddit.com" + (chosen.get("permalink") or "")
        if not media_url:
            await source_msg.reply_text("❌ *Is post ka media URL nahi mila.*", parse_mode="Markdown")
            return

        lower = media_url.lower()
        dl_dir = f"downloads_reddit_{user.id}_{source_msg.message_id}"
        os.makedirs(dl_dir, exist_ok=True)
        try:
            req2 = urllib.request.Request(media_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req2, timeout=30) as resp, open(
                os.path.join(dl_dir, "media.bin"), "wb"
            ) as out:
                shutil.copyfileobj(resp, out)
            local_path = os.path.join(dl_dir, "media.bin")
            size = os.path.getsize(local_path)
            if size > 50 * 1024 * 1024:
                await source_msg.reply_text("❌ *Media 50MB se bada hai, Telegram upload nahi hoga.*", parse_mode="Markdown")
                return

            caption = f"🔴 *r/{chosen.get('subreddit','')}*\n*{title}*\n{permalink}"
            if any(lower.endswith(ext) for ext in (".gif", ".gifv")):
                with open(local_path, "rb") as f:
                    await source_msg.reply_animation(f, caption=caption)
            elif any(lower.endswith(ext) for ext in (".mp4",)):
                with open(local_path, "rb") as f:
                    await source_msg.reply_video(f, caption=caption, supports_streaming=True)
            else:
                with open(local_path, "rb") as f:
                    await source_msg.reply_photo(f, caption=caption)
            track_download(user.id)
        finally:
            cleanup(dl_dir)
    except Exception as e:
        print(f"Reddit error: {e}")
        await source_msg.reply_text(f"❌ *Reddit se fetch nahi ho paya:* `{e}`", parse_mode="Markdown")


# ─── /ycomments — Pull the top comments from a YouTube video ─────────────────

async def ycomments_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: `/ycomments <youtube_video_url>` (shows top 10)."""
    source_msg = update.effective_message
    user = update.effective_user

    if not context.args:
        await source_msg.reply_text(
            "❌ *Hey link toh de!*\n\nExample: `/ycomments https://youtu.be/xxx`",
            parse_mode="Markdown",
        )
        return

    url = context.args[0]
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    fetch_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "getcomments": True,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    if YOUTUBE_COOKIES_FILE:
        fetch_opts["cookiefile"] = YOUTUBE_COOKIES_FILE

    try:
        with yt_dlp.YoutubeDL(fetch_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        comments = (info or {}).get("comments") or []
        if not comments:
            await source_msg.reply_text(
                "❌ *Comments nahi mile.* (video ne comments disable kiye hain ya auth chahiye)",
                parse_mode="Markdown",
            )
            return
        top = comments[:10]
        title = (info or {}).get("title", "Video")
        author = (info or {}).get("uploader") or (info or {}).get("channel") or "Unknown"
        lines = [f"💬 *Top comments on* `{title}`\n📺 *Channel:* {author}\n━━━━━━━━━━━━━━━━━━━━━"]
        for i, c in enumerate(top, 1):
            who = c.get("author") or "anon"
            text = (c.get("text") or "").replace("\n", " ").strip()
            text = text[:280] + ("…" if len(text) > 280 else "")
            likes = c.get("like_count")
            like_str = f"  👍 `{likes}`" if isinstance(likes, int) else ""
            lines.append(f"*{i}.* {who}{like_str}\n   {text}")
        # 4096 char Telegram cap — split if needed
        chunks = []
        cur = ""
        for line in lines:
            if len(cur) + len(line) + 1 > 3900:
                chunks.append(cur)
                cur = line
            else:
                cur = cur + "\n" + line if cur else line
        if cur:
            chunks.append(cur)
        for chunk in chunks:
            await source_msg.reply_text(chunk, parse_mode="Markdown", disable_web_page_preview=True)
        track_download(user.id)
    except Exception as e:
        print(f"ycomments error: {e}")
        await source_msg.reply_text(f"❌ *Comments fetch nahi ho paye:* `{e}`", parse_mode="Markdown")


# ─── /ttslideshow — Download a TikTok slideshow (multi-image post) ────────────

async def ttslideshow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: `/ttslideshow <tiktok_url>` — pulls every image off a TikTok photo post."""
    source_msg = update.effective_message
    user = update.effective_user

    if not context.args:
        await source_msg.reply_text(
            "❌ *Hey TikTok link toh de!*\n\n"
            "Example: `/ttslideshow https://www.tiktok.com/@user/video/xxx`",
            parse_mode="Markdown",
        )
        return

    url = context.args[0]
    status_msg = await source_msg.reply_text("🎞️ *TikTok slideshow scan ho rahi hai...*", parse_mode="Markdown")
    download_dir = f"downloads_ttslideshow_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = (info or {}).get("entries") or []
        if not entries:
            # Photo-mode TikTok: the post itself is the slideshow, and yt-dlp
            # returns the image list under formats[].url or under the
            # 'thumbnails' list. We just bail with a helpful message.
            await status_msg.edit_text(
                "❌ *Slideshow images nahi mile.* (ya toh video hai, ya yt-dlp version outdated)",
                parse_mode="Markdown",
            )
            return

        # Walk the entries — TikTok slideshows come as a sequence of image
        # entries with direct https URLs in the 'url' field.
        sent = 0
        for idx, e in enumerate(entries, 1):
            media_url = e.get("url")
            if not media_url:
                continue
            try:
                import urllib.request
                req = urllib.request.Request(media_url, headers={"User-Agent": "Mozilla/5.0"})
                out = os.path.join(download_dir, f"slide_{idx:02d}.jpg")
                with urllib.request.urlopen(req, timeout=30) as resp, open(out, "wb") as f:
                    shutil.copyfileobj(resp, f)
                with open(out, "rb") as photo:
                    await source_msg.reply_photo(photo, caption=f"🖼️ Slide {idx}")
                sent += 1
            except Exception as inner:
                print(f"ttslideshow slide {idx} failed: {inner}")
                continue

        if sent == 0:
            await status_msg.edit_text("❌ *Koi image download nahi ho payi.*", parse_mode="Markdown")
        else:
            await status_msg.edit_text(
                f"✅ *Slideshow delivered!* Sent *{sent}* image(s). 🎉",
                parse_mode="Markdown",
            )
            track_download(user.id)
    except Exception as e:
        print(f"ttslideshow error: {e}")
        await status_msg.edit_text(f"❌ *Slideshow pull nahi ho paya:* `{e}`", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


# ─── /pinboard — Download every Pin on a public Pinterest board ──────────────

async def pinboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: `/pinboard <pinterest_board_url> [count]` (max 20 images)."""
    source_msg = update.effective_message
    user = update.effective_user

    if not context.args:
        await source_msg.reply_text(
            "❌ *Hey Pinterest board URL de!*\n\n"
            "Example: `/pinboard https://pinterest.com/user/board-name/`",
            parse_mode="Markdown",
        )
        return

    url = context.args[0]
    count = 10
    if len(context.args) > 1:
        try:
            count = max(1, min(20, int(context.args[1])))
        except ValueError:
            pass

    status_msg = await source_msg.reply_text(f"📌 *Pinterest board scan ho rahi hai...* (asking for {count} pins)", parse_mode="Markdown")
    download_dir = f"downloads_pinboard_{user.id}_{source_msg.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        # Pinterest rate-limits anonymous scrapers, so use yt-dlp's built-in
        # pinterest extractor with a permissive format selector. Then resolve
        # each entry's largest image via a separate HEAD/GET.
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "playlistend": count,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = (info or {}).get("entries") or []
        if not entries:
            await status_msg.edit_text("❌ *Board empty hai ya URL galat hai.*", parse_mode="Markdown")
            return
        entries = entries[:count]

        sent = 0
        import urllib.request
        for idx, e in enumerate(entries, 1):
            pin_url = e.get("url") or e.get("webpage_url")
            if not pin_url:
                continue
            try:
                # Pull the pin page HTML to find the largest image URL inside
                # the og:image meta tag — Pinterest embeds it in <meta property>.
                req = urllib.request.Request(
                    pin_url,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; PinterestBot/1.0)"},
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                # og:image is the canonical full-size Pin image
                m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
                if not m:
                    m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html, re.IGNORECASE)
                if not m:
                    continue
                img_url = m.group(1)
                # Pinterest's CDN sometimes serves originals behind /originals/
                img_url = img_url.replace("/236x/", "/736x/").replace("/474x/", "/736x/")
                ext = os.path.splitext(img_url.split("?")[0])[1].lower() or ".jpg"
                if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
                    ext = ".jpg"
                out = os.path.join(download_dir, f"pin_{idx:02d}{ext}")
                req2 = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req2, timeout=30) as resp, open(out, "wb") as f:
                    shutil.copyfileobj(resp, f)
                if os.path.getsize(out) > 50 * 1024 * 1024:
                    continue
                with open(out, "rb") as photo:
                    await source_msg.reply_photo(photo, caption=f"📌 Pin {idx}/{len(entries)}")
                sent += 1
            except Exception as inner:
                print(f"pinboard pin {idx} failed: {inner}")
                continue
        if sent == 0:
            await status_msg.edit_text("❌ *Koi pin download nahi ho paya.* (board private ho sakti hai)", parse_mode="Markdown")
        else:
            await status_msg.edit_text(
                f"✅ *Board fetched!* Sent *{sent}* pin(s). 🎉",
                parse_mode="Markdown",
            )
            track_download(user.id)
    except Exception as e:
        print(f"pinboard error: {e}")
        await status_msg.edit_text(f"❌ *Pinterest board pull nahi ho paya:* `{e}`", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


# ─── Global Error Handler ─────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f"❌ Exception while handling an update: {context.error}")
    if _is_expired_callback_query_error(context.error):
        print(f"ℹ️ Ignoring expired callback query error for update: {update}")
        return
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            f"⚠️ *Hey thoda error aagaya:* `{context.error}`",
            parse_mode="Markdown"
        )

# ─── App Bootstrap ────────────────────────────────────────────────────────────

async def post_init(application: Application):
    # Set bot commands in the menu
    commands = [
        ("start",      "Start the bot"),
        ("help",       "How to use the bot"),
        ("search",     "Search YouTube videos"),
        ("mp4",        "Download video via link"),
        ("mp3",        "Download audio via link"),
        ("extract",    "Extract contact info (phone/email)"),
        ("transcribe", "Transcribe audio to text"),
        ("effect",     "Apply DSP voice changer effects"),
        ("compress",   "Compress video size/resolution"),
        ("watermark",  "Burn watermark on photo/video"),
        ("mute",       "Mute audio track of video"),
        ("trim",       "Trim video link or reply"),
        ("tag",        "Edit audio metadata tags"),
        ("iginfo",     "Instagram profile details"),
        ("qr",         "Generate QR code image"),
        ("short",      "Shorten any link URL"),
        ("voice",      "Convert to native voice note"),
        ("speed",      "Change media tempo speed"),
        ("reverse",    "Play video/audio backward"),
        ("boost",      "Boost audio volume in dB"),
        ("filter",     "Apply photo filters (gray/blur/edge)"),
        ("thumb",      "Hi-res thumbnail download"),
        ("subs",       "Download subtitles (SRT)"),
        ("gif",        "Convert clip to animated GIF"),
        ("stats",      "View your download stats"),
        ("tr",         "Translate to Hindi"),
        ("playlist",   "Download a YouTube playlist (mp3/mp4) 📜"),
        ("hashtags",   "Score an Instagram reel's hashtag reach 📊"),
        ("reddit",     "Pull top image/video from a subreddit 🔴"),
        ("ycomments",  "Show top comments on a YouTube video 💬"),
        ("ttslideshow", "Download every slide of a TikTok slideshow 🎞️"),
        ("pinboard",   "Download all images from a Pinterest board 📌"),
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

# ─── Health Check Server ──────────────────────────────────────────────────────
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is alive!")
    def log_message(self, format, *args):
        return # Quiet logs

def run_health_check():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"✅ Health check server running on port {port}")
    server.serve_forever()

def main():
    # Start health check server in background
    threading.Thread(target=run_health_check, daemon=True).start()

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing! Add it to environment variables.")
        return
    if not GEMINI_API_KEY:
        print("⚠️  GEMINI_API_KEY missing — AI features disabled, bot will still run.")

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

    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("help",       help_command))
    app.add_handler(CommandHandler("stats",      stats_command))
    app.add_handler(CommandHandler("search",     search_command))
    app.add_handler(CommandHandler("mp3",        mp3_command))
    app.add_handler(CommandHandler("mp4",        mp4_command))
    app.add_handler(CommandHandler("extract",    extract_command))
    app.add_handler(CommandHandler("transcribe", transcribe_command))
    app.add_handler(CommandHandler("effect",     effect_command))
    app.add_handler(CommandHandler("compress",   compress_command))
    app.add_handler(CommandHandler("watermark",  watermark_command))
    app.add_handler(CommandHandler("mute",       mute_command))
    app.add_handler(CommandHandler("trim",       trim_command))
    app.add_handler(CommandHandler("tag",        tag_command))
    app.add_handler(CommandHandler("iginfo",     iginfo_command))
    app.add_handler(CommandHandler("qr",         qr_command))
    app.add_handler(CommandHandler("short",      short_command))
    app.add_handler(CommandHandler("voice",      voice_command))
    app.add_handler(CommandHandler("speed",      speed_command))
    app.add_handler(CommandHandler("reverse",    reverse_command))
    app.add_handler(CommandHandler("boost",      boost_command))
    app.add_handler(CommandHandler("filter",     filter_command))
    app.add_handler(CommandHandler("thumb",      thumb_command))
    app.add_handler(CommandHandler("subs",       subs_command))
    app.add_handler(CommandHandler("gif",        gif_command))
    app.add_handler(CommandHandler("translate", translate_command))
    app.add_handler(CommandHandler("tr",        translate_command))
    app.add_handler(CommandHandler("remind",    remind_command))
    app.add_handler(CommandHandler("sticker",   sticker_command))
    app.add_handler(CommandHandler("caption",   caption_command))
    app.add_handler(CommandHandler("ocr",       ocr_command))
    app.add_handler(CommandHandler("tts",       tts_command))
    app.add_handler(CommandHandler("notes",     notes_command))
    app.add_handler(CommandHandler("playlist",  playlist_command))
    app.add_handler(CommandHandler("hashtags",  hashtags_command))
    app.add_handler(CommandHandler("reddit",    reddit_command))
    app.add_handler(CommandHandler("ycomments", ycomments_command))
    app.add_handler(CommandHandler("ttslideshow", ttslideshow_command))
    app.add_handler(CommandHandler("pinboard",  pinboard_command))
    app.add_handler(CallbackQueryHandler(button_callback, pattern="^(mode_|show_)"))
    app.add_handler(CallbackQueryHandler(dl_callback,     pattern="^dl_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    print("🤖 Bot is starting up... waiting for messages.")
    app.run_polling()

if __name__ == "__main__":
    main()

