"""
Production-ready Telegram bot with media downloader and AI features.
Supports YouTube, Instagram, Twitter/X, Facebook downloads + Groq AI modes.
"""

import os
import re
import shutil
import asyncio
from typing import Optional, Tuple
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from yt_dlp import YoutubeDL
import instaloader
from groq import AsyncGroq

# Load environment variables
load_dotenv()

# Initialize clients
bot_token = os.getenv("BOT_TOKEN")
groq_api_key = os.getenv("GROQ_API_KEY")

if not bot_token:
    raise ValueError("BOT_TOKEN environment variable is required!")

groq_client = AsyncGroq(api_key=groq_api_key) if groq_api_key else None

# Platform detection patterns
URL_PATTERNS = {
    "youtube": r"(youtube\.com|youtu\.be)/",
    "instagram": r"instagram\.com/(p|reel)/",
    "twitter": r"(twitter\.com|x\.com)/",
    "facebook": r"(facebook\.com|fb\.watch)/"
}

# System prompts for AI modes
SYSTEM_PROMPTS = {
    "roast": "You are a savage funny Indian roaster. Roast in Hinglish in exactly 4 lines. Be hilarious but not offensive or abusive.",
    "shayari": "You are a Mirza Ghalib style poet writing in Hinglish. Write a beautiful or funny 4-line shayari on the given topic.",
    "rap": "You are an Indian underground rapper like Divine or Emiway. Write energetic desi Hinglish rap in exactly 8 lines with rhymes.",
    "fortune": "You are a funny Indian jyotishi (astrologer). Tell an absurd humorous 3-4 line fortune in Hinglish for the given name."
}

# Helper functions
async def cleanup(download_dir: str) -> None:
    """Safely remove download directory and its contents."""
    try:
        if os.path.exists(download_dir):
            shutil.rmtree(download_dir)
    except Exception as e:
        print(f"Cleanup error for {download_dir}: {e}")


def detect_platform(url: str) -> Optional[str]:
    """Detect which platform the URL belongs to."""
    for platform, pattern in URL_PATTERNS.items():
        if re.search(pattern, url, re.IGNORECASE):
            return platform
    return None


async def download_video(url: str, download_dir: str, user_id: int, message_id: int) -> Tuple[Optional[str], Optional[str]]:
    """
    Download video using yt-dlp for YouTube, Twitter, Facebook.
    Returns: (video_path, thumbnail_path) or (None, None) on failure.
    """
    try:
        os.makedirs(download_dir, exist_ok=True)

        ydl_opts = {
            'outtmpl': os.path.join(download_dir, '%(title)s.%(ext)s'),
            'format': 'best[filesize<50M]/best',
            'quiet': True,
            'no_warnings': True,
        }

        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, lambda: _extract_info(url, ydl_opts))

        # Find downloaded file
        files = os.listdir(download_dir)
        if not files:
            return None, None

        video_file = os.path.join(download_dir, files[0])

        # Try to get thumbnail if available
        thumbnail = None
        if info and 'thumbnail' in info:
            thumb_path = os.path.join(download_dir, 'thumb.jpg')
            # Download thumbnail would require additional code
            # For now, return None for thumbnail

        return video_file, thumbnail

    except Exception as e:
        print(f"Download error for user {user_id}: {e}")
        return None, None


def _extract_info(url: str, ydl_opts: dict) -> dict:
    """Extract video info in blocking thread."""
    with YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=True)


async def download_instagram(url: str, download_dir: str, user_id: int, message_id: int) -> Optional[str]:
    """
    Download Instagram post/reel using instaloader.
    Returns: path to downloaded video or None.
    """
    try:
        os.makedirs(download_dir, exist_ok=True)

        # Extract shortcode from URL
        match = re.search(r"instagram\.com/(p|reel)/([^/?]+)", url, re.IGNORECASE)
        if not match:
            return None

        shortcode = match.group(2)

        # Run instaloader in thread to avoid blocking
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, lambda: _download_instagram_post(shortcode, download_dir))

        if not success:
            return None

        # Find downloaded file
        for root, dirs, files in os.walk(download_dir):
            for file in files:
                if file.lower().endswith(('.mp4', '.mov')):
                    return os.path.join(root, file)

        return None

    except Exception as e:
        print(f"Instagram download error for user {user_id}: {e}")
        return None


def _download_instagram_post(shortcode: str, download_dir: str) -> bool:
    """Download Instagram post in blocking thread."""
    L = instaloader.Instaloader(
        download_pictures=True,
        download_videos=True,
        download_video_thumbnails=False,
        save_metadata=False,
        dirname_pattern=download_dir
    )
    try:
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        L.download_post(post, target=download_dir)
        return True
    except Exception as e:
        print(f"Instaloader error: {e}")
        return False


