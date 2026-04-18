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
    \"\"\"
    Login priority:
      1. Session file (platform-specific location)
      2. Instagram cookies file (INSTAGRAM_COOKIES_FILE)
      3. Username + Password from env
      4. Anonymous (public posts only, rate-limited heavily)
    \"\"\"
    if not INSTA_USERNAME:
        print("Warning: INSTA_USERNAME missing — using anonymous session (rate-limits likely)")
        return

    # Get the correct session file path where instaloader actually saves it
    # On Windows: %LOCALAPPDATA%\\Instaloader\\session-USERNAME
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
            has_session = \"sessionid\" in set(jar.keys())
            if has_session:
                L.context._session.cookies = jar
                print(f"✅ Instaloader: logged in via cookies ({len(jar)} cookies loaded)")
                return
            else:
                print(\"⚠️  Instagram cookies file has no sessionid — not logged in\")
        except Exception as e:
            print(f\"⚠️  Failed to load Instagram cookies: {e}\")
            traceback.print_exc()

    # 3. Try password login
    if INSTA_PASSWORD:
        try:
            L.login(INSTA_USERNAME, INSTA_PASSWORD)
            os.makedirs(os.path.dirname(session_file), exist_ok=True)
            L.save_session_to_file(session_file)
            print(f\"✅ Instaloader: logged in as @{INSTA_USERNAME}, session saved.\")
            return
        except Exception as e:
            error_msg = str(e).lower()
            if \"checkpoint\" in error_msg or \"challenge\" in error_msg:
                print(f\"❌ Instagram checkpoint required!\")
                print(f\"   Your account needs additional verification.\")
                print(f\"   Steps to fix:\")
                print(f\"   1. Log into https://instagram.com in your browser\")
                print(f\"   2. Complete any security challenges\")
                print(f\"   3. Or use cookies file instead of password:\")
                print(f\"      Export Instagram cookies and set INSTAGRAM_COOKIES_FILE\")
            else:
                print(f\"❌ Instaloader login failed: {e}\")

    # 4. Fallback to anonymous
    print(\"⚠️  Falling back to anonymous session (rate-limited).\")

# ─── Scheduler ───────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler()

# ─────────────────────────────────────────────────────────────────────────────
# HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        \"Hello bhai! 👋\\n\\n\"
        \"Main tera all-in-one Telegram bot hoon.\\n\\n\"
        \"📥 *Media Downloader*\\n\"
        \"Mujhe kisi bhi YouTube, Instagram, Twitter (X), ya Facebook video ka link bhej \"
        \"aur main tujhe media bhej dunga (<50MB).\\n\\n\"
        \"🤖 *AI Fun Modes*\\n\"
        \"Niche wale buttons pe click karke AI ke maze le!\"
    )
    keyboard = [
        [InlineKeyboardButton(\"😂 Roast Karo\",    callback_data=\"mode_roast\"),
         InlineKeyboardButton(\"🎤 Shayari Likho\",  callback_data=\"mode_shayari\")],
        [InlineKeyboardButton(\"🎵 Rap Banao\",      callback_data=\"mode_rap\"),
         InlineKeyboardButton(\"🔮 Bhavishya Batao\", callback_data=\"mode_fortune\")],
        [InlineKeyboardButton(\"📝 Story Likho\",    callback_data=\"mode_story\"),
         InlineKeyboardButton(\"🍕 Recipe Batao\",   callback_data=\"mode_recipe\")]
    ]
    await update.message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=\"Markdown\"
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    mode_map = {
        \"mode_roast\":   (\"roast\",   \"Naam bata jisko roast karna hai! 🔥\"),
        \"mode_shayari\": (\"shayari\", \"Kis topic pe shayari likhun? 📝\"),
        \"mode_rap\":     (\"rap\",     \"Rap ka topic bata, aag laga denge! 🔥🎤\"),
        \"mode_fortune\": (\"fortune\", \"Naam bata, tera bhavishya dekhta hoon! 🔮\"),
        \"mode_story\":   (\"story\",   \"Kis topic pe story likhun? 📝\"),
        \"mode_recipe\":  (\"recipe\",  \"Kaunsi recipe seekhni hai? Ingredients batao! 🍕\"),
    }

    if query.data in mode_map:
        mode, prompt_text = mode_map[query.data]
        context.user_data[\"mode\"] = mode
        await query.edit_message_text(prompt_text)

# ─── AI ──────────────────────────────────────────────────────────────────────

async def ask_ai(prompt: str, system_prompt: str) -> str:
    if not groq_client:
        return \"Bhai GROQ_API_KEY missing hai! Railway Dashboard mein add kar do. 🙏\"
    try:
        chat_completion = await groq_client.chat.completions.create(
            messages=[
                {\"role\": \"system\", \"content\": system_prompt},
                {\"role\": \"user\",   \"content\": prompt}
            ],
            model=\"llama-3.1-8b-instant\",
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        print(f\"Groq API Error: {e}\")
        return \"Bhai Groq AI mein thodi dikkat aa rahi hai. Baad mein try karna! 🙏\"

async def handle_ai_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, user_text: str):
    prompts = {
        \"roast\":   {
            \"system\": \"You are a savage, funny Indian roaster. Roast the person named in the prompt in exactly 4 lines using Hinglish. Be hilarious but don't cross community guidelines.\",
            \"user\":   f\"Roast this person: {user_text}\"
        },
        \"shayari\": {
            \"system\": \"You are a master Mirza Ghalib style poet but you write in Hinglish. Write a 4 line beautiful or funny shayari about the topic given.\",
            \"user\":   f\"Topic: {user_text}\"
        },
        \"rap\":     {
            \"system\": \"You are an Indian underground rapper like Divine or Emiway. Write an energetic desi Hindi rap with rhymes in exactly 8 lines using Hinglish about the given topic.\",
            \"user\":   f\"Topic: {user_text}\"
        },
        \"fortune\": {
            \"system\": \"You are a funny Indian jyotishi (astrologer). Tell a humorous 3-4 line fortune in Hinglish for the given name. Make it absurd and funny.\",
            \"user\":   f\"Name: {user_text}\"
        },
        \"story\":   {
            \"system\": \"You are a creative storyteller. Write a short, engaging 10-line story in Hinglish about the given topic. Make it interesting and desi.\",
            \"user\":   f\"Topic: {user_text}\"
        },
        \"recipe\":  {
            \"system\": \"You are a Desi Chef. Provide a simple and tasty recipe in Hinglish with clear steps based on the ingredients or dish name provided. Use a friendly, 'Bhai' style tone.\",
            \"user\":   f\"Recipe/Ingredients: {user_text}\"
        },
    }

    if mode in prompts:
        msg = await update.message.reply_text(\"Typing... 🤖\")
        response = await ask_ai(prompts[mode][\"user\"], prompts[mode][\"system\"])
        await msg.edit_text(response)
        context.user_data.pop(\"mode\", None)

# ─── Downloads ───────────────────────────────────────────────────────────────

def _parse_extractor_args(args_str: str) -> dict:
    \"\"\"Parse YOUTUBE_EXTRACTOR_ARGS into yt-dlp format.

    Format: \"youtubetab:skip=webpage\" \"youtube:player_skip=webpage,configs;visitor_data=VALUE\"
    Returns: {'youtubetab': {'skip': 'webpage'}, 'youtube': {'player_skip': 'webpage,configs', 'visitor_data': 'VALUE'}}
    \"\"\"
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


def download_video(url: str, output_path: str, audio_only: bool = False, cookies_file: str = None) -> str:
    \"\"\"Blocking yt-dlp download — run via asyncio.to_thread.\"\"\"
    ydl_opts = {
        'format': 'bestaudio/best' if audio_only else 'best[filesize<50M]/best',
        'outtmpl': f'{output_path}/%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
    }
    if cookies_file:
        ydl_opts['cookiefile'] = cookies_file
    if YOUTUBE_EXTRACTOR_ARGS:
        extractor_args = _parse_extractor_args(YOUTUBE_EXTRACTOR_ARGS)
        if extractor_args:
            ydl_opts['extractor_args'] = extractor_args
            print(f\"🔧 Using extractor args: {extractor_args}\")
    if audio_only:
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info_dict)   # caller must handle ext swap for audio


def download_instagram(url: str, output_path: str):
    \"\"\"
    Blocking instaloader download with retry on rate-limit (401).
    Raises on final failure so the caller can surface a proper error message.
    \"\"\"
    match = re.search(r'/(?:p|reel|tv)/([A-Za-z0-9_-]+)', url)
    if not match:
        raise ValueError(\"Invalid Instagram URL — shortcode not found\")

    shortcode = match.group(1)
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            L.download_post(post, target=output_path)
            return  # success
        except instaloader.exceptions.LoginRequiredException:
            raise RuntimeError(
                \"Yeh post private hai ya login chahiye! \"
                \"INSTA_USERNAME / INSTA_PASSWORD env mein add kar.\"
            )
        except Exception as e:
            err_str = str(e).lower()
            is_rate_limit = (
                \"401\" in err_str
                or \"please wait\" in err_str
                or \"429\" in err_str
                or \"checkpoint\" in err_str
            )
            if is_rate_limit and attempt < max_retries:
                wait = 60 * attempt          # 60s → 120s → 180s
                print(f\"Instagram rate-limit (attempt {attempt}/{max_retries}), waiting {wait}s…\")
                import time; time.sleep(wait)
            else:
                raise RuntimeError(f\"Instagram download failed: {e}\")


def cleanup(path: str):
    try:
        if os.path.exists(path):
            shutil.rmtree(path)
    except Exception as e:
        print(f\"Cleanup Error: {e}\")

# ─── Commands ─────────────────────────────────────────────────────────────────

async def mp3_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            \"Bhai link toh bhej! Example: /mp3 https://youtube.com/watch?v=xxx\"
        )
        return

    url = context.args[0]
    status_msg = await update.message.reply_text(\"⏳ MP3 ban raha hai... thoda ruk bhai!\")
    download_dir = f\"downloads_mp3_{update.effective_user.id}_{update.message.message_id}\"
    os.makedirs(download_dir, exist_ok=True)

    try:
        await asyncio.to_thread(download_video, url, download_dir, True, YOUTUBE_COOKIES_FILE)

        # yt-dlp converts to .mp3 after postprocessing — glob for it
        mp3_files = glob.glob(f\"{download_dir}/*.mp3\")
        if not mp3_files:
            await status_msg.edit_text(\"Bhai MP3 nahi bani. Link check kar! 😔\")
            return

        file_path = mp3_files[0]
        if os.path.getsize(file_path) <= 50 * 1024 * 1024:
            with open(file_path, 'rb') as audio:
                await update.message.reply_audio(audio)
            await status_msg.delete()
        else:
            await status_msg.edit_text(\"Bhai MP3 50MB se badi hai! 😔\")

    except Exception as e:
        print(f\"MP3 Error: {e}\")
        await status_msg.edit_text(\"Bhai error aagaya MP3 banane mein. 🙏\")
    finally:
        cleanup(download_dir)


async def translate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_to_translate = \"\"
    if context.args:
        text_to_translate = \" \".join(context.args)
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        text_to_translate = update.message.reply_to_message.text
    else:
        await update.message.reply_text(
            \"Bhai kya translate karun? Text likho ya kisi message ko reply karo! 🙏\"
        )
        return

    try:
        # GoogleTranslator auto-detects source language — no extra API key needed
        translated = GoogleTranslator(source='auto', target='hindi').translate(text_to_translate)
        await update.message.reply_text(f\"🌐 Auto → Hindi:\\n{translated}\")
    except Exception as e:
        print(f\"Translation Error: {e}\")
        await update.message.reply_text(\"Bhai translation mein error aagaya! 🙏\")


# ─── Reminder ─────────────────────────────────────────────────────────────────
# FIX: APScheduler jobs run outside PTB's context — pass `bot` directly, not `context`

async def _send_reminder_job(bot, chat_id: int, message: str):
    await bot.send_message(chat_id=chat_id, text=f\"⏰ Yaad dilaya bhai: {message}\")


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            \"Bhai format galat hai! Example: /remind 10m Chai peeni hai\"
        )
        return

    time_val = context.args[0]
    remind_text = \" \".join(context.args[1:])

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
        await update.message.reply_text(\"Bhai time sahi se bata! (30s, 10m, 2h) 🙏\")
        return

    run_date = update.message.date + timedelta(seconds=seconds)
    scheduler.add_job(
        _send_reminder_job,
        'date',
        run_date=run_date,
        args=[context.bot, update.effective_chat.id, remind_text]  # bot, not context
    )
    await update.message.reply_text(f\"Done bhai! {time_val} baad yaad dila dunga. 👍\")


# ─── Main message handler ────────────────────────────────────────────────────

URL_PATTERN = re.compile(
    r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    mode = context.user_data.get(\"mode\")

    if mode:
        await handle_ai_mode(update, context, mode, user_text)
        return

    urls = re.findall(URL_PATTERN, user_text)
    if not urls:
        await update.message.reply_text(
            \"Bhai samajh nahi aaya! Koi link bhej video download ke liye ya \"
            \"/start likh maze karne ke liye! 🙏\"
        )
        return

    url = urls[0]
    status_msg = await update.message.reply_text(\"⏳ Download ho raha hai... ruk bhai!\")
    download_dir = f\"downloads_{update.effective_user.id}_{update.message.message_id}\"
    os.makedirs(download_dir, exist_ok=True)

    try:
        if \"instagram.com\" in url:
            try:
                await asyncio.to_thread(download_instagram, url, download_dir)
            except RuntimeError as e:
                await status_msg.edit_text(f\"❌ {e}\")
                return

            files = glob.glob(f\"{download_dir}/**/*\", recursive=True) + glob.glob(f\"{download_dir}/*\")
            media_sent = False

            for f in files:
                if not os.path.isfile(f):
                    continue
                ext = f.rsplit(\".\", 1)[-1].lower()
                if ext in ('mp4', 'jpg', 'jpeg', 'png', 'webp'):
                    if os.path.getsize(f) <= 50 * 1024 * 1024:
                        with open(f, 'rb') as sf:
                            if ext == 'mp4':
                                await update.message.reply_video(sf)
                            else:
                                await update.message.reply_photo(sf)
                            media_sent = True

            if not media_sent:
                await status_msg.edit_text(
                    \"Bhai media nahi mili ya 50MB se badi hai. 😔\\n\"
                    \"Private post hai toh INSTA_USERNAME add kar env mein.\"
                )
            else:
                await status_msg.delete()

        elif any(d in url for d in (\"youtube.com\", \"youtu.be\", \"twitter.com\", \"x.com\", \"facebook.com\", \"fb.watch\")):
            try:
                file_path = await asyncio.to_thread(download_video, url, download_dir, False, YOUTUBE_COOKIES_FILE)

                # yt-dlp sometimes gives wrong ext in prepare_filename; find actual file
                if not os.path.exists(file_path):
                    base = os.path.splitext(file_path)[0]
                    candidates = glob.glob(f\"{base}.*\")
                    file_path = candidates[0] if candidates else file_path

                if file_path and os.path.exists(file_path):
                    if os.path.getsize(file_path) <= 50 * 1024 * 1024:
                        with open(file_path, 'rb') as video:
                            await update.message.reply_video(video)
                        await status_msg.delete()
                    else:
                        await status_msg.edit_text(
                            \"Bhai video 50MB se badi hai, main sirf 50MB tak ka bhej sakta hoon! 😔\"
                        )
                else:
                    await status_msg.edit_text(\"Bhai file nahi mili download ke baad. 😔\")

            except Exception as e:
                print(f\"yt-dlp error: {e}\")
                await status_msg.edit_text(
                    \"Bhai video download nahi hui. Private ya age-restricted ho sakti hai! 😔\"
                )
        else:
            await status_msg.edit_text(
                \"Bhai is platform ka link abhi support nahi karta. \"
                \"Sirf YT, Insta, Twitter aur FB bhej! 🙏\"
            )

    except Exception as e:
        print(f\"Download Error: {e}\")
        await status_msg.edit_text(
            \"Bhai error aagaya download karne mein. Valid link bhej doosri baar dekhte hain! 🤕\"
        )
    finally:
        cleanup(download_dir)


# ─── App Bootstrap ────────────────────────────────────────────────────────────

async def post_init(application: Application):
    setup_instaloader_session()
    if not scheduler.running:
        scheduler.start()
    print(\"✅ Bot ready — scheduler started, instaloader configured.\")


def main():
    if not BOT_TOKEN:
        print(\"❌ BOT_TOKEN missing! Add it to environment variables.\")
        return
    if not GROQ_API_KEY:
        print(\"⚠️  GROQ_API_KEY missing — AI features disabled, bot will still run.\")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler(\"start\",     start))
    app.add_handler(CommandHandler(\"mp3\",       mp3_command))
    app.add_handler(CommandHandler(\"translate\", translate_command))
    app.add_handler(CommandHandler(\"tr\",        translate_command))
    app.add_handler(CommandHandler(\"remind\",    remind_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print(\"🤖 Bot is starting up... waiting for messages.\")
    app.run_polling()


if __name__ == \"__main__\":
    main()
