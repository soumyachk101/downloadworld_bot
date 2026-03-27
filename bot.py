import os
import re
import glob
import asyncio
import shutil
import subprocess
from dotenv import load_dotenv

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
from telegram.error import Conflict
from datetime import timedelta

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

groq_client = None
if GROQ_API_KEY:
    groq_client = AsyncGroq(api_key=GROQ_API_KEY)
else:
    print("Warning: GROQ_API_KEY is missing. AI features will not work.")

L = instaloader.Instaloader(
    download_pictures=True,
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False
)

scheduler = AsyncIOScheduler()

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    if isinstance(error, Conflict):
        print("⚠️ Conflict: Dusra instance chal raha hai. 10 sec mein retry...")
        await asyncio.sleep(10)
    else:
        print(f"❌ Error: {error}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "Hello bhai! 👋\n\n"
        "Main tera all-in-one Telegram bot hoon.\n\n"
        "📥 *Media Downloader*\n"
        "Mujhe kisi bhi YouTube, Instagram, Twitter (X), ya Facebook video ka link bhej aur main tujhe media bhej dunga (<50MB).\n\n"
        "🤖 *AI Fun Modes*\n"
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
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    mode_map = {
        "mode_roast": ("roast", "Naam bata jisko roast karna hai! 🔥"),
        "mode_shayari": ("shayari", "Kis topic pe shayari likhun? 📝"),
        "mode_rap": ("rap", "Rap ka topic bata, aag laga denge! 🔥🎤"),
        "mode_fortune": ("fortune", "Naam bata, tera bhavishya dekhta hoon! 🔮"),
        "mode_story": ("story", "Kis topic pe story likhun? 📝"),
        "mode_recipe": ("recipe", "Kaunsi recipe seekhni hai? Ingredients batao! 🍕"),
    }
    if data in mode_map:
        mode, prompt = mode_map[data]
        context.user_data["mode"] = mode
        await query.edit_message_text(prompt)

async def ask_ai(prompt: str, system_prompt: str) -> str:
    if not groq_client:
        return "Bhai Groq API Key missing hai! Railway Dashboard mein 'GROQ_API_KEY' add kar do. 🙏"
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
        return "Bhai Groq AI mein thodi dikkat aa rahi hai. Baad mein try karna! 🙏"

async def handle_ai_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, user_text: str):
    prompts = {
        "roast": {
            "system": "You are a savage, funny Indian roaster. Roast the person named in the prompt in exactly 4 lines using Hinglish. Be hilarious but don't cross community guidelines.",
            "format": f"Roast this person: {user_text}"
        },
        "shayari": {
            "system": "You are a master Mirza Ghalib style poet but you write in Hinglish. Write a 4 line beautiful or funny shayari about the topic given.",
            "format": f"Topic: {user_text}"
        },
        "rap": {
            "system": "You are an Indian underground rapper like Divine or Emiway. Write an energetic desi Hindi rap with rhymes in exactly 8 lines using Hinglish about the given topic.",
            "format": f"Topic: {user_text}"
        },
        "fortune": {
            "system": "You are a funny Indian jyotishi (astrologer). Tell a humorous 3-4 line fortune in Hinglish for the given name. Make it absurd and funny.",
            "format": f"Name: {user_text}"
        },
        "story": {
            "system": "You are a creative storyteller. Write a short, engaging 10-line story in Hinglish about the given topic. Make it interesting and desi.",
            "format": f"Topic: {user_text}"
        },
        "recipe": {
            "system": "You are a Desi Chef. Provide a simple and tasty recipe in Hinglish with clear steps based on the ingredients or dish name provided. Use a friendly, 'Bhai' style tone.",
            "format": f"Recipe/Ingredients: {user_text}"
        }
    }

    if mode in prompts:
        msg = await update.message.reply_text("Typing... 🤖")
        response = await ask_ai(prompts[mode]["format"], prompts[mode]["system"])
        await msg.edit_text(response)
        context.user_data.pop("mode", None)

# ✅ FIX 3: Accept bot directly instead of full context
async def send_reminder(chat_id: int, message: str, bot):
    await bot.send_message(chat_id=chat_id, text=f"⏰ Yaad dilaya bhai: {message}")

async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Bhai format galat hai! Example: /remind 10m Chai peeni hai")
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
        await update.message.reply_text("Bhai time sahi se bata! (Example: 30s, 10m, 2h) 🙏")
        return

    run_date = update.message.date + timedelta(seconds=seconds)
    # ✅ FIX 3: Pass context.bot, not the entire context object
    scheduler.add_job(send_reminder, 'date', run_date=run_date,
                      args=[update.effective_chat.id, remind_text, context.bot])

    await update.message.reply_text(f"Done bhai! {time_val} baad yaad dila dunga. 👍")

async def translate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_to_translate = ""
    if context.args:
        text_to_translate = " ".join(context.args)
    elif update.message.reply_to_message:
        text_to_translate = update.message.reply_to_message.text
    else:
        await update.message.reply_text("Bhai kya translate karun? Text likho ya kisi message ko reply karo! 🙏")
        return

    try:
        # ✅ FIX 1: Removed broken single_detection import; source='auto' handles detection
        translator = GoogleTranslator(source='auto', target='hindi')
        translated = translator.translate(text_to_translate)
        await update.message.reply_text(f"🌐 Translated → Hindi:\n{translated}")
    except Exception as e:
        print(f"Translation Error: {e}")
        await update.message.reply_text("Bhai translation mein error aagaya! 🙏")

