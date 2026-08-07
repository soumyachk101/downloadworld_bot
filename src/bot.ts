import { Bot, InlineKeyboard, InputFile, GrammyError, HttpError } from 'grammy';
import fs from 'fs';
import path from 'path';
import os from 'os';
import crypto from 'crypto';
import QRCode from 'qrcode';
import { DownloaderService } from './downloader.js';

const downloader = new DownloaderService();
const botStartTime = Date.now();
let totalDownloadsCount = 0;

// Store detected URLs mapped to short hex keys so callback_data never exceeds Telegram's 64-byte limit
interface StoredUrl {
  url: string;
  createdAt: number;
}
const urlStore = new Map<string, StoredUrl>();

function storeUrl(url: string): string {
  const key = crypto.randomBytes(6).toString('hex'); // 12 characters
  urlStore.set(key, { url, createdAt: Date.now() });

  // Maintain cache size
  if (urlStore.size > 1000) {
    const now = Date.now();
    for (const [k, val] of urlStore.entries()) {
      if (now - val.createdAt > 3600000) {
        urlStore.delete(k);
      }
    }
  }
  return key;
}

function getStoredUrl(keyOrUrl: string): string {
  const entry = urlStore.get(keyOrUrl);
  return entry ? entry.url : keyOrUrl;
}

