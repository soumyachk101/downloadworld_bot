# Railway Deployment Guide

## ✅ Changes Made (v3.0 Production Ready)

### 🐛 Bug Fixes
- Fixed `Dockerfile` to include `yt-dlp` package (Alpine)
- Fixed nixpacks.toml configuration for Railway/NixOS
- Fixed yt-dlp binary detection and error handling

### 🚀 Production Features Added
1. **Config validation** - validates BOT_TOKEN and GROQ_API_KEY formats on startup
2. **Health check server** - HTTP server on PORT env var (default 3000)
   - `GET /health` - returns JSON health status
   - `GET /metrics` - Prometheus-compatible metrics
3. **Retry logic** - yt-dlp commands now retry 2 times with 2s delay
4. **Better error handling** - detailed logging, graceful degradation
5. **Admin commands** (restricted via ADMIN_ID):
   - `/status` - shows uptime, memory, session count
   - `/clean` - cleans temp download directories
6. **Graceful shutdown** - proper SIGINT/SIGTERM handling
7. **Enhanced yt-dlp options**:
   - `--retries`, `--fragment-retries` for network reliability
   - `--continue` to resume partial downloads
   - `--no-overwrites` to avoid re-downloads
8. **Improved logging** - timestamped, levels (info/error), file sizes

### 📁 File Structure
```
.
├── index.js          # Main bot (production-ready)
├── Dockerfile        # Docker with yt-dlp + ffmpeg
├── nixpacks.toml     # Railway Nix config (ffmpeg, yt-dlp)
├── Procfile          # Railway process type
├── package.json      # Dependencies
├── .env.example      # Environment template
└── .gitignore        # Clean repo
```

## 🚀 Deploy to Railway

### Prerequisites
1. GitHub repository with this code
2. Telegram Bot Token from @BotFather
3. (Optional) Groq API key from console.groq.com

### Steps
1. Push code to GitHub
2. Go to [railway.app](https://railway.app) > New Project
3. Connect your GitHub repository
4. Set environment variables in Railway dashboard:
   - **Required**: `BOT_TOKEN=your_telegram_bot_token`
   - **Optional**: `GROQ_API_KEY=your_groq_key`
   - **Optional**: `ADMIN_ID=your_telegram_user_id` (for admin commands)
5. Deploy! Railway auto-detects nixpacks.toml

### Verify Deployment
```bash
# Check logs
railway logs

# Check health endpoint
curl https://your-app.up.railway.app/health

# Check metrics
curl https://your-app.up.railway.app/metrics
```

## 🐳 Local Docker Testing

```bash
# Copy environment file
cp .env.example .env
# Edit .env with your tokens

# Build and run
docker build -t tg-bot .
docker run -p 3000:3000 --env-file .env tg-bot

# Check health
curl http://localhost:3000/health
```

## ⚠️ Important Notes

### Railway Specific
- Railway uses **NixOS**, not standard Linux
- Standalone yt-dlp binaries fail due to non-standard dynamic linker (`/lib64/ld-linux-x86-64.so.2` missing)
- **Solution**: Use Nix packages (nixpacks.toml) - binaries work with musl libc
- yt-dlp installs to `/usr/bin/yt-dlp` and is in PATH

### Docker/Local Specific
- Alpine Linux uses **musl libc**, not glibc
- Standalone yt-dlp binary (glibc) won't work
- **Solution**: Use Alpine package `yt-dlp` (compiled for musl)
- Dockerfile uses `apk add yt-dlp` (Alpine package manager)

### File System
- Downloads go to `/tmp/dl_<user>_<timestamp>` (ephemeral)
- Auto-cleaned after sending or on error
- 50MB file size limit (Telegram bot API limitation)

### Session Storage
- In-memory sessions (per-user AI mode state)
- Not persistent across restarts (acceptable for this use case)
- Railway restarts will clear sessions automatically

## 🐛 Troubleshooting

### "yt-dlp not found" error
**Railway**: Ensure `nixpacks.toml` has `["ffmpeg", "yt-dlp"]`
**Docker**: Ensure `Dockerfile` has `RUN apk add yt-dlp`

### "YouTube block" error
Some videos require cookies for age-restricted/region-locked content:
1. Export cookies from browser (cookies.txt format)
2. Upload `cookies.txt` to bot's root directory
3. Bot auto-detects and uses it

### 409 Conflict on launch
Multiple bot instances running. Bot auto-retries with 5s delay.
Railway might spawn multiple workers. Use `--concurrency 1` if needed.

### Health check failing
- Check PORT environment variable (default: 3000)
- Ensure port is not in use
- Check logs for binding errors

### Memory issues
- Large downloads can consume memory
- Monitor via `/status` admin command
- Restart bot if memory grows unbounded

## 📊 Monitoring

### Health Endpoint
```bash
curl https://your-app.railway.app/health
```
Returns:
```json
{
  "status": "ok",
  "uptime": 1234,
  "memory": {
    "rss": 12345678,
    "heapUsed": 1234567,
    "heapTotal": 2345678
  },
  "timestamp": "2025-03-27T10:30:00.000Z",
  "version": "3.0"
}
```

### Metrics Endpoint (Prometheus)
```bash
curl https://your-app.railway.app/metrics
```

## 🔐 Security
- Never commit `.env` file (in .gitignore)
- Use strong random BOT_TOKEN
- Set `ADMIN_ID` to restrict admin commands
- cookies.txt contains session data - keep secret

## 📝 Version History
- **v3.0** - Production ready with health checks, retry, admin commands
- **v2.0** - Docker + AI features
- **v1.0** - Basic download bot
