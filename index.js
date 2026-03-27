const { Telegraf, Markup } = require('telegraf');
const ytDlp = require('yt-dlp-exec');
const Groq = require('groq-sdk');
const { translate } = require('google-translate-api-x');
const schedule = require('node-schedule');
const path = require('path');
const fs = require('fs-extra');
const { spawn } = require('child_process');
require('dotenv').config();

const BOT_TOKEN = process.env.BOT_TOKEN;
const GROQ_API_KEY = process.env.GROQ_API_KEY;

if (!BOT_TOKEN) {
  console.error("Error: BOT_TOKEN is missing!");
  process.exit(1);
}

const bot = new Telegraf(BOT_TOKEN);
const groq = GROQ_API_KEY ? new Groq({ apiKey: GROQ_API_KEY }) : null;

// --- UTILS ---

const cleanup = async (dir) => {
  try {
    if (await fs.pathExists(dir)) {
      await fs.remove(dir);
    }
  } catch (err) {
    console.error(`Cleanup error: ${err.message}`);
  }
};

const askAi = async (prompt, systemPrompt) => {
  if (!groq) return "Bhai API Key missing hai Railway mein! 🙏";
  try {
    const completion = await groq.chat.completions.create({
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: prompt }
      ],
      model: "llama-3.1-8b-instant",
    });
    return completion.choices[0].message.content;
  } catch (err) {
    console.error(`Groq error: ${err.message}`);
    return "Bhai AI abhi thoda busy hai, baad mein try kar! 🙏";
  }
};

// --- DOWNLOAD LOGIC ---

const downloadMedia = async (url, outputDir, audioOnly = false) => {
  const options = {
    output: `${outputDir}/%(id)s.%(ext)s`,
    format: audioOnly ? 'bestaudio/best' : 'bestvideo[filesize<45M]+bestaudio/best[filesize<45M]',
    noWarnings: true,
    verbose: true,
  };

  if (await fs.pathExists("cookies.txt")) {
    options.cookiefile = "cookies.txt";
    console.log("DEBUG: Using cookies.txt for yt-dlp");
  }

  if (audioOnly) {
    options.extractAudio = true;
    options.audioFormat = 'mp3';
    options.audioQuality = '192';
  }

  return ytDlp(url, options);
};

// --- HANDLERS ---

bot.start((ctx) => {
  const welcomeText = 
    "Hello bhai! 👋 Main tera Node.js par chalne wala bot hoon.\n\n" +
    "📥 *Media Downloader*\n" +
    "YouTube, Instagram, Twitter aur FB links bhej aur main media bhej dunga (<50MB).\n\n" +
    "🤖 *AI Fun Mode*\n" +
    "Niche waale buttons try kar!";
  
  return ctx.replyWithMarkdown(welcomeText, Markup.inlineKeyboard([
    [Markup.button.callback("😂 Roast Karo", "mode_roast"), Markup.button.callback("🎤 Shayari Likho", "mode_shayari")],
    [Markup.button.callback("🎵 Rap Banao", "mode_rap"), Markup.button.callback("🔮 Bhavishya Batao", "mode_fortune")],
    [Markup.button.callback("📝 Story Likho", "mode_story"), Markup.button.callback("🍕 Recipe Batao", "mode_recipe")]
  ]));
});

bot.command('mp3', async (ctx) => {
  const url = ctx.message.text.split(' ')[1];
  if (!url) return ctx.reply("Bhai link toh bhej! Example: /mp3 [link]");

  const statusMsg = await ctx.reply("⏳ MP3 download ho raha hai... thoda ruk bhai!");
  const dlDir = path.join(__dirname, `dl_mp3_${ctx.from.id}_${Date.now()}`);
  await fs.ensureDir(dlDir);

  try {
    await downloadMedia(url, dlDir, true);
    const files = await fs.readdir(dlDir);
    const mp3File = files.find(f => f.endsWith('.mp3'));

    if (mp3File) {
      const filePath = path.join(dlDir, mp3File);
      const stats = await fs.stat(filePath);
      if (stats.size <= 50 * 1024 * 1024) {
        await ctx.replyWithAudio({ source: filePath });
        await ctx.deleteMessage(statusMsg.message_id);
      } else {
        await ctx.telegram.editMessageText(ctx.chat.id, statusMsg.message_id, null, "Bhai MP3 50MB se badi hai! 😔");
      }
    } else {
      await ctx.telegram.editMessageText(ctx.chat.id, statusMsg.message_id, null, "Bhai MP3 download nahi hui. 😔");
    }
  } catch (err) {
    const errorMsg = err.message || "";
    if (errorMsg.includes("Sign in to confirm") || errorMsg.includes("403")) {
      await ctx.telegram.editMessageText(ctx.chat.id, statusMsg.message_id, null, "Bhai YouTube ne block kiya hua hai! 🛑 `cookies.txt` upload karein.");
    } else {
      await ctx.telegram.editMessageText(ctx.chat.id, statusMsg.message_id, null, "Bhai download error aagaya! 🙏");
    }
  } finally {
    await cleanup(dlDir);
  }
});

bot.command('translate', async (ctx) => {
  const text = ctx.message.text.split(' ').slice(1).join(' ') || (ctx.message.reply_to_message ? ctx.message.reply_to_message.text : "");
  if (!text) return ctx.reply("Bhai kya translate karun? 🙏");
  try {
    const res = await translate(text, { to: 'hi' });
    await ctx.reply(`🌐 Translated (Auto → Hindi):\n\n${res.text}`);
  } catch (err) {
    await ctx.reply("Translation error bhai! 🙏");
  }
});

