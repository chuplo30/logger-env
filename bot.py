import os
import discord
from discord.ext import commands
from lupa import LuaRuntime
import traceback
from datetime import datetime

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = "!"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# ====================== Lupa Runtime + Hooks ======================

def create_hooked_runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)

    # Logger
    logs = []

    def log(msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {msg}"
        logs.append(entry)
        print(entry)

    # === Hook loadstring / load ===
    original_loadstring = lua.eval("loadstring or load")

    def hooked_loadstring(code, chunkname="=(load)"):
        log(f"[LOADSTRING] chunkname={chunkname} | len={len(str(code))}")
        log(f"[LOADSTRING CODE]\n{str(code)[:2000]}")
        if len(str(code)) > 2000:
            log("... (truncated)")
        return original_loadstring(code, chunkname)

    lua.globals().loadstring = hooked_loadstring
    lua.globals().load = hooked_loadstring

    # === Hook getfenv / setfenv ===
    def hooked_getfenv(level=1):
        log(f"[GETFENV] level={level}")
        env = lua.eval("getfenv")(level)
        return env

    def hooked_setfenv(f, env):
        log(f"[SETFENV] called")
        return lua.eval("setfenv")(f, env)

    try:
        lua.globals().getfenv = hooked_getfenv
        lua.globals().setfenv = hooked_setfenv
    except:
        pass  # Luau / một số runtime không có

    # === Hook _G / ENV dump ===
    def dump_env():
        g = lua.globals()
        keys = []
        for k in g:
            keys.append(str(k))
        log(f"[ENV DUMP] Total keys in _G: {len(keys)}")
        log(f"[ENV KEYS] {', '.join(keys[:80])}")
        return keys

    lua.globals().dump_env = dump_env

    # Fake một số Roblox function để script không crash ngay
    lua.execute("""
        game = {GetService = function() return {} end}
        workspace = {}
        Instance = {new = function() return {} end}
        task = {wait = function() end, spawn = function(f) f() end}
        print = print
        warn = print
    """)

    return lua, logs

# ====================== Discord Commands ======================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.command()
async def run(ctx, *, code: str = None):
    """Chạy Lua code với hook"""
    if not code:
        # Nếu reply message có code block
        if ctx.message.reference:
            ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            code = ref.content
        else:
            return await ctx.send("Gửi code hoặc reply message chứa code")

    # Lấy code trong ```lua ... ```
    if "```" in code:
        code = code.split("```")[1]
        if code.startswith("lua"):
            code = code[3:]
        code = code.strip()

    await ctx.send("Đang chạy với hook...")

    try:
        lua, logs = create_hooked_runtime()
        result = lua.execute(code)

        log_text = "\n".join(logs[-30:])  # lấy 30 dòng log gần nhất
        if len(log_text) > 1800:
            log_text = log_text[:1800] + "\n... (truncated)"

        await ctx.send(f"**Result:**\n```\n{result}\n```")
        await ctx.send(f"**Hook Logs:**\n```\n{log_text}\n```")

    except Exception as e:
        err = traceback.format_exc()
        await ctx.send(f"**Error:**\n```\n{err[-1500:]}\n```")

@bot.command()
async def dump(ctx):
    """Dump ENV hiện tại"""
    lua, logs = create_hooked_runtime()
    lua.globals().dump_env()
    log_text = "\n".join(logs)
    await ctx.send(f"```\n{log_text[:1900]}\n```")

bot.run(TOKEN)
