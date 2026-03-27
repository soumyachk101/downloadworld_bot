import os
import re
import glob
import asyncio
import shutil
import subprocess
from datetime import timedelta
from dotenv import load_dotenv

# Telegram libraries
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from telegram.error import Conflict

# Third-party libraries
import yt_dlp
import instaloader
from groq import AsyncGroq
from deep_translator import GoogleTranslator
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- CONFIGURATION & INITIALIZATION ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# AI Client
groq_client = None
if GROQ_API_KEY:
    groq_client = AsyncGroq(api_key=GROQ_API_KEY)
else:
    print("Warning: GROQ_API_KEY is missing. AI features will not work.")

# Instagram Client
L = instaloader.Instaloader(
    download_pictures=True,
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False
)

# Global Scheduler
scheduler = AsyncIOScheduler()

# --- UTILITIES ---

def cleanup(path: str):
    """Safely remove a directory."""
    try:
        if os.path.exists(path):
            shutil.rmtree(path)
    except Exception as e:
        print(f"Cleanup Error: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Gracefully handle bot-level errors, specifically Conflict."""
    error = context.error
    if isinstance(error, Conflict):
        print("⚠️ Conflict: Another instance is running. Retrying in 10s...")
        await asyncio.sleep(10)
    else:
        print(f"❌ Bot Error: {error}")

# --- AI LOGIC ---

async def ask_ai(prompt: str, system_prompt: str) -> str:
    """Helper to query Groq AI."""
    if not groq_client:
        return "Bhai, Groq API Key missing hai Railway dashboard mein! 🙏"
    try:
        chat_completion = await groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            model="llama-3.1-8b-instant",
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        print(f"Groq API Error: {e}")
        return "Bhai, AI abhi thoda busy hai. Baad mein try kar! 🙏"

# --- DOWNLOAD LOGIC ---

def download_video(url: str, output_path: str, audio_only: bool = False):
    """Common download function for YouTube, Twitter, FB via yt-dlp."""
    ydl_opts = {
        # Robust format selection: stay under 45MB if possible for Telegram's 50MB limit
        'format': 'bestvideo[filesize<45M]+bestaudio/best[filesize<45M]' if not audio_only else 'bestaudio/best',
        'outtmpl': f'{output_path}/%(id)s.%(ext)s',
        'quiet': False,
        'no_warnings': False,
        'verbose': True,
        'max_filesize': 50 * 1024 * 1024, # 50MB hard limit
    }
    
    # Check for cookies.txt
    if os.path.exists("cookies.txt"):
        ydl_opts['cookiefile'] = "cookies.txt"
        print("DEBUG: Using cookies.txt for authentication.")

    if audio_only:
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
        
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info_dict)

def download_instagram(url: str, output_path: str):
    """Download Instagram posts/reels via Instaloader."""
    try:
        match = re.search(r'/p/([A-Za-z0-9_-]+)', url) or re.search(r'/reel/([A-Za-z0-9_-]+)', url)
        shortcode = match.group(1) if match else None
        if not shortcode:
            raise ValueError("Invalid Instagram URL")
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        L.download_post(post, target=output_path)
    except Exception as e:
        print(f"Instaloader Error: {e}")

