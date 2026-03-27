# 🤖 Telegram Media Downloader + AI Bot

Production-ready Telegram bot for downloading videos from YouTube, Instagram, Twitter, Facebook with AI chat features (Roast, Shayari, Rap, Fortune).

## ✨ Features

### 📥 Media Downloader
- **YouTube** - Download videos (best quality < 50MB)
- **Instagram** - Posts & Reels download
- **Twitter/X** - Video downloads
- **Facebook** - Video downloads

**Smart Features:**
- ✅ Auto-detects platform from URL
- ✅ 50MB size limit for Telegram
- ✅ Auto-cleanup of downloaded files
- ✅ Unique download directory per user
- ✅ Friendly Hinglish messages

### 🤖 AI Features (Groq)
- **😂 Roast Karo** - 4-line savage Hinglish roasts
- **🎤 Shayari Likho** - Mirza Ghalib style shayari
- **🎵 Rap Banao** - 8-line desi hip-hop rap
- **🔮 Bhavishya Batao** - Funny fortune telling

Powered by `llama-3.1-8b-instant` via Groq API.

## 🚀 Quick Setup

### 1. Get Telegram Bot Token

1. Open Telegram and search **@BotFather**
2. Send `/newbot` command
3. Choose a name for your bot (e.g., `My Downloader Bot`)
4. Choose a username (must end in `bot`, e.g., `my_downloader_bot`)
5. **Copy the token** (looks like: `1234567890:ABCdefGHI...`)

### 2. Get Groq API Key (FREE)

1. Go to [console.groq.com](https://console.groq.com)
2. Sign up / Log in
3. Click **"Create API Key"**
4. **Copy the key** (starts with `gsk_...`)

> 💡 **Groq is completely FREE** with generous rate limits!

### 3. Clone & Setup

```bash
# Clone your repo
git clone <your-repo-url>
cd <your-repo>

# Copy environment template
cp .env.example .env
```

### 4. Configure Environment

Edit `.env` file:

```env
BOT_TOKEN=your_telegram_bot_token_here
GROQ_API_KEY=your_groq_api_key_here
```

### 5. Install Dependencies

```bash
# Using pip (recommended)
pip install -r requirements.txt

# Or manually
pip install python-telegram-bot==21.5 yt-dlp instaloader groq python-dotenv
```

### 6. Run Locally

```bash
python bot.py
```

The bot will start polling and be ready to respond!

## ☁️ Deploy to Railway

Railway provides **free hosting** perfect for Telegram bots.

### Step 1: Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/yourusername/yourrepo.git
git push -u origin main
```

### Step 2: Deploy to Railway

1. Go to [railway.app](https://railway.app) and sign up
2. Click **"New Project"**
3. Choose **"Deploy from GitHub repo"**
4. Select your repository
5. Railway auto-detects Python project

### Step 3: Set Environment Variables

In Railway dashboard:

1. Go to **"Variables"** tab
2. Add these variables:

| Key | Value |
|-----|-------|
| `BOT_TOKEN` | Your Telegram bot token |
| `GROQ_API_KEY` | Your Groq API key |

3. Click **"Deploy"**

### Step 4: Keep Bot Running

Telegram bots need persistent process. Railway needs **always-on** configuration:

1. Install Railway CLI: `npm i -g @railway/cli`
2. Login: `railway login`
3. Link project: `railway link`
4. Scale worker: `railway scale worker 1`

**OR** use the Railway dashboard:
- Go to **"Settings"**
- Set **"Scale"** to **1/1**
- Ensure **"Public Networking"** is enabled

### ✅ Done!

Your bot is now live on Railway with a public URL. It will auto-restart if crashes.

**Note:** Railway free tier has **5 hours/month** sleep quota. For 24/7 uptime, upgrade ($5/month) or consider other hosts like Render, Heroku, or a VPS.

## 📂 Project Structure

```
.
├── bot.py              # Main bot code
├── requirements.txt    # Python dependencies (pinned)
├── Procfile           # Railway deployment config
├── runtime.txt        # Python version
├── .env               # Your secrets (not in git)
├── .env.example       # Template for .env
└── README.md          # This file
```

## 🛠️ Tech Stack

- **Python 3.11**
- **python-telegram-bot** 21.5 - Telegram API wrapper
- **yt-dlp** - Video downloading (fork of youtube-dl)
- **instaloader** - Instagram downloads
- **groq** - AI API client
- **python-dotenv** - Environment management

All versions pinned for reproducible builds.

## 🎯 How to Use

### Download Videos
Simply send any supported URL:
```
https://youtube.com/watch?v=...
https://instagram.com/p/...
https://twitter.com/user/status/...
https://facebook.com/watch?v=...
```

Bot will:
1. Show "⏳ Download ho raha hai... ruk bhai!"
2. Download the video
3. Send it back to you
4. Cleanup temporary files

### AI Features
1. Send `/start`
2. Choose AI mode button
3. Type your input
4. Get AI response in Hinglish!

## 🐛 Error Handling

Bot handles all errors gracefully:
- ✅ Download failures → friendly Hinglish message
- ✅ API errors → retry suggestion
- ✅ File size limits → clear explanation
- ✅ Never crashes on single user error

All errors logged to console for debugging.

## 📝 Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | Yes | Telegram bot token from @BotFather |
| `GROQ_API_KEY` | No | Groq API key for AI features (optional but recommended) |

If `GROQ_API_KEY` is not set, AI features will show error message.

## 🔒 Security Notes

- **Never** commit `.env` file (already in `.gitignore`)
- Keep your API keys secret
- Don't share bot token publicly
- Use environment variables in production

## 🧹 Cleanup Downloaded Files

Bot automatically creates unique download directories like `dl_12345_678` and cleans them up after processing. If bot crashes, leftover files can be manually removed:

```bash
rm -rf dl_*
```

## 🆘 Troubleshooting

### Bot not responding
- Check if `BOT_TOKEN` is correct
- Check Railway logs: `railway logs`
- Ensure bot is running: `railway status`

### AI not working
- Check if `GROQ_API_KEY` is set correctly
- Groq API might be down - check console.groq.com
- Free tier has rate limits

### Downloads failing
- URL might be private/protected
- Instagram requires cookies for some posts (not implemented yet)
- Video might be >50MB (Telegram limit)
- Platform might have changed their API

### "Forbidden: bot was blocked by the user"
- User blocked your bot. Can't do anything about it.

## 📜 License

Free to use and modify. No warranty provided.

## 🙏 Credits

Built with ❤️ in India
Using amazing open-source libraries: python-telegram-bot, yt-dlp, instaloader, groq

---

**Enjoy your bot!** 🎉
