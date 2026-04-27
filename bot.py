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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
            from requests.cookies import RequestsCookieJar
            jar = RequestsCookieJar()
            ncjar = http.cookiejar.MozillaCookieJar(INSTAGRAM_COOKIES_FILE)
            ncjar.load(ignore_discard=True, ignore_expires=True)
            # Convert to RequestsCookieJar
            for cookie in ncjar:
                jar.set(cookie.name, cookie.value, domain=cookie.domain,
                        path=cookie.path, secure=cookie.secure)
            # Check if sessionid cookie is present
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
    welcome_text = (
        "✨ *Welcome to Everything Downloader!* ✨\n\n"
        "I am a professional media downloader bot. I can download videos and audio from almost anywhere!\n\n"
        "📥 *Quick Commands:*\n"
        "• `/mp4 <link>` - Download Video\n"
        "• `/mp3 <link>` - Download Audio\n"
        "• `/search <query>` - Search YouTube\n"
        "• `/help` - View all features\n\n"
        "🤖 *AI Fun Modes:* (Click below)"
    )
    keyboard = [
        [InlineKeyboardButton("😂 Roast Karo",    callback_data="mode_roast"),
         InlineKeyboardButton("🎤 Shayari Likho",  callback_data="mode_shayari")],
        [InlineKeyboardButton("🎵 Rap Banao",      callback_data="mode_rap"),
         InlineKeyboardButton("🔮 Bhavishya Batao", callback_data="mode_fortune")],
        [InlineKeyboardButton("📝 Story Likho",    callback_data="mode_story"),
         InlineKeyboardButton("🍕 Recipe Batao",   callback_data="mode_recipe")]
    ]
    await update.message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🛠️ *Everything Downloader Help* 🛠️\n\n"
        "🚀 *Media Downloading:*\n"
        "1. Just paste a link from YouTube, Insta, FB, Twitter.\n"
        "2. Or use commands:\n"
        "   • `/mp4 <link>`: High quality video\n"
        "   • `/mp3 <link>`: High quality audio\n"
        "   • `/search <query>`: Search and download from YT\n\n"
        "🤖 *AI Features:*\n"
        "Use `/start` to see AI buttons like Roast, Shayari, Rap, etc.\n\n"
        "🌐 *Translation:*\n"
        "• `/tr <text>`: Translate anything to Hindi.\n\n"
        "⏰ *Reminders:*\n"
        "• `/remind 10m Do work`: Reminds you in 10 minutes.\n\n"
        "📊 *Statistics:*\n"
        "• `/stats`: Check bot usage stats."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

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
    await update.message.reply_text(msg, parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    mode_map = {
        "mode_roast":   ("roast",   "Naam bata jisko roast karna hai! 🔥"),
        "mode_shayari": ("shayari", "Kis topic pe shayari likhun? 📝"),
        "mode_rap":     ("rap",     "Rap ka topic bata, aag laga denge! 🔥🎤"),
        "mode_fortune": ("fortune", "Naam bata, tera bhavishya dekhta hoon! 🔮"),
        "mode_story":   ("story",   "Kis topic pe story likhun? 📝"),
        "mode_recipe":  ("recipe",  "Kaunsi recipe seekhni hai? Ingredients batao! 🍕"),
    }

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
    
    # Base options
    base_opts = {
        'outtmpl': f'{output_path}/%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [progress_hook] if progress_hook else [],
        'socket_timeout': 30, # Prevent "uncomplete" downloads
        'retries': 10,
    }
    
    if cookies_file:
        base_opts['cookiefile'] = cookies_file

    # 1. Try High Quality (Force MP4 merge)
    ydl_opts = base_opts.copy()
    if audio_only:
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    else:
        # Better format string for YouTube high quality
        ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        ydl_opts['merge_output_format'] = 'mp4'
    
    if YOUTUBE_EXTRACTOR_ARGS:
        ydl_opts['extractor_args'] = _parse_extractor_args(YOUTUBE_EXTRACTOR_ARGS)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    except Exception as e:
        print(f"⚠️ HQ failed: {e}. Trying simple format...")
        
        # 2. Try Simple 'best' format
        ydl_opts = base_opts.copy()
        ydl_opts['format'] = 'best'
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)
        except Exception as e2:
            print(f"⚠️ Simple failed: {e2}. Trying absolute fallback...")
            
            # 3. Try: No restrictions, no extractor args
            ydl_opts = base_opts.copy()
            ydl_opts['format'] = 'b' # single best file
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    return ydl.prepare_filename(info)
            except Exception as e3:
                print(f"⚠️ Fallback failed: {e3}. FINAL attempt without cookies...")
                
                # 4. FINAL attempt: No cookies, no restrictions
                ydl_opts = base_opts.copy()
                ydl_opts.pop('cookiefile', None) # STRIP COOKIES
                ydl_opts['format'] = 'best'
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        return ydl.prepare_filename(info)
                except Exception as e4:
                    raise RuntimeError(f"All 4 download tiers failed. Last error: {e4}")


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
    if not context.args:
        await update.message.reply_text(
            "❌ *Bhai link toh bhej!*\n\nExample: `/mp3 https://youtube.com/watch?v=xxx`",
            parse_mode="Markdown"
        )
        return

    url = context.args[0]
    status_msg = await update.message.reply_text("⏳ *Initializing MP3 request...*", parse_mode="Markdown")
    download_dir = f"downloads_mp3_{update.effective_user.id}_{update.message.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        loop = asyncio.get_running_loop()
        hook = progress_hook_factory(loop, context.bot, update.effective_chat.id, status_msg.message_id)
        await asyncio.to_thread(download_video, url, download_dir, True, YOUTUBE_COOKIES_FILE, hook)

        # yt-dlp converts to .mp3 after postprocessing — glob for it
        mp3_files = glob.glob(f"{download_dir}/*.mp3")
        if not mp3_files:
            await status_msg.edit_text("❌ *Bhai MP3 nahi bani. Link check kar!* 😔", parse_mode="Markdown")
            return

        file_path = mp3_files[0]
        if os.path.getsize(file_path) <= 50 * 1024 * 1024:
            await status_msg.edit_text("📤 *Uploading MP3...*", parse_mode="Markdown")
            with open(file_path, 'rb') as audio:
                await update.message.reply_audio(audio, caption="Enjoy your music! 🎵")
            track_download(update.effective_user.id)
            await status_msg.delete()
        else:
            await status_msg.edit_text("❌ *Bhai MP3 50MB se badi hai!* 😔", parse_mode="Markdown")

    except Exception as e:
        print(f"MP3 Error: {e}")
        await status_msg.edit_text("❌ *Bhai error aagaya MP3 banane mein.* 🙏", parse_mode="Markdown")
    finally:
        cleanup(download_dir)


