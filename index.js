/**
 * Everything Downloader TG Bot — v2.0
 *
 * Production-grade Telegram bot running on Node.js / Railway.
 * Downloads media from YouTube, Instagram, Twitter, Facebook via yt-dlp.
 * AI features powered by Groq (Llama 3.1).
 *
 * Key design decisions
 * ────────────────────
 * 1. yt-dlp standalone binary is downloaded at boot using Node's native
 *    fetch(). This means ZERO dependency on Python, pip, or curl at runtime.
 *    Only ffmpeg (via nixpacks) is needed for muxing.
 *
 * 2. Telegraf session middleware stores per-user AI mode state.
 *
 * 3. All downloads go to /tmp and are cleaned up in finally blocks.
 *
 * 4. Every spawn / execFile has an error handler so the process never
 *    crashes on ENOENT.
 */

'use strict';

const { Telegraf, Markup, session } = require('telegraf');
const Groq                          = require('groq-sdk');
const { translate }                 = require('google-translate-api-x');
const schedule                      = require('node-schedule');
const path                          = require('path');
const fs                            = require('fs-extra');
const { execFile, execSync }        = require('child_process');
const { pipeline }                  = require('stream/promises');
const { createWriteStream }         = require('fs');
require('dotenv').config();

/* ──────────────────────────── Config ──────────────────────────── */

const BOT_TOKEN    = process.env.BOT_TOKEN;
const GROQ_API_KEY = process.env.GROQ_API_KEY;
const MAX_SIZE     = 50 * 1024 * 1024;          // Telegram 50 MB limit
const YT_DLP       = '/tmp/yt-dlp';             // standalone binary path
const YT_DLP_URL   = 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux';

if (!BOT_TOKEN) { console.error('❌ BOT_TOKEN missing'); process.exit(1); }

const bot  = new Telegraf(BOT_TOKEN);
const groq = GROQ_API_KEY ? new Groq({ apiKey: GROQ_API_KEY }) : null;

/* ──────────────── yt-dlp binary installer ─────────────────────── */

async function installYtDlp() {
  if (await fs.pathExists(YT_DLP)) {
    console.log('✅ yt-dlp already present');
    return;
  }

  console.log('📥 Downloading yt-dlp standalone binary …');
  const res = await fetch(YT_DLP_URL, { redirect: 'follow' });
  if (!res.ok) throw new Error(`GitHub returned ${res.status}`);

  await pipeline(res.body, createWriteStream(YT_DLP));
  await fs.chmod(YT_DLP, 0o755);

  try {
    const ver = execSync(`${YT_DLP} --version`, { encoding: 'utf8' }).trim();
    console.log(`✅ yt-dlp ${ver} ready`);
  } catch {
    console.log('✅ yt-dlp downloaded (version check skipped)');
  }
}

/* ──────────────── Session middleware ───────────────────────────── */

bot.use(session());

/* ──────────────── Utility helpers ─────────────────────────────── */

const cleanup = (dir) => fs.remove(dir).catch(() => {});

async function editMsg(ctx, id, text) {
  try { await ctx.telegram.editMessageText(ctx.chat.id, id, null, text); }
  catch { /* deleted / unchanged */ }
}

async function askAi(prompt, system) {
  if (!groq) return 'GROQ_API_KEY set nahi hai bhai! 🙏';
  const r = await groq.chat.completions.create({
    messages: [{ role: 'system', content: system }, { role: 'user', content: prompt }],
    model: 'llama-3.1-8b-instant',
  });
  return r.choices[0]?.message?.content ?? 'AI ne kuch nahi bola 🤷';
}

