
readme_content = """<div align="center">

# 🤖 Telegram Media Downloader + AI Bot

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![Groq](https://img.shields.io/badge/Groq-AI-F55036?style=for-the-badge&logo=openai&logoColor=white)](https://groq.com)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)
[![Railway](https://img.shields.io/badge/Deploy-Railway-0B0D0E?style=for-the-badge&logo=railway&logoColor=white)](https://railway.app)

### 🚀 Production-ready Telegram bot for downloading videos from YouTube, Instagram, Twitter, Facebook with AI chat features

[Features](#-features) • [Quick Start](#-quick-setup) • [Deploy](#-deploy-to-railway) • [Usage](#-how-to-use) • [Troubleshooting](#-troubleshooting)

</div>

---

## ✨ Features

### 📥 Media Downloader

<table>
<tr>
<td width="50%">

| Platform | Status | Quality |
|----------|--------|---------|
| 🎬 **YouTube** | ✅ Working | Best < 500MB |
| 🎵 **YouTube MP3** | ✅ Working | 128kbps |
| 📸 **Instagram** | ✅ Working | Posts & Reels |
| 🐦 **Twitter/X** | ✅ Working | HD Video |
| 📘 **Facebook** | ✅ Working | HD Video |

</td>
<td width="50%">

**Smart Features:**
- ✅ Auto-detects platform from URL
- ✅ 500MB size limit for Telegram
- ✅ Auto-cleanup of downloaded files
- ✅ Unique download directory per user
- ✅ Friendly Hinglish messages

</td>
</tr>
</table>

### 🤖 AI Features (Powered by Groq)

<div align="center">

| Command | Feature | Description |
|:-------:|:-------:|:------------|
| 😂 | **Roast Karo** | 4-line savage Hinglish roasts |
| 🎤 | **Shayari Likho** | Mirza Ghalib style shayari |
| 🎵 | **Rap Banao** | 8-line desi hip-hop rap |
| 🔮 | **Bhavishya Batao** | Funny fortune telling |

</div>

> 💡 Powered by `llama-3.1-70b-instant` via Groq API — **Completely FREE!**

---

## 🚀 Quick Setup

### 1️⃣ Get Telegram Bot Token

```
🤖 Open Telegram → Search @BotFather → Send /newbot
   Name: My Downloader Bot
   Username: my_downloader_bot (must end in 'bot')
   ↓
   📋 Copy the token (looks like: 1234567890:ABCdefGHI...)
```

### 2️⃣ Get Groq API Key (FREE)

```
🌐 Go to console.groq.com
   ↓
   Sign up / Log in
   ↓
   Click "Create API Key"
   ↓
   📋 Copy the key (starts with gsk_...)
```

> 💰 **Groq is completely FREE** with generous rate limits!

### 3️⃣ Clone & Setup

```bash
# 📥 Clone your repo
git clone <your-repo-url>
cd <your-repo>

# 📋 Copy environment template
cp .env.example .env
```

### 4️⃣ Configure Environment

```env
# .env file
BOT_TOKEN=your_telegram_bot_token_here
GROQ_API_KEY=your_groq_api_key_here
```

### 5️⃣ Install Dependencies

```bash
# 📦 Using pip (recommended)
pip install -r requirements.txt

# 🛠️ Or manually
pip install python-telegram-bot==21.5 yt-dlp instaloader groq python-dotenv
```

### 6️⃣ Run Locally

```bash
🚀 python bot.py
```

> ✅ The bot will start polling and be ready to respond!

---

## ☁️ Deploy to Railway

<div align="center">

### Railway provides **free hosting** perfect for Telegram bots 🚂

</div>

### Step 1: Push to GitHub

```bash
🔧 git init
📤 git add .
💾 git commit -m "Initial commit"
🌿 git branch -M main
🔗 git remote add origin https://github.com/yourusername/yourrepo.git
⬆️  git push -u origin main
```

### Step 2: Deploy to Railway

```
1. 🌐 Go to railway.app and sign up
2. ➕ Click "New Project"
3. 📂 Choose "Deploy from GitHub repo"
4. ✅ Select your repository
5. 🤖 Railway auto-detects Python project
```

### Step 3: Set Environment Variables

| Variable | Value | Required |
|----------|-------|----------|
| `BOT_TOKEN` | Your Telegram bot token | ✅ Yes |
| `GROQ_API_KEY` | Your Groq API key | ⚡ Recommended |

```
💡 In Railway dashboard → "Variables" tab → Add variables → Click "Deploy"
```

### Step 4: Keep Bot Running

**Option A: CLI**
```bash
npm i -g @railway-cli
railway login
railway link
railway scale worker 1
```

**Option B: Dashboard**
```
Settings → Scale → 1/1 → Enable Public Networking
```

### ✅ Done!

> 🎉 Your bot is now live on Railway with a public URL. It will auto-restart if crashes.

> ⚠️ **Note:** Railway free tier has **5 hours/month** sleep quota. For 24/7 uptime, upgrade ($5/month) or consider [Render](https://render.com), [Heroku](https://heroku.com), or a VPS.

---

## 📂 Project Structure

```
📁 your-repo/
├── 🤖 bot.py              # Main bot code
├── 📦 requirements.txt    # Python dependencies (pinned)
├── 🚂 Procfile           # Railway deployment config
├── 🐍 runtime.txt        # Python version
├── 🔐 .env               # Your secrets (not in git)
├── 📋 .env.example       # Template for .env
└── 📖 README.md          # This file
```

---

## 🛠️ Tech Stack

<div align="center">

| Technology | Version | Purpose |
|:----------:|:-------:|:--------|
| ![Python](https://img.shields.io/badge/-Python-3776AB?logo=python&logoColor=white&style=flat-square) |
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

### Download MP3
Use any one of these:
```bash
/mp3 https://youtube.com/watch?v=...
/audio https://youtube.com/watch?v=...
```

Or send YouTube link with text like `mp3`, `audio`, `song`, or `music`.

Bot will:
1. Show "⏳ Download ho raha hai... ruk bhai!" (video) or "⏳ MP3 ban raha hai... thoda ruk bhai!" (audio)
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
- Video might be >500MB (Telegram limit)
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
