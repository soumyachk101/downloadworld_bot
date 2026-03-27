# 📦 Cookies Setup Guide (Instagram/Facebook/Youtube)

## 🎯 Problem

Instagram, Facebook, and some YouTube content require **authentication** to access:
- Age-restricted videos
- Private/restricted content
- Region-locked content
- Rate-limited requests

**Solution**: Provide cookies from your logged-in session.

---

## 🔧 TWO METHODS TO PROVIDE COOKIES

### **Method 1: COOKIES_B64 Environment Variable (Recommended) ⭐**

This is the **easiest** method for Railway deployment - no file uploads needed.

#### Step 1: Export cookies.txt from browser

**Chrome/Edge:**
1. Go to chrome://extensions/
2. Search for: **"Get cookies.txt"** by EJan**
3. Install extension
4. Login to Instagram/Facebook/Youtube
5. Click extension icon → "Export" → save as `cookies.txt`

**Firefox:**
1. Go to addons.mozilla.org
2. Search for: **"cookies.txt"** by pde
3. Install addon
4. Login to the site
5. Right-click → "Export cookies" → save as `cookies.txt`

**Safari:**
- Use "Export Cookies" extension or manually copy cookies

#### Step 2: Encode cookies.txt to Base64

**Windows PowerShell:**
```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes('cookies.txt'))
```
Copy the output (single long line).

**Mac/Linux:**
```bash
base64 -i cookies.txt
```

#### Step 3: Set COOKIES_B64 in Railway

1. Go to Railway dashboard → Your project
2. **Variables** tab
3. Add new variable:
   ```
   Name: COOKIES_B64
   Value: <paste base64 string here>
   ```
4. Save
5. **Redeploy** the bot

✅ **Done!** Bot will auto-decode and use cookies.

---

### **Method 2: Upload cookies.txt File**

If you prefer file-based approach:

#### Step 1: Export cookies.txt (same as Method 1)

#### Step 2: Upload to Railway Files

1. Railway dashboard → Your project
2. **Files** tab (left sidebar)
3. Upload button → Select `cookies.txt`
4. Wait for upload to complete

#### Step 3: Redeploy

- Click **"Deploy"** button or wait for auto-deploy
- Bot will automatically detect `cookies.txt` in current directory

✅ **Done!** No environment variable needed.

---

## 🐛 Troubleshooting

### "Cookies not working"
- **Refresh cookies**: Cookies expire after some weeks (1-2). Re-export fresh.
- **Check domain**: Make sure cookies are for correct domain (instagram.com, youtube.com)
- **Format check**: cookies.txt should be **Netscape format** (most exporters produce this)
- **Permissions**: Ensure cookies.txt is readable (644)

### "Base64 string too long"
- Railway limit: 64KB for env var (more than enough for cookies)
- If still failing, cookies.txt file method might be better

### "Still getting rate limit errors"
- Railway uses **shared IP addresses** - multiple users from same IP
- **Solution 1**: Use fresh cookies (less used accounts)
- **Solution 2**: Add delays between downloads (code already has 2s retry)
- **Solution 3**: Switch to different server (Railway free tier uses shared IP)

### "File upload not working"
- Railway free tier: Files are ephemeral (cleared on redeploy)
- **Better**: Use COOKIES_B64 method (persistent with env vars)
- **Alternative**: Mount persistent volume (paid Railway)

---

## 🔄 Cookie Refresh Schedule

| Platform | Typical Expiry | Tips |
|----------|---------------|------|
| Instagram | 1-2 weeks | Use fresh account, enable 2FA extends life |
| YouTube | 2-4 weeks | Google cookies last longer |
| Facebook | 2-3 weeks | Same as Instagram |

**Best Practice**: Set a weekly reminder to refresh cookies.

---

## 📊 Testing Your Cookies

After deployment:

1. **Check logs**: `railway logs`
2. Look for: `🍪 Using cookies from COOKIES_B64 env var` or `🍪 Using cookies.txt`
3. Test with Instagram link that previously failed
4. Should see: `✅ yt-dlp completed successfully`

---

## 🚀 Quick Reference

### Export (Chrome)
```
Extension: "Get cookies.txt"
→ Login to site
→ Click extension → Export → cookies.txt
```

### Encode (Windows PowerShell)
```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes('cookies.txt'))
```

### Set (Railway)
```
Variable: COOKIES_B64
Value: <base64 string>
```

### Verify (After Deploy)
```bash
railway logs | find "🍪"
```
Should show: `🍪 Using cookies from COOKIES_B64 env var`

---

## ❓ FAQ

**Q: Do I need cookies for YouTube?**
A: Not usually, unless age-restricted or region-locked.

**Q: Will cookies work for all Instagram posts?**
A: Most yes, but some may still fail due to Instagram's aggressive anti-bot measures.

**Q: Can I reuse same cookies for multiple Railway projects?**
A: Yes, copy same COOKIES_B64 value to different projects.

**Q: Are cookies secure on Railway?**
A: Yes, env vars are encrypted. But treat cookies.txt like passwords - don't share.

**Q: What if I don't have cookies?**
A: Bot works for public content without cookies. Only restricted content needs cookies.

**Q: Can I automate cookie refresh?**
A: Advanced: Set up external service to auto-refresh cookies (not recommended for beginners).

---

## 📚 Additional Resources

- yt-dlp Cookies Wiki: https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp
- Get cookies.txt Chrome Extension: https://chrome.google.com/webstore/detail/get-cookiestxt/bgaddhkoddajcdgocldbbfleckgcbcid
- Railway Variables Docs: https://docs.railway.app/references/env-var

---

**🎯 TL;DR**: Export cookies → Base64 encode → Paste in COOKIES_B64 → Redeploy! ✅
