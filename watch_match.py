#!/usr/bin/env python3
"""watch_match.py — hufuatilia mechi live: goal, aliyefunga, timu, dakika, na full time; hutuma push kwa lugha 4 (sw, en, fr, ar).

Vyanzo (vyote vya wazi, vinaendeshwa ndani ya GitHub Actions, hakuna API ya nje ya matokeo):
  • Picha: scoreboard ya stream inasomwa kwa OCR (Tesseract) -> alama na dakika. Alama ikibadilika mara 2 mfululizo = goli la KWELI.
  • Sauti: model yako (goal_model.json) hugundua mngurumo wa goal; hutumika kuharakisha usomaji na kuita ASR.
  • Maelezo ya mtangazaji: faster-whisper (sw/en/fr/ar) -> jina la mfungaji; pia banner ya goli (OCR) ikiwepo.
  • Full time: 'FT/Full time/Malizia/نهاية...' kwenye picha mara 2, au stream ikiisha baada ya dakika 80+.
Push haitumwi kwa sauti peke yake (inaweza kudanganywa na shangwe); inahitaji alama kubadilika kwenye scoreboard.
Env muhimu: URL, AUTH, SLUG, TITLE, KICKOFF (HH:MM Dar es Salaam), PUSH_URL, PUSH_SECRET, DRY_RUN (default 1: haitumi, inaonyesha tu)."""
import glob, json, os, re, subprocess, sys, threading, time, urllib.parse, urllib.request
from collections import deque
from datetime import datetime, timedelta
import numpy as np
import goalaudio as ga
import matchtext as mt

E = os.environ.get
URL, AUTH, SLUG, TITLE = E("URL", "").strip(), E("AUTH", "").strip(), E("SLUG", "").strip(), E("TITLE", "Home vs Away").strip()
KICKOFF, MAX_MIN, WAIT_MIN = E("KICKOFF", "").strip(), int(E("MAX_MINUTES", "150")), int(E("WAIT_START_MIN", "25"))
DRY, SCORER_WAIT, WHISPER = E("DRY_RUN", "1") != "0", int(E("SCORER_WAIT", "12")), E("WHISPER_MODEL", "small")
LANGS = [l for l in E("PUSH_LANGS", "sw,en,fr,ar").split(",") if l in mt.LANGS] or ["sw", "en"]
PUSH_URL, PUSH_SECRET = E("PUSH_URL", ""), E("PUSH_SECRET", "")
SCORE_BOX = [float(v) for v in E("SCORE_BOX", "0.02,0.03,0.32,0.09").split(",")]    # x,y,w,h kama sehemu ya fremu
BANNER_BOX = [float(v) for v in E("BANNER_BOX", "0.05,0.74,0.60,0.14").split(",")]
SAVE_DEBUG = E("SAVE_DEBUG", "1") != "0"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def post(title, message, url):
    log(("[DRY] " if DRY else "📣 ") + title + " | " + message.replace("\n", " ⏐ "))
    if DRY:
        return
    body = json.dumps({"record": {"title": title, "message": message, "image_url": None, "url": url}}).encode()
    req = urllib.request.Request(PUSH_URL, data=body, method="POST", headers={"Content-Type": "application/json", "x-webhook-secret": PUSH_SECRET})
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        log("⚠️ push imeshindwa:", e)


class GoalAudio:
    """Hugundua mngurumo wa goal kwa sauti: model yako ikiwepo, vinginevyo kanuni rahisi (kelele +8 dB kwa sekunde 3)."""
    def __init__(self, path="goal_model.json"):
        self.m = json.load(open(path)) if os.path.exists(path) else None
        self.F, self.run, self.last = [], 0, -1e9
        log("🎧 model ya goal:", "imepakiwa" if self.m else "haipo (natumia kanuni rahisi)")

    def push(self, row):
        self.F.append(row); n = len(self.F)
        tail = np.array(self.F[-(ga.WIN + ga.BASE):], np.float32)
        v = ga.window_feats(tail, len(tail))
        if v is None:
            return False
        hit = (ga.predict(self.m, v[None])[0] >= self.m["thr"]) if self.m else (v[0] >= 8 and v[2] >= 8)
        self.run = self.run + 1 if hit else 0
        if self.run >= (self.m or {}).get("min_run", 3) and n - self.last >= (self.m or {}).get("cooldown", 90):
            self.last, self.run = n, 0
            return True
        return False


