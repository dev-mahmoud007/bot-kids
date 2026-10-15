import os
import re
import time
import asyncio
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from telethon import TelegramClient, events, utils
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from telethon.tl.types import MessageMediaWebPage

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("vera-kids")

# =====================  الإعدادات  =====================
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
STRING_SESSION = os.environ["STRING_SESSION"]          # لازم جلسة جديدة مختلفة عن جلسة البوت الأول
TARGET_CHANNEL = os.getenv("TARGET_CHANNEL", "VeraKidsGaza")
NOTIFY_TO = os.getenv("NOTIFY_TO", "me")               # وين توصلك الإشعارات ("me" = الرسائل المحفوظة، فاضي = تعطيل)
BOT_TAG = os.getenv("BOT_TAG", "KIDS")                 # بيتحط قبل كل إشعار عشان تفرّق بين البوتين
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"             # 1 = يطبع المنشور المنسّق باللوج بدون ما ينشر

CAPTION_WAIT = 3          # ثواني انتظار بعد آخر وسائط فيها كابشن (نص ضمن الألبوم)
MEDIA_WAIT = 120          # كم ثانية نستنى النص بعد وسائط بدون نص؛ إذا ما إجا نتجاهل الوسائط (ما بتنشر أبداً بدون نص)
MERGE_WINDOW = 15         # وسائط جديدة بعد هالمدة من آخر وسائط بدون نص = موديل جديد، والقديم بنتجاهله
REPLACE_WINDOW = 180      # المدة اللي نستنى فيها نص بديل بعد ما المصدر يحذف النص
POST_TEXT_ONLY = True     # نشر الرسائل النصية اللي مالها وسائط
DELETE_TEXT_IF_NOT_REPLACED = False  # لو انحذف النص ولم يأتِ بديل: احذف نصي أنا كمان؟
MAX_HISTORY = 3000
POLL_SECONDS = 20         # كل قديش يفحص القنوات مباشرة (شبكة أمان لو فاتت تحديثات حية)
POLL_MIN_AGE = 8          # ما يلتقط رسالة إلا إذا مر عليها هالعدد من الثواني
ON_REQUEST = "على الخاص"
WA_LINK = os.getenv("WA_LINK", "https://wa.me/970592417956")
BRAND = os.getenv("BRAND", "✨ فيرا كيدز | Vera Kids 🧸")

SOURCES = {
    "nourkidsclothes": dict(markup=2),
    "salykidsstyle":   dict(markup=2),
}

# =====================  التحليل والترجمة  =====================
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
# توحيد الحروف التركية/العربية للمطابقة فقط (بنفس الطول 1:1 فالمواقع تبقى صحيحة)
_TR = str.maketrans({"ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u",
                     "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
                     "أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"})


def norm(t):
    return t.translate(_DIGITS)


def fold(t):
    return norm(t).translate(_TR).lower()


def fmt_num(v):
    return f"{v:g}"


# ---- قواميس الترجمة (المفاتيح بعد fold: أحرف لاتينية صغيرة بدون تشكيل تركي) ----
COLORS = {
    "red": "أحمر", "kirmizi": "أحمر", "green": "أخضر", "yesil": "أخضر",
    "fuchsia": "فوشي", "fuschia": "فوشي", "fusya": "فوشي", "beige": "بيج", "bej": "بيج",
    "black": "أسود", "siyah": "أسود", "white": "أبيض", "beyaz": "أبيض",
    "blue": "أزرق", "mavi": "أزرق", "navy": "كحلي", "lacivert": "كحلي",
    "pink": "وردي", "pembe": "وردي", "yellow": "أصفر", "sari": "أصفر",
    "orange": "برتقالي", "turuncu": "برتقالي", "purple": "بنفسجي", "mor": "بنفسجي",
    "brown": "بني", "kahve": "بني", "kahverengi": "بني", "gray": "رمادي", "grey": "رمادي", "gri": "رمادي",
    "cream": "كريمي", "krem": "كريمي", "mustard": "خردلي", "hardal": "خردلي",
    "khaki": "زيتي", "haki": "زيتي", "burgundy": "عنابي", "bordo": "عنابي",
    "lilac": "ليلكي", "lila": "ليلكي", "turquoise": "تركوازي", "turkuaz": "تركوازي",
    "gold": "ذهبي", "altin": "ذهبي", "silver": "فضي", "gumus": "فضي", "mint": "مينت",
    "salmon": "سلموني", "somon": "سلموني", "coral": "مرجاني", "mercan": "مرجاني",
}

