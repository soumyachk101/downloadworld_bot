const { Telegraf, Markup, session } = require('telegraf');
const Groq = require('groq-sdk');
const { translate } = require('google-translate-api-x');
const schedule = require('node-schedule');
const path = require('path');
const fs = require('fs-extra');
const { execFile } = require('child_process');
require('dotenv').config();

// ═══════════════════════════════════════════
//  CONFIG
// ═══════════════════════════════════════════

const BOT_TOKEN = process.env.BOT_TOKEN;
const GROQ_API_KEY = process.env.GROQ_API_KEY;
const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50 MB Telegram limit

if (!BOT_TOKEN) {
  console.error('❌ BOT_TOKEN is missing! Set it in .env or Railway variables.');
  process.exit(1);
}

const bot = new Telegraf(BOT_TOKEN);
const groq = GROQ_API_KEY ? new Groq({ apiKey: GROQ_API_KEY }) : null;

// ═══════════════════════════════════════════
//  SESSION MIDDLEWARE  (fixes AI mode bug)
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
  } catch (_) { /* message may already be deleted */ }
}

async function askAi(userPrompt, systemPrompt) {
  if (!groq) return 'Bhai GROQ_API_KEY set nahi hai! Railway dashboard mein add karo. 🙏';
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
    return 'Bhai AI abhi busy hai. Baad mein try kar! 🙏';
  }
}

// ═══════════════════════════════════════════
//  DOWNLOAD – calls yt-dlp binary directly
// ═══════════════════════════════════════════