# --- COMMAND HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "Hello bhai! 👋 Main tera all-in-one Telegram bot hoon.\n\n"
        "📥 *Media Downloader*\n"
        "Mujhe kisi bhi YouTube, Instagram, Twitter (X), ya Facebook link bhej aur main media bhej dunga (<50MB).\n\n"
        "🤖 *AI Fun Mode*\n"
        "Niche wale buttons pe click karke AI ke maze le!"
    )
    keyboard = [
        [InlineKeyboardButton("😂 Roast Karo", callback_data="mode_roast"),
         InlineKeyboardButton("🎤 Shayari Likho", callback_data="mode_shayari")],
        [InlineKeyboardButton("🎵 Rap Banao", callback_data="mode_rap"),
         InlineKeyboardButton("🔮 Bhavishya Batao", callback_data="mode_fortune")],
        [InlineKeyboardButton("📝 Story Likho", callback_data="mode_story"),
         InlineKeyboardButton("🍕 Recipe Batao", callback_data="mode_recipe")]
    ]
    await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def mp3_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Bhai link toh bhej! Example: /mp3 [link]")
        return
        
    url = context.args[0]
    status_msg = await update.message.reply_text("⏳ MP3 download ho raha hai... thoda ruk bhai!")
    download_dir = f"dl_mp3_{update.effective_user.id}_{update.message.message_id}"
    os.makedirs(download_dir, exist_ok=True)
    
    try:
        await asyncio.to_thread(download_video, url, download_dir, audio_only=True)
        mp3_files = glob.glob(f"{download_dir}/*.mp3")
        file_path = mp3_files[0] if mp3_files else None
        
        if file_path and os.path.exists(file_path):
            if os.path.getsize(file_path) <= 50 * 1024 * 1024:
                with open(file_path, 'rb') as audio:
                    await update.message.reply_audio(audio)
                await status_msg.delete()
            else:
                await status_msg.edit_text("Bhai MP3 50MB se badi hai! 😔")
        else:
            await status_msg.edit_text("Bhai MP3 download nahi hui. Link check kar! 😔")
    except Exception as e:
        err = str(e)
        if "Sign in to confirm" in err or "403" in err:
            await status_msg.edit_text("Bhai, YouTube block kar raha hai! 🛑 `cookies.txt` upload karein.")
        else:
            await status_msg.edit_text("Bhai error aagaya download mein. 🙏")
    finally:
        cleanup(download_dir)

async def translate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else (update.message.reply_to_message.text if update.message.reply_to_message else "")
    if not text:
        await update.message.reply_text("Bhai kya translate karun? Text likho ya reply karo! 🙏")
        return
    try:
        translator = GoogleTranslator(source='auto', target='hindi')
        translated = translator.translate(text)
        await update.message.reply_text(f"🌐 Translated (Auto → Hindi):\n\n{translated}")
    except Exception as e:
        await update.message.reply_text("Translation error bhai! 🙏")

async def send_reminder(chat_id: int, message: str, bot):
    await bot.send_message(chat_id=chat_id, text=f"⏰ Yaad dilaya bhai: {message}")

async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Bhai format: /remind 10m Chai peeni hai")
        return
    time_val = context.args[0]
    msg = " ".join(context.args[1:])
    
    seconds = 0
    try:
        if time_val.endswith('s'): seconds = int(time_val[:-1])
        elif time_val.endswith('m'): seconds = int(time_val[:-1]) * 60
        elif time_val.endswith('h'): seconds = int(time_val[:-1]) * 3600
        else: seconds = int(time_val)
    except:
        await update.message.reply_text("Time sahi se batao (e.g. 30s, 10m, 2h) 🙏")
        return
        
    scheduler.add_job(send_reminder, 'date', run_date=update.message.date + timedelta(seconds=seconds), 
                      args=[update.effective_chat.id, msg, context.bot])
    await update.message.reply_text(f"Done bhai! {time_val} baad yaad dila dunga. 👍")