ITEMS = {
    "strappy blouse": "بلوزة بحمالات", "triko tisort": "تيشيرت تريكو", "kot pantolon": "بنطلون جينز",
    "kot ceket": "جاكيت جينز", "kot etek": "تنورة جينز",
    "jacket": "جاكيت", "ceket": "جاكيت", "skirt": "تنورة", "etek": "تنورة",
    "blouse": "بلوزة", "bluz": "بلوزة", "bag": "شنطة", "canta": "شنطة", "handbag": "شنطة",
    "pants": "بنطلون", "pant": "بنطلون", "trousers": "بنطلون", "pantolon": "بنطلون",
    "jeans": "جينز", "kot": "جينز", "dress": "فستان", "elbise": "فستان",
    "shirt": "قميص", "gomlek": "قميص", "tshirt": "تيشيرت", "tisort": "تيشيرت", "tee": "تيشيرت",
    "triko": "تريكو", "belt": "حزام", "kemer": "حزام", "vest": "جيليه", "gilet": "جيليه", "yelek": "جيليه",
    "coat": "بالطو", "kaban": "بالطو", "mont": "جاكيت شتوي", "cardigan": "كارديغان", "hirka": "كارديغان",
    "sweater": "كنزة", "kazak": "كنزة", "hoodie": "هودي", "sweatshirt": "سويتشيرت",
    "shorts": "شورت", "short": "شورت", "sort": "شورت", "leggings": "ليجنج", "tayt": "ليجنج",
    "jumpsuit": "سالوبيت", "tulum": "سالوبيت", "tunic": "تونيك", "tunik": "تونيك",
    "top": "توب", "crop": "كروب", "hat": "قبعة", "cap": "كاب", "sapka": "قبعة", "beret": "بيريه",
    "scarf": "وشاح", "atki": "وشاح", "shawl": "شال", "sal": "شال", "shoes": "حذاء", "ayakkabi": "حذاء",
    "boots": "بوت", "bot": "بوت", "suit": "بدلة", "esofman": "ترنج", "pajama": "بيجامة", "pijama": "بيجامة",
    "socks": "جوارب", "corap": "جوارب", "bow": "فيونكة", "tie": "ربطة عنق", "kravat": "ربطة عنق",
    "hairband": "طوق شعر", "takim": "طقم", "set": "طقم",
    # عربي (بعد fold: ة→ه)
    "جاكيت": "جاكيت", "تنوره": "تنورة", "بلوزه": "بلوزة", "شنطه": "شنطة", "بنطلون": "بنطلون",
    "فستان": "فستان", "قميص": "قميص", "تيشيرت": "تيشيرت", "حزام": "حزام", "شورت": "شورت",
    "جيليه": "جيليه", "كارديغان": "كارديغان", "بالطو": "بالطو", "طقم": "طقم",
}

FABRICS = {
    "cotton": "قطن", "pamuk": "قطن", "linen": "كتان", "keten": "كتان", "polyester": "بوليستر",
    "viscose": "فيسكوز", "viskon": "فيسكوز", "lycra": "ليكرا", "likra": "ليكرا", "denim": "جينز",
    "jeans": "جينز", "kot": "جينز", "knit": "تريكو", "triko": "تريكو", "velvet": "قطيفة", "kadife": "قطيفة",
    "wool": "صوف", "yun": "صوف", "fleece": "فليس", "polar": "بولار", "muslin": "موسلين", "krep": "كريب",
    "crepe": "كريب", "satin": "ستان", "saten": "ستان", "chiffon": "شيفون", "sifon": "شيفون",
    "silk": "حرير", "ipek": "حرير", "interlock": "إنترلوك", "ribana": "ريبانا", "astarli": "مبطن",
    "lined": "مبطن", "dantel": "دانتيل", "lace": "دانتيل", "kasmir": "كشمير", "kase": "كاشيه",
}
QUALITY = {1: "درجة أولى", 2: "درجة ثانية", 3: "درجة ثالثة"}

# ---- السعر ----
NUM = r"\d+(?:[.,]\d+)?"
P_PIECE = re.compile(rf"(?:سعر[ \t]*القطع[هة]|price[ \t]*per[ \t]*piece|piece[ \t]*price|adet[ \t]*fiyat\w*)[ \t]*[:\-–=]*[ \t]*\$?[ \t]*({NUM})")
P_KEY = re.compile(rf"(?:wholesale(?:\s*price)?|toptan(?:\s*fiyat\w*)?|fiyat\w*|price|السعر|سعر)\s*[:\-–=]*\s*\$?\s*({NUM})")
P_CUR = re.compile(rf"({NUM})\s*(?:\$|دولار|دولارات|usd|dollars?)")
P_DOL = re.compile(rf"\$\s*({NUM})")


def find_price(f):
    """f = نص بعد fold. بيرجع (القيمة، موقع الرقم، هل هو سعر القطعة)"""
    for pat, per in ((P_PIECE, True), (P_KEY, False), (P_CUR, False), (P_DOL, False)):
        m = pat.search(f)
        if m:
            return float(m.group(1).replace(",", ".")), m.span(1), per
    return None, None, False