async def mp4_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "❌ *Bhai link toh bhej!*\n\nExample: `/mp4 https://youtube.com/watch?v=xxx`",
            parse_mode="Markdown"
        )
        return

    url = context.args[0]
    status_msg = await update.message.reply_text("⏳ *Initializing Video request...*", parse_mode="Markdown")
    download_dir = f"downloads_mp4_{update.effective_user.id}_{update.message.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        loop = asyncio.get_running_loop()
        hook = progress_hook_factory(loop, context.bot, update.effective_chat.id, status_msg.message_id)
        file_path = await asyncio.to_thread(download_video, url, download_dir, False, YOUTUBE_COOKIES_FILE, hook)

        if not os.path.exists(file_path):
            base = os.path.splitext(file_path)[0]
            candidates = glob.glob(f"{base}.*")
            file_path = candidates[0] if candidates else file_path

        if file_path and os.path.exists(file_path):
            if os.path.getsize(file_path) <= 50 * 1024 * 1024:
                await status_msg.edit_text("📤 *Uploading Video...*", parse_mode="Markdown")
                with open(file_path, 'rb') as video:
                    await update.message.reply_video(video, caption="Your video is ready! 🎬")
                track_download(update.effective_user.id)
                await status_msg.delete()
            else:
                await status_msg.edit_text("❌ *Bhai video 50MB se badi hai!* 😔", parse_mode="Markdown")
        else:
            await status_msg.edit_text("❌ *Bhai file nahi mili download ke baad.* 😔", parse_mode="Markdown")

    except Exception as e:
        print(f"MP4 Error: {e}")
        await status_msg.edit_text("❌ *Bhai error aagaya video download karne mein.* 🙏", parse_mode="Markdown")
    finally:
        cleanup(download_dir)

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🔍 *Kya search karna hai?*\n\nExample: `/search divine gully gang`", parse_mode="Markdown")
        return
    
    query = " ".join(context.args)
    status_msg = await update.message.reply_text(f"🔍 *Searching for:* `{query}`...", parse_mode="Markdown")
    
    try:
        # Search using yt-dlp
        ydl_opts = {'quiet': True, 'no_warnings': True, 'format': 'best', 'noplaylist': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, f"ytsearch1:{query}", download=False)
            if not info or 'entries' not in info or not info['entries']:
                await status_msg.edit_text("❌ *Kuch nahi mila!* 😔")
                return
            
            entry = info['entries'][0]
            url = entry['webpage_url']
            title = entry.get('title', 'Video')
            duration = entry.get('duration_string', 'N/A')
            
            # Store URL in user_data
            context.user_data["current_url"] = url

            keyboard = [
                [
                    InlineKeyboardButton("🎬 Video (MP4)", callback_data="dl_mp4"),
                    InlineKeyboardButton("🎵 Audio (MP3)", callback_data="dl_mp3")
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
    # Store URL in user_data to avoid Telegram's 64-character button limit
    context.user_data["current_url"] = url
    
    keyboard = [
        [
            InlineKeyboardButton("🎬 Video (MP4)", callback_data="dl_mp4"),
            InlineKeyboardButton("🎵 Audio (MP3)", callback_data="dl_mp3")
        ]
    ]
    await update.message.reply_text(
        "✨ *Link Detected!*\n\nWhat would you like to do with this link?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def dl_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    url = context.user_data.get("current_url")
    if not url:
        await query.edit_message_text("❌ *Error:* Link not found in memory. Please send the link again.")
        return

    data = query.data
    context.args = [url]
    
    if data == "dl_mp4":
        await mp4_command(update, context)
    elif data == "dl_mp3":
        await mp3_command(update, context)


# ─── Global Error Handler ─────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f"❌ Exception while handling an update: {context.error}")
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            f"⚠️ *Bhai thoda error aagaya:* `{context.error}`",
            parse_mode="Markdown"
        )

# ─── App Bootstrap ────────────────────────────────────────────────────────────

async def post_init(application: Application):
    setup_instaloader_session()
    
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


async def main_async():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing! Add it to environment variables.")
        return
    if not GROQ_API_KEY:
        print("⚠️  GROQ_API_KEY missing — AI features disabled, bot will still run.")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("help",      help_command))
    app.add_handler(CommandHandler("stats",     stats_command))
    app.add_handler(CommandHandler("search",    search_command))
    app.add_handler(CommandHandler("mp3",       mp3_command))
    app.add_handler(CommandHandler("mp4",       mp4_command))
    app.add_handler(CommandHandler("translate", translate_command))
    app.add_handler(CommandHandler("tr",        translate_command))
    app.add_handler(CommandHandler("remind",    remind_command))
    app.add_handler(CallbackQueryHandler(button_callback, pattern="^mode_"))
    app.add_handler(CallbackQueryHandler(dl_callback,     pattern="^dl_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    print("🤖 Bot is starting up... waiting for messages.")
    
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        # Run until the program is stopped
        await asyncio.Event().wait()


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