async function findFiles(dir) {
  const out = [];
  for (const e of await fs.readdir(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    e.isDirectory() ? out.push(...await findFiles(p)) : out.push(p);
  }
  return out;
}

function ytBlock(err) {
  const m = (err?.message || '').toLowerCase();
  return m.includes('sign in') || m.includes('confirm') || m.includes('403');
}

/* ──────────────── Download via yt-dlp ─────────────────────────── */

function download(url, dir, audio = false) {
  return new Promise((resolve, reject) => {
    const fmt = audio
      ? 'bestaudio/best'
      : 'bestvideo[filesize<45M]+bestaudio/best[filesize<45M]/best';

    const args = [
      url,
      '-o', path.join(dir, '%(id)s.%(ext)s'),
      '--no-warnings', '--no-playlist',
      '--merge-output-format', 'mp4',
      '-f', fmt,
    ];

    if (fs.existsSync('cookies.txt')) args.push('--cookies', 'cookies.txt');
    if (audio) args.push('-x', '--audio-format', 'mp3', '--audio-quality', '192');

    console.log(`⬇ ${YT_DLP} ${args.join(' ')}`);

    const proc = execFile(YT_DLP, args, { timeout: 180_000 }, (err, out, stderr) => {
      if (out) console.log(out);
      if (stderr) console.error(stderr);
      err ? reject(new Error(stderr || err.message)) : resolve();
    });

    proc.on('error', (e) => reject(new Error(`yt-dlp binary error: ${e.message}`)));
  });
}

/* ──────────────── Shared send-media logic ─────────────────────── */

async function sendMedia(ctx, statusId, dir, audio = false) {
  const files = await findFiles(dir);
  const exts  = audio ? /\.mp3$/i : /\.(mp4|mkv|webm|mov|jpg|jpeg|png|webp)$/i;
  const media = files.find(f => exts.test(f));

  if (!media) {
    await editMsg(ctx, statusId, 'Bhai media nahi mili. Link check kar! 😔');
    return;
  }

  const { size } = await fs.stat(media);
  if (size > MAX_SIZE) {
    await editMsg(ctx, statusId, 'Bhai file 50 MB se badi hai! 😔');
    return;
  }

  const ext = path.extname(media).toLowerCase();
  if (audio || ext === '.mp3')       await ctx.replyWithAudio({ source: media });
  else if (['.mp4', '.mkv', '.webm', '.mov'].includes(ext)) await ctx.replyWithVideo({ source: media });
  else                                await ctx.replyWithPhoto({ source: media });

  await ctx.deleteMessage(statusId).catch(() => {});
}

/* ──────────────── Bot commands ────────────────────────────────── */

bot.start(ctx =>
  ctx.replyWithMarkdown(
    'Hello bhai! 👋 Main tera all-in-one bot hoon.\n\n' +
    '📥 *Media Download* — koi bhi link bhej\n' +
    '🎵 `/mp3 <link>` — audio download\n' +
    '🌐 `/translate <text>` — Hindi translate\n' +
    '⏰ `/remind 10m Chai` — reminder\n\n' +
    '🤖 *AI Fun* — buttons daba!',
    Markup.inlineKeyboard([
      [Markup.button.callback('😂 Roast',   'mode_roast'),   Markup.button.callback('🎤 Shayari', 'mode_shayari')],
      [Markup.button.callback('🎵 Rap',     'mode_rap'),     Markup.button.callback('🔮 Fortune', 'mode_fortune')],
      [Markup.button.callback('📝 Story',   'mode_story'),   Markup.button.callback('🍕 Recipe',  'mode_recipe')],
    ]),
  ),
);

bot.command('mp3', async ctx => {
  const url = ctx.message.text.split(' ')[1];
  if (!url) return ctx.reply('Example: /mp3 https://youtu.be/...');

  const s = await ctx.reply('⏳ MP3 download ho raha hai …');
  const dir = path.join('/tmp', `mp3_${ctx.from.id}_${Date.now()}`);
  await fs.ensureDir(dir);

  try {
    await download(url, dir, true);
    await sendMedia(ctx, s.message_id, dir, true);
  } catch (err) {
    console.error('mp3 err:', err.message);
    await editMsg(ctx, s.message_id,
      ytBlock(err) ? 'YouTube block! 🛑 cookies.txt upload karo.' : `Error: ${err.message.slice(0, 200)}`);
  } finally { await cleanup(dir); }
});

bot.command('translate', async ctx => {
  const txt = ctx.message.text.split(' ').slice(1).join(' ') || ctx.message.reply_to_message?.text || '';
  if (!txt) return ctx.reply('Kya translate karun? Text likh ya reply kar 🙏');
  try {
    const r = await translate(txt, { to: 'hi' });
    await ctx.reply(`🌐 *Translated:*\n\n${r.text}`, { parse_mode: 'Markdown' });
  } catch { await ctx.reply('Translation error! 🙏'); }
});

bot.command('remind', async ctx => {
  const p = ctx.message.text.split(' ');
  if (p.length < 3) return ctx.reply('/remind 10m Chai peeni hai');
  const t = p[1], msg = p.slice(2).join(' ');
  let ms = parseInt(t) * 1000;
  if (t.endsWith('m')) ms = parseInt(t) * 60_000;
  if (t.endsWith('h')) ms = parseInt(t) * 3_600_000;
  if (!ms || ms <= 0) return ctx.reply('Time galat hai 🙏');
  schedule.scheduleJob(new Date(Date.now() + ms), () => ctx.reply(`⏰ Reminder: ${msg}`));
  await ctx.reply(`👍 ${t} baad yaad dila dunga!`);
});

/* ──────────────── AI Mode (buttons + text) ────────────────────── */

const AI = {
  roast:   { q: 'Naam bata roast ke liye! 🔥',   s: 'Savage Indian roaster. 4-line Hinglish roast.' },
  shayari: { q: 'Topic bata shayari ke liye! 📝', s: 'Deep 4-line shayari, Ghalib style, Hinglish.' },
  rap:     { q: 'Rap ka topic bata! 🎤',          s: '8-line desi underground rap. Hinglish.' },
  fortune: { q: 'Apna naam bata! 🔮',             s: 'Funny Indian jyotishi, 3-4 line. Hinglish.' },
  story:   { q: 'Story topic bata! 📝',           s: '10-line creative story. Hinglish.' },
  recipe:  { q: 'Dish / ingredients batao! 🍕',   s: 'Desi chef recipe. Hinglish.' },
};

bot.on('callback_query', async ctx => {
  const m = ctx.callbackQuery.data?.replace('mode_', '');
  if (!AI[m]) return;
  ctx.session ??= {};
  ctx.session.mode = m;
  await ctx.answerCbQuery();
  await ctx.editMessageText(AI[m].q);
});

bot.on('text', async ctx => {
  const txt  = ctx.message.text;
  const mode = ctx.session?.mode;

  // AI mode active
  if (mode && AI[mode]) {
    const s = await ctx.reply('Typing … 🤖');
    try {
      const r = await askAi(txt, AI[mode].s);
      await editMsg(ctx, s.message_id, r);
    } catch (e) {
      await editMsg(ctx, s.message_id, 'AI error! 🙏');
    }
    ctx.session.mode = null;
    return;
  }

  // Link detected — download
  const m = txt.match(/https?:\/\/[^\s]+/);
  if (!m) return ctx.reply('Link bhej ya /start se shuru kar! 🙏');

  const s   = await ctx.reply('⏳ Download ho raha hai …');
  const dir = path.join('/tmp', `dl_${ctx.from.id}_${Date.now()}`);
  await fs.ensureDir(dir);

  try {
    await download(m[0], dir);
    await sendMedia(ctx, s.message_id, dir);
  } catch (err) {
    console.error('dl err:', err.message);
    await editMsg(ctx, s.message_id,
      ytBlock(err) ? 'YouTube block! 🛑 cookies.txt upload karo.' : `Error: ${err.message.slice(0, 200)}`);
  } finally { await cleanup(dir); }
});

/* ──────────────── Boot sequence ───────────────────────────────── */

(async () => {
  try {
    await installYtDlp();
  } catch (e) {
    console.error('⚠️ yt-dlp install failed:', e.message);
  }

  // Retry logic for 409 Conflict (old instance still polling)
  for (let attempt = 1; attempt <= 5; attempt++) {
    try {
      await bot.launch({ dropPendingUpdates: true });
      console.log('✅ Bot is alive!');
      break;
    } catch (e) {
      if (e.message.includes('409') && attempt < 5) {
        console.log(`⏳ Attempt ${attempt}/5 — old instance still running, waiting 5s…`);
        await new Promise(r => setTimeout(r, 5000));
      } else {
        console.error('❌ Bot launch failed:', e.message);
        process.exit(1);
      }
    }
  }
})();

process.once('SIGINT',  () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));
