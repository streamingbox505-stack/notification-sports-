"""goalaudio.py — vipengele vya sauti + model ndogo (numpy tu) ya kugundua 'goal' kutokana na kelele za mashabiki na mtangazaji.

Wazo: goal huleta mngurumo wa ghafla na wa muda mrefu kuliko kawaida ya mechi hiyo. Kwa kila sekunde tunapima sauti,
kisha tunalinganisha dirisha la sekunde 6 za mwisho na kiwango cha kawaida cha dakika 1.5 zilizotangulia.
"""
import subprocess
import numpy as np

SR, WIN, BASE = 16000, 6, 90          # sampuli/sek, dirisha (sek), msingi wa kulinganisha (sek)
FEATS = ["excess_mean", "excess_max", "excess_last3", "rise", "centroid_shift", "hi_mean", "zcr_mean", "db_std"]


def load_audio(path):
    """Soma faili lolote la sauti/video kupitia ffmpeg -> mono 16 kHz float32."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"], capture_output=True)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors="ignore")[-300:])
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def sec_features(x):
    """Kwa kila sekunde: [ukubwa (dB), kituo cha masafa, uwiano wa sauti nyembamba juu ya 2kHz, zero-crossing]."""
    n, w, f = len(x) // SR, np.hanning(SR).astype(np.float32), np.fft.rfftfreq(SR, 1 / SR)
    out = np.zeros((n, 4), np.float32)
    for i in range(n):
        s = x[i * SR:(i + 1) * SR]
        p = np.abs(np.fft.rfft(s * w)) ** 2 + 1e-12
        out[i] = [20 * np.log10(np.sqrt(np.mean(s * s)) + 1e-6), (f * p).sum() / p.sum(), p[f > 2000].sum() / p.sum(),
                  np.mean(np.abs(np.diff(np.signbit(s).astype(np.int8))))]
    return out


def window_feats(F, t):
    """Vipengele vya dirisha linaloishia sekunde t (haitumii kitu cha baadaye, kwa hiyo linafaa kwa live)."""
    if t < WIN + 20:
        return None
    w, b = F[t - WIN:t], F[max(0, t - WIN - BASE):t - WIN]
    ex = w[:, 0] - np.median(b[:, 0])
    return np.array([ex.mean(), ex.max(), ex[-3:].mean(), w[-3:, 0].mean() - w[:3, 0].mean(),
                     w[:, 1].mean() - np.median(b[:, 1]), w[:, 2].mean(), w[:, 3].mean(), w[:, 0].std()], np.float32)


def all_windows(F):
    ts = np.arange(WIN + 20, len(F) + 1)
    return ts, (np.array([window_feats(F, int(t)) for t in ts]) if len(ts) else np.zeros((0, len(FEATS))))


def sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def fit_logit(X, y, sw=None, l2=1e-2, iters=800, lr=0.1):
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Z, w, b = (X - mu) / sd, np.zeros(X.shape[1]), 0.0
    pos = max(float(y.sum()), 1.0); neg = max(float(len(y) - y.sum()), 1.0)
    sw = (np.ones(len(y)) if sw is None else sw) * np.where(y == 1, len(y) / (2 * pos), len(y) / (2 * neg))  # usawa wa madarasa
    for _ in range(iters):
        g = (sigmoid(Z @ w + b) - y) * sw
        w -= lr * ((Z.T @ g) / len(y) + l2 * w); b -= lr * g.mean()
    return {"w": w.tolist(), "b": float(b), "mean": mu.tolist(), "std": sd.tolist()}


def predict(m, X):
    return sigmoid(((X - np.array(m["mean"])) / np.array(m["std"])) @ np.array(m["w"]) + m["b"])


def detect(ts, p, thr, min_run=3, cooldown=90):
    """Goal inatangazwa p ikiwa juu ya thr kwa sekunde min_run mfululizo; kisha kimya kwa cooldown (inazuia marudio)."""
    events, run, last = [], 0, -1e9
    for t, v in zip(ts, p):
        run = run + 1 if v >= thr else 0
        if run >= min_run and t - last >= cooldown:
            events.append(int(t)); last, run = t, 0
    return events