# --- MESSAGE & BUTTON HANDLERS ---

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    prompts = {
        "mode_roast": ("roast", "Naam bata jisko roast karna hai! 🔥"),
        "mode_shayari": ("shayari", "Kis topic pe shayari likhun? 📝"),
        "mode_rap": ("rap", "Rap ka topic bata! 🔥🎤"),
        "mode_fortune": ("fortune", "Naam bata bhavishya dekhne ke liye! 🔮"),
        "mode_story": ("story", "Kis topic pe story likhun? 📝"),
        "mode_recipe": ("recipe", "Ingredients ya Dish name batao! 🍕")
    }
    if query.data in prompts:
        mode, text = prompts[query.data]
        context.user_data["mode"] = mode
        await query.edit_message_text(text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    mode = context.user_data.get("mode")
    
    # Handle AI Modes
    if mode:
        status = await update.message.reply_text("Typing... 🤖")
        ai_meta = {
            "roast": ("You are a savage Indian roaster. 4 lines Hinglish.", f"Roast: {user_text}"),
            "shayari": ("Deep poet Mirza Ghalib style. 4 lines Hinglish.", f"Topic: {user_text}"),
            "rap": ("Desi Underground Rapper. 8 lines Hinglish.", f"Topic: {user_text}"),
            "fortune": ("Funny Indian jyotishi. 3-4 lines Hinglish.", f"Name: {user_text}"),
            "story": ("Creative storyteller. 10 lines Hinglish.", f"Topic: {user_text}"),
            "recipe": ("Desi Chef. Ingredients/Dish recipe in Hinglish.", f"Recipe: {user_text}")
        }
        sys, user = ai_meta.get(mode, ("", ""))
        resp = await ask_ai(user, sys)
        await status.edit_text(resp)
        context.user_data.pop("mode", None)
        return

    # Handle Downloads
    urls = re.findall(r'http[s]?://[^\s]+', user_text)
    if urls:
        url = urls[0]
        status_msg = await update.message.reply_text("⏳ Download ho raha hai... ruk bhai!")
        dl_dir = f"dl_{update.effective_user.id}_{update.message.message_id}"
        os.makedirs(dl_dir, exist_ok=True)
        
        try:
            if "instagram.com" in url:
                await asyncio.to_thread(download_instagram, url, dl_dir)
                files = glob.glob(f"{dl_dir}/**/*", recursive=True)
                media_sent = False
                for f in files:
                    if not os.path.isfile(f) or os.path.getsize(f) > 50*1024*1024: continue
                    ext = f.split(".")[-1].lower()
                    if ext in ['mp4', 'jpg', 'jpeg', 'png', 'webp']:
                        with open(f, 'rb') as sf:
                            if ext == 'mp4': await update.message.reply_video(sf)
                            else: await update.message.reply_photo(sf)
                            media_sent = True
                if media_sent: await status_msg.delete()
                else: await status_msg.edit_text("Bhai media nahi mili ya badi file thi. 😔")
                
            elif any(d in url for d in ["youtube.com", "youtu.be", "twitter.com", "x.com", "facebook.com", "fb.watch"]):
                try:
                    fp = await asyncio.to_thread(download_video, url, dl_dir)
                    if fp and os.path.exists(fp) and os.path.getsize(fp) <= 50*1024*1024:
                        with open(fp, 'rb') as v: await update.message.reply_video(v)
                        await status_msg.delete()
                    else:
                        await status_msg.edit_text("Bhai video 50MB se badi hai! 😔")
                except Exception as e:
                    err = str(e)
                    print(f"ytdlp Error: {err}")
                    if "Sign in to confirm" in err or "403" in err:
                        await status_msg.edit_text("Bhai YouTube ne block kiya hua hai! 🛑 `cookies.txt` upload karein.")
                    else:
                        await status_msg.edit_text("Bhai download nahi hua. Private ho sakta hai! 😔")
            else:
                await status_msg.edit_text("Bhai ye platform support nahi hai abhi. 🙏")
        except Exception as e:
            print(f"Global DL Error: {e}")
            await status_msg.edit_text("Bhai error aagaya download mein! 🙏")
        finally:
            cleanup(dl_dir)
    else:
        await update.message.reply_text("Bhai samajh nahi aaya! Link bhej ya /start karo maze ke liye. 🙏")

# --- STARTUP ---

async def post_init(application: Application):
    if not scheduler.running: scheduler.start()
    print("Scheduler started!")

def main():
    # Setup: yt-dlp auto-update
    print("Railway Setup: Updating yt-dlp...")
    subprocess.run(["pip", "install", "--upgrade", "yt-dlp", "-q"], check=False)

    if not BOT_TOKEN:
        print("Error: BOT_TOKEN missing!")
        return

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mp3", mp3_command))
    app.add_handler(CommandHandler("translate", translate_command))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is alive! Waiting for messages...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