class Watcher:
    def __init__(self, home, away, slug, kickoff, push, asr=None, goal_audio=None, now=time.time):
        self.home, self.away, self.slug, self.kickoff, self.push, self.asr, self.ga, self.now = home, away, slug, kickoff, push, asr, goal_audio, now
        self.score, self.cand, self.cand_n, self.clock = None, None, 0, None
        self.goals, self.pending, self.ft_n, self.done, self.burst_until = [], [], 0, False, 0
        self.audio = deque(maxlen=120)
        self.exclude = re.findall(r"[^\W\d_]{3,}", f"{home} {away}")
        self.url = "./#/match/" + urllib.parse.quote(slug)

    # ── sauti ──
    def on_second(self, x, row):
        self.audio.append(x)
        if self.ga and self.ga.push(row):
            self.burst_until = self.now() + 45
            log("🔊 Mngurumo unaofanana na goal (sauti). Nangoja uthibitisho wa scoreboard…")

    # ── picha ──
    def on_frame(self, score_txt, banner_txt):
        if self.done:
            return
        self.ft_n = self.ft_n + 1 if mt.FT_OCR.search(f"{score_txt} {banner_txt}") else 0
        if self.ft_n >= 2 and self.score is not None:
            return self.finish("scoreboard (FT)")
        sb = mt.parse_scorebug(score_txt)
        if sb:
            if sb["clock"] is not None:
                self.clock = sb["clock"]
            cur = (sb["home"], sb["away"])
            self.cand_n = self.cand_n + 1 if cur == self.cand else 1
            self.cand = cur
            if self.cand_n >= 2:
                if self.score is None:
                    self.score = cur; log(f"📋 Alama ya kuanzia: {cur[0]}-{cur[1]}")
                elif cur != self.score:
                    self._changed(cur)
        name = mt.parse_banner(banner_txt, self.exclude) if banner_txt else None
        for g in self.pending:
            if name and not g["banner"]:
                g["banner"] = name

    def _minute(self):
        if self.clock is not None:
            return self.clock, False
        if self.kickoff:
            m = (datetime.fromtimestamp(self.now()) - self.kickoff).total_seconds() / 60
            return max(1, min(130, int(m - (15 if m > 62 else 0)))), True
        return None, False

    def _changed(self, cur):
        da, db = cur[0] - self.score[0], cur[1] - self.score[1]
        if da < 0 or db < 0 or not 0 < da + db <= 2:      # alama kupungua au kuruka sana = hitilafu ya OCR; tunapuuza
            self.cand, self.cand_n = None, 0
            return
        minute, approx = self._minute()
        scorer = None
        if self.asr:
            try:
                lang, text = self.asr(list(self.audio)[-60:])
                scorer = mt.extract_scorer(text, lang, self.exclude)
                log(f"🎙️ ASR[{lang}]: …{text[-160:]}  => mfungaji: {scorer}")
            except Exception as e:
                log("⚠️ ASR imeshindwa:", e)
        s, steps = list(self.score), []
        for side in ["home"] * max(da, 0) + ["away"] * max(db, 0):
            s[0 if side == "home" else 1] += 1
            steps.append({"team": self.home if side == "home" else self.away, "side": side, "score": tuple(s), "minute": minute,
                          "approx": approx, "scorer": scorer, "banner": None, "deadline": self.now() + SCORER_WAIT})
        self.pending += steps
        self.score, self.cand, self.cand_n = cur, None, 0
        log(f"⚽ Goli! {self.home} {cur[0]}-{cur[1]} {self.away} (dakika {minute}{'~' if approx else ''})")

    def tick(self):
        for g in [g for g in self.pending if self.now() >= g["deadline"]]:
            self.pending.remove(g)
            g["scorer"] = g["banner"] or g["scorer"]
            self.goals.append(g)
            self.push(mt.push_title("goal", self.home, g["score"][0], self.away, g["score"][1]),
                      mt.compose(LANGS, mt.goal_message, team=g["team"], scorer=g["scorer"], minute=g["minute"], approx=g["approx"]), self.url)

    def finish(self, why):
        if self.done or self.score is None:
            return
        self.tick_all()
        self.done = True
        log(f"🏁 Mwisho wa mechi ({why})")
        self.push(mt.push_title("ft", self.home, self.score[0], self.away, self.score[1]),
                  mt.compose(LANGS, mt.ft_message, goals=self.goals), self.url)

    def tick_all(self):
        for g in self.pending:
            g["deadline"] = 0
        self.tick()

    def on_eof(self):
        if not self.done and self.score is not None and ((self.clock or 0) >= 80 or self._minute()[0] and self._minute()[0] >= 85):
            self.finish("stream imeisha")


# ── ffmpeg / OCR / ASR (IO) ──
def ffmpeg_cmd():
    sx, sy, sw, sh = SCORE_BOX; bx, by, bw, bh = BANNER_BOX
    fc = (f"[0:v]fps=1/2,split=2[a][b];[a]crop=iw*{sw}:ih*{sh}:iw*{sx}:ih*{sy}[s];[b]crop=iw*{bw}:ih*{bh}:iw*{bx}:ih*{by}[n]")
    cmd = ["ffmpeg", "-nostdin", "-v", "error"]
    if URL.startswith("http"):
        cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        if AUTH:
            cmd += ["-headers", f"Authorization: {AUTH}\r\n"]
    return cmd + ["-i", URL, "-filter_complex", fc, "-map", "[s]", "-q:v", "2", "frames/s_%06d.jpg", "-map", "[n]", "-q:v", "2", "frames/b_%06d.jpg",
                 "-map", "0:a:0", "-ac", "1", "-ar", str(ga.SR), "-f", "s16le", "pipe:1"]


