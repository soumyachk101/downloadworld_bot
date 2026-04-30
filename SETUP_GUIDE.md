# Bot Setup & Troubleshooting Guide

## Immediate Action Required

### 1. ROTATE YOUR CREDENTIALS (URGENT)
Your credentials have been exposed in logs. Immediately:
- Go to @BotFather on Telegram and regenerate your BOT_TOKEN
- Go to Groq Console and generate a new GROQ_API_KEY
- Update your `.env` file with the new credentials
- Restart the bot

## Current Issues

### YouTube Downloads - BOT DETECTION ERROR
**Symptom:** `ERROR: [youtube] Sign in to confirm you're not a bot`

**Cause:** YouTube is blocking yt-dlp's anonymous access.

**Solution Option 1: Cookies (Recommended)**
1. Install a cookies export extension:
   - Chrome/Firefox: "Get cookies.txt" or "cookies.txt"
   - Edge: "Get cookies.txt"

2. Export cookies while logged into YouTube:
   - Go to https://youtube.com
   - Click the extension icon
   - Export/Save as `youtube_cookies.txt`

3. Place the file in your project directory (JSON exports are OK — the bot auto-converts to Netscape)

4. Add to `.env`:
   ```
   YOUTUBE_COOKIES_FILE=youtube_cookies.txt
   ```

5. Restart the bot

**Solution Option 2: Visitor Data (No Cookies File)**
If you don't want to manage cookies files, you can extract YouTube's visitor data token:

1. Open YouTube.com in your browser (logged in)
2. Press F12 to open DevTools
3. Go to Console tab
4. Paste this command and press Enter:
   ```javascript
   copy(JSON.parse(localStorage.getItem('youtube-visitor-data')))
   ```
5. This copies an object to clipboard. Copy the `visitorData` value.
6. Add to `.env`:
   ```
   YOUTUBE_EXTRACTOR_ARGS="youtube:player_skip=webpage,configs;visitor_data=YOUR_VISITOR_DATA_HERE"
   ```
7. Restart the bot

**Note:** Visitor data may also expire, but typically lasts longer than cookies. You may need to re-extract periodically.

### Instagram - CHECKPOINT REQUIRED
**Symptom:** `Instaloader login failed: Login: Checkpoint required` OR `"fail" status`

**Cause:** Instagram requires additional verification (2FA/session validation). This often happens when logging in from a different location (e.g., cloud server/container).

**Solutions:**

#### Option 1: Instagram Cookies (Easiest for Containers/Cloud)
1. Install a cookies export extension in your browser (e.g., "Get cookies.txt").
2. Go to https://instagram.com while logged in.
3. Export cookies as `instagram_cookies.txt` (Netscape or JSON format — JSON is auto-converted).
4. Upload `instagram_cookies.txt` to your bot's directory.
5. Add to `.env`:
   ```
   INSTAGRAM_COOKIES_FILE=instagram_cookies.txt
   ```
6. Remove `INSTA_PASSWORD` from `.env` (optional, cookies are more reliable).
7. Restart the bot.

The bot will load the cookies and create a session file automatically.

#### Option 2: Manual Session File (Local/Linux)
```bash
# Install instaloader if not already installed
pip install instaloader

# Create session file
instaloader --login _lost_in_pixels
# Enter password when prompted
```

The session file will be saved to platform-specific location:
- **Windows:** `%LOCALAPPDATA%\Instaloader\session-_lost_in_pixels`
- **Linux/Mac:** `~/.config/instaloader/session-_lost_in_pixels`

The bot automatically uses this if it exists.

**Note:** If you're running the bot in a container (Railway/Render), you need to create the session file inside that container, which is difficult. Use **Option 1 (cookies)** instead.

#### Option 3: Use Cookies to Create Session (One-time)
If you already have Instagram cookies exported:
```bash
instaloader --load-cookies instagram_cookies.txt --login _lost_in_pixels
```
This creates a session file you can reuse.

#### Option 4: Resolve Checkpoint Manually
1. Log into Instagram.com in your browser
2. Complete any security challenges (email/SMS verification)
3. Make sure your account has no login alerts
4. Retry creating the session file

## Verify Your Setup

Run the setup assistant:
```bash
python setup_assistant.py
```

## Environment Variables Reference

### Required:
- `BOT_TOKEN`: Telegram bot token from @BotFather
- `GROQ_API_KEY`: Groq API key for AI features

### Optional:
- `INSTA_USERNAME`: Instagram username (for private posts)
- `INSTA_PASSWORD`: Instagram password (for private posts) - prefer session file or cookies
- `INSTAGRAM_COOKIES_FILE`: Path to Instagram cookies.txt file (byass checkpoint issues, great for containers)
- `YOUTUBE_COOKIES_FILE`: Path to cookies.txt file for YouTube
- `YOUTUBE_EXTRACTOR_ARGS`: yt-dlp extractor arguments (alternative to cookies), e.g. `"youtube:player_skip=webpage,configs;visitor_data=VISITOR_DATA"`
- `TELEGRAM_STREAMING_LIMIT_MB`: Size (MB) above which media is sent as document (default: 50)
- `TELEGRAM_MAX_UPLOAD_MB`: Max file size (MB) bot will attempt to send (default: 500)

## File Structure
```
project/
├── bot.py
├── .env (with your credentials)
├── youtube_cookies.txt (optional, for YouTube)
├── setup_assistant.py (this helper script)
├── downloads/ (temporary, auto-cleaned)
└── ~/.config/instaloader/session-USERNAME (optional Instagram session)
```

## Testing

1. Start the bot:
   ```bash
   python bot.py
   ```

2. Send `/start` to test basic functionality

3. Send a YouTube link (with cookies configured) to test downloads

4. Send an Instagram public post link to test Instagram

## Troubleshooting

### Bot won't start
- Check BOT_TOKEN is valid
- Check .env file is in the same directory as bot.py
- Run: `python -c "from dotenv import load_dotenv; load_dotenv(); print('OK')"`

### YouTube still blocked even with cookies
- JSON cookies are auto-converted, but make sure the export isn't truncated.
- Cookies can expire; export a fresh file if downloads start failing again.
- Try logging into YouTube in the browser first

### Instagram "Private post" error
- The post is actually private, or
- Your login failed - check credentials
- Look for checkpoint/2FA on Instagram.com
- Create a session file using instaloader CLI

### Downloads over 50MB
Bot sends files above 50MB as documents and supports up to 500MB downloads.

## Security Best Practices

1. **Never commit .env file** - Already in .gitignore ✓
2. **Rotate credentials if exposed** - Do it now!
3. **Use session files instead of passwords** when possible
4. **Cookie files have your browsing history** - secure them
5. **Restart bot after credential changes**

## Need Help?

Check the logs in the console for error messages.
Refer to:
- yt-dlp FAQ: https://github.com/yt-dlp/yt-dlp/wiki/FAQ
- Instaloader docs: https://instaloader.readthedocs.io/