# ---- باقي الحقول ----
SERIES_RE = re.compile(r"(?:^|[^\w])(?:(?:ال)?سيري|series|seri|siri|pk|pack|paket)\s*[:\-–.]*\s*(\d+)\s*(?:قطع[هة]?|pcs?|pieces?|adet|parca)?")
PIECES_RE = re.compile(r"(\d+)\s*(?:pieces?|pcs|pc|parca|قطع[هة]?)\b")
CODE_RE = re.compile(r"\b(?:cod|kod|code|كود|الكود)\b\s*[:\-–.]*\s*([a-z]*\d[\w\-]*)")
AGE_KEY = re.compile(r"\b(?:sizes?|age|ages|yas|beden|olcu)\b|مقاس|عمر|اعمار|سنوات|سنه")
PAIR_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[.\-–_]\s*(\d{1,2})(?!\d)")
LETTER_SIZES = re.compile(r"\b(?:xxs|xs|s|m|l|xl|xxl|xxxl|2xl|3xl|4xl)\b")
COLOR_KEY = re.compile(r"colou?rs?\b|renkler\b|renk\b|الوان|لون")


def parse_ages(flines):
    for i, f in enumerate(flines):
        if not AGE_KEY.search(f):
            continue
        seg = f
        if not re.search(r"\d", seg) and i + 1 < len(flines):
            seg = flines[i + 1]                       # الأعمار بالسطر اللي بعد الكلمة
        pairs = PAIR_RE.findall(seg)
        if pairs:
            return "الأعمار", " | ".join(f"{a}-{b}" for a, b in pairs) + " سنوات"
        nums = re.findall(r"\d+", seg)
        if len(nums) == 2 and "/" in seg:
            return "الأعمار", f"{nums[0]}-{nums[1]} سنوات"
        if nums:
            return "الأعمار", " | ".join(nums) + " سنوات"
        letters = LETTER_SIZES.findall(seg)
        if letters:
            return "المقاسات", " - ".join(x.upper() for x in letters)
    return None, ""


def item_tokens(f):
    f = re.sub(r"\bt[\s\-]?shirt\b", "tshirt", f)
    return [t.strip(" ._") for t in re.split(r"[^\w\s]+", f) if t.strip(" ._")]


def item_name(tok):
    tok = re.sub(r"\s+", " ", tok)
    if tok in ITEMS:
        return ITEMS[tok]
    if tok.endswith("s") and tok[:-1] in ITEMS:
        return ITEMS[tok[:-1]]
    return None


def parse_items(flines):
    for f in flines:
        toks = item_tokens(f)
        got = [n for n in (item_name(t) for t in toks) if n and n != "طقم"]
        if len(got) >= 2 and len(got) >= len(toks) / 2:
            return got
    return []


def parse_colors(flines):
    count, names = None, []
    for i, f in enumerate(flines):
        if not COLOR_KEY.search(f):
            continue
        m = re.search(r"(\d+)\s*(?:colou?rs?|renk\w*|الوان|لون)", f)
        if m and count is None:
            count = int(m.group(1))
        m2 = re.search(r"\b([a-z]+)\s+colou?r\b", f)
        if m2 and m2.group(1) in COLORS:
            names.append(COLORS[m2.group(1)])
        rest = re.split(r"colou?rs?|renkler|renk|الوان|لون", f, maxsplit=1)[-1]
        cand = [w for w in re.findall(r"[a-z]+|[\u0600-\u06FF]{2,}", rest)
                if w in COLORS or re.match(r"[\u0600-\u06FF]", w)]
        cand = [COLORS.get(w, w) for w in cand if w not in ("من", "في", "على", "جاهز", "و")]
        if not cand and i + 1 < len(flines):
            words = re.findall(r"[a-z]+|[\u0600-\u06FF]{2,}", flines[i + 1])
            if any(w in COLORS for w in words):       # السطر التالي قائمة ألوان فعلاً
                cand = [COLORS.get(w, w) for w in words if w in COLORS or re.match(r"[\u0600-\u06FF]", w)]
        names += cand
    seen, uniq = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    if not uniq and not count:
        return ""
    if uniq:
        return (f"{count} ألوان: " if count else "") + " - ".join(uniq)
    return f"{count} ألوان"


def translate_words(s):
    out = []
    for w in re.split(r"([+/,\-\s]+)", s):
        out.append(FABRICS.get(w, w) if re.fullmatch(r"[a-z]+", w) else w)
    return "".join(out).strip()


def parse_fabric(lines, flines):
    for f in flines:
        m = re.search(r"(\d+)\s*خيط", f)
        if m:
            return f"{m.group(1)} خيط"
    for f in flines:
        m = re.search(r"(\d)\s*\.?\s*kalite\s*kumas", f)
        if m:
            return "قماش " + QUALITY.get(int(m.group(1)), f"درجة {m.group(1)}")
    for line, f in zip(lines, flines):
        m = re.search(r"(?:kumas|fabric|قماش|خامه)\w*\s*[:\-–=]+\s*(.+)", f)
        if m:
            return translate_words(m.group(1).strip())
    return ""


