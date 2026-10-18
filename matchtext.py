"""matchtext.py — lugha 4 (Kiswahili, English, Français, العربية): maneno muhimu, ujumbe wa push, na kusoma
alama/dakika/jina kutoka maandishi ya OCR (scoreboard/banner) na maelezo ya mtangazaji (ASR)."""
import re

LANGS = ("sw", "en", "fr", "ar")
GOAL_WORDS = {"sw": ["goli", "gori", "bao", "goal"], "en": ["goal", "scores", "scored", "finishes", "strikes"],
              "fr": ["but", "marque", "marqué", "buteur"], "ar": ["هدف", "جول", "غول", "سجل", "يسجل", "سجّل"]}
FT_WORDS = {"sw": ["mwisho wa mchezo", "mwisho wa mechi", "filimbi ya mwisho", "muda kamili"],
            "en": ["full time", "full-time", "final whistle", "it's all over"],
            "fr": ["fin du match", "coup de sifflet final", "temps réglementaire"],
            "ar": ["نهاية المباراة", "صافرة النهاية", "انتهت المباراة"]}
FT_OCR = re.compile(r"(?<![A-Za-z])(FT|FULL[\s-]?TIME|FINAL|FIN|MALIZIA|MWISHO)(?![A-Za-z])|نهاية|انتهت|إنتهت", re.I)
HT_OCR = re.compile(r"(?<![A-Za-z])(HT|HALF[\s-]?TIME|MI[\s-]?TEMPS|MAPUMZIKO|NUSU)(?![A-Za-z])|استراحة|نهاية الشوط", re.I)
AR_MARK = {"سجل", "يسجل", "سجّل", "بتوقيع", "عبر", "بواسطة"}
AR_STOP = {"هدف", "جول", "في", "من", "الأول", "الثاني", "الثالث", "رائع", "جميل", "المباراة", "للفريق", "هنا", "إنه", "انه"}
STOP = {"goal", "goals", "but", "goli", "gori", "bao", "the", "and", "what", "he", "it", "oh", "yes", "wow", "le", "la", "les", "un", "une",
        "kwa", "na", "ni", "ya", "wa", "pour", "par", "and", "for", "from", "vs", "fc"}
BANNER_STOP = {"goal", "gool", "but", "goli", "gori", "bao", "scored", "penalty", "pen", "own", "og", "assist", "ft", "ht", "vs", "fc", "min", "هدف"}
MIN_FMT = {"sw": "dk {m}'", "en": "{m}'", "fr": "{m}e", "ar": "د{m}"}
HEAD = {"goal": {"sw": "⚽ GOLI!", "en": "⚽ GOAL!", "fr": "⚽ BUT !", "ar": "⚽ هدف!"},
        "ft": {"sw": "🏁 Mwisho wa mechi", "en": "🏁 Full time", "fr": "🏁 Fin du match", "ar": "🏁 نهاية المباراة"},
        "ko": {"sw": "▶️ Mechi imeanza", "en": "▶️ Kick-off", "fr": "▶️ Coup d'envoi", "ar": "▶️ انطلقت المباراة"},
        "ht": {"sw": "⏸️ Mapumziko", "en": "⏸️ Half time", "fr": "⏸️ Mi-temps", "ar": "⏸️ استراحة"},
        "sh": {"sw": "▶️ Kipindi cha pili kimeanza", "en": "▶️ Second half", "fr": "▶️ Début de la 2e mi-temps", "ar": "▶️ بدأ الشوط الثاني"}}
ICON = {"goal": "⚽", "ft": "🏁", "ht": "⏸️", "ko": "▶️", "sh": "▶️"}


def split_teams(title):
    t = re.split(r"\s+(?:vs\.?|v|x|-|–|—|dhidi ya|contre)\s+", str(title or ""), maxsplit=1, flags=re.I)
    return (t[0].strip(), t[1].strip()) if len(t) == 2 else (str(title or "").strip() or "Home", "Away")


def parse_clock(text):
    t = str(text or "")
    m = re.search(r"(\d{1,3})\s*\+\s*(\d{1,2})", t)
    if m:
        return int(m[1]) + int(m[2])
    m = re.search(r"(\d{1,3})\s*[:.]\s*\d{2}(?!\d)", t)
    if m and int(m[1]) <= 130:
        return int(m[1])
    m = re.search(r"(\d{1,3})\s*['’′`]", t)
    return int(m[1]) if m and int(m[1]) <= 130 else None


