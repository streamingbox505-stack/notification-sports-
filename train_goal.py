#!/usr/bin/env python3
"""train_goal.py — hufundisha model ya kugundua goal kwa rekodi zako mwenyewe.

Data:  folda AUDIO_DIR (default 'audio') yenye faili za mechi + labels.csv (safu: file,sec,label)
         label = goal | near_miss   (near_miss = shuti lililokosa/penalti/shangwe isiyo goal: inafundisha model isidanganyike)
         sec   = sekunde au mm:ss ambapo mngurumo wa goal ulianza
Pato:  goal_model.json (uzito, kizingiti na kipimo cha CV). Ukiwa na HF_TOKEN na --upload inapakiwa kwenye HF Model.
Env:   AUDIO_DIR, LABELS, MODEL_REPO (default jaynesmaxtv/goal-audio-model), HF_TOKEN
"""
import csv, json, os, sys
import numpy as np
import goalaudio as ga

AUDIO_DIR = os.environ.get("AUDIO_DIR", "audio")
LABELS = os.environ.get("LABELS", os.path.join(AUDIO_DIR, "labels.csv"))
MODEL_REPO = os.environ.get("MODEL_REPO", "jaynesmaxtv/goal-audio-model")
MATCH_AFTER = 35  # tukio linahesabiwa sahihi likitokea ndani ya sekunde hizi baada ya goal


def secs(v):
    v = str(v).strip()
    if ":" in v:
        parts = [float(x) for x in v.split(":")]
        return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
    return float(v)


def read_labels():
    per = {}
    with open(LABELS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r.get("file") or not r.get("sec"):
                continue
            d = per.setdefault(r["file"].strip(), {"goal": [], "near_miss": []})
            d["near_miss" if r.get("label", "goal").strip() == "near_miss" else "goal"].append(secs(r["sec"]))
    return per


def build(ts, goals, nears):
    y, w, keep = np.zeros(len(ts)), np.ones(len(ts)), np.ones(len(ts), bool)
    for g in goals:
        y[(ts >= g + 3) & (ts <= g + 15)] = 1                              # dirisha lina mngurumo wa goal
        keep &= ~(((ts > g - 10) & (ts < g + 3)) | ((ts > g + 15) & (ts <= g + 45)))   # eneo lenye utata: tunaliacha
    for n in nears:
        w[(ts >= n + 3) & (ts <= n + 15)] = 3                              # negatives ngumu zina uzito zaidi
        keep &= ~(((ts > n - 10) & (ts < n + 3)) | ((ts > n + 15) & (ts <= n + 30)))
        keep |= (ts >= n + 3) & (ts <= n + 15)
    return y, w, keep


def score_events(events, goals):
    hit, tp = set(), 0
    for e in events:
        m = [i for i, g in enumerate(goals) if g <= e <= g + MATCH_AFTER and i not in hit]
        if m:
            hit.add(m[0]); tp += 1
    return tp, len(events) - tp, len(goals) - tp


def main():
    per = read_labels()
    data = {}
    for fn, lab in per.items():
        path = os.path.join(AUDIO_DIR, fn)
        if not os.path.exists(path):
            print(f"⚠️ {fn} haipo kwenye {AUDIO_DIR}, naruka"); continue
        F = ga.sec_features(ga.load_audio(path)); ts, X = ga.all_windows(F)
        y, w, keep = build(ts, lab["goal"], lab["near_miss"])
        data[fn] = dict(ts=ts, X=X, y=y, w=w, keep=keep, goals=lab["goal"])
        print(f"  {fn}: dakika {len(F) // 60}, goals {len(lab['goal'])}, near_miss {len(lab['near_miss'])}", flush=True)
    total = sum(len(d["goals"]) for d in data.values())
    if total == 0:
        print("❌ Hakuna goal zilizowekwa alama."); return 2
    if total < 30 or len(data) < 5:
        print(f"⚠️ Data ni ndogo ({total} goals, mechi {len(data)}). Matokeo ni ya kujaribu tu; lengo ni angalau goals 30 kutoka mechi 5+.")

    names = list(data)
    cv = {fn: np.full(len(data[fn]["ts"]), np.nan) for fn in names}
    if len(names) >= 2:  # leave-one-match-out: kila mechi inapimwa na model isiyoiona
        for hold in names:
            tr = [data[n] for n in names if n != hold]
            Xtr = np.concatenate([d["X"][d["keep"]] for d in tr]); ytr = np.concatenate([d["y"][d["keep"]] for d in tr])
            wtr = np.concatenate([d["w"][d["keep"]] for d in tr])
            if ytr.sum() == 0:
                continue
            cv[hold] = ga.predict(ga.fit_logit(Xtr, ytr, wtr), data[hold]["X"])
    best = {"f1": -1, "thr": 0.8, "p": 0, "r": 0}
    if any(not np.isnan(v).all() for v in cv.values()):
        for thr in np.arange(0.3, 0.99, 0.05):
            tp = fp = fn_ = 0
            for fn in names:
                if np.isnan(cv[fn]).all():
                    continue
                a, b, c = score_events(ga.detect(data[fn]["ts"], cv[fn], thr), data[fn]["goals"]); tp += a; fp += b; fn_ += c
            p, r = tp / max(tp + fp, 1), tp / max(tp + fn_, 1); f1 = 2 * p * r / max(p + r, 1e-9)
            if f1 > best["f1"]:
                best = {"f1": f1, "thr": float(round(thr, 2)), "p": p, "r": r}
        print(f"📊 CV (mechi ambazo model haikuziona): precision {best['p']:.2f}, recall {best['r']:.2f}, kizingiti {best['thr']}")
    else:
        print("ℹ️ Mechi moja tu: hakuna CV; ongeza mechi zaidi ili kupima kwa uaminifu.")

    X = np.concatenate([d["X"][d["keep"]] for d in data.values()]); y = np.concatenate([d["y"][d["keep"]] for d in data.values()])
    w = np.concatenate([d["w"][d["keep"]] for d in data.values()])
    model = ga.fit_logit(X, y, w)
    model.update(thr=best["thr"], min_run=3, cooldown=90, feats=ga.FEATS, win=ga.WIN, base=ga.BASE,
                 trained_on={"matches": len(data), "goals": int(total)}, cv={"precision": round(best["p"], 3), "recall": round(best["r"], 3)})
    json.dump(model, open("goal_model.json", "w"), indent=1)
    print("✅ goal_model.json imeandikwa")
    if "--upload" in sys.argv and os.environ.get("HF_TOKEN"):
        from huggingface_hub import HfApi
        api = HfApi(token=os.environ["HF_TOKEN"]); api.create_repo(MODEL_REPO, repo_type="model", private=True, exist_ok=True)
        api.upload_file(path_or_fileobj="goal_model.json", path_in_repo="goal_model.json", repo_id=MODEL_REPO, repo_type="model")
        print(f"✅ imepakiwa: https://huggingface.co/{MODEL_REPO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