def parse_tags(ft):
    tags = []
    if re.search(r"new\s*model|yeni\s*model|موديل\s*جديد", ft):
        tags.append("موديل جديد")
    mc = re.search(r"(?:(20\d\d)\s*)?(?:new\s*-?\s*collection|yeni\s*koleksiyon|yeni\s*sezon|new\s*season|كوليكشن\s*جديد|كولكشن\s*جديد)", ft)
    if mc:
        tags.append(f"كوليكشن {mc.group(1)} الجديد" if mc.group(1) else "كوليكشن جديد")
    if re.search(r"\beid\b|\bbayram\b|عيد", ft):
        tags.append("كوليكشن العيد")
    if re.search(r"warehouse\s*clearance|zararina\s*satis|selling\s*at\s*(?:a\s*)?loss|tasfiye|تصفيه|بخساره", ft):
        tags.append("تصفية مستودع" if "warehouse" in ft else "تصفية")
        if re.search(r"loss|zarar|بخساره", ft):
            tags.append("بيع بخسارة")
    if re.search(r"limited\s*stock|stok\s*sinirli|كميه\s*محدوده", ft):
        tags.append("كمية محدودة")
    return tags


def parse_status(ft):
    parts = []
    if re.search(r"\bready\b|hazir|جاهز|hemen\s*teslim", ft):
        parts.append("جاهز")
    if re.search(r"hemen\s*teslim|immediate\s*delivery|تسليم\s*فوري", ft):
        parts.append("تسليم فوري من المحل")
    return " – ".join(parts)


def pieces_text(n):
    return {1: "قطعة واحدة", 2: "قطعتين"}.get(n, f"{n} قطع")


def parse_kids(text, markup):
    lines = [l.replace("*", " ").strip() for l in text.split("\n")]
    lines = [l for l in lines if re.search(r"\w", l)]
    flines = [fold(l) for l in lines]
    ft = "\n".join(flines)
    f = {}

    # السعر (+ الزيادة)
    v, _, per = find_price(ft)
    f["price"] = f"{fmt_num(v + markup)}$" if v is not None else ON_REQUEST
    f["price_label"] = "سعر القطعة" if per else "السعر"

    # السيري (عدد القطع بالسيري) وعدد قطع الطقم
    ms = SERIES_RE.search(ft)
    f["series"] = pieces_text(int(ms.group(1))) if ms else ""
    masked = ft if not ms else ft[:ms.start()] + " " * (ms.end() - ms.start()) + ft[ms.end():]
    mp = PIECES_RE.search(masked)
    pieces = int(mp.group(1)) if mp else None
    items = parse_items(flines)
    n = pieces or (len(items) if items else None)
    if items and n:
        f["set"] = f"{pieces_text(n)} — {' - '.join(items)}"
    elif n:
        f["set"] = pieces_text(n)
    else:
        f["set"] = ""

    mc = CODE_RE.search(ft)
    f["code"] = mc.group(1).upper() if mc else ""
    f["age_label"], f["ages"] = parse_ages(flines)
    f["colors"] = parse_colors(flines)
    f["fabric"] = parse_fabric(lines, flines)
    f["status"] = parse_status(ft)

    # المتبقي (مثال: Beige color last 10 series)
    f["left"] = ""
    for fl in flines:
        m = re.search(r"(?:last|remaining|kalan|son|باقي|اخر)\s*(\d+)\s*(?:series|seri|siri|سيري|set|takim)", fl)
        if m:
            mcol = re.search(r"\b([a-z]+)\s+colou?r\b", fl)
            color = COLORS.get(mcol.group(1)) if mcol else None
            f["left"] = f"{m.group(1)} سيري" + (f" من اللون {color}" if color else "")
            break

    # الفئة (ولادي/بناتي)
    gender = ""
    if re.search(r"\berkek\b|\bboys?\b|ولادي|اولاد", ft):
        gender = "ولادي"
    elif re.search(r"\bkiz\b|\bgirls?\b|بناتي|بنات", ft):
        gender = "بناتي"

    # العنوان
    title = " – ".join(parse_tags(ft))
    gender_used = False
    if not title:
        if re.search(r"\btakim\b|\bset\b|طقم", ft):
            title = f"طقم {gender}".strip()
            gender_used = bool(gender)
        else:
            for l in lines:
                if re.search(r"[\u0600-\u06FF]", l) and not re.search(r"\d", norm(l)):
                    title = l
                    break
    f["gender"] = "" if gender_used else gender

    shown = ("set", "fabric", "colors", "ages", "series", "left", "code", "status", "gender")
    if not title and not any(f.get(k) for k in shown) and v is None:
        title = "\n".join(lines[:6])                  # ما فهمنا شي: ما نضيّع النص الأصلي
    return title, f


def build(title, f):
    out = f"{BRAND}\n\n"
    if title:
        out += f"{title}\n\n"
    out += "ـــــــــــــــــــــــــــــ\n"
    if f.get("set"):      out += f"🧩 الطقم: {f['set']}\n"
    if f.get("gender"):   out += f"🧒 الفئة: {f['gender']}\n"
    if f.get("fabric"):   out += f"🧵 الخامة: {f['fabric']}\n"
    if f.get("colors"):   out += f"🎨 الألوان: {f['colors']}\n"
    if f.get("ages"):     out += f"📏 {f.get('age_label') or 'الأعمار'}: {f['ages']}\n"
    if f.get("series"):   out += f"📦 السيري: {f['series']}\n"
    if f.get("left"):     out += f"⚠️ المتبقي: {f['left']}\n"
    if f.get("code"):     out += f"🏷 الكود: {f['code']}\n"
    if f.get("price"):    out += f"💲 {f.get('price_label', 'السعر')}: {f['price']}\n"
    if f.get("status"):   out += f"🚚 الحالة: {f['status']}\n"
    out += f"\n🛍 بيع جملة فقط\n📲 للتواصل والطلب:\n{WA_LINK}"
    return rtl(out)