def download_video(url: str, output_path: str, audio_only: bool = False):
    ydl_opts = {
        # Robust format selection: try to stay under 45MB to be safe for Telegram's 50MB limit
        'format': 'bestvideo[filesize<45M]+bestaudio/best[filesize<45M]' if not audio_only else 'bestaudio/best',
        'outtmpl': f'{output_path}/%(id)s.%(ext)s',
        'quiet': False,
        'no_warnings': False,
        'verbose': True,
        'max_filesize': 50 * 1024 * 1024, # Hard cap for safety
    }
    
    # Check for cookies.txt in the current directory or project root
    cookie_path = "cookies.txt"
    if os.path.exists(cookie_path):
        ydl_opts['cookiefile'] = cookie_path
        print(f"DEBUG: Using {cookie_path} for yt-dlp authentication.")
    else:
        print("DEBUG: cookies.txt not found. YouTube may block data center IPs.")

    if audio_only:
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
        
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info_dict)

async def mp3_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Bhai link toh bhej! Example: /mp3 https://youtube.com/watch?v=xxx")
        return

    url = context.args[0]
    status_msg = await update.message.reply_text("⏳ MP3 ban raha hai... thoda ruk bhai!")

    download_dir = f"downloads_mp3_{update.effective_user.id}_{update.message.message_id}"
    os.makedirs(download_dir, exist_ok=True)

    try:
        await asyncio.to_thread(download_video, url, download_dir, audio_only=True)

        # ✅ FIX 4: Reliably find the .mp3 file by scanning the directory
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
            await status_msg.edit_text("Bhai MP3 download nahi ho payi. Link check kar! 😔")
    except Exception as e:
        print(f"MP3 Error: {e}")
        await status_msg.edit_text("Bhai error aagaya MP3 banane mein. 🙏")
    finally:
        cleanup(download_dir)

def download_instagram(url: str, output_path: str):
    try:
        match = re.search(r'/p/([A-Za-z0-9_-]+)', url) or re.search(r'/reel/([A-Za-z0-9_-]+)', url)
        shortcode = match.group(1) if match else None
        if not shortcode:
            raise ValueError("Invalid Instagram URL")
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        L.download_post(post, target=output_path)
    except Exception as e:
        print(f"Instaloader Error: {e}")

def cleanup(path: str):
    try:
        if os.path.exists(path):
            shutil.rmtree(path)
    except Exception as e:
        print(f"Cleanup Error: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    mode = context.user_data.get("mode")

    if mode:
        await handle_ai_mode(update, context, mode, user_text)
        return

    url_pattern = re.compile(
        r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    )
    urls = re.findall(url_pattern, user_text)

    if urls:
        url = urls[0]
        status_msg = await update.message.reply_text("⏳ Download ho raha hai... ruk bhai!")
        download_dir = f"downloads_{update.effective_user.id}_{update.message.message_id}"
        os.makedirs(download_dir, exist_ok=True)

        try:
            if "instagram.com" in url:
                await asyncio.to_thread(download_instagram, url, download_dir)

                # ✅ FIX 2: Recursive glob to find files in instaloader subdirectories
                files = glob.glob(f"{download_dir}/**/*", recursive=True)
                media_sent = False

                for f in files:
                    if not os.path.isfile(f):
                        continue
                    ext = f.split(".")[-1].lower()
                    if ext in ['mp4', 'jpg', 'jpeg', 'png', 'webp']:
                        if os.path.getsize(f) <= 50 * 1024 * 1024:
                            with open(f, 'rb') as sf:
                                if ext == 'mp4':
                                    await update.message.reply_video(sf)
                                else:
                                    await update.message.reply_photo(sf)
                                media_sent = True

                if not media_sent:
                    await status_msg.edit_text("Bhai media nahi mili ya file size > 50MB hai. 😔")
                else:
                    await status_msg.delete()

            elif any(domain in url for domain in ["youtube.com", "youtu.be", "twitter.com", "x.com", "facebook.com", "fb.watch"]):
                try:
                    file_path = await asyncio.to_thread(download_video, url, download_dir)
                    if file_path and os.path.exists(file_path) and os.path.getsize(file_path) <= 50 * 1024 * 1024:
                        with open(file_path, 'rb') as video:
                            await update.message.reply_video(video)
                        await status_msg.delete()
                    else:
                        await status_msg.edit_text("Bhai video 50MB se badi hai, main sirf 50MB tak ka bhej sakta hoon! 😔")
                except Exception as e:
                    print(f"ytdlp FULL error: {type(e).__name__}: {e}")
                    await status_msg.edit_text("Bhai video download nahi hui. Private ho sakti hai! 😔")
            else:
                await status_msg.edit_text("Bhai is platform ka link abhi support nahi karta main. Sirf YT, Insta, Twitter aur FB bhej! 🙏")
        except Exception as e:
            print(f"Download Error: {e}")
            await status_msg.edit_text("Bhai error aagaya download karne mein. Valid link bhej doosri baar dekhte hain! 🤕")
        finally:
            cleanup(download_dir)
    else:
        await update.message.reply_text("Bhai samajh nahi aaya! Koi link bhej video download ke liye ya /start likh maze karne ke liye! 🙏")

async def post_init(application: Application):
    if not scheduler.running:
        scheduler.start()
    print("Scheduler started!")

def main():
    # Auto-update yt-dlp on every startup for Railway
    print("Updating yt-dlp...")
    subprocess.run(["pip", "install", "--upgrade", "yt-dlp", "-q"], check=False)

    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is missing!")
        return
    if not GROQ_API_KEY:
        print("Warning: GROQ_API_KEY is missing. AI features are disabled but bot will start.")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # ✅ Error handler add karo
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mp3", mp3_command))
    app.add_handler(CommandHandler("translate", translate_command))
    app.add_handler(CommandHandler("tr", translate_command))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot is starting up... waiting for messages.")
    # ✅ drop_pending_updates=True — purane stuck updates ignore karega
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