def parse_scorebug(text):
    """-> {'home','away','clock'} au None. Dash (-) inapendelewa; ':' inakubaliwa tu kama tarakimu moja kila upande (1:0), kuepuka saa kama 7:05."""
    t = re.sub(r"[\r\n|]+", " ", str(text or ""))
    best = None
    for m in re.finditer(r"(?<!\d)(\d{1,2})\s*([-–—:])\s*(\d{1,2})(?!\d)", t):
        if m[2] == ":" and (len(m[1]) > 1 or len(m[3]) > 1):
            continue
        if best is None or (m[2] != ":" and best.group(2) == ":"):
            best = m
    if not best:
        return None
    rest = t[:best.start()] + " " + t[best.end():]
    return {"home": int(best[1]), "away": int(best[3]), "clock": parse_clock(rest)}


def parse_banner(text, exclude=()):
    """Jina la mfungaji kutoka banner ya goli (mf. 'GOAL SAKA 34')."""
    words = re.findall(r"[^\W\d_][\w'’\-\.]*", re.sub(r"[|\n]+", " ", str(text or "")))
    skip = {w.lower() for w in exclude} | BANNER_STOP
    best, seq = "", []
    for w in words + [""]:
        ok = bool(w) and len(w.strip(".")) >= 2 and w.lower().strip(".") not in skip and (w[0].isupper() or re.search(r"[\u0600-\u06FF]", w))
        if ok:
            seq.append(w.strip("."))
        else:
            if seq and len(" ".join(seq)) > len(best):
                best = " ".join(seq)
            seq = []
    if best and best.isupper() and not re.search(r"[\u0600-\u06FF]", best):
        best = best.title()
    return best[:30] or None


def extract_scorer(text, lang="en", exclude=()):
    """Jina la mfungaji kutoka maelezo ya mtangazaji (ASR). Latin: majina yenye herufi kubwa karibu na neno la goli; Kiarabu: neno baada ya 'سجل'."""
    t = str(text or "")
    skip = {w.lower() for w in exclude} | STOP
    if re.search(r"[\u0600-\u06FF]", t):
        toks = re.findall(r"[\u0600-\u06FF]+", t)
        for i, w in enumerate(toks):
            if w in AR_MARK:
                names = [x for x in toks[i + 1:i + 3] if x not in AR_STOP and x not in AR_MARK and x.lower() not in skip]
                if names:
                    return " ".join(names[:2])
        return None
    toks = re.findall(r"[^\W\d_][\w'’\-]*", t)
    kws = {w for l in LANGS for w in GOAL_WORDS[l] if w.isascii()}
    best = (99, None)
    for i, w in enumerate(toks):
        if w.lower() not in kws:
            continue
        for j in range(max(0, i - 6), min(len(toks), i + 8)):
            if j == i or not toks[j][0].isupper() or toks[j].lower() in skip or toks[j].lower() in kws:
                continue
            k = j
            while k + 1 < len(toks) and k - j < 1 and toks[k + 1][0].isupper() and toks[k + 1].lower() not in skip | kws:
                k += 1
            d = abs(j - i) + (0 if j > i else 0.5)
            if d < best[0]:
                best = (d, " ".join(toks[j:k + 1]))
    return best[1]


def _who(lang, scorer, minute, approx):
    parts = ([scorer] if scorer else []) + ([("~" if approx else "") + MIN_FMT[lang].format(m=minute)] if minute is not None else [])
    return " ".join(parts)


def goal_message(lang, team, scorer=None, minute=None, approx=False):
    w = _who(lang, scorer, minute, approx)
    return f"{HEAD['goal'][lang]} {team}" + (f" — {w}" if w else "")


def _summary(head, goals):
    items = []
    for g in goals:
        who = f"{g['scorer']} ({g['team']})" if g.get("scorer") else g["team"]
        mn = ("~" if g.get("approx") else "") + str(g["minute"]) + "'" if g.get("minute") is not None else ""
        items.append(f"{who} {mn}".strip())
    return head + (": " + ", ".join(items) if items else "")


def ft_message(lang, goals):
    return _summary(HEAD["ft"][lang], goals)


def ht_message(lang, goals):
    return _summary(HEAD["ht"][lang], goals)


def ko_message(lang, home, away):
    return f"{HEAD['ko'][lang]}: {home} vs {away}"


def sh_message(lang, home, away):
    return f"{HEAD['sh'][lang]}: {home} vs {away}"


def push_title(kind, home, hs, away, as_):
    return f"{ICON.get(kind, '🏁')} {home} {hs}-{as_} {away}"


def compose(langs, fn, limit=240, **kw):
    """Unganisha lugha zote; zikizidi herufi 240 (kikomo cha push) lugha za mwisho zinaachwa."""
    msgs = [fn(l, **kw) for l in langs if l in LANGS]
    while len(msgs) > 1 and len("\n".join(msgs)) > limit:
        msgs.pop()
    return "\n".join(msgs)[:limit]