RLM = "\u200f"  # علامة اتجاه يمين-ليسار: بتخلي السطر RTL بدون ما تقلب ترتيب الإنجليزي والأرقام


def rtl(text):
    lines = []
    for l in text.split("\n"):
        if l.strip() and not l.lstrip().startswith("http"):
            l = RLM + l
        lines.append(l)
    return "\n".join(lines)


def format_post(text, source):
    if not text or not text.strip():
        return None
    cfg = SOURCES.get(source.lower())
    if not cfg:
        return None
    title, f = parse_kids(text, cfg["markup"])
    log.info("PARSED [%s] %s | title=%s", source, {k: v for k, v in f.items() if v}, short(title, 60))
    return build(title, f)


def minimal_fallback(text, source):
    """لو التحليل فشل: النص الأصلي مع زيادة السعر، بنفس الإطار."""
    cfg = SOURCES.get(source.lower(), {})
    body = text.strip()
    ft = fold(body)
    v, span, _ = find_price(ft)
    if v is not None:
        body = body[:span[0]] + fmt_num(v + cfg.get("markup", 0)) + body[span[1]:]
    return build(body, {})


def safe_format(text, source):
    try:
        return format_post(text, source)
    except Exception:
        log.exception("format_post failed for %s, using minimal fallback", source)
        try:
            return minimal_fallback(text, source)
        except Exception:
            log.exception("minimal_fallback also failed")
            return None


# =====================  الحالة  =====================
@dataclass(eq=False)
class Post:
    source: str
    media: list = field(default_factory=list)       # رسائل المصدر (قبل الإرسال)
    text: Optional[str] = None
    text_id: Optional[int] = None
    last_group: Optional[int] = None
    version: int = 0
    last_ts: float = 0.0
    lock: Any = field(default_factory=asyncio.Lock)
    sent: bool = False
    cancelled: bool = False
    media_ids: set = field(default_factory=set)
    sent_media: dict = field(default_factory=dict)  # id في المصدر -> رسالتي المرسلة
    sent_text: Any = None
    shown: Optional[str] = None
    text_deleted: bool = False
    awaiting_until: float = 0.0
    keys: set = field(default_factory=set)


class Chan:
    def __init__(self, name):
        self.name = name
        self.lock = asyncio.Lock()
        self.pending: Optional[Post] = None
        self.recent = deque(maxlen=8)
        self.seen = set()      # ids الرسائل اللي عالجناها (حية أو من الفحص)
        self.baseline = 0      # آخر id وقت التشغيل: ما نعالج شي أقدم منه


client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)
target = None
id_to_name = {}
entities = {}
channels = {}
queue = None
index = {}          # (source, msg_id) -> Post
history = deque()
bg_tasks = set()


def spawn(coro):
    t = asyncio.create_task(coro)
    bg_tasks.add(t)
    t.add_done_callback(bg_tasks.discard)


def is_media(msg):
    return bool(msg.media) and not isinstance(msg.media, MessageMediaWebPage)


async def retry(fn, *a, **k):
    for _ in range(6):
        try:
            return await fn(*a, **k)
        except FloodWaitError as e:
            log.warning("FloodWait %ss", e.seconds)
            await asyncio.sleep(e.seconds + 1)
    raise RuntimeError("too many flood waits")


async def notify(text):
    text = f"[{BOT_TAG}] {text}"
    log.info(text.replace("\n", " | "))
    if NOTIFY_TO and not DRY_RUN:
        try:
            await retry(client.send_message, NOTIFY_TO, text)
        except Exception:
            log.exception("notify failed")


def add_key(post, mid):
    k = (post.source, mid)
    index[k] = post
    post.keys.add(k)


def register(post):
    for mid in post.media_ids:
        add_key(post, mid)
    if post.text_id:
        add_key(post, post.text_id)
    history.append(post)
    while len(history) > MAX_HISTORY:
        old = history.popleft()
        for k in old.keys:
            index.pop(k, None)


def short(t, n=150):
    t = (t or "").replace("\n", " ")
    return t if len(t) <= n else t[:n] + "…"


