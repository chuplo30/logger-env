import os
import re
import json
import asyncio
import threading
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

import discord
from discord.ext import commands
from flask import Flask

# ====================== CONFIG ======================
TOKEN      = os.getenv("DISCORD_TOKEN")
PREFIX     = "!"
PORT       = int(os.getenv("PORT", "10000"))
LUNE       = os.getenv("LUNE_PATH", "lune")
HOOK       = os.getenv("HOOK_PATH", str(Path(__file__).parent / "hook.lua"))
WORK       = Path(os.getenv("WORK_DIR", str(Path(__file__).parent / "work")))
TIMEOUT    = int(os.getenv("TIMEOUT", "60"))
MAX_INPUT  = int(os.getenv("MAX_INPUT", 4 * 1024 * 1024))
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "5"))
WHITELIST  = {int(x) for x in os.getenv("WHITELIST", "").split(",") if x.strip().isdigit()}

WORK.mkdir(parents=True, exist_ok=True)

# ====================== BOT ======================
intents = discord.Intents.default()
intents.message_content = True
intents.attachments = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

_calls = defaultdict(list)

def rate_ok(uid: int):
    now = datetime.now().timestamp()
    keep = [t for t in _calls[uid] if now - t < 60]
    if len(keep) >= RATE_LIMIT:
        _calls[uid] = keep
        return False, int(60 - (now - keep[0]))
    keep.append(now)
    _calls[uid] = keep
    return True, 0

# ====================== HELPERS ======================
CODE_BLOCK = re.compile(r"```(?:lua|luau)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)

def extract_code(text: str):
    m = CODE_BLOCK.search(text or "")
    return m.group(1).encode("utf-8") if m else None

JSON_BEGIN = "@@@LUNE_JSON_BEGIN@@@"
JSON_END   = "@@@LUNE_JSON_END@@@"

async def run_lune(inp: Path):
    cmd = [LUNE, "run", str(HOOK), str(inp)]
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(WORK),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        so, se = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
        return so.decode("utf-8", "replace"), se.decode("utf-8", "replace"), proc.returncode
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return None, None, "TIMEOUT"

def parse_json(stdout: str):
    if not stdout:
        return None
    try:
        s = stdout.index(JSON_BEGIN) + len(JSON_BEGIN)
        e = stdout.index(JSON_END)
        blob = stdout[s:e].strip()
        return json.loads(blob)
    except (ValueError, json.JSONDecodeError):
        return None

def format_logs(logs, limit=3500):
    lines = []
    for entry in logs:
        kind = entry.get("kind", "?")
        msg  = entry.get("msg", "")
        tag = {
            "print": "🖨 ",
            "warn":  "⚠️ ",
            "loadstring": "📦 LOADSTRING",
            "loadstring_code": "📜 CODE",
            "loadstring_error": "❌ LOAD-ERR",
            "getfenv": "🌐 GETFENV",
            "setfenv": "🌐 SETFENV",
            "HttpGet": "🌍 HTTP",
            "HttpGetAsync": "🌍 HTTP",
            "HttpPost": "🌍 HTTP",
            "GetService": "🔧 SVC",
            "require": "📎 require",
            "task.wait": "⏱ wait",
            "writefile": "💾 WRITE",
            "readfile": "📂 READ",
            "queue_on_teleport": "🚀 TELEPORT",
        }.get(kind, f"[{kind}] ")
        lines.append(f"{tag}{msg}")
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit] + "\n... (truncated)"
    return text or "(no logs)"

# ====================== COMMANDS ======================
@bot.event
async def on_ready():
    print(f"[BOT]   Logged in as {bot.user}")
    print(f"[FLASK] Listening on 0.0.0.0:{PORT}")
    print(f"[LUNE]  {LUNE}")
    print(f"[HOOK]  {HOOK}")


