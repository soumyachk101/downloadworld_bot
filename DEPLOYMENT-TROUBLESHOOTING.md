# 🚨 Railway Deployment Troubleshooting

## Quick Checklist

### 1. ✅ Verify Environment Variables in Railway

**MUST DO:** Go to Railway dashboard → Your Project → **Variables** tab

Ensure these EXACT variables exist (case-sensitive):

```
BOT_TOKEN = <your-bot-token-from-botfather>
GROQ_API_KEY = <your-groq-api-key>
```

⚠️ **Common mistake:** Variables not set, or typo in name (e.g., `bot_token` instead of `BOT_TOKEN`)

---

### 2. ✅ Check Build Status

In Railway dashboard:
- **Deployments** tab → Check latest deployment status
- Should show **"Succeeded"** (green) for build
- If failed → click on it → view **"Logs"**

---

### 3. ✅ Check Runtime Logs

In Railway dashboard:
- **Logs** tab (or `railway logs` if CLI installed)
- Look for:
  - ✅ `🤖 Starting Telegram Bot...`
  - ✅ `✅ Bot is running...`
  - ❌ Any `ERROR`, `Traceback`, or `ValueError`

**Expected startup logs:**
```
🤖 Starting Telegram Bot...
✅ Bot is running...
```

If you see `ValueError: BOT_TOKEN environment variable is required!` → Token not set.

---

### 4. ✅ Verify Bot Token Works

Test your bot token locally (if Python installed):

```bash
python -c "
from telegram import Bot
import asyncio
bot = Bot(token='<your-bot-token>')
try:
    info = asyncio.run(bot.get_me())
    print('✅ Bot valid:', info.username)
except Exception as e:
    print('❌ Bot token invalid:', e)
"
```

If token is invalid, get a new one from @BotFather.

---

### 5. ✅ Force Redeploy

Sometimes Railway caches old builds:

**Option A:** Railway dashboard
- Settings → **"Force Redeploy"** button

**Option B:** Railway CLI
```bash
railway up
```

---

### 6. ✅ Check if Bot Was Blocked

Even if bot works, if **you** blocked it on Telegram, it won't respond.

- Search your bot username in Telegram
- If you see "Start" button → not blocked ✓
- If chat exists → make sure you clicked **Start**

---

## 🔍 Common Errors & Fixes

### Error: `No module named 'yt_dlp'`
**Cause:** Dependencies not installed properly.
**Fix:** Ensure `pip install -r requirements.txt` completes in build logs.

---

### Error: `ValueError: BOT_TOKEN environment variable is required!`
**Cause:** BOT_TOKEN not set in Railway Variables.
**Fix:** Add `BOT_TOKEN` variable in Railway dashboard.

---

### Error: `NetworkError: could not connect to Telegram`
**Cause:** Railway outbound network blocked (rare).
**Fix:** Check Railway status page, try redeploy.

---

### Build succeeds but bot exits immediately
**Cause:** Exception at startup (import error, etc.).
**Fix:** Check logs for traceback, fix code, redeploy.

---

### Bot responds but downloads fail
**Cause:** yt-dlp/instaloader issues, or URLs unsupported.
**Fix:** Check logs for download errors, test with known working URL.

---

## 📋 Test After Deployment

Once bot is running (green status), test in Telegram:

1. **Start command:**
```
/start
```
Expected: Welcome message with 4 AI buttons.

2. **AI test:**
Click "😂 Roast Karo" → Type your name → Should get 4-line roast.

3. **Download test:**
Send YouTube Shorts URL:
```
https://www.youtube.com/shorts/abc123
```
Expected: "⏳ Download ho raha hai... ruk bhai!" then video sent.

---

## 📊 View Logs in Real-Time

**Via Railway CLI:**
```bash
# Install CLI first
npm i -g @railway/cli
railway login
railway link <your-project>
railway logs --follow
```

**Via Dashboard:**
- Project → Logs tab → Auto-refreshes

---

## 🔄 Restart Bot

If bot is frozen or not responding:

**CLI:**
```bash
railway restart
```

**Dashboard:**
- Deployments → New Deployment → Trigger Deploy

---

## ⚠️ Still Not Working?

1. **Share the Railway logs** (full output from startup)
2. **Confirm:** Did you set both environment variables in Railway?
3. **Confirm:** Build succeeded (green checkmark)?
4. **Confirm:** You're testing in Telegram with the correct bot username?

Copy-paste the logs here and I'll diagnose!

---

## 🆓 Free Tier Quotas

Railway free tier has limits:
- **5 hours/month** sleep quota (bot inactive)
- **$5 credit** monthly (enough for small bot)
- If quota exhausted → bot stops until next month

Check: Dashboard → Usage tab

---

✅ **Most likely:** You forgot to set `BOT_TOKEN` in Railway Variables tab.

Go set it now, then redeploy! 🚀