# =====================  الإرسال (طابور واحد، كل منشور وحدة كاملة)  =====================
async def send_one(m):
    """يرسل وسائط وحدة؛ إذا فشل بالمرجع ينزّلها ويرفعها من جديد."""
    try:
        return await retry(client.send_file, target, m.media)
    except Exception as e:
        log.exception("send by reference failed (%s), trying download/upload", type(e).__name__)
        tmp = tempfile.mkdtemp()
        try:
            path = await client.download_media(m, file=tmp + os.sep)
            return await retry(client.send_file, target, path, supports_streaming=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


async def send_post(post):
    media = sorted(post.media, key=lambda m: m.id)
    if DRY_RUN:
        formatted = safe_format(post.text, post.source) if post.text else None
        log.info("DRY_RUN [%s] كان رح ينشر %d وسائط + النص:\n%s", post.source, len(media), formatted or "(بدون نص)")
        post.shown = formatted
        post.sent = True
        return
    try:
        for i in range(0, len(media), 10):
            chunk = media[i:i + 10]
            try:
                res = await retry(client.send_file, target, [m.media for m in chunk])
                res = res if isinstance(res, list) else [res]
                pairs = list(zip(chunk, res))
            except Exception:
                log.exception("album failed, sending one by one")
                pairs = []
                for m in chunk:
                    pairs.append((m, await send_one(m)))
            for m, s in pairs:
                post.sent_media[m.id] = s
            if i + 10 < len(media):
                await asyncio.sleep(1.5)
        if post.text:
            formatted = safe_format(post.text, post.source)
            if formatted:
                post.sent_text = await retry(client.send_message, target, formatted)
                post.shown = formatted
    finally:
        post.sent = True


async def sender():
    while True:
        post = await queue.get()
        try:
            async with post.lock:
                if not post.cancelled:
                    await send_post(post)
        except Exception as e:
            log.exception("send failed")
            await notify(f"⚠️ [{post.source}] فشل إرسال منشور: {type(e).__name__}: {short(str(e), 200)}")
        finally:
            queue.task_done()
        await asyncio.sleep(0.5)


# =====================  تجميع المنشورات لكل قناة  =====================
async def reconcile(p):
    """يجيب من سيرفر تليجرام الوسائط الناقصة (اللي ما وصلت بالتحديثات الحية)."""
    ent = entities.get(p.source)
    if not ent:
        return
    try:
        have = {m.id for m in p.media}
        found = []
        if p.text_id and p.text_id not in have:
            # نص منفصل: كل الوسائط قبله مباشرة اللي لسا ما اننشرت
            msgs = await retry(client.get_messages, ent, limit=30, max_id=p.text_id)
            prev = min((m.date for m in p.media), default=None)
            for m in msgs:                      # من الأحدث للأقدم
                if (p.source, m.id) in index or not is_media(m) or (m.raw_text or "").strip():
                    break
                if prev is not None and (prev - m.date).total_seconds() > MERGE_WINDOW:
                    break                       # فجوة زمنية = موديل ثاني، لا تخلطه
                prev = m.date if prev is None else min(prev, m.date)
                if m.id not in have:
                    found.append(m)
        elif p.media:
            # نص كابشن داخل الألبوم: كمّل باقي صور نفس الألبوم
            groups = {m.grouped_id for m in p.media if m.grouped_id}
            if groups:
                lo, hi = min(have), max(have)
                msgs = await retry(client.get_messages, ent, limit=40, min_id=lo - 11, max_id=hi + 11)
                found = [m for m in msgs if m.grouped_id in groups and m.id not in have
                         and (p.source, m.id) not in index]
        if found:
            p.media.extend(found)
            await notify(f"🧩 [{p.source}] لقيت {len(found)} وسائط ناقصة ما وصلت بالتحديثات وضفتها للموديل")
    except Exception:
        log.exception("reconcile failed for %s", p.source)


async def drop_pending(ch, why):
    """يتجاهل وسائط بدون نص (ما بتنشر، فما في شي يتحذف من قناتك)."""
    p = ch.pending
    ch.pending = None
    if p and p.media and not p.text:
        await notify(f"🧹 [{ch.name}] تجاهلت {len(p.media)} وسائط بدون نص ({why}) — ما نشرتها")


async def flush(ch):
    """يُستدعى والقفل ممسوك."""
    p = ch.pending
    ch.pending = None
    if not p or (not p.media and not p.text):
        return
    if p.media and not p.text:      # وسائط بدون نص: ممنوع تنشر
        log.info("flush skipped: media without text [%s]", p.source)
        return
    await reconcile(p)
    if not p.media and not POST_TEXT_ONLY:
        return
    p.media.sort(key=lambda m: m.id)
    p.media_ids = {m.id for m in p.media}
    register(p)
    ch.recent.append(p)
    await queue.put(p)


def arm_timer(ch, p, delay):
    v = p.version

    async def run():
        await asyncio.sleep(delay)
        async with ch.lock:
            if ch.pending is p and p.version == v:
                if p.text:
                    await flush(ch)
                else:
                    await drop_pending(ch, f"ما إجا نص خلال {MEDIA_WAIT} ثانية")

    spawn(run())


def find_replaceable(ch):
    now = time.time()
    for post in reversed(ch.recent):
        if post.text_deleted and now < post.awaiting_until:
            return post
    return None


async def apply_text(post, mid, text):
    """القفل الخاص بالمنشور ممسوك. يطبّق نصاً جديداً/معدلاً."""
    post.text_id, post.text, post.text_deleted = mid, text, False
    add_key(post, mid)
    if not post.sent:
        return
    formatted = safe_format(text, post.source)
    if not formatted:
        return
    if DRY_RUN:
        log.info("DRY_RUN [%s] كان رح يعدّل النص إلى:\n%s", post.source, formatted)
        post.shown = formatted
        return
    if post.sent_text:
        if formatted != post.shown:
            await retry(client.edit_message, target, post.sent_text.id, formatted)
            post.shown = formatted
    else:
        post.sent_text = await retry(client.send_message, target, formatted)
        post.shown = formatted


async def on_new(event):
    src = id_to_name.get(event.chat_id)
    if src:
        await process_new(src, event.message)


async def process_new(src, msg):
    ch = channels[src]
    if msg.id in ch.seen:
        return
    ch.seen.add(msg.id)
    if len(ch.seen) > 1000:
        ch.seen = set(sorted(ch.seen)[-500:])
    text = msg.raw_text or ""
    log.info("IN  [%s] id=%s group=%s media=%s text=%s | pending=%s | %s",
             src, msg.id, msg.grouped_id, type(msg.media).__name__ if msg.media else "-",
             bool(text.strip()),
             (f"{len(ch.pending.media)}m/{'T' if ch.pending.text else '-'}" if ch.pending else "none"),
             short(text, 40))
    if (src, msg.id) in index:   # وصلت متأخرة وانضافت للموديل أصلاً
        log.info("SKIP [%s] id=%s already posted", src, msg.id)
        return
    async with ch.lock:
        p = ch.pending
        if is_media(msg):
            same_album = bool(msg.grouped_id) and msg.grouped_id == p.last_group if p else False
            if p and p.text and not same_album:
                await flush(ch)
                p = None
            if p and not p.text and not same_album and time.time() - p.last_ts > MERGE_WINDOW:
                await drop_pending(ch, "وصلت وسائط موديل جديد وما إجا نص للقديم")
                p = None
            if p is None:
                p = ch.pending = Post(src)
            p.media.append(msg)
            p.last_ts = time.time()
            p.last_group = msg.grouped_id
            if text.strip():
                p.text, p.text_id = text, msg.id
            p.version += 1
            arm_timer(ch, p, CAPTION_WAIT if p.text else MEDIA_WAIT)
            return

        if not text.strip():
            return

        # رسالة نصية بدون وسائط
        if p and p.media and not p.text:
            p.text, p.text_id = text, msg.id
            p.version += 1
            await flush(ch)                      # النص وصل: انشر الموديل فوراً
            return
        if p:
            await flush(ch)

        post = find_replaceable(ch)              # المصدر حذف النص وبعث نص جديد؟
        if post:
            async with post.lock:
                await apply_text(post, msg.id, text)
            await notify(f"🔁 [{src}] وصل نص بديل (msg {msg.id}) → عدّلت نص منشورك\n{short(text)}")
            return

        np = Post(src)
        np.text, np.text_id = text, msg.id
        ch.pending = np
        await flush(ch)


async def on_edit(event):
    msg = event.message
    src = id_to_name.get(event.chat_id)
    if not src:
        return
    ch = channels[src]
    new_text = msg.raw_text or ""

    async with ch.lock:
        p = ch.pending
        if p and (msg.id == p.text_id or any(m.id == msg.id for m in p.media)):
            if msg.id == p.text_id and new_text != p.text:
                old = p.text
                p.text, p.text_id = (new_text, msg.id) if new_text.strip() else (None, None)
                p.version += 1
                await notify(f"✏️ [{src}] تعديل نص (msg {msg.id}) قبل النشر\nقبل: {short(old)}\nبعد: {short(new_text)}")
            elif new_text.strip() and not p.text:
                p.text, p.text_id = new_text, msg.id
                p.version += 1
            for i, m in enumerate(p.media):
                if m.id == msg.id:
                    p.media[i] = msg
            return

    post = index.get((src, msg.id))
    if not post:
        return
    async with post.lock:
        if msg.id == post.text_id:
            if new_text == post.text:
                return
            old = post.text
            if not new_text.strip():
                await mark_text_removed(post, msg.id)
                return
            await apply_text(post, msg.id, new_text)
            await notify(f"✏️ [{src}] تعديل نص (msg {msg.id}) → عدّلت منشورك\nقبل: {short(old)}\nبعد: {short(new_text)}")
        elif post.text_id is None and new_text.strip() and msg.id in post.media_ids:
            await apply_text(post, msg.id, new_text)
            await notify(f"✏️ [{src}] انضاف نص لموديل منشور (msg {msg.id}) → نشرت النص\n{short(new_text)}")


async def mark_text_removed(post, mid):
    post.text_deleted = True
    post.awaiting_until = time.time() + REPLACE_WINDOW
    if not post.sent:
        post.text, post.text_id = None, None
    spawn(expiry_notice(post, mid))
    await notify(f"🗑 [{post.source}] انحذف النص (msg {mid}) → بستنى نص بديل {REPLACE_WINDOW} ثانية")


async def expiry_notice(post, mid):
    await asyncio.sleep(REPLACE_WINDOW + 1)
    if post.text_deleted and not post.cancelled:
        async with post.lock:
            if not post.text_deleted:
                return
            if DELETE_TEXT_IF_NOT_REPLACED and post.sent_text:
                await retry(client.delete_messages, target, [post.sent_text.id])
                post.sent_text = None
                await notify(f"🗑 [{post.source}] ما إجا نص بديل، حذفت النص من قناتك")
            else:
                await notify(f"ℹ️ [{post.source}] ما إجا نص بديل لـ msg {mid}، تركت النص الحالي")


async def on_delete(event):
    src = id_to_name.get(event.chat_id)
    if not src:
        return
    ch = channels[src]
    for mid in event.deleted_ids:
        touched = False
        async with ch.lock:
            p = ch.pending
            if p:
                if mid == p.text_id:
                    p.text, p.text_id = None, None
                    p.version += 1
                    touched = True
                    await notify(f"🗑 [{src}] انحذف النص (msg {mid}) قبل النشر")
                if any(m.id == mid for m in p.media):
                    p.media = [m for m in p.media if m.id != mid]
                    p.version += 1
                    touched = True
                    await notify(f"🗑 [{src}] انحذفت وسائط (msg {mid}) قبل النشر")
                    if not p.media and not p.text:
                        ch.pending = None
        if touched:
            continue
        post = index.get((src, mid))
        if post:
            async with post.lock:
                await handle_deleted(post, mid)


async def handle_deleted(post, mid):
    is_text = mid == post.text_id
    if mid in post.media_ids:
        post.media_ids.discard(mid)
        if post.sent:
            sent = post.sent_media.pop(mid, None)
            if sent:
                await retry(client.delete_messages, target, [sent.id])
        else:
            post.media = [m for m in post.media if m.id != mid]
        await notify(f"🗑 [{post.source}] انحذفت وسائط (msg {mid}) → حذفتها من قناتك")
    if is_text:
        post.text_deleted = True
        post.awaiting_until = time.time() + REPLACE_WINDOW
        if not post.sent:
            post.text, post.text_id = None, None

    gone = not post.media_ids and (post.text_deleted or post.text_id is None)
    if gone:
        post.cancelled = True
        if post.sent:
            ids = [s.id for s in post.sent_media.values()]
            if post.sent_text:
                ids.append(post.sent_text.id)
            if ids:
                await retry(client.delete_messages, target, ids)
        for k in post.keys:
            index.pop(k, None)
        await notify(f"🗑 [{post.source}] انحذف الموديل كاملاً من المصدر → حذفته من قناتك")
    elif is_text:
        spawn(expiry_notice(post, mid))
        await notify(f"🗑 [{post.source}] انحذف النص (msg {mid}) → بستنى نص بديل {REPLACE_WINDOW} ثانية")


async def poller():
    """شبكة أمان: كل فترة بيسأل تليجرام مباشرة عن آخر رسائل كل قناة، وبيعالج أي رسالة ما وصلت بالتحديثات الحية."""
    await asyncio.sleep(POLL_SECONDS)
    while True:
        for src, ch in channels.items():
            try:
                msgs = await retry(client.get_messages, entities[src], limit=30)
                now = datetime.now(timezone.utc)
                fresh = sorted(
                    (m for m in msgs if m.id > ch.baseline and m.id not in ch.seen
                     and (now - m.date).total_seconds() > POLL_MIN_AGE),
                    key=lambda m: m.id)
                for m in fresh:
                    await notify(f"🛟 [{src}] التقطت رسالة (id {m.id}) ما وصلت بالتحديثات الحية")
                    await process_new(src, m)
            except Exception:
                log.exception("poll failed for %s", src)
        await asyncio.sleep(POLL_SECONDS)


# =====================  التشغيل  =====================
async def main():
    global target, queue
    queue = asyncio.Queue()
    await client.start()
    target = await client.get_entity(TARGET_CHANNEL)

    for name in SOURCES:
        try:
            ent = await client.get_entity(name)
            id_to_name[utils.get_peer_id(ent)] = name
            entities[name] = ent
            channels[name] = Chan(name)
            last = await client.get_messages(ent, limit=1)
            channels[name].baseline = last[0].id if last else 0
        except Exception:
            log.exception("cannot resolve source %s", name)

    chats = list(id_to_name)
    client.add_event_handler(on_new, events.NewMessage(chats=chats))
    client.add_event_handler(on_edit, events.MessageEdited(chats=chats))
    client.add_event_handler(on_delete, events.MessageDeleted())

    spawn(sender())
    spawn(poller())
    log.info("🔥 KIDS BOT STARTED [%s] — target=%s — watching: %s", "DRY_RUN" if DRY_RUN else "LIVE", TARGET_CHANNEL, ", ".join(id_to_name.values()))
    await client.run_until_disconnected()


if __name__ == "__main__":
    client.loop.run_until_complete(main())
