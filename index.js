const { Telegraf, Markup, session } = require('telegraf');
const Groq = require('groq-sdk');
const { translate } = require('google-translate-api-x');
const schedule = require('node-schedule');
const path = require('path');
const fs = require('fs-extra');
const { execSync, execFile } = require('child_process');
require('dotenv').config();

// ═══════════════════════════════════════════
//  CONFIG
// ═══════════════════════════════════════════

const BOT_TOKEN = process.env.BOT_TOKEN;
const GROQ_API_KEY = process.env.GROQ_API_KEY;
const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50 MB
const YT_DLP_BIN = '/tmp/yt-dlp';

if (!BOT_TOKEN) {
  console.error('❌ BOT_TOKEN missing!');
  process.exit(1);
}

const bot = new Telegraf(BOT_TOKEN);
const groq = GROQ_API_KEY ? new Groq({ apiKey: GROQ_API_KEY }) : null;

// ═══════════════════════════════════════════
//  INSTALL yt-dlp STANDALONE BINARY
//  No Python needed! Self-contained executable.
// ═══════════════════════════════════════════

function installYtDlp() {
  if (fs.existsSync(YT_DLP_BIN)) {
    console.log('✅ yt-dlp binary already exists at', YT_DLP_BIN);
    return true;
  }
  console.log('📥 Downloading yt-dlp standalone binary...');
  try {
    execSync(
      `curl -L -o ${YT_DLP_BIN} https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp && chmod +x ${YT_DLP_BIN}`,
      { stdio: 'inherit', timeout: 60000 }
    );
    console.log('✅ yt-dlp installed at', YT_DLP_BIN);
    // Print version to verify
    execSync(`${YT_DLP_BIN} --version`, { stdio: 'inherit' });
    return true;
  } catch (e) {
    console.error('❌ Failed to download yt-dlp:', e.message);
    return false;
  }
}

// ═══════════════════════════════════════════
//  SESSION MIDDLEWARE
// ═══════════════════════════════════════════

bot.use(session());

// ═══════════════════════════════════════════
//  UTILITIES
// ═══════════════════════════════════════════

async function cleanup(dir) {
  try { await fs.remove(dir); } catch (_) { /* ignore */ }
}

async function editStatus(ctx, msgId, text) {
  try {
    await ctx.telegram.editMessageText(ctx.chat.id, msgId, null, text);
  } catch (_) { /* message may be deleted */ }
}

async function askAi(userPrompt, systemPrompt) {
  if (!groq) return 'Bhai GROQ_API_KEY set nahi hai! 🙏';
  try {
    const res = await groq.chat.completions.create({
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userPrompt },
      ],
      model: 'llama-3.1-8b-instant',
    });
    return res.choices[0].message.content;
  } catch (err) {
    console.error('Groq error:', err.message);
    return 'Bhai AI abhi busy hai, baad mein try kar! 🙏';
  }
}

async function walkDir(dir) {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  const files = [];
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) files.push(...(await walkDir(full)));
    else files.push(full);
  }
  return files;
}

function isYouTubeBlock(err) {
  const m = (err.message || '').toLowerCase();
  return m.includes('sign in to confirm') || m.includes('403') || m.includes('bot');
}

// ═══════════════════════════════════════════
//  DOWNLOAD – uses standalone yt-dlp binary
// ═══════════════════════════════════════════

function downloadMedia(url, outputDir, audioOnly = false) {
  return new Promise((resolve, reject) => {
    // Re-download binary if somehow deleted
    if (!fs.existsSync(YT_DLP_BIN)) {
      try { installYtDlp(); } catch (_) {}
    }

    const args = [
      url,
      '-o', path.join(outputDir, '%(id)s.%(ext)s'),
      '--no-warnings',
      '--no-playlist',
      '--merge-output-format', 'mp4',
      '--format', audioOnly
        ? 'bestaudio/best'
        : 'bestvideo[filesize<45M]+bestaudio/best[filesize<45M]/best',
    ];

    if (fs.existsSync('cookies.txt')) {
      args.push('--cookies', 'cookies.txt');
      console.log('🍪 Using cookies.txt');
    }

    if (audioOnly) {
      args.push('-x', '--audio-format', 'mp3', '--audio-quality', '192');
    }

    console.log(`⬇️  Running: ${YT_DLP_BIN} ${args.join(' ')}`);

    execFile(YT_DLP_BIN, args, { timeout: 180000 }, (err, stdout, stderr) => {
      if (stdout) console.log('yt-dlp stdout:', stdout);
      if (stderr) console.error('yt-dlp stderr:', stderr);
      if (err) return reject(new Error(stderr || err.message));
      resolve();
    });
  });
}