export function setupBot(token: string): Bot {
  const bot = new Bot(token);

  // ─── Global Error Handler ──────────────────────────────────────────────────
  bot.catch((err) => {
    const ctx = err.ctx;
    console.error(`[bot.catch] Error while handling update ${ctx.update.update_id}:`);
    const e = err.error;
    if (e instanceof GrammyError) {
      console.error('[bot.catch] Telegram API error:', e.description);
    } else if (e instanceof HttpError) {
      console.error('[bot.catch] Network error connecting to Telegram:', e);
    } else {
      console.error('[bot.catch] Unhandled error:', e);
    }
  });

  // ─── Command: /start ────────────────────────────────────────────────────────
  bot.command('start', async (ctx) => {
    const keyboard = new InlineKeyboard()
      .text('ℹ️ Help & Commands', 'show_help')
      .text('📊 Bot Stats', 'show_stats')
      .row()
      .url('📢 Update Channel', 'https://t.me/telegram');

    const welcomeMsg = `⚡ **Welcome to Everything Downloader Bot!** ⚡\n\n` +
      `Send me any link from **YouTube, Instagram, TikTok, Twitter/X, Facebook, Reddit, Pinterest**, and I will download high quality video or audio for you!\n\n` +
      `💡 **Quick Features:**\n` +
      `• Send any social media link directly\n` +
      `• \`/mp3 <url>\` — Download audio\n` +
      `• \`/mp4 <url>\` — Download video\n` +
      `• \`/qr <text>\` — Generate QR code\n` +
      `• \`/stats\` — Check bot performance`;

    await ctx.reply(welcomeMsg, {
      parse_mode: 'Markdown',
      reply_markup: keyboard
    });
  });

  // ─── Command: /help ─────────────────────────────────────────────────────────
  bot.command('help', async (ctx) => {
    const helpMsg = `📖 **Everything Downloader Bot Guide**\n\n` +
      `**Supported Platforms:**\n` +
      `• YouTube (Videos, Shorts, Music)\n` +
      `• Instagram (Reels, Posts, IGTV)\n` +
      `• TikTok (Videos without watermark)\n` +
      `• Twitter/X & Facebook\n` +
      `• Reddit, Pinterest & 1000+ sites\n\n` +
      `**Commands List:**\n` +
      `• \`/start\` - Start bot & main menu\n` +
      `• \`/help\` - Show help guide\n` +
      `• \`/stats\` - Show bot status & usage\n` +
      `• \`/mp3 <url>\` - Extract & download MP3 audio\n` +
      `• \`/mp4 <url>\` - Download video\n` +
      `• \`/qr <text>\` - Generate QR code image\n` +
      `• \`/short <url>\` - Create short link\n\n` +
      `Simply paste any link in chat to get started!`;

    await ctx.reply(helpMsg, { parse_mode: 'Markdown' });
  });

  // ─── Command: /stats ────────────────────────────────────────────────────────
  bot.command('stats', async (ctx) => {
    const uptimeSec = Math.floor((Date.now() - botStartTime) / 1000);
    const hours = Math.floor(uptimeSec / 3600);
    const mins = Math.floor((uptimeSec % 3600) / 60);
    const secs = uptimeSec % 60;
    const memUsage = (process.memoryUsage().heapUsed / 1024 / 1024).toFixed(2);

    const statsMsg = `📊 **Bot System Statistics**\n\n` +
      `⏱ **Uptime:** ${hours}h ${mins}m ${secs}s\n` +
      `💾 **Memory Usage:** ${memUsage} MB\n` +
      `💻 **Node Version:** ${process.version}\n` +
      `📥 **Total Downloads:** ${totalDownloadsCount}\n` +
      `🖥 **Platform:** ${os.type()} (${os.arch()})\n` +
      `🟢 **Status:** Healthy & Active`;

    await ctx.reply(statsMsg, { parse_mode: 'Markdown' });
  });

  // ─── Command: /mp3 ──────────────────────────────────────────────────────────
  bot.command('mp3', async (ctx) => {
    const url = ctx.match?.trim();
    if (!url) {
      return ctx.reply('⚠️ Please provide a URL!\nUsage: `/mp3 https://youtube.com/...`', { parse_mode: 'Markdown' });
    }

    const statusMsg = await ctx.reply('⏳ Processing audio download...');
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dl_mp3_'));

    try {
      const result = await downloader.downloadAudio(url, tempDir);
      await ctx.api.editMessageText(ctx.chat.id, statusMsg.message_id, '📤 Uploading MP3 to Telegram...');
      await ctx.replyWithAudio(new InputFile(result.filePath), { title: result.title });
      totalDownloadsCount++;
      await ctx.api.deleteMessage(ctx.chat.id, statusMsg.message_id).catch(() => {});
    } catch (err: any) {
      await ctx.api.editMessageText(ctx.chat.id, statusMsg.message_id, `❌ Failed to download audio: ${err.message}`);
    } finally {
      fs.rmSync(tempDir, { recursive: true, force: true });
    }
  });

  // ─── Command: /mp4 ──────────────────────────────────────────────────────────
  bot.command('mp4', async (ctx) => {
    const url = ctx.match?.trim();
    if (!url) {
      return ctx.reply('⚠️ Please provide a URL!\nUsage: `/mp4 https://youtube.com/...`', { parse_mode: 'Markdown' });
    }

    const statusMsg = await ctx.reply('⏳ Processing video download...');
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dl_mp4_'));

    try {
      const result = await downloader.downloadVideo(url, tempDir);
      await ctx.api.editMessageText(ctx.chat.id, statusMsg.message_id, '📤 Uploading Video to Telegram...');
      await ctx.replyWithVideo(new InputFile(result.filePath));
      totalDownloadsCount++;
      await ctx.api.deleteMessage(ctx.chat.id, statusMsg.message_id).catch(() => {});
    } catch (err: any) {
      await ctx.api.editMessageText(ctx.chat.id, statusMsg.message_id, `❌ Failed to download video: ${err.message}`);
    } finally {
      fs.rmSync(tempDir, { recursive: true, force: true });
    }
  });

  // ─── Command: /qr ───────────────────────────────────────────────────────────
  bot.command('qr', async (ctx) => {
    const text = ctx.match?.trim();
    if (!text) {
      return ctx.reply('⚠️ Usage: `/qr your text or link here`', { parse_mode: 'Markdown' });
    }

    try {
      const tempPath = path.join(os.tmpdir(), `qr_${Date.now()}.png`);
      await QRCode.toFile(tempPath, text, { width: 400 });
      await ctx.replyWithPhoto(new InputFile(tempPath), { caption: `✅ QR Code generated for:\n\`${text}\``, parse_mode: 'Markdown' });
      fs.unlinkSync(tempPath);
    } catch (err: any) {
      await ctx.reply(`❌ QR Generation failed: ${err.message}`);
    }
  });

  // ─── Message Handler (URL Auto-Detect) ──────────────────────────────────────
  bot.on('message:text', async (ctx) => {
    const text = ctx.message.text;
    const urlRegex = /(https?:\/\/[^\s]+)/g;
    const matches = text.match(urlRegex);

    if (!matches || matches.length === 0) {
      return ctx.reply("💡 Send me any valid link (YouTube, Instagram, TikTok, etc.) to download media!");
    }

    const targetUrl = matches[0];
    const urlKey = storeUrl(targetUrl);

    const keyboard = new InlineKeyboard()
      .text('🎥 Download Video', `dl_video:${urlKey}`)
      .text('🎵 Download Audio', `dl_audio:${urlKey}`)
      .row()
      .text('ℹ️ Media Info', `dl_info:${urlKey}`);

    await ctx.reply(`🔗 **Detected Link:**\n\`${targetUrl}\`\n\nChoose an option below:`, {
      parse_mode: 'Markdown',
      reply_markup: keyboard
    });
  });

  // ─── Callback Query Handlers ────────────────────────────────────────────────
  bot.on('callback_query:data', async (ctx) => {
    const data = ctx.callbackQuery.data;

    if (data === 'show_help') {
      await ctx.answerCallbackQuery();
      return ctx.reply('📖 Use `/help` to view all features and usage examples.');
    }

    if (data === 'show_stats') {
      await ctx.answerCallbackQuery();
      const uptimeSec = Math.floor((Date.now() - botStartTime) / 1000);
      return ctx.reply(`📊 **Bot Uptime:** ${uptimeSec}s | Downloads: ${totalDownloadsCount}`);
    }

    if (data.startsWith('dl_video:')) {
      const key = data.replace('dl_video:', '');
      const url = getStoredUrl(key);
      if (!url) {
        return ctx.answerCallbackQuery({ text: '⚠️ Link session expired. Please send the link again.', show_alert: true });
      }
      await ctx.answerCallbackQuery('Starting video download...');
      if (!ctx.chat) return;
      const chatId = ctx.chat.id;
      const statusMsg = await ctx.reply('⏳ Downloading video...');
      const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dl_video_cb_'));

      try {
        const result = await downloader.downloadVideo(url, tempDir);
        await ctx.api.editMessageText(chatId, statusMsg.message_id, '📤 Uploading video...');
        await ctx.replyWithVideo(new InputFile(result.filePath));
        totalDownloadsCount++;
        await ctx.api.deleteMessage(chatId, statusMsg.message_id).catch(() => {});
      } catch (err: any) {
        await ctx.api.editMessageText(chatId, statusMsg.message_id, `❌ Download failed: ${err.message}`);
      } finally {
        fs.rmSync(tempDir, { recursive: true, force: true });
      }
    }

    if (data.startsWith('dl_audio:')) {
      const key = data.replace('dl_audio:', '');
      const url = getStoredUrl(key);
      if (!url) {
        return ctx.answerCallbackQuery({ text: '⚠️ Link session expired. Please send the link again.', show_alert: true });
      }
      await ctx.answerCallbackQuery('Starting audio download...');
      if (!ctx.chat) return;
      const chatId = ctx.chat.id;
      const statusMsg = await ctx.reply('⏳ Extracting MP3 audio...');
      const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dl_audio_cb_'));

      try {
        const result = await downloader.downloadAudio(url, tempDir);
        await ctx.api.editMessageText(chatId, statusMsg.message_id, '📤 Uploading audio...');
        await ctx.replyWithAudio(new InputFile(result.filePath), { title: result.title });
        totalDownloadsCount++;
        await ctx.api.deleteMessage(chatId, statusMsg.message_id).catch(() => {});
      } catch (err: any) {
        await ctx.api.editMessageText(chatId, statusMsg.message_id, `❌ Extraction failed: ${err.message}`);
      } finally {
        fs.rmSync(tempDir, { recursive: true, force: true });
      }
    }

    if (data.startsWith('dl_info:')) {
      const key = data.replace('dl_info:', '');
      const url = getStoredUrl(key);
      if (!url) {
        return ctx.answerCallbackQuery({ text: '⚠️ Link session expired. Please send the link again.', show_alert: true });
      }
      await ctx.answerCallbackQuery('Fetching info...');
      try {
        const info = await downloader.getInfo(url);
        const durationStr = info.duration ? `${Math.floor(info.duration / 60)}m ${info.duration % 60}s` : 'Unknown';
        const msg = `ℹ️ **Media Information:**\n\n` +
          `📌 **Title:** ${info.title}\n` +
          `👤 **Uploader:** ${info.uploader || 'N/A'}\n` +
          `⏱ **Duration:** ${durationStr}\n` +
          `🌐 **Platform:** ${info.extractor || 'Web'}`;
        await ctx.reply(msg, { parse_mode: 'Markdown' });
      } catch (err: any) {
        await ctx.reply(`❌ Could not fetch media info: ${err.message}`);
      }
    }
  });

  return bot;
}

