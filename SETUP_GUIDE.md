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

**Solution:**
1. Install a cookies export extension:
   - Chrome: "Get cookies.txt" or "cookies.txt"
   - Firefox: "cookies.txt"
   - Edge: "Get cookies.txt"

2. Export cookies while logged into YouTube:
   - Go to https://youtube.com
   - Click the extension icon
   - Export/Save as `youtube_cookies.txt`

3. Place the file in your project directory

4. Add to `.env`:
   ```
   YOUTUBE_COOKIES_FILE=youtube_cookies.txt
   ```

5. Restart the bot

**Note:** Cookies expire after some time. You'll need to re-export periodically.

### Instagram - CHECKPOINT REQUIRED
**Symptom:** `Instaloader login failed: Login: Checkpoint required`

**Cause:** Instagram requires additional verification (2FA/session validation).

**Solutions (try in order):**

#### Option 1: Manual Session File (Recommended)
```bash
# Install instaloader if not already installed
pip install instaloader

# Create session file
instaloader --login _lost_in_pixels
# Enter password when prompted
```

The session file will be saved to `~/.config/instaloader/session-_lost_in_pixels`. The bot automatically uses this if it exists.

#### Option 2: Resolve Checkpoint Manually
1. Log into Instagram.com in your browser
2. Complete any security challenges (email/SMS verification)
3. Make sure your account has no login alerts
4. Retry the bot

#### Option 3: Use Session File with Cookies
Instead of password, you can export Instagram cookies and convert to instaloader session:
```bash
# Export cookies using browser extension
# Then convert:
instaloader --load-cookies insta_cookies.txt --login _lost_in_pixels
```

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
- `INSTA_PASSWORD`: Instagram password (for private posts) - prefer session file
- `YOUTUBE_COOKIES_FILE`: Path to cookies.txt file for YouTube

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
- Make sure cookies file is in Netscape format (not JSON)
- Re-export cookies (they may have expired)
- Try logging into YouTube in the browser first

### Instagram "Private post" error
- The post is actually private, or
- Your login failed - check credentials
- Look for checkpoint/2FA on Instagram.com
- Create a session file using instaloader CLI

### Downloads over 50MB
Bot limits downloads to 50MB to comply with Telegram's file size limits.

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