bot.command('remind', async (ctx) => {
  const parts = ctx.message.text.split(' ');
  if (parts.length < 3) return ctx.reply("Format: /remind 10m Chai peeni hai 🙏");
  const timeStr = parts[1];
  const msg = parts.slice(2).join(' ');
  
  let delay = 0;
  if (timeStr.endsWith('s')) delay = parseInt(timeStr) * 1000;
  else if (timeStr.endsWith('m')) delay = parseInt(timeStr) * 60 * 1000;
  else if (timeStr.endsWith('h')) delay = parseInt(timeStr) * 3600 * 1000;
  else delay = parseInt(timeStr) * 1000;

  const runDate = new Date(Date.now() + delay);
  schedule.scheduleJob(runDate, () => {
    ctx.reply(`⏰ Yaad dilaya bhai: ${msg}`);
  });
  await ctx.reply(`Done bhai! ${timeStr} baad yaad dila dunga. 👍`);
});

bot.on('callback_query', async (ctx) => {
  const data = ctx.callbackQuery.data;
  const prompts = {
    mode_roast: "Naam bata jisko roast karna hai! 🔥",
    mode_shayari: "Kis topic pe shayari likhun? 📝",
    mode_rap: "Rap ka topic bata! 🔥🎤",
    mode_fortune: "Naam bata bhavishya dekhne ke liye! 🔮",
    mode_story: "Kis topic pe story likhun? 📝",
    mode_recipe: "Ingredients ya Dish name batao! 🍕"
  };
  if (prompts[data]) {
    ctx.session = { mode: data.replace('mode_', '') };
    await ctx.editMessageText(prompts[data]);
  }
});

bot.on('text', async (ctx) => {
  const userText = ctx.message.text;
  const session = ctx.session || {};

  if (session.mode) {
    const status = await ctx.reply("Typing... 🤖");
    const aiMeta = {
      roast: ["You are a savage Indian roaster. 4 lines Hinglish.", `Roast: ${userText}`],
      shayari: ["Deep poet Mirza Ghalib style. 4 lines Hinglish.", `Topic: ${userText}`],
      rap: ["Desi Underground Rapper. 8 lines Hinglish.", `Topic: ${userText}`],
      fortune: ["Funny Indian jyotishi. 3-4 lines Hinglish.", `Name: ${userText}`],
      story: ["Creative storyteller. 10 lines Hinglish.", `Topic: ${userText}`],
      recipe: ["Desi Chef. Ingredients/Dish recipe in Hinglish.", `Recipe: ${userText}`]
    };
    const [sys, user] = aiMeta[session.mode];
    const resp = await askAi(user, sys);
    await ctx.telegram.editMessageText(ctx.chat.id, status.message_id, null, resp);
    ctx.session = null;
    return;
  }

  const urls = userText.match(/http[s]?:\/\/[^\s]+/);
  if (urls) {
    const url = urls[0];
    const statusMsg = await ctx.reply("⏳ Download ho raha hai... ruk bhai!");
    const dlDir = path.join(__dirname, `dl_${ctx.from.id}_${Date.now()}`);
    await fs.ensureDir(dlDir);

    try {
      await downloadMedia(url, dlDir);
      const files = await fs.readdir(dlDir);
      const mediaFile = files.find(f => /\.(mp4|jpg|jpeg|png|webp|mp3)$/i.test(f));

      if (mediaFile) {
        const filePath = path.join(dlDir, mediaFile);
        const stats = await fs.stat(filePath);
        if (stats.size <= 50 * 1024 * 1024) {
          if (mediaFile.endsWith('.mp4')) await ctx.replyWithVideo({ source: filePath });
          else if (mediaFile.endsWith('.mp3')) await ctx.replyWithAudio({ source: filePath });
          else await ctx.replyWithPhoto({ source: filePath });
          await ctx.deleteMessage(statusMsg.message_id);
        } else {
          await ctx.telegram.editMessageText(ctx.chat.id, statusMsg.message_id, null, "Bhai file 50MB se badi hai! 😔");
        }
      } else {
        await ctx.telegram.editMessageText(ctx.chat.id, statusMsg.message_id, null, "Bhai media nahi mili. 😔");
      }
    } catch (err) {
      console.error(`Download error: ${err.message}`);
      const errorMsg = err.message || "";
      if (errorMsg.includes("Sign in to confirm") || errorMsg.includes("403")) {
        await ctx.telegram.editMessageText(ctx.chat.id, statusMsg.message_id, null, "Bhai YouTube ne block kiya hua hai! 🛑 `cookies.txt` upload karein.");
      } else {
        await ctx.telegram.editMessageText(ctx.chat.id, statusMsg.message_id, null, "Bhai download fail ho gaya! 🙏");
      }
    } finally {
      await cleanup(dlDir);
    }
  } else {
    await ctx.reply("Bhai link bhej ya /start se maze le! 🙏");
  }
});

// Setup yt-dlp on startup
console.log("Railway Setup: Updating yt-dlp...");
const child = spawn('pip', ['install', '--upgrade', 'yt-dlp', '-q']);
child.on('exit', () => {
  console.log("yt-dlp updated! Launching bot...");
  bot.launch({
    allowedUpdates: ['message', 'callback_query'],
    dropPendingUpdates: true
  }).then(() => console.log("Bot is alive!"));
});

process.once('SIGINT', () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));
