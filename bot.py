import os
import re
import glob
import asyncio
import shutil
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

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

groq_client = AsyncGroq(api_key=GROQ_API_KEY)

# Instaloader instance (login not required for public posts but helps)
L = instaloader.Instaloader(
    download_pictures=True,
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False
)

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
         InlineKeyboardButton("🔮 Bhavishya Batao", callback_data="mode_fortune")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # Store selected mode in context
    if data == "mode_roast":
        context.user_data["mode"] = "roast"
        await query.edit_message_text("Naam bata jisko roast karna hai! 🔥")
    elif data == "mode_shayari":
        context.user_data["mode"] = "shayari"
        await query.edit_message_text("Kis topic pe shayari likhun? 📝")
    elif data == "mode_rap":
        context.user_data["mode"] = "rap"
        await query.edit_message_text("Rap ka topic bata, aag laga denge! 🔥🎤")
    elif data == "mode_fortune":
        context.user_data["mode"] = "fortune"
        await query.edit_message_text("Naam bata, tera bhavishya dekhta hoon! 🔮")

async def ask_ai(prompt: str, system_prompt: str) -> str:
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
        }
    }
    
    if mode in prompts:
        msg = await update.message.reply_text("Typing... 🤖")
        response = await ask_ai(prompts[mode]["format"], prompts[mode]["system"])
        await msg.edit_text(response)
        
        # Clear mode after use
        context.user_data["mode"] = None
        context.user_data.pop("mode", None)

def download_video(url: str, output_path: str):
    ydl_opts = {
        'format': 'best[filesize<50M]/best',
        'outtmpl': f'{output_path}/%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info_dict)

def download_instagram(url: str, output_path: str):
    try:
        # Extract shortcode
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

    # Check if text contains a URL
    url_pattern = re.compile(
        r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    )
    urls = re.findall(url_pattern, user_text)
    
    if urls:
        url = urls[0]
        status_msg = await update.message.reply_text("⏳ Download ho raha hai... ruk bhai!")
        
        # Unique download directory to prevent overlaps
        download_dir = f"downloads_{update.effective_user.id}_{update.message.message_id}"
        os.makedirs(download_dir, exist_ok=True)
        
        try:
            if "instagram.com" in url:
                # Instagram download via thread to avoid blocking
                await asyncio.to_thread(download_instagram, url, download_dir)
                
                # Send all downloaded files
                files = glob.glob(f"{download_dir}/*")
                media_sent = False
                
                for f in files:
                    ext = f.split(".")[-1].lower()
                    if ext in ['mp4', 'jpg', 'jpeg', 'png', 'webp']:
                        # Telegram file limit check (50MB)
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
                # yt-dlp download via thread to avoid blocking
                try:
                    # Using asyncio.to_thread
                    d_task = asyncio.to_thread(download_video, url, download_dir)
                    file_path = await d_task
                    
                    if file_path and os.path.getsize(file_path) <= 50 * 1024 * 1024:
                        with open(file_path, 'rb') as video:
                            await update.message.reply_video(video)
                        await status_msg.delete()
                    else:
                        await status_msg.edit_text("Bhai video 50MB se badi hai, main sirf 50MB tak ka bhej sakta hoon! 😔")
                except Exception as e:
                    print(f"ytdlp error: {e}")
                    await status_msg.edit_text("Bhai video download nahi hui. Private ho sakti hai! 😔")
            else:
                await status_msg.edit_text("Bhai is platform ka link abhi support nahi karta main. Sirf YT, Insta, Twitter aur FB bhej! 🙏")
        except Exception as e:
            print(f"Download Error: {e}")
            await status_msg.edit_text("Bhai error aagaya download karne mein. Valid link bhej doosri baar dekhte hain! 🤕")
        finally:
            # Cleanup downloaded files after sending
            cleanup(download_dir)
    else:
        # Help message for unknown text / no active mode
        await update.message.reply_text("Bhai samajh nahi aaya! Koi link bhej video download ke liye ya /start likh maze karne ke liye! 🙏")

def main():
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is missing in .env")
        return
        
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is starting up... waiting for messages.")
    app.run_polling()

if __name__ == "__main__":
    main()