// ═══════════════════════════════════════════
//  /start
// ═══════════════════════════════════════════

bot.start((ctx) => {
  const text =
    'Hello bhai! 👋 Main tera all-in-one bot hoon.\n\n' +
    '📥 *Media Downloader*\n' +
    'YouTube, Instagram, Twitter ya FB ka link bhej!\n\n' +
    '🎵 MP3: `/mp3 <link>`\n' +
    '🌐 Translate: `/translate <text>`\n' +
    '⏰ Remind: `/remind 10m Chai peeni hai`\n\n' +
    '🤖 *AI Fun Mode* — niche buttons try kar!';

  return ctx.replyWithMarkdown(
    text,
    Markup.inlineKeyboard([
      [Markup.button.callback('😂 Roast', 'mode_roast'), Markup.button.callback('🎤 Shayari', 'mode_shayari')],
      [Markup.button.callback('🎵 Rap', 'mode_rap'), Markup.button.callback('🔮 Fortune', 'mode_fortune')],
      [Markup.button.callback('📝 Story', 'mode_story'), Markup.button.callback('🍕 Recipe', 'mode_recipe')],
    ]),
  );
});

// ═══════════════════════════════════════════
//  /mp3
// ═══════════════════════════════════════════

bot.command('mp3', async (ctx) => {
  const url = ctx.message.text.split(' ')[1];
  if (!url) return ctx.reply('Bhai link toh bhej! Example: /mp3 <link>');

  const statusMsg = await ctx.reply('⏳ MP3 download ho raha hai…');
  const dlDir = path.join('/tmp', `mp3_${ctx.from.id}_${Date.now()}`);
  await fs.ensureDir(dlDir);

  try {
    await downloadMedia(url, dlDir, true);
    const allFiles = await walkDir(dlDir);
    const mp3 = allFiles.find((f) => /\.mp3$/i.test(f));

    if (!mp3) return editStatus(ctx, statusMsg.message_id, 'Bhai MP3 nahi mili. 😔');

    const { size } = await fs.stat(mp3);
    if (size > MAX_FILE_SIZE) return editStatus(ctx, statusMsg.message_id, 'MP3 50 MB se badi hai! 😔');

    await ctx.replyWithAudio({ source: mp3 });
    await ctx.deleteMessage(statusMsg.message_id).catch(() => {});
  } catch (err) {
    console.error('MP3 error:', err.message);
    const msg = isYouTubeBlock(err)
      ? 'YouTube ne block kar diya! 🛑 cookies.txt upload karo.'
      : `Download error! 🙏\n\n${err.message.substring(0, 200)}`;
    await editStatus(ctx, statusMsg.message_id, msg);
  } finally {
    await cleanup(dlDir);
  }
});

// ═══════════════════════════════════════════
//  /translate
// ═══════════════════════════════════════════

bot.command('translate', async (ctx) => {
  const text =
    ctx.message.text.split(' ').slice(1).join(' ') ||
    (ctx.message.reply_to_message?.text ?? '');
  if (!text) return ctx.reply('Bhai kya translate karun? 🙏');
  try {
    const res = await translate(text, { to: 'hi' });
    await ctx.reply(`🌐 *Translated:*\n\n${res.text}`, { parse_mode: 'Markdown' });
  } catch {
    await ctx.reply('Translation error bhai! 🙏');
  }
});

// ═══════════════════════════════════════════
//  /remind
// ═══════════════════════════════════════════

bot.command('remind', async (ctx) => {
  const parts = ctx.message.text.split(' ');
  if (parts.length < 3) return ctx.reply('Format: /remind 10m Chai peeni hai');
  const timeStr = parts[1];
  const msg = parts.slice(2).join(' ');

  let ms = parseInt(timeStr) * 1000;
  if (timeStr.endsWith('m')) ms = parseInt(timeStr) * 60000;
  else if (timeStr.endsWith('h')) ms = parseInt(timeStr) * 3600000;

  if (isNaN(ms) || ms <= 0) return ctx.reply('Time sahi se batao (30s, 10m, 2h) 🙏');

  schedule.scheduleJob(new Date(Date.now() + ms), () => {
    ctx.reply(`⏰ Yaad dilaya bhai: ${msg}`);
  });
  await ctx.reply(`Done! ${timeStr} baad yaad dila dunga. 👍`);
});

