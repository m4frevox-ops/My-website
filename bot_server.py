"""Night Call: Discord bot + website server (one process).
Tracks voice time / messages / boosts, serves the site, Discord login, and staff applications."""
import asyncio, base64, hashlib, hmac, json, os, random, secrets, sqlite3, time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
import discord
from aiohttp import web
from discord.ext import tasks
from dotenv import load_dotenv

from levels import cum, lvl, make_card, prog

load_dotenv()
TOKEN = os.environ["DISCORD_TOKEN"]
CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
GUILD_ID = int(os.environ["GUILD_ID"])
APPLY_CHANNEL = int(os.environ["APPLY_CHANNEL_ID"])
BASE_URL = os.environ["BASE_URL"].rstrip("/")
SECRET = os.environ["SESSION_SECRET"]
PORT = int(os.getenv("PORT", "8080"))
EXCLUDED = {int(x) for x in os.getenv("EXCLUDED_VOICE_IDS", "").split(",") if x.strip()}
WEB = Path(__file__).parent / "web"

# ---------- database ----------
db = sqlite3.connect(os.getenv("DB_PATH", str(Path(__file__).parent / "data.db")), check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT,avatar TEXT,"
           "voice INTEGER DEFAULT 0,msgs INTEGER DEFAULT 0,boosts INTEGER DEFAULT 0)")