function downloadMedia(url, outputDir, audioOnly = false) {
  return new Promise((resolve, reject) => {
    const args = [
      url,
      '-o', path.join(outputDir, '%(id)s.%(ext)s'),
      '--no-warnings',
      '--no-playlist',
      '--format', audioOnly
        ? 'bestaudio/best'
        : 'bestvideo[filesize<45M]+bestaudio/best[filesize<45M]/best',
    ];

    // Cookie support for YouTube IP-block bypass
    if (fs.existsSync('cookies.txt')) {
      args.push('--cookies', 'cookies.txt');
      console.log('🍪  Using cookies.txt');
    }

    if (audioOnly) {
      args.push('-x', '--audio-format', 'mp3', '--audio-quality', '192');
    }

    console.log(`⬇️  yt-dlp ${args.join(' ')}`);

    execFile('yt-dlp', args, { timeout: 120_000 }, (err, stdout, stderr) => {
      if (stdout) console.log(stdout);
      if (stderr) console.error(stderr);
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
    'YouTube, Instagram, Twitter ya FB ka link bhej — main video/photo bhej dunga (<50 MB).\n\n' +
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
    const files = (await fs.readdir(dlDir)).filter((f) => f.endsWith('.mp3'));
    if (!files.length) {
      return editStatus(ctx, statusMsg.message_id, 'Bhai MP3 nahi mili. Link check kar! 😔');
    }
    const filePath = path.join(dlDir, files[0]);
    const { size } = await fs.stat(filePath);
    if (size > MAX_FILE_SIZE) {
      return editStatus(ctx, statusMsg.message_id, 'Bhai MP3 50 MB se badi hai! 😔');
    }
    await ctx.replyWithAudio({ source: filePath });
    await ctx.deleteMessage(statusMsg.message_id).catch(() => {});
  } catch (err) {
    console.error('MP3 error:', err.message);
    const msg = isYouTubeBlock(err)
      ? 'YouTube ne block kar diya hai! 🛑 cookies.txt upload karo.'
      : `MP3 download error! 🙏\n\nError: ${err.message.substring(0, 200)}`;
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
  if (!text) return ctx.reply('Bhai kya translate karun? Text likh ya reply kar! 🙏');
  try {
    const res = await translate(text, { to: 'hi' });
    await ctx.reply(`🌐 *Translated (Auto → Hindi):*\n\n${res.text}`, { parse_mode: 'Markdown' });
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
  let ms = parseInt(timeStr) * 1000; // default seconds
  if (timeStr.endsWith('m')) ms = parseInt(timeStr) * 60_000;
  else if (timeStr.endsWith('h')) ms = parseInt(timeStr) * 3_600_000;

  if (isNaN(ms) || ms <= 0) return ctx.reply('Time sahi se batao (e.g. 30s, 10m, 2h) 🙏');

  schedule.scheduleJob(new Date(Date.now() + ms), () => {
    ctx.reply(`⏰ Yaad dilaya bhai: ${msg}`);
  });
  await ctx.reply(`Done bhai! ${timeStr} baad yaad dila dunga. 👍`);
});

// ═══════════════════════════════════════════
//  AI MODE BUTTONS
// ═══════════════════════════════════════════

const AI_PROMPTS = {
  roast: { ask: 'Naam bata jisko roast karna hai! 🔥', sys: 'You are a savage Indian roaster. Give a 4-line roast in Hinglish.' },
  shayari: { ask: 'Kis topic pe shayari likhun? 📝', sys: 'Write a 4-line deep shayari in Mirza Ghalib style. Hinglish.' },
  rap: { ask: 'Rap ka topic bata! 🎤🔥', sys: 'Write an 8-line desi underground rap. Hinglish.' },
  fortune: { ask: 'Apna naam bata! 🔮', sys: 'Be a funny Indian jyotishi. 3-4 lines Hinglish.' },
  story: { ask: 'Kis topic pe story likhun? 📝', sys: 'Write a creative 10-line story. Hinglish.' },
  recipe: { ask: 'Dish ya ingredients batao! 🍕', sys: 'Give a recipe in Hinglish like a desi chef.' },
};

bot.on('callback_query', async (ctx) => {
  const mode = ctx.callbackQuery.data?.replace('mode_', '');
  if (!AI_PROMPTS[mode]) return;

  // Initialise session safely
  ctx.session ??= {};
  ctx.session.mode = mode;

  await ctx.answerCbQuery();
  await ctx.editMessageText(AI_PROMPTS[mode].ask);
});

// ═══════════════════════════════════════════
//  TEXT HANDLER – AI reply OR download
// ═══════════════════════════════════════════

bot.on('text', async (ctx) => {
  const userText = ctx.message.text;
  const mode = ctx.session?.mode;

  // --- AI Mode ----
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

    // Find the downloaded file (recursively)
    const allFiles = await walkDir(dlDir);
    const media = allFiles.find((f) => /\.(mp4|mkv|webm|jpg|jpeg|png|webp)$/i.test(f));

    if (!media) {
      return editStatus(ctx, statusMsg.message_id, 'Bhai media nahi mili. Link check kar! 😔');
    }

    const { size } = await fs.stat(media);
    if (size > MAX_FILE_SIZE) {
      return editStatus(ctx, statusMsg.message_id, 'Bhai file 50 MB se badi hai! 😔');
    }

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
      ? 'YouTube ne block kar diya hai! 🛑 cookies.txt upload karo.'
      : `Bhai download fail ho gaya! 🙏\n\nError: ${err.message.substring(0, 200)}`;
    await editStatus(ctx, statusMsg.message_id, msg);
  } finally {
    await cleanup(dlDir);
  }
});

// ═══════════════════════════════════════════
//  HELPERS
// ═══════════════════════════════════════════

function isYouTubeBlock(err) {
  const m = (err.message || '').toLowerCase();
  return m.includes('sign in to confirm') || m.includes('403') || m.includes('bot');
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

// ═══════════════════════════════════════════
//  LAUNCH
// ═══════════════════════════════════════════

console.log('🚀 Launching bot…');
bot
  .launch({ dropPendingUpdates: true })
  .then(() => console.log('✅ Bot is alive!'))
  .catch((err) => {
    console.error('❌ Bot failed to launch:', err.message);
    process.exit(1);
  });

process.once('SIGINT', () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));
