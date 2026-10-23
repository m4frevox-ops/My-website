"""XP / level math and the rank card image for Night Call."""
import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG = Path(__file__).parent / "web" / "assets" / "rank_bg.png"

def cum(level):
    """Total XP needed to reach `level` (infinite levels). Next-level cost = 200*L-100."""
    return 0 if level <= 0 else 100 + 100 * (level - 1) ** 2

def lvl(xp):
    return 0 if xp < 100 else 1 + math.isqrt((xp - 100) // 100)

def prog(xp):
    """(level, xp inside this level, xp needed for the next level)"""
    level = lvl(xp)
    base = cum(level)
    return level, xp - base, cum(level + 1) - base

def make_card(name, uname, av_bytes, text_xp, voice_xp, r_total, r_text, r_voice):
    W, H = 934, 282
    im = Image.open(BG).convert("RGBA").resize((W, H))
    d = ImageDraw.Draw(im)
    F = lambda s: ImageFont.load_default(size=s)
    grey = (196, 190, 222)
    av = Image.open(io.BytesIO(av_bytes)).convert("RGBA").resize((144, 144))
    mask = Image.new("L", (144, 144), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 143, 143), fill=255)
    d.ellipse((38, 63, 194, 219), outline=(139, 92, 246), width=5)
    im.paste(av, (44, 69), mask)
    d.text((218, 28), name[:24], font=F(36), fill="white")
    d.text((218, 76), "@" + uname, font=F(20), fill=grey)
    for i, (label, xp, col) in enumerate((("TEXT", text_xp, (59, 130, 246)), ("VOICE", voice_xp, (168, 85, 247)))):
        level, cur, need = prog(xp)
        y = 112 + i * 62
        d.text((218, y + 2), label, font=F(16), fill=col)
        d.text((298, y - 4), f"Level {level}", font=F(26), fill="white")
        d.text((440, y), f"{cur:,} / {need:,} XP", font=F(18), fill=grey)
        d.rounded_rectangle((218, y + 28, 668, y + 44), 8, fill=(45, 30, 80))
        d.rounded_rectangle((218, y + 28, 218 + max(16, int(450 * cur / need)), y + 44), 8, fill=col)
    d.text((218, 252), f"Total XP: {text_xp + voice_xp:,}", font=F(16), fill=grey)
    d.text((750, 70), f"Rank: #{r_total}", font=F(24), fill="white")
    d.text((750, 108), f"Text: #{r_text}", font=F(20), fill=grey)
    d.text((750, 138), f"Voice: #{r_voice}", font=F(20), fill=grey)
    out = io.BytesIO()
    im.convert("RGB").save(out, "PNG")
    out.seek(0)
    return out
