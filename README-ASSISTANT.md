# 🚀 Railway Deployment Assistant

## Quick Start

1. **Open the Assistant**
   - Double-click `railway-assistant.html` in your file explorer
   - Or open it in any browser (Chrome recommended)

2. **Go to "Deploy" Tab**
   - Click **"Open Railway Dashboard"** to open railway.app
   - Or click **"Deploy to Railway Now"** for one-click deployment

3. **Switch to "Verify" Tab**
   - Enter your Railway app URL (e.g., `https://your-app.up.railway.app`)
   - Click **"🔍 Run Checks"**
   - See real-time verification results

---

## Features

### 🎯 **4 Tabs:**

1. **🚀 Deploy** - Quick links + one-click deployment
2. **🔍 Verify** - Health checks + diagnostics
3. **⚙️ Config** - Environment variables reference
4. **📚 Help** - Documentation + quick commands

### ✨ **Live Features:**

- ✅ Auto-detects Railway accessibility
- ✅ One-click open Railway dashboard
- ✅ One-click deploy from GitHub
- ✅ Real-time health checks (`/health`, `/metrics`)
- ✅ Beautiful terminal-style logs
- ✅ Copy config to clipboard
- ✅ Floating quick-check button
- ✅ Mobile responsive

---

## How to Use

### Step 1: Deploy to Railway

**Option A: Manual (Recommended for first time)**
1. Click "Open Railway Dashboard"
2. Create new project
3. Connect GitHub repo
4. Set BOT_TOKEN variable
5. Deploy

**Option B: One-Click**
1. Click "Deploy to Railway Now"
2. Authorize Railway on GitHub
3. Set variables
4. Done!

### Step 2: Verify Deployment

1. Copy your Railway app URL from dashboard
2. Paste in "Verify" tab
3. Click "Run Checks"
4. Wait for all checks to turn green ✅

### Step 3: Test Bot

1. Open Telegram
2. Start chat with your bot
3. Send `/start`
4. Send a YouTube link
5. Should download successfully!

---

## Quick Check Button

Hover over the **🔍 floating button** (bottom-right) for instant access to verification.

---

## Browser Compatibility

- ✅ Chrome (recommended)
- ✅ Firefox
- ✅ Edge
- ✅ Safari
- ✅ Any modern browser (no installation needed)

---

## Notes

- **No data sent** to external servers (all checks run locally in browser)
- **CORS restrictions** apply: Railway must be accessible from your browser
- **Railway Status** badge shows if Railway is reachable
- **Health checks** require your app to be deployed and running

---

## Troubleshooting

### "Cannot access Railway dashboard"
- Check internet connection
- Verify you're logged into Railway
- Try opening railway.app directly

### "Checks failing with CORS error"
- Railway app is not deployed yet
- App URL is incorrect
- App is still building
- Wait 30-60 seconds after deploy

### "Floating button not appearing"
- Refresh the page
- Check browser console (F12)
- Ensure JavaScript is enabled

---

## Files in This Package

```
railway-assistant.html    ← Open this!
deployment-checker.html   ← Standalone checker (can be used separately)
DEPLOYMENT.md             ← Complete deployment guide
README-COOKIES.md         ← Cookie setup guide
index.js                  ← Bot code
Dockerfile                ← Docker config
nixpacks.toml             ← Railway Nix config
Procfile                  ← Process definition
```

---

## Support

- Issues? Check `DEPLOYMENT.md`
- Cookie help? See `README-COOKIES.md`
- Code? https://github.com/soumyachk101/Everything_Downloader_bot

---

**Ready to deploy? Open `railway-assistant.html` now!** 🚀