// ═══════════════════════════════════════════
//  AI MODE BUTTONS
// ═══════════════════════════════════════════

const AI_PROMPTS = {
  roast: { ask: 'Naam bata jisko roast karna hai! 🔥', sys: 'You are a savage Indian roaster. 4-line roast in Hinglish.' },
  shayari: { ask: 'Kis topic pe shayari? 📝', sys: 'Write 4-line deep shayari Mirza Ghalib style. Hinglish.' },
  rap: { ask: 'Rap ka topic bata! 🎤🔥', sys: 'Write 8-line desi underground rap. Hinglish.' },
  fortune: { ask: 'Apna naam bata! 🔮', sys: 'Funny Indian jyotishi. 3-4 lines Hinglish.' },
  story: { ask: 'Story ka topic? 📝', sys: 'Creative 10-line story. Hinglish.' },
  recipe: { ask: 'Dish ya ingredients batao! 🍕', sys: 'Recipe in Hinglish like desi chef.' },
};

bot.on('callback_query', async (ctx) => {
  const mode = ctx.callbackQuery.data?.replace('mode_', '');
  if (!AI_PROMPTS[mode]) return;
  ctx.session ??= {};
  ctx.session.mode = mode;
  await ctx.answerCbQuery();
  await ctx.editMessageText(AI_PROMPTS[mode].ask);
});

// ═══════════════════════════════════════════
//  TEXT – AI reply OR media download
// ═══════════════════════════════════════════

bot.on('text', async (ctx) => {
  const userText = ctx.message.text;
  const mode = ctx.session?.mode;

  // --- AI Mode ---
  if (mode && AI_PROMPTS[mode]) {
    const status = await ctx.reply('Typing… 🤖');
    const resp = await askAi(`${mode}: ${userText}`, AI_PROMPTS[mode].sys);
    await editStatus(ctx, status.message_id, resp);
    ctx.session.mode = null;
    return;
  }

  // --- Download links ---
  const urlMatch = userText.match(/https?:\/\/[^\s]+/);
  if (!urlMatch) return ctx.reply('Bhai link bhej ya /start se maze le! 🙏');

  const url = urlMatch[0];
  const statusMsg = await ctx.reply('⏳ Download ho raha hai… ruk bhai!');
  const dlDir = path.join('/tmp', `dl_${ctx.from.id}_${Date.now()}`);
  await fs.ensureDir(dlDir);

  try {
    await downloadMedia(url, dlDir);
    const allFiles = await walkDir(dlDir);
    const media = allFiles.find((f) => /\.(mp4|mkv|webm|jpg|jpeg|png|webp)$/i.test(f));

    if (!media) return editStatus(ctx, statusMsg.message_id, 'Bhai media nahi mili. Link check kar! 😔');

    const { size } = await fs.stat(media);
    if (size > MAX_FILE_SIZE) return editStatus(ctx, statusMsg.message_id, 'File 50 MB se badi hai! 😔');

    const ext = path.extname(media).toLowerCase();
    if (['.mp4', '.mkv', '.webm'].includes(ext)) {
      await ctx.replyWithVideo({ source: media });
    } else {
      await ctx.replyWithPhoto({ source: media });
    }
    await ctx.deleteMessage(statusMsg.message_id).catch(() => {});
  } catch (err) {
    console.error('Download error:', err.message);
    const msg = isYouTubeBlock(err)
      ? 'YouTube ne block kar diya! 🛑 cookies.txt upload karo.'
      : `Download fail! 🙏\n\n${err.message.substring(0, 200)}`;
    await editStatus(ctx, statusMsg.message_id, msg);
  } finally {
    await cleanup(dlDir);
  }
});

// ═══════════════════════════════════════════
//  LAUNCH
// ═══════════════════════════════════════════

console.log('🚀 Starting Everything Downloader Bot...');

// Step 1: Install yt-dlp standalone binary
const ytdlpReady = installYtDlp();
if (!ytdlpReady) {
  console.error('⚠️  yt-dlp not available. Downloads will fail.');
}

// Step 2: Launch bot
bot
  .launch({ dropPendingUpdates: true })
  .then(() => console.log('✅ Bot is alive and ready!'))
  .catch((err) => {
    console.error('❌ Bot failed:', err.message);
    process.exit(1);
  });

process.once('SIGINT', () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));