async def ask_ai(prompt: str, mode: str) -> str:
    """Call Groq API with the appropriate system prompt."""
    if not groq_client:
        return "❌ Groq API key nahi hai! Admin se poocho bhai."

    try:
        system_prompt = SYSTEM_PROMPTS.get(mode, "You are a helpful assistant.")

        chat_completion = await groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            model="llama-3.1-8b-instant",
            temperature=0.8,
            max_tokens=256
        )

        return chat_completion.choices[0].message.content.strip()

    except Exception as e:
        print(f"Groq API error: {e}")
        return f"❌ AI error: {str(e)[:100]}. Phir se try kar!"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    keyboard = [
        [InlineKeyboardButton("😂 Roast Karo", callback_data="mode_roast")],
        [InlineKeyboardButton("🎤 Shayari Likho", callback_data="mode_shayari")],
        [InlineKeyboardButton("🎵 Rap Banao", callback_data="mode_rap")],
        [InlineKeyboardButton("🔮 Bhavishya Batao", callback_data="mode_fortune")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_text = (
        "🙏 *Welcome to Telegram Downloader Bot!*\n\n"
        "📥 *Features:*\n"
        "• YouTube, Instagram, Twitter/X, Facebook se videos download karo\n"
        "• AI se baat karo: Roast, Shayari, Rap, Fortune\n\n"
        "🎯 *Kaise use kare?*\n"
        "1. URL send karo → auto download\n"
        "2. AI button dabao → type karo → AI reply\n\n"
        "Made with ❤️ in India"
    )

    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button clicks."""
    query = update.callback_query
    await query.answer()

    mode = query.data.replace("mode_", "")
    context.user_data["mode"] = mode

    prompts = {
        "roast": "😂 Kisko roast karna hai? Name batao!",
        "shayari": "🎤 kis topic pe shayari chahiye?",
        "rap": "🎵 kis baat pe rap banana hai? Topic batao!",
        "fortune": "🔮 Apna naam batao, future bataunga!"
    }

    await query.edit_message_text(
        text=prompts.get(mode, "Type your message..."),
        parse_mode="Markdown"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all incoming messages."""
    user = update.effective_user
    user_id = user.id
    message = update.message
    message_id = message.message_id
    text = message.text or ""

    # Check if user has an active AI mode
    mode = context.user_data.get("mode")

    if mode:
        # AI mode active
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        response = await ask_ai(text, mode)

        # Clear mode after use
        context.user_data.pop("mode", None)

        await message.reply_text(response)
        return

    # Check if it's a URL
    if not text.startswith("http"):
        help_text = (
            "❓ *Kuch samajh nahi aaya!*\n\n"
            "• URL bhejo → main download kar dunga\n"
            "• /start dabao → AI features try karo\n\n"
            "Support: YouTube, Instagram, Twitter/X, Facebook"
        )
        await message.reply_text(help_text, parse_mode="Markdown")
        return

    # URL detected - start download
    platform = detect_platform(text)
    if not platform:
        await message.reply_text(
            "❌ Ye platform supported nahi hai bhai!\n\n"
            "Supported: YouTube, Instagram, Twitter/X, Facebook"
        )
        return

    # Create unique download directory
    download_dir = os.path.abspath(f"dl_{user_id}_{message_id}")

    # Send processing status
    status_msg = await message.reply_text("⏳ Download ho raha hai... ruk bhai!")

    try:
        if platform == "instagram":
            # Download Instagram
            video_path = await download_instagram(text, download_dir, user_id, message_id)

            if video_path and os.path.exists(video_path):
                file_size = os.path.getsize(video_path) / (1024 * 1024)  # MB

                if file_size > 50:
                    await status_msg.edit_text(
                        "❌ Video 50MB se zyada hai bhai! Telegram limit exceed.\n"
                        "Koi chhoti video bhejo."
                    )
                else:
                    # Send video
                    with open(video_path, 'rb') as f:
                        await message.reply_video(video=f, caption="📥 Downloaded via @YourBot")

                    await status_msg.delete()
            else:
                await status_msg.edit_text("❌ Instagram video download nahi hua. Private ho sakta hai ya link valid nahi hai.")

        else:
            # Download YouTube/Twitter/Facebook
            video_path, thumb_path = await download_video(text, download_dir, user_id, message_id)

            if video_path and os.path.exists(video_path):
                file_size = os.path.getsize(video_path) / (1024 * 1024)  # MB

                if file_size > 50:
                    await status_msg.edit_text(
                        "❌ Video 50MB se zyada hai bhai! Telegram limit exceed.\n"
                        "Koi chhoti video bhejo."
                    )
                else:
                    # Send video
                    with open(video_path, 'rb') as f:
                        await message.reply_video(video=f, caption=f"📥 {platform.title()} se downloaded via @YourBot")

                    await status_msg.delete()
            else:
                await status_msg.edit_text("❌ Video download nahi hua. Link check karo ya private ho sakta hai.")

    except Exception as e:
        print(f"Error processing URL {text} for user {user_id}: {e}")
        await status_msg.edit_text(f"❌ Download failed: {str(e)[:100]}... Try again bhai!")
    finally:
        # Always cleanup
        await cleanup(download_dir)


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors and send friendly message to user."""
    print(f"Update {update} caused error: {context.error}")

    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ Kuch error hua bhai! Phir se try kar.\n"
                "Agar problem aati rahe to admin se contact karo."
            )
    except Exception as e:
        print(f"Error handler failed: {e}")


def main() -> None:
    """Start the bot."""
    print("🤖 Starting Telegram Bot...")

    # Create application
    application = Application.builder().token(bot_token).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Error handler
    application.add_error_handler(error_handler)

    # Start bot with polling
    print("✅ Bot is running...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
