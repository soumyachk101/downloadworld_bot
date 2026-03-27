# 🚀 Complete Railway Deployment Guide

## ✅ What's New (v3.1 - Production Ready)

### 🔧 Core Features
- ✅ **yt-dlp pre-installed** (Alpine/Nix packages)
- ✅ **Health check server** (`/health`, `/metrics`)
- ✅ **Retry logic** (2 retries, 2s delay)
- ✅ **Graceful shutdown** (SIGTERM/SIGINT)
- ✅ **Config validation** (BOT_TOKEN format check)
- ✅ **Admin commands** (`/status`, `/clean`)
- ✅ **Rate limiting** (5 downloads/user/min, configurable)
- ✅ **Comprehensive logging** (timestamps, levels)

### 🍪 Cookie Support (For Instagram/Facebook)
- ✅ **COOKIES_B64** env var (base64 encoded cookies)
- ✅ **Auto-detect cookies.txt** file
- ✅ Clear guidance for cookie export
- See: **`README-COOKIES.md`** for detailed setup

---

## 📦 Prerequisites

1. **GitHub Repository** with this code (already done ✅)
2. **Telegram Bot Token** from [@BotFather](https://t.me/botfather)
3. **(Optional) Groq API Key** from [console.groq.com](https://console.groq.com)
4. **(Optional) Cookies.txt** for Instagram/Facebook access
5. **Railway Account** at [railway.app](https://railway.app)

---

## 🚀 Step-by-Step Deployment

### Step 1: Push Code (Already Done)

```bash
git push origin main
```

✅ Code is live at: https://github.com/soumyachk101/Everything_Downloader_bot

---

### Step 2: Create Railway Project

1. Login to [Railway Dashboard](https://railway.app)
2. Click **"New Project"**
3. Select **"Deploy from GitHub repo"**
4. Authorize Railway to access your GitHub
5. Select repository: `Everything_Downloader_bot`
6. Railway auto-detects:
   - `nixpacks.toml` → installs ffmpeg, yt-dlp
   - `Procfile` → starts `node index.js`
7. Wait for initial build (1-2 minutes)

---

### Step 3: Set Environment Variables

Go to your Railway project → **"Variables"** tab:

| Variable | Required | Value | How to get |
|----------|----------|-------|------------|
| `BOT_TOKEN` | ✅ YES | `123456:ABC...` | [@BotFather](https://t.me/botfather) → `/mybots` → API Token |
| `GROQ_API_KEY` | ⭕ NO | `gsk_...` | [console.groq.com](https://console.groq.com) → API Keys |
| `COOKIES_B64` | ⭕ NO | `<base64 string>` | See **README-COOKIES.md** |
| `ADMIN_ID` | ⭕ NO | `123456789` | Your Telegram user ID (use @userinfobot) |
| `RATE_LIMIT` | ⭕ NO | `5` | Max downloads per minute per user (default: 5) |
| `PORT` | ❌ Auto | `3000` | Railway sets automatically |
| `NODE_ENV` | ❌ Auto | `production` | Railway sets automatically |

**Click "Save"** after adding variables.

---

### Step 4: Deploy!

- Railway auto-deploys on git push
- Or click **"Deploy"** button manually
- Wait for build (check "Deployments" tab)

---

## 🧪 Verification Steps

### 1️⃣ Check Logs
```bash
railway logs
```
Expected output:
```
✅ Configuration validated
✅ yt-dlp 2025.03.31 ready
🏥 Health server listening on port 3000
✅ Bot launched successfully!
```

### 2️⃣ Health Check
```bash
curl https://your-app.up.railway.app/health
```
Expected JSON:
```json
{
  "status": "ok",
  "uptime": 123,
  "memory": { "rss": 12345678, ... },
  "timestamp": "2025-03-27T...",
  "version": "3.1"
}
```

### 3️⃣ Test Bot on Telegram
- Open Telegram
- Start chat with your bot
- Send `/start`
- Send YouTube link: `https://youtu.be/...`
- Should download and send video ✅

---

## 🛠️ Advanced Configuration

### Rate Limiting
Default: 5 downloads per minute per user.

Change it:
```bash
# In Railway Variables:
RATE_LIMIT=10  # Allow 10 downloads/min
```

### Admin Commands
If `ADMIN_ID` is set, only that user can use:
- `/status` - bot health, memory, sessions
- `/clean` - clean temp directories

Get your Telegram ID: [@userinfobot](https://t.me/userinfobot)

---

## 📊 Monitoring & Maintenance

### View Real-time Logs
```bash
railway logs --follow
```

### Check Resource Usage
```bash
# Access Railway dashboard → Monitoring tab
# Or use /status command in Telegram (admin only)
```

### Restart Bot
```bash
railway up  # Redeploy
# Or
railway restart
```

### View Environment Variables
```bash
railway variables
```

---

## 🐛 Troubleshooting

### ❌ "yt-dlp not found"
**Cause**: nixpacks.toml not detected or corrupted
**Fix**:
1. Ensure `nixpacks.toml` exists in root
2. Redeploy: `railway up`
3. Check logs: `railway logs`

### ❌ "Bot not responding"
**Cause**: BOT_TOKEN not set or invalid
**Fix**:
1. Double-check BOT_TOKEN format: `123456:ABC...`
2. Check logs for "Configuration validated"
3. Re-set variable in Railway → Redeploy

### ⚠️ "Instagram rate limit / login required"
**Cause**: Instagram blocks shared IP or requires auth
**Fix**:
1. **Option A**: Set `COOKIES_B64` with your cookies (recommended)
   - See **README-COOKIES.md** for step-by-step
2. **Option B**: Upload `cookies.txt` file to Railway Files tab
3. Redeploy after setting cookies

### ❌ "Health check failing"
**Cause**: Port conflict or server not starting
**Fix**:
1. Check PORT variable (should be 3000)
2. Ensure no other process using port
3. Check logs for binding errors
4. Restart: `railway restart`

### ❌ "Memory limit exceeded"
**Cause**: Large files or memory leak
**Fix**:
1. Reduce MAX_SIZE in code (currently 50MB)
2. Restart bot: `railway restart`
3. Monitor with `/status` command
4. Upgrade Railway plan for more memory

---

## 🧪 Local Docker Testing

Before deploying, test locally:

```bash
# Copy env template
cp .env.example .env
# Edit .env with your tokens

# Build Docker image
docker build -t tg-bot .

# Run container
docker run -p 3000:3000 --env-file .env tg-bot

# Test health endpoint
curl http://localhost:3000/health

# Check logs
docker logs <container_id>
```

---

## 📁 Project Structure

```
.
├── index.js                 # Main bot (production-ready)
├── Dockerfile              # Docker with Alpine packages
├── nixpacks.toml           # Railway Nix config (yt-dlp, ffmpeg)
├── Procfile                # Process definition
├── package.json            # Dependencies
├── .env.example            # Environment template
├── .gitignore              # Excludes secrets
├── deployment-checker.html # Browser-based checker ⭐ NEW
├── README-COOKIES.md       # Cookie setup guide ⭐ NEW
└── DEPLOYMENT.md           # This file
```

---

## 🎯 Quick Start Checklist

- [ ] Code pushed to GitHub ✅
- [ ] Railway project created
- [ ] `BOT_TOKEN` set in Railway Variables
- [ ] Deployed successfully
- [ ] Health endpoint returns `{"status":"ok"}`
- [ ] Bot responds to `/start` on Telegram
- [ ] Video download works
- [ ] (Optional) `COOKIES_B64` set for Instagram
- [ ] (Optional) `GROQ_API_KEY` set for AI features
- [ ] (Optional) `ADMIN_ID` set for admin commands

---

## 📈 Production Checklist

| Item | Status | Notes |
|------|--------|-------|
| HTTPS enabled | ✅ Railway handles | Auto SSL |
| Health checks | ✅ `/health` | Returns JSON |
| Error handling | ✅ Retry + graceful | 2 retries |
| Logging | ✅ Structured | See `railway logs` |
| Secrets management | ✅ Env vars | Never in code |
| Rate limiting | ✅ Per user | Configurable |
| File cleanup | ✅ Auto | `/tmp` cleaning |
| Cookie support | ✅ Both methods | Env var or file |
| Monitoring | ✅ Health + Metrics | `/metrics` endpoint |
| Graceful shutdown | ✅ SIGTERM | Clean exit |

---

## 🔗 Useful Links

- **Code Repository**: https://github.com/soumyachk101/Everything_Downloader_bot
- **Railway Dashboard**: https://railway.app/dashboard
- **BotFather**: https://t.me/botfather
- **Groq Console**: https://console.groq.com
- **Health Check**: `https://your-app.up.railway.app/health`
- **Metrics**: `https://your-app.up.railway.app/metrics`

---

## 📞 Support

Issues? Check:
1. **Logs**: `railway logs` (most helpful!)
2. **Health endpoint**: `curl /health`
3. **Cookies guide**: `README-COOKIES.md`
4. **Deployment checker**: Open `deployment-checker.html` in browser

---

**🎉 You're all set! Deploy now and enjoy your Telegram download bot!**