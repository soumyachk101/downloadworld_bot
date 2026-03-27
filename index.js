/**
 * Everything Downloader TG Bot — v3.0 (Production)
 *
 * Architecture
 * ────────────
 * • yt-dlp is installed via Nix (nixpacks.toml). This is the ONLY way it works
 *   on Railway because NixOS uses a non-standard dynamic linker path.
 *   Downloaded standalone ELF binaries get ENOENT — not because the file is
 *   missing, but because /lib64/ld-linux-x86-64.so.2 doesn't exist on NixOS.
 *
 * • Telegraf session middleware stores per-user AI mode state in memory.
 *
 * • All downloads go to /tmp/<unique_dir> and are cleaned up in finally blocks.
 *
 * • Every child process has an 'error' listener so the Node process never
 *   crashes on spawn failures.
 */

'use strict';

const { Telegraf, Markup, session } = require('telegraf');
const Groq                          = require('groq-sdk');
const { translate }                 = require('google-translate-api-x');
const schedule                      = require('node-schedule');
const path                          = require('path');
const fs                            = require('fs-extra');
const { execFile }                  = require('child_process');
const http                          = require('http');
require('dotenv').config();

/* ─────────────── Config & Validation ─────────────────────────────── */

const BOT_TOKEN    = process.env.BOT_TOKEN;
const GROQ_API_KEY = process.env.GROQ_API_KEY;
const MAX_SIZE     = 50 * 1024 * 1024;
const PORT         = process.env.PORT || 3000; // Railway health check port
const NODE_ENV     = process.env.NODE_ENV || 'production';

// Validate critical environment variables
function validateConfig() {
  const errors = [];

  if (!BOT_TOKEN) {
    errors.push('BOT_TOKEN environment variable is required');
  } else if (!/^\d+:[\w-]+$/.test(BOT_TOKEN)) {
    errors.push('BOT_TOKEN format invalid (should be: <number>:<string>)');
  }

  if (GROQ_API_KEY && !/^gsk_[\w-]+$/.test(GROQ_API_KEY)) {
    errors.push('GROQ_API_KEY format looks invalid');
  }

  if (errors.length > 0) {
    console.error('❌ Configuration errors:');
    errors.forEach(err => console.error(`   - ${err}`));
    return false;
  }

  console.log('✅ Configuration validated');
  return true;
}

if (!validateConfig()) {
  process.exit(1);
}

const bot  = new Telegraf(BOT_TOKEN);
const groq = GROQ_API_KEY ? new Groq({ apiKey: GROQ_API_KEY }) : null;

/* ─────────────── Session ─────────────────────────────────────── */

bot.use(session());

/* ─────────────── Helpers ─────────────────────────────────────── */

const clean = (d) => fs.remove(d).catch(() => {});

async function edit(ctx, id, txt) {
  try {
    await ctx.telegram.editMessageText(ctx.chat.id, id, null, txt);
  } catch (e) {
    // Message already deleted or can't be edited - ignore
    if (!e.message.includes('message to edit not found')) {
      console.debug('Edit failed:', e.message);
    }
  }
}

function retry(operation, maxRetries = 3, delay = 1000) {
  return new Promise((resolve, reject) => {
    const attempt = (n) => {
      operation()
        .then(resolve)
        .catch(async (err) => {
          if (n >= maxRetries) {
            reject(err);
            return;
          }
          console.log(`⏳ Retry ${n + 1}/${maxRetries} after ${delay}ms...`);
          setTimeout(() => attempt(n + 1), delay);
        });
    };
    attempt(0);
  });
}

async function ai(prompt, sys) {
  if (!groq) return 'GROQ_API_KEY set nahi hai bhai! 🙏';
  try {
    const r = await groq.chat.completions.create({
      messages: [{ role: 'system', content: sys }, { role: 'user', content: prompt }],
      model: 'llama-3.1-8b-instant',
    });
    return r.choices[0]?.message?.content ?? 'AI ne kuch nahi bola 🤷';
  } catch (e) {
    console.error('AI err:', e.message);
    return 'AI busy hai bhai! 🙏';
  }
}

async function walk(dir) {
  const result = [];
  for (const e of await fs.readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name);
    e.isDirectory() ? result.push(...await walk(full)) : result.push(full);
  }
  return result;
}

function isBlock(e) {
  const m = (e?.message || '').toLowerCase();
  return m.includes('sign in') || m.includes('confirm') || m.includes('403') || m.includes('youtube block');
}

/* ─────────────── yt-dlp download (with retry) ───────────────────── */

function ytdlp(url, dir, audio = false) {
  const operation = () => {
    return new Promise((resolve, reject) => {
      const fmt = audio
        ? 'bestaudio[ext=m4a]/bestaudio/best'
        : 'bestvideo[filesize<50M]+bestaudio/best[filesize<50M]/best';

      const args = [
        url,
        '-o', path.join(dir, '%(id)s.%(ext)s'),
        '--no-warnings',
        '--no-playlist',
        '--merge-output-format', 'mp4',
        '-f', fmt,
        '--no-check-certificate',
        '--geo-bypass',
        '--retries', '3',
        '--fragment-retries', '3',
        '--file-access-retries', '3',
        '--extractor-retries', '3',
        '--continue', // Resume partial downloads
        '--no-overwrites',
      ];

      if (fs.existsSync('cookies.txt')) {
        args.push('--cookies', 'cookies.txt');
        console.log('🍪 Using cookies.txt for authentication');
      }

      if (audio) {
        args.push('-x', '--audio-format', 'mp3', '--audio-quality', '192K');
      }

      // Additional options for better compatibility
      if (audio) {
        args.push('--prefer-ffmpeg');
      }

      console.log(`⬇ yt-dlp: downloading ${audio ? 'audio' : 'video'} from ${url.substring(0, 50)}...`);

      const p = execFile('yt-dlp', args, {
        timeout: 300_000, // 5 minutes
        maxBuffer: 1024 * 1024, // 1MB buffer
      }, (err, out, stderr) => {
        if (out) console.log(`   📤 yt-dlp: ${out.trim()}`);
        if (stderr) console.error(`   ⚠️ yt-dlp: ${stderr.trim()}`);

        if (err) {
          const errorMsg = (stderr || err.message).trim();
          console.error(`   ❌ yt-dlp failed: ${errorMsg}`);
          reject(new Error(errorMsg));
        } else {
          console.log(`   ✅ yt-dlp completed successfully`);
          resolve();
        }
      });

      p.on('error', (e) => {
        console.error(`   ❌ yt-dlp spawn error: ${e.message}`);
        reject(new Error(`yt-dlp spawn error: ${e.message}`));
      });

      p.stderr.on('data', (data) => {
        const msg = data.toString();
        // Only log error-level messages, not warnings
        if (msg.includes('ERROR') || msg.includes('FATAL')) {
          console.error(`   ⚠️ yt-dlp error: ${msg.trim()}`);
        }
      });
    });
  };

  // Retry logic for transient failures
  return retry(operation, 2, 2000).catch(err => {
    // Check for specific error patterns
    if (err.message.includes('sign in') ||
        err.message.includes('confirm') ||
        err.message.includes('403') ||
        err.message.includes('unavailable')) {
      throw new Error('YOUTUBE_BLOCK: ' + err.message);
    }
    throw err;
  });
}

/* ─────────────── Send result ─────────────────────────────────── */

async function send(ctx, sid, dir, audio = false) {
  try {
    const files = await walk(dir);
    const ext   = audio ? /\.mp3$/i : /\.(mp4|mkv|webm|mov|jpg|jpeg|png|webp|gif)$/i;
    const f     = files.find(x => ext.test(x));

    if (!f) {
      console.log(`   ❌ No media file found in ${dir}`);
      return edit(ctx, sid, 'Media nahi mili. Link ya format check kar! 😔');
    }

    const { size } = await fs.stat(f);
    console.log(`   📊 Found file: ${path.basename(f)} (${(size/1024/1024).toFixed(2)} MB)`);

    if (size > MAX_SIZE) {
      console.log(`   ⚠️ File too large: ${(size/1024/1024).toFixed(2)} MB > 50 MB`);
      return edit(ctx, sid, 'File 50 MB se badi hai! 😔 Try smaller video.');
    }

    const e = path.extname(f).toLowerCase();
    console.log(`   📤 Sending as ${audio ? 'audio' : 'media'}...`);

    if (audio || e === '.mp3') {
      await ctx.replyWithAudio({ source: f });
    } else if (['.mp4','.mkv','.webm','.mov'].includes(e)) {
      await ctx.replyWithVideo({ source: f });
    } else {
      await ctx.replyWithPhoto({ source: f });
    }

    await ctx.deleteMessage(sid).catch(() => {});
    console.log(`   ✅ Download & send complete`);
  } catch (e) {
    console.error(`   ❌ Send failed:`, e.message);
    throw e;
  }
}

/* ─────────────── /start ──────────────────────────────────────── */

bot.start(ctx => ctx.replyWithMarkdown(
  'Hello bhai! 👋\n\n' +
  '📥 *Media Download* — link bhej\n' +
  '🎵 `/mp3 <link>` — audio\n' +
  '🌐 `/translate <text>` — Hindi translate\n' +
  '⏰ `/remind 10m Chai` — reminder\n\n' +
  '🤖 *AI Fun* — buttons daba!',
  Markup.inlineKeyboard([
    [Markup.button.callback('😂 Roast','mode_roast'), Markup.button.callback('🎤 Shayari','mode_shayari')],
    [Markup.button.callback('🎵 Rap','mode_rap'),     Markup.button.callback('🔮 Fortune','mode_fortune')],
    [Markup.button.callback('📝 Story','mode_story'), Markup.button.callback('🍕 Recipe','mode_recipe')],
  ]),
));

/* ─────────────── /mp3 ────────────────────────────────────────── */

bot.command('mp3', async ctx => {
  const url = ctx.message.text.split(' ')[1];
  if (!url) return ctx.reply('Example: /mp3 https://youtu.be/...');

  const s   = await ctx.reply('⏳ MP3 download ho raha hai …');
  const dir = path.join('/tmp', `mp3_${ctx.from.id}_${Date.now()}`);
  await fs.ensureDir(dir);

  try {
    await ytdlp(url, dir, true);
    await send(ctx, s.message_id, dir, true);
  } catch (e) {
    console.error('mp3:', e.message);
    await edit(ctx, s.message_id, isBlock(e)
      ? 'YouTube block! 🛑 cookies.txt upload karo.'
      : `Error: ${e.message.slice(0, 200)}`);
  } finally { await clean(dir); }
});

/* ─────────────── /translate ──────────────────────────────────── */

bot.command('translate', async ctx => {
  const t = ctx.message.text.split(' ').slice(1).join(' ') || ctx.message.reply_to_message?.text || '';
  if (!t) return ctx.reply('Kya translate karun? 🙏');
  try {
    const r = await translate(t, { to: 'hi' });
    await ctx.reply(`🌐 *Translated:*\n\n${r.text}`, { parse_mode: 'Markdown' });
  } catch { await ctx.reply('Translation error! 🙏'); }
});

/* ─────────────── /remind ─────────────────────────────────────── */

bot.command('remind', async ctx => {
  const p = ctx.message.text.split(' ');
  if (p.length < 3) return ctx.reply('/remind 10m Chai peeni hai');
  const t = p[1], msg = p.slice(2).join(' ');
  let ms = parseInt(t) * 1000;
  if (t.endsWith('m')) ms = parseInt(t) * 60_000;
  if (t.endsWith('h')) ms = parseInt(t) * 3_600_000;
  if (!ms || ms <= 0) return ctx.reply('Time sahi batao 🙏');
  schedule.scheduleJob(new Date(Date.now() + ms), () => ctx.reply(`⏰ Reminder: ${msg}`));
  await ctx.reply(`👍 ${t} baad yaad dila dunga!`);
});

/* ─────────────── AI Modes ────────────────────────────────────── */

const AI = {
  roast:   { q:'Naam bata roast ke liye! 🔥',   s:'Savage Indian roaster. 4-line Hinglish.' },
  shayari: { q:'Topic bata shayari ke liye! 📝', s:'Deep 4-line shayari Ghalib style. Hinglish.' },
  rap:     { q:'Rap ka topic bata! 🎤',          s:'8-line desi underground rap. Hinglish.' },
  fortune: { q:'Apna naam bata! 🔮',             s:'Funny Indian jyotishi. 3-4 lines Hinglish.' },
  story:   { q:'Story topic bata! 📝',           s:'10-line creative story. Hinglish.' },
  recipe:  { q:'Dish / ingredients batao! 🍕',   s:'Desi chef recipe. Hinglish.' },
};

/* ─────────────── Admin Commands (restricted) ───────────────────── */

const ADMIN_ID = process.env.ADMIN_ID; // Optional: restrict admin commands

function isAdmin(ctx) {
  if (!ADMIN_ID) return true; // If not set, all users are admins (for backwards compatibility)
  return ctx.from?.id.toString() === ADMIN_ID;
}

bot.command('status', async ctx => {
  if (!isAdmin(ctx)) {
    return ctx.reply('⛔ Admin only command');
  }

  const mem = process.memoryUsage();
  const rss = (mem.rss / 1024 / 1024).toFixed(2);
  const heapTotal = (mem.heapTotal / 1024 / 1024).toFixed(2);
  const heapUsed = (mem.heapUsed / 1024 / 1024).toFixed(2);

  await ctx.reply(
    `📊 *Bot Status*\n\n` +
    `🕐 Uptime: ${Math.floor(process.uptime() / 60)}m\n` +
    `💾 Memory: ${rss} MB RSS (heap: ${heapUsed}/${heapTotal} MB)\n` +
    `📡 Users in session: ${new Set(bot._session._sessions.keys()).size}\n` +
    `🤖 AI: ${groq ? '✅ Enabled' : '❌ Disabled (no GROQ_API_KEY)'}`,
    { parse_mode: 'Markdown' }
  );
});

bot.command('clean', async ctx => {
  if (!isAdmin(ctx)) {
    return ctx.reply('⛔ Admin only command');
  }

  // Clean tmp directories
  const tmpDir = '/tmp';
  let cleaned = 0;
  try {
    const entries = await fs.readdir(tmpDir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.name.startsWith('dl_') || entry.name.startsWith('mp3_')) {
        await fs.remove(path.join(tmpDir, entry.name));
        cleaned++;
      }
    }
    await ctx.reply(`✅ Cleaned ${cleaned} temporary directories`);
  } catch (e) {
    await ctx.reply(`❌ Clean failed: ${e.message}`);
  }
});

bot.on('callback_query', async ctx => {
  const m = ctx.callbackQuery.data?.replace('mode_', '');
  if (!AI[m]) return;
  ctx.session ??= {};
  ctx.session.mode = m;
  await ctx.answerCbQuery();
  await ctx.editMessageText(AI[m].q);
});

/* ─────────────── Text handler ────────────────────────────────── */

bot.on('text', async ctx => {
  const txt  = ctx.message.text;
  const mode = ctx.session?.mode;

  // AI mode
  if (mode && AI[mode]) {
    const s = await ctx.reply('Typing … 🤖');
    try {
      await edit(ctx, s.message_id, await ai(txt, AI[mode].s));
    } catch { await edit(ctx, s.message_id, 'AI error! 🙏'); }
    ctx.session.mode = null;
    return;
  }

  // Link → download
  const u = txt.match(/https?:\/\/[^\s]+/);
  if (!u) return ctx.reply('Link bhej ya /start se shuru kar! 🙏');

  const s   = await ctx.reply('⏳ Download ho raha hai …');
  const dir = path.join('/tmp', `dl_${ctx.from.id}_${Date.now()}`);
  await fs.ensureDir(dir);

  try {
    await ytdlp(u[0], dir);
    await send(ctx, s.message_id, dir);
  } catch (e) {
    console.error('dl:', e.message);
    await edit(ctx, s.message_id, isBlock(e)
      ? 'YouTube block! 🛑 cookies.txt upload karo.'
      : `Error: ${e.message.slice(0, 200)}`);
  } finally { await clean(dir); }
});

/* ─────────────── Health Check Server ───────────────────────────── */

let healthServer = null;

function startHealthServer(port) {
  healthServer = http.createServer((req, res) => {
    if (req.url === '/health' || req.url === '/') {
      const uptime = Math.floor(process.uptime());
      const mem = process.memoryUsage();
      const health = {
        status: 'ok',
        uptime: uptime,
        memory: {
          rss: Math.round(mem.rss),
          heapUsed: Math.round(mem.heapUsed),
          heapTotal: Math.round(mem.heapTotal),
        },
        timestamp: new Date().toISOString(),
        version: '3.0',
      };

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(health, null, 2));
    } else if (req.url === '/metrics') {
      // Simple metrics endpoint
      const mem = process.memoryUsage();
      const metrics = `# HELP bot_uptime_seconds Bot uptime in seconds
# TYPE bot_uptime_seconds gauge
bot_uptime_seconds ${process.uptime()}
# HELP bot_memory_rss_bytes Memory RSS in bytes
# TYPE bot_memory_rss_bytes gauge
bot_memory_rss_bytes ${mem.rss}
# HELP bot_memory_heap_used_bytes Heap used in bytes
# TYPE bot_memory_heap_used_bytes gauge
bot_memory_heap_used_bytes ${mem.heapUsed}`;
      res.writeHead(200, { 'Content-Type': 'text/plain' });
      res.end(metrics);
    } else {
      res.writeHead(404);
      res.end('Not Found');
    }
  });

  healthServer.listen(port, '0.0.0.0', () => {
    console.log(`🏥 Health server listening on port ${port}`);
  });

  healthServer.on('error', (err) => {
    if (err.code === 'EADDRINUSE') {
      console.error(`❌ Port ${port} already in use, health check disabled`);
      healthServer = null;
    } else {
      console.error(`❌ Health server error:`, err.message);
    }
  });

  return healthServer;
}

/* ─────────────── Boot ────────────────────────────────────────── */

async function verifyYtDlp() {
  return new Promise((resolve, reject) => {
    execFile('yt-dlp', ['--version'], (err, out, stderr) => {
      if (err) {
        console.error('❌ yt-dlp not found or not working!');
        console.error('   Error:', err.message);
        console.error('   stderr:', stderr || 'none');
        console.error('\n🔧 Solutions:');
        console.error('   1. Railway: nixpacks.toml MUST include yt-dlp');
        console.error('   2. Docker: Dockerfile MUST install yt-dlp (apk add yt-dlp)');
        console.error('   3. Local: npm install -g yt-dlp OR pip install yt-dlp');
        reject(new Error('yt-dlp unavailable'));
      } else {
        console.log('✅ yt-dlp', out.trim(), 'ready');
        resolve();
      }
    });
  });
}

(async () => {
  console.log('\n' + '='.repeat(50));
  console.log('🤖 Everything Downloader TG Bot — v3.0');
  console.log('='.repeat(50));

  // 1. Verify yt-dlp
  try {
    await verifyYtDlp();
  } catch (e) {
    console.error('❌ Boot failed: yt-dlp not available');
    process.exit(1);
  }

  // 2. Start health check server
  const healthServer = startHealthServer(PORT);

  // 3. Launch bot with retry for 409 (multiple instances)
  let botLaunched = false;
  for (let i = 1; i <= 5; i++) {
    try {
      await bot.launch({ dropPendingUpdates: true });
      console.log('✅ Bot launched successfully!');
      botLaunched = true;
      break;
    } catch (e) {
      if (e.message.includes('409') && i < 5) {
        console.log(`⏳ Instance conflict (409), retry ${i}/5 in 5s...`);
        await new Promise(r => setTimeout(r, 5000));
      } else {
        console.error('❌ Failed to launch bot:', e.message);
        if (healthServer) healthServer.close();
        process.exit(1);
      }
    }
  }

  if (!botLaunched) {
    console.error('❌ Failed to launch bot after 5 retries');
    if (healthServer) healthServer.close();
    process.exit(1);
  }

  console.log(`🏥 Health: http://0.0.0.0:${PORT}/health`);
  console.log(`📊 Metrics: http://0.0.0.0:${PORT}/metrics`);
  console.log('='.repeat(50) + '\n');
})();

// Graceful shutdown
async function shutdown(signal) {
  console.log(`\n🛑 Received ${signal}, shutting down gracefully...`);

  if (healthServer) {
    console.log('   Closing health server...');
    healthServer.close();
  }

  console.log('   Stopping bot...');
  await bot.stop(signal);

  console.log('   Clean exit ✨');
  process.exit(0);
}

process.once('SIGINT',  () => shutdown('SIGINT'));
process.once('SIGTERM', () => shutdown('SIGTERM'));
process.on('uncaughtException', (err) => {
  console.error('💥 Uncaught exception:', err);
  shutdown('uncaughtException');
});
process.on('unhandledRejection', (reason, promise) => {
  console.error('💥 Unhandled rejection at:', promise, 'reason:', reason);
});
