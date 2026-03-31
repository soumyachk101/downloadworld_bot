#!/usr/bin/env python3
"""
Setup script to help configure the Telegram bot.
This script will:
1. Warn about exposed credentials
2. Guide you through creating YouTube cookies
3. Validate your .env configuration
"""

import os
import sys
from pathlib import Path

# Fix Windows console encoding (same as bot.py)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

def banner():
    print("=" * 60)
    print("   Telegram Bot Setup Assistant")
    print("=" * 60)
    print()

def check_env_file():
    """Check if .env file exists and has required variables."""
    env_path = Path(".env")
    if not env_path.exists():
        print("❌ .env file not found!")
        print("   Please create one from .env.example")
        return False

    print("✅ .env file found")
    return True

def check_exposed_credentials():
    """Warn if .env file is committed or contains sensitive data."""
    print("\n🔒 Security Check:")
    print("-" * 40)

    # Check if .env is in git
    result = os.system('git check-ignore -q .env 2>/dev/null')
    if result == 0:
        print("⚠️  WARNING: .env is in .gitignore (GOOD)")
    else:
        print("❌ WARNING: .env might be tracked by git!")
        print("   Run: git rm --cached .env")
        print("   Then add .env to .gitignore")

    # Check if .env has placeholder values
    with open('.env', 'r') as f:
        content = f.read()
        if 'telegram_bot_token_here' in content:
            print("⚠️  WARNING: .env contains placeholder values")
            print("   You need to replace them with real values")
        else:
            print("✅ .env appears to have real values")
            print("   ⚠️  Remember: Never commit real credentials!")

def validate_env_vars():
    """Check required environment variables."""
    print("\n📋 Environment Variables Check:")
    print("-" * 40)

    from dotenv import load_dotenv
    load_dotenv()

    required_vars = {
        'BOT_TOKEN': 'Telegram Bot Token',
        'GROQ_API_KEY': 'Groq API Key (for AI features)',
    }

    optional_vars = {
        'INSTA_USERNAME': 'Instagram Username (for downloading private posts)',
        'INSTA_PASSWORD': 'Instagram Password (for downloading private posts)',
        'YOUTUBE_COOKIES_FILE': 'YouTube Cookies File Path (to avoid bot detection)',
    }

    all_ok = True
    for var, desc in required_vars.items():
        value = os.getenv(var)
        if value and 'placeholder' not in value.lower():
            print(f"✅ {var}: set")
        else:
            print(f"❌ {var}: MISSING or placeholder")
            all_ok = False

    print("\nOptional variables:")
    for var, desc in optional_vars.items():
        value = os.getenv(var)
        if value:
            print(f"✅ {var}: set")
        else:
            print(f"⚠️  {var}: not set (bot will work with limitations)")

    return all_ok

def guide_youtube_cookies():
    """Guide user to create YouTube cookies file."""
    print("\n🍪 YouTube Cookies Setup:")
    print("-" * 40)
    print("To avoid YouTube's bot detection, you need to export cookies from your browser.")
    print("\nSteps:")
    print("1. Install a cookie export extension for your browser:")
    print("   - Chrome/Firefox: 'Get cookies.txt' or 'cookies.txt' extension")
    print("   - Edge: 'Get cookies.txt'")
    print("\n2. While logged into YouTube in your browser:")
    print("   - Go to youtube.com")
    print("   - Click the extension icon")
    print("   - Export cookies to a file")
    print("\n3. Save the file as 'youtube_cookies.txt' in this directory")
    print("\n4. Add to .env: YOUTUBE_COOKIES_FILE=youtube_cookies.txt")
    print("\n5. Restart the bot")
    print("\n⚠️  Important: YouTube cookies expire! You may need to re-export periodically.")

def guide_instagram_setup():
    """Guide user about Instagram setup."""
    print("\n📸 Instagram Setup:")
    print("-" * 40)
    print("Current status: INSTA_USERNAME and INSTA_PASSWORD are set.")
    print("\n⚠️  If you see 'Checkpoint required' errors:")
    print("1. Instagram may require additional verification (2FA)")
    print("2. Log in to Instagram.com in your browser first")
    print("3. Complete any security challenges")
    print("4. Try logging in again")
    print("\nAlternative: Use instaloader session file:")
    print("1. Run: pip install instaloader")
    print("2. Run: instaloader --login YOUR_USERNAME")
    print("3. Enter password when prompted")
    print("4. Session file will be saved automatically")
    print("5. The bot will use the session file if it exists")

def show_summary():
    print("\n" + "=" * 60)
    print("   Setup Complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Rotate any exposed credentials (BOT_TOKEN, GROQ_API_KEY)")
    print("2. Create YouTube cookies file if you need YouTube downloads")
    print("3. Set YOUTUBE_COOKIES_FILE in .env")
    print("4. Test Instagram login by running the bot")
    print("5. If issues persist, check session files: ~/.config/instaloader/")
    print("=" * 60)

def main():
    banner()

    if not check_env_file():
        sys.exit(1)

    check_exposed_credentials()

    if not validate_env_vars():
        print("\n❌ Missing required variables!")
        sys.exit(1)

    guide_youtube_cookies()
    guide_instagram_setup()
    show_summary()

if __name__ == "__main__":
    main()
