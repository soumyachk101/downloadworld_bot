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
require('dotenv').config();

/* ─────────────── Config ──────────────────────────────────────── */

const BOT_TOKEN    = process.env.BOT_TOKEN;
const GROQ_API_KEY = process.env.GROQ_API_KEY;
const MAX_SIZE     = 50 * 1024 * 1024;

if (!BOT_TOKEN) { console.error('❌ BOT_TOKEN missing'); process.exit(1); }

const bot  = new Telegraf(BOT_TOKEN);
const groq = GROQ_API_KEY ? new Groq({ apiKey: GROQ_API_KEY }) : null;

/* ─────────────── Session ─────────────────────────────────────── */

bot.use(session());

/* ─────────────── Helpers ─────────────────────────────────────── */

const clean = (d) => fs.remove(d).catch(() => {});

async function edit(ctx, id, txt) {
  try { await ctx.telegram.editMessageText(ctx.chat.id, id, null, txt); }
  catch { /* already deleted or unchanged */ }
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
  return m.includes('sign in') || m.includes('confirm') || m.includes('403');
}

/* ─────────────── yt-dlp download ─────────────────────────────── */

function ytdlp(url, dir, audio = false) {
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

    console.log(`⬇ yt-dlp ${args.join(' ')}`);

    const p = execFile('yt-dlp', args, { timeout: 180_000 }, (err, out, stderr) => {
      if (out) console.log(out);
      if (stderr) console.error(stderr);
      err ? reject(new Error(stderr || err.message)) : resolve();
    });

    p.on('error', (e) => reject(new Error(`yt-dlp spawn error: ${e.message}`)));
  });
}

/* ─────────────── Send result ─────────────────────────────────── */

async function send(ctx, sid, dir, audio = false) {
  const files = await walk(dir);
  const ext   = audio ? /\.mp3$/i : /\.(mp4|mkv|webm|mov|jpg|jpeg|png|webp)$/i;
  const f     = files.find(x => ext.test(x));

  if (!f) return edit(ctx, sid, 'Media nahi mili. Link check kar! 😔');

  const { size } = await fs.stat(f);
  if (size > MAX_SIZE) return edit(ctx, sid, 'File 50 MB se badi hai! 😔');

  const e = path.extname(f).toLowerCase();
  if (audio || e === '.mp3')                            await ctx.replyWithAudio({ source: f });
  else if (['.mp4','.mkv','.webm','.mov'].includes(e)) await ctx.replyWithVideo({ source: f });
  else                                                  await ctx.replyWithPhoto({ source: f });

  await ctx.deleteMessage(sid).catch(() => {});
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

/* ─────────────── Boot ────────────────────────────────────────── */

(async () => {
  // Verify yt-dlp is installed via nix
  try {
    execFile('yt-dlp', ['--version'], (err, out) => {
      if (err) console.error('⚠️ yt-dlp not found in PATH:', err.message);
      else     console.log('✅ yt-dlp', out.trim(), 'ready');
    });
  } catch {}

  // Launch with retry for 409 conflict
  for (let i = 1; i <= 5; i++) {
    try {
      await bot.launch({ dropPendingUpdates: true });
      console.log('✅ Bot is alive!');
      break;
    } catch (e) {
      if (e.message.includes('409') && i < 5) {
        console.log(`⏳ Retry ${i}/5 — waiting 5s for old instance to stop …`);
        await new Promise(r => setTimeout(r, 5000));
      } else { console.error('❌', e.message); process.exit(1); }
    }
  }
})();

process.once('SIGINT',  () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));