@bot.command(name="run", aliases=["l", "r"])
async def cmd_run(ctx: commands.Context, *, args: str = ""):
    """Chạy file/code Lua qua Lune + hook."""
    if WHITELIST and ctx.author.id not in WHITELIST:
        return await ctx.reply("❌ Không có quyền.")

    ok, wait = rate_ok(ctx.author.id)
    if not ok:
        return await ctx.reply(f"⏳ Rate limit, thử lại sau **{wait}s**.")

    if args.strip() in ("help", "-h", "--help"):
        return await ctx.reply(HELP)

    src, name = None, "input.lua"

    # 1) attachment
    if ctx.message.attachments:
        a = ctx.message.attachments[0]
        if a.size > MAX_INPUT:
            return await ctx.reply(f"❌ File quá lớn ({a.size} B).")
        src = await a.read()
        name = a.filename
    # 2) reply file/code
    elif ctx.message.reference:
        ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        if ref.attachments:
            a = ref.attachments[0]
            if a.size > MAX_INPUT:
                return await ctx.reply("❌ File quá lớn.")
            src = await a.read()
            name = a.filename
        elif ref.content:
            src = extract_code(ref.content)
    # 3) code block
    else:
        src = extract_code(args)

    if not src:
        return await ctx.reply("❌ Gửi file, reply file/code, hoặc code block ```lua ...```.")
    if len(src) > MAX_INPUT:
        return await ctx.reply(f"❌ Source {len(src)} B vượt giới hạn.")

    session = f"{ctx.author.id}_{int(datetime.now().timestamp()*1000)}"
    stem = re.sub(r"[^\w\-.]", "_", Path(name).stem)[:40] or "input"
    inp = WORK / f"{session}.lua"
    inp.write_bytes(src)

    status = await ctx.reply(f"⏳ `{stem}` ({len(src)} B) — running...")

    try:
        so, se, rc = await run_lune(inp)
    except Exception as e:
        inp.unlink(missing_ok=True)
        return await status.edit(content=f"❌ spawn lune lỗi: `{e}`")

    inp.unlink(missing_ok=True)

    if rc == "TIMEOUT":
        return await status.edit(content=f"⏱️ Timeout sau {TIMEOUT}s.")

    data = parse_json(so)
    if data is None:
        tail = (so or "")[-1000:] + "\n---stderr---\n" + (se or "")[-600:]
        return await status.edit(content=f"❌ Không parse được output (rc={rc}).\n```\n{tail[-1500:]}\n```")

    logs      = data.get("logs", [])
    ok_flag   = data.get("ok", False)
    err_msg   = data.get("err")

    # Đếm log types
    counts = defaultdict(int)
    for e in logs:
        counts[e.get("kind", "?")] += 1

    embed = discord.Embed(
        title="🪝 Lune Hook Result",
        color=0x57F287 if ok_flag else 0xED4245,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Input", value=f"`{stem}` · {len(src)} B", inline=True)
    embed.add_field(name="Status", value="✅ ok" if ok_flag else "❌ error", inline=True)
    embed.add_field(name="Logs", value=str(len(logs)), inline=True)

    if counts:
        summary = " · ".join(f"{k}×{v}" for k, v in sorted(counts.items()))
        embed.add_field(name="Hooks", value=summary[:1000], inline=False)

    if err_msg:
        embed.add_field(
            name="Error",
            value=f"```\n{err_msg[:1500]}\n```",
            inline=False,
        )

    log_text = format_logs(logs)
    if log_text and log_text != "(no logs)":
        # chia thành nhiều field nếu dài
        chunks = [log_text[i:i+1000] for i in range(0, min(len(log_text), 5000), 1000)]
        for i, chunk in enumerate(chunks[:5]):
            embed.add_field(name=f"Log {'(cont.)' if i else ''}", value=f"```\n{chunk}\n```", inline=False)

    # File log đầy đủ
    files = []
    full = json.dumps(data, indent=2, ensure_ascii=False)
    if len(full) > 3000:
        log_file = WORK / f"{session}.log.json"
        log_file.write_text(full, encoding="utf-8")
        files.append(discord.File(str(log_file), filename=f"{stem}.log.json"))

    try:
        await status.edit(content="", embed=embed, attachments=files)
    except discord.HTTPException:
        # fallback text
        await status.edit(content=f"Result:\n```\n{log_text[:1800]}\n```")

    # cleanup file log
    for f in files:
        try:
            Path(f.fp.name).unlink(missing_ok=True)
        except Exception:
            pass


@bot.command(name="ping")
async def cmd_ping(ctx):
    await ctx.reply(f"pong 🏓 `{round(bot.latency * 1000)}ms`")

# ====================== FLASK (keep-alive) ======================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is alive!", 200

@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": str(bot.user) if bot.is_ready() else "starting",
        "lune": LUNE,
        "hook": HOOK,
    }, 200

def run_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


# ====================== MAIN ======================
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Missing DISCORD_TOKEN env var")

    # Flask trong thread riêng (daemon) → không block bot
    threading.Thread(target=run_flask, daemon=True).start()
    print(f"[FLASK] Thread started on port {PORT}")

    # Bot chạy ở main thread
    bot.run(TOKEN)