db.execute("CREATE TABLE IF NOT EXISTS applies(user_id INTEGER,type TEXT,ts INTEGER)")
for _c in ("txp", "vxp"):
    try:
        db.execute(f"ALTER TABLE users ADD COLUMN {_c} INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
db.commit()

def bump(u, field, n=1):
    assert field in ("voice", "msgs", "boosts", "txp", "vxp")
    db.execute("INSERT INTO users(id,name,avatar) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE "
               "SET name=excluded.name,avatar=excluded.avatar",
               (u.id, u.display_name, u.display_avatar.with_size(128).url))
    db.execute(f"UPDATE users SET {field}={field}+? WHERE id=?", (n, u.id))
    db.commit()

# ---------- bot ----------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # enable "Message Content Intent" (needed for the ; commands)  # enable "Server Members Intent" in the Developer Portal
bot = discord.Client(intents=intents)
STATS = {"m": 0, "o": 0, "v": 0, "c": 0, "r": 0, "b": 0}
BOOST_TYPES = {discord.MessageType.premium_guild_subscription,
               discord.MessageType.premium_guild_tier_1,
               discord.MessageType.premium_guild_tier_2,
               discord.MessageType.premium_guild_tier_3}

@bot.event
async def on_message(m):
    if not m.guild or m.guild.id != GUILD_ID:
        return
    if m.type in BOOST_TYPES:
        bump(m.author, "boosts")
        return
    if m.author.bot:
        return
    if m.content.startswith(PREFIX) and await command(m):
        return
    bump(m.author, "msgs")
    now = time.time()
    if now - LAST.get(m.author.id, 0) >= 60:  # 1 XP award per minute per user
        LAST[m.author.id] = now
        up = await gain(m.author, "txp", random.randint(15, 25))
        if up:
            await m.channel.send(f"🎉 {m.author.mention} reached **Text Level {up}**!")

@tasks.loop(seconds=60)
async def voice_tick():
    g = bot.get_guild(GUILD_ID)
    if not g:
        return
    afk = g.afk_channel.id if g.afk_channel else 0
    for vc in list(g.voice_channels) + list(g.stage_channels):
        if vc.id == afk or vc.id in EXCLUDED:
            continue
        for m in vc.members:
            if not m.bot:
                bump(m, "voice", 60)
                await gain(m, "vxp", 10)  # 10 voice XP per minute

@tasks.loop(minutes=5)
async def stats_tick():
    try:
        info = await bot.fetch_guild(GUILD_ID, with_counts=True)
        g = bot.get_guild(GUILD_ID)
        v = sum(1 for vc in list(g.voice_channels) + list(g.stage_channels)
                for m in vc.members if not m.bot) if g else 0
        STATS.update(m=info.approximate_member_count or 0, o=info.approximate_presence_count or 0, v=v,
                     c=len(g.channels) if g else 0, r=max(len(g.roles) - 1, 0) if g else 0,
                     b=info.premium_subscription_count or 0)
    except Exception as e:
        print("stats error:", e)

@bot.event
async def on_ready():
    print("Bot ready:", bot.user)
    g = bot.get_guild(GUILD_ID)
    if g:  # current boosters count as 1 boost if not tracked yet
        for m in g.premium_subscribers:
            row = db.execute("SELECT boosts FROM users WHERE id=?", (m.id,)).fetchone()
            if not row or row[0] == 0:
                bump(m, "boosts")
    if not voice_tick.is_running():
        voice_tick.start()
    if not stats_tick.is_running():
        stats_tick.start()

# ---------- levels ----------
PREFIX = ";"
LAST = {}
# one reward role per category: the highest milestone reached (lower ones are removed)
VROLES = {10: 1522979685791825971, 20: 1522979770541936760, 25: 1522979847549358292, 30: 1522979908358246490,
          35: 1522980138625667092, 40: 1522980211317149767, 50: 1522980310642458795}
TROLES = {10: 1522984652812587108, 20: 1522984544074989649, 30: 1522984440278683680,
          35: 1522984299517706260, 40: 1525081127616905226}

def row(uid):
    return db.execute("SELECT txp,vxp FROM users WHERE id=?", (uid,)).fetchone() or (0, 0)

def rank_of(uid, expr):
    return db.execute(f"SELECT COUNT(*)+1 FROM users WHERE ({expr}) > (SELECT {expr} FROM users WHERE id=?)",
                      (uid,)).fetchone()[0]

async def rewards(m):
    t, v = row(m.id)
    for table, xp in ((TROLES, t), (VROLES, v)):
        level = lvl(xp)
        want = max((k for k in table if k <= level), default=None)
        have = {r.id for r in m.roles}
        for k, rid in table.items():
            role = m.guild.get_role(rid)
            if not role:
                continue
            try:
                if k == want and rid not in have:
                    await m.add_roles(role, reason="Level reward")
                elif k != want and rid in have:
                    await m.remove_roles(role, reason="Level reward")
            except discord.HTTPException as e:
                print("role error:", e)

async def gain(m, field, n):
    i = 0 if field == "txp" else 1
    old = lvl(row(m.id)[i])
    bump(m, field, n)
    new = lvl(row(m.id)[i])
    if new > old:
        await rewards(m)
        return new
    return 0

async def target(m, args):
    if m.mentions:
        u = m.mentions[0]
        return u if isinstance(u, discord.Member) else await m.guild.fetch_member(u.id)
    for a in args:
        if a.isdigit() and len(a) > 15:
            try:
                return await m.guild.fetch_member(int(a))
            except discord.NotFound:
                return None
    return m.author

async def command(m):
    parts = m.content[len(PREFIX):].split()
    if not parts:
        return False
    c, args = parts[0].lower(), parts[1:]
    if c == "rank":
        t = await target(m, args)
        if not t:
            await m.reply("User not found.")
            return True
        bump(t, "txp", 0)
        tx, vx = row(t.id)
        av = await t.display_avatar.replace(size=256, static_format="png").read()
        card = make_card(t.display_name, t.name, av, tx, vx, rank_of(t.id, "txp+vxp"),
                         rank_of(t.id, "txp"), rank_of(t.id, "vxp"))
        await m.reply(file=discord.File(card, "rank.png"))
        return True
    if c in ("leader", "leaderboard", "lb", "top"):
        e = discord.Embed(title="Night Call Leaderboard", color=0x8B5CF6)
        for title, expr in (("Top Total XP", "txp+vxp"), ("Top Voice", "vxp"), ("Top Text", "txp")):
            rows = db.execute(f"SELECT name,({expr}) FROM users WHERE ({expr})>0 ORDER BY ({expr}) DESC LIMIT 10").fetchall()
            lines = [f"`{i}.` {n[:20]} · {x:,} XP" + (f" (Lvl {lvl(x)})" if expr != "txp+vxp" else "")
                     for i, (n, x) in enumerate(rows, 1)]
            e.add_field(name=title, value="\n".join(lines) or "No data yet", inline=False)
        await m.reply(embed=e)
        return True
    if c in ("addlevel", "removelevel", "addxp", "removexp"):
        if not m.author.guild_permissions.administrator:
            await m.reply("Administrators only.")
            return True
        t = await target(m, args)
        nums = [a for a in args if a.lstrip("-").isdigit() and len(a) < 12]
        kinds = [a.lower() for a in args if a.lower() in ("text", "voice")]
        if not t or not nums or not kinds:
            await m.reply(f"Usage: `{PREFIX}{c} @user <text|voice> <amount>`")
            return True
        n = max(-10**6, min(int(nums[0]), 10**6))
        f = "txp" if kinds[0] == "text" else "vxp"
        bump(t, f, 0)
        cur = row(t.id)[0 if f == "txp" else 1]
        new = {"addxp": cur + n, "removexp": cur - n, "addlevel": cum(lvl(cur) + n),
               "removelevel": cum(max(0, lvl(cur) - n))}[c]
        new = max(0, new)
        db.execute(f"UPDATE users SET {f}=? WHERE id=?", (new, t.id))
        db.commit()
        await rewards(t)
        await m.reply(f"Done. {t.display_name}: {kinds[0]} level is now **{lvl(new)}** ({new:,} XP).")
        return True
    return False

async def profile(req):
    u = me(req)
    if not u:
        return J({"error": "auth"}, 401)
    uid = int(u["id"])
    t, v = row(uid)
    def pk(x):
        level, cur, need = prog(x)
        return {"level": level, "xp": cur, "need": need}
    ranks = {k: rank_of(uid, e) for k, e in (("text", "txp"), ("voice", "vxp"), ("total", "txp+vxp"))} if (t or v) else {}
    return J({"id": u["id"], "name": u["name"], "username": u.get("username", ""), "avatar": u["avatar"],
              "text": pk(t), "voice": pk(v), "total": t + v, "rank": ranks})

# ---------- sessions ----------
def sign(d):
    raw = base64.urlsafe_b64encode(json.dumps(d).encode()).decode()
    return raw + "." + hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()

def unsign(s):
    try:
        raw, sig = s.rsplit(".", 1)
        if not hmac.compare_digest(sig, hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()):
            return None
        d = json.loads(base64.urlsafe_b64decode(raw))
        return d if d["exp"] > time.time() else None
    except Exception:
        return None

def me(req):
    c = req.cookies.get("nc")
    return unsign(c) if c else None

def J(d, s=200):
    return web.json_response(d, status=s, headers={"Cache-Control": "no-store"})

# ---------- routes ----------
async def index(_):
    return web.FileResponse(WEB / "index.html")

async def stats(_):
    return J(STATS)

async def leaderboard(_):
    def top(f, div=1):
        rows = db.execute(f"SELECT name,{f},avatar FROM users WHERE {f}>0 ORDER BY {f} DESC LIMIT 3").fetchall()
        return [[n, v // div, a] for n, v, a in rows]
    return J({"voice": top("voice", 60), "chat": top("msgs"), "boost": top("boosts")})

OW = {"t": 0, "d": None}

async def owner(_):
    if time.time() - OW["t"] > 3600:
        try:
            u = await bot.fetch_user(int(os.getenv("OWNER_ID", "1384297210925285507")))
            OW.update(t=time.time(), d={"name": u.global_name or u.name, "user": u.name,
                                        "avatar": u.display_avatar.with_size(256).url})
        except Exception as e:
            print("owner error:", e)
    return J(OW["d"] or {})

async def api_me(req):
    u = me(req)
    return J({"user": {"name": u["name"], "username": u.get("username", ""), "avatar": u["avatar"]} if u else None})

async def login(req):
    st = secrets.token_urlsafe(16)
    url = "https://discord.com/oauth2/authorize?" + urlencode({
        "client_id": CLIENT_ID, "response_type": "code", "scope": "identify",
        "redirect_uri": BASE_URL + "/auth/callback", "state": st})
    r = web.HTTPFound(url)
    r.set_cookie("st", st, max_age=600, httponly=True, secure=True, samesite="Lax")
    raise r

async def callback(req):
    code, st = req.query.get("code"), req.query.get("state")
    if not code or not st or st != req.cookies.get("st"):
        return web.Response(text="Invalid login state. Go back and try again.", status=400)
    async with aiohttp.ClientSession() as s:
        async with s.post("https://discord.com/api/oauth2/token", data={
                "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "grant_type": "authorization_code",
                "code": code, "redirect_uri": BASE_URL + "/auth/callback"}) as r:
            if r.status != 200:
                return web.Response(text="Discord login failed.", status=400)
            tok = (await r.json())["access_token"]
        async with s.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {tok}"}) as r:
            u = await r.json()
    uid = u["id"]
    avatar = (f"https://cdn.discordapp.com/avatars/{uid}/{u['avatar']}.png?size=128" if u.get("avatar")
              else f"https://cdn.discordapp.com/embed/avatars/{(int(uid) >> 22) % 6}.png")
    r = web.HTTPFound("/")
    r.set_cookie("nc", sign({"id": uid, "name": u.get("global_name") or u["username"], "avatar": avatar, "username": u["username"],
                             "exp": time.time() + 7 * 86400}),
                 max_age=7 * 86400, httponly=True, secure=True, samesite="Lax")
    r.del_cookie("st")
    raise r

async def logout(_):
    r = web.HTTPFound("/")
    r.del_cookie("nc")
    raise r

Q = {"staff": ["What's your name?", "How old are you?", "Why do you want this position?",
               "Do you have a staff team experience?"],
     "event": ["What's your name?", "How old are you?", "Why do you want this position?",
               "Do you have a event hoster experience?"]}
TITLES = {"staff": "Staff Team", "event": "Event Hoster"}

async def apply(req):
    u = me(req)
    if not u:
        return J({"error": "Sign in with Discord first"}, 401)
    try:
        d = await req.json()
    except Exception:
        return J({"error": "Bad request"}, 400)
    t, a = d.get("type"), d.get("answers")
    if t not in Q or not isinstance(a, list) or len(a) != 4 or not all(isinstance(x, str) and x.strip() for x in a):
        return J({"error": "Please answer every question"}, 400)
    try:
        await bot.get_guild(GUILD_ID).fetch_member(int(u["id"]))
    except discord.NotFound:
        return J({"error": "Join the Night Call server first"}, 403)
    last = db.execute("SELECT MAX(ts) FROM applies WHERE user_id=? AND type=?", (int(u["id"]), t)).fetchone()[0]
    if last and time.time() - last < 86400:
        return J({"error": "You already applied recently. Try again in 24 hours."}, 429)
    ch = bot.get_channel(APPLY_CHANNEL) or await bot.fetch_channel(APPLY_CHANNEL)
    e = discord.Embed(title=f"{TITLES[t]} Application", description=f"From <@{u['id']}> ({u['name']})", color=0x8B5CF6)
    for q, x in zip(Q[t], a):
        e.add_field(name=q, value=x.strip()[:1000], inline=False)
    e.set_thumbnail(url=u["avatar"])
    await ch.send(embed=e)
    db.execute("INSERT INTO applies VALUES(?,?,?)", (int(u["id"]), t, int(time.time())))
    db.commit()
    return J({"ok": True})

async def main():
    app = web.Application()
    app.add_routes([web.get("/", index), web.get("/api/stats", stats), web.get("/api/leaderboard", leaderboard),
                    web.get("/api/me", api_me), web.get("/api/owner", owner), web.get("/api/profile", profile), web.get("/auth/login", login), web.get("/auth/callback", callback),
                    web.get("/auth/logout", logout), web.post("/api/apply", apply),
                    web.static("/assets", WEB / "assets")])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, os.getenv("HOST", "0.0.0.0"), PORT).start()
    print(f"Website on port {PORT}")
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