def ocr_text(path):
    from PIL import Image, ImageOps
    import pytesseract
    im = Image.open(path).convert("L"); im = im.resize((im.width * 3, im.height * 3)); a = ImageOps.autocontrast(im)
    outs = [pytesseract.image_to_string(x, config="--psm 7", lang="eng+ara+fra").strip() for x in (a, ImageOps.invert(a))]
    return " | ".join(o for o in outs if o)


def make_asr():
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(WHISPER, device="cpu", compute_type="int8")
    except Exception as e:
        log("ℹ️ ASR haipo (jina la mfungaji litatoka kwenye banner tu):", e); return None

    def asr(chunks):
        segs, info = model.transcribe(np.concatenate(chunks).astype(np.float32), beam_size=1)
        return (info.language if info.language in mt.LANGS else "en"), " ".join(s.text for s in segs)
    return asr


def kickoff_dt():
    if not re.fullmatch(r"\d{1,2}:\d{2}", KICKOFF):
        return None
    from zoneinfo import ZoneInfo
    z = ZoneInfo("Africa/Dar_es_Salaam"); now = datetime.now(z)
    h, m = map(int, KICKOFF.split(":")); k = now.replace(hour=h, minute=m, second=0, microsecond=0)
    k = min((k + timedelta(days=d) for d in (-1, 0, 1)), key=lambda x: abs((x - now).total_seconds()))
    return k.replace(tzinfo=None) + (datetime.now() - datetime.now(z).replace(tzinfo=None))   # saa ya mashine


def main():
    if not URL:
        sys.exit("❌ URL haijawekwa")
    home, away = mt.split_teams(TITLE)
    os.makedirs("frames", exist_ok=True); os.makedirs("debug", exist_ok=True)
    w = Watcher(home, away, SLUG or "match", kickoff_dt(), post, make_asr(), GoalAudio())
    started, buf, lock, seen_idx, dbg = time.time(), bytearray(), threading.Lock(), -1, 0
    log(f"▶️ {home} vs {away} | DRY_RUN={DRY} | lugha: {','.join(LANGS)}")
    while time.time() - started < WAIT_MIN * 60 and not w.done:                  # ngoja stream ianze
        proc = subprocess.Popen(ffmpeg_cmd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=lambda: [buf.extend(c) if not lock.acquire() else (buf.extend(c), lock.release()) for c in iter(lambda: proc.stdout.read(32000), b"")], daemon=True).start()
        t0 = time.time()
        while proc.poll() is None and time.time() - started < MAX_MIN * 60 and not w.done:
            time.sleep(0.5)
            with lock:
                n = len(buf) // (2 * ga.SR); data = bytes(buf[:n * 2 * ga.SR]); del buf[:n * 2 * ga.SR]
            for i in range(n):
                x = np.frombuffer(data[i * 2 * ga.SR:(i + 1) * 2 * ga.SR], dtype=np.int16).astype(np.float32) / 32768.0
                w.on_second(x, ga.sec_features(x)[0])
            frames = sorted(glob.glob("frames/s_*.jpg"))
            for f in frames[:-1]:                                              # ya mwisho inaweza kuwa bado inaandikwa
                idx = int(re.search(r"s_(\d+)", f)[1])
                if idx <= seen_idx:
                    continue
                seen_idx = idx
                if idx % 2 and w.now() > w.burst_until:                        # kawaida: kila fremu ya pili; baada ya mngurumo: zote
                    continue
                try:
                    st, bt = ocr_text(f), (ocr_text(f.replace("/s_", "/b_")) if os.path.exists(f.replace("/s_", "/b_")) else "")
                except Exception as e:
                    log("⚠️ OCR imeshindwa:", e); st = bt = ""
                if SAVE_DEBUG and dbg < 6:
                    dbg += 1; log(f"🔎 OCR[{idx}] scoreboard='{st}' banner='{bt}'")
                    os.replace(f, f"debug/{os.path.basename(f)}") if not os.path.exists(f"debug/{os.path.basename(f)}") else None
                    b = f.replace("/s_", "/b_")
                    if os.path.exists(b):
                        subprocess.run(["cp", b, "debug/"])
                w.on_frame(st, bt)
            for old in frames[:-4]:
                if os.path.exists(old):
                    os.remove(old); b = old.replace("/s_", "/b_"); os.path.exists(b) and os.remove(b)
            w.tick()
        proc.kill()
        if proc.returncode is not None and time.time() - t0 > 120:               # stream ilifanya kazi kisha ikaisha
            w.tick_all(); w.on_eof(); break
        if not w.done:
            log("⏳ stream haijaanza/imekatika, najaribu tena baada ya sekunde 30…"); time.sleep(30)
    w.tick_all()
    log("✅ Imemaliza. Goals:", [(g["team"], g["scorer"], g["minute"]) for g in w.goals], "| FT:", w.done)


if __name__ == "__main__":
    main()
