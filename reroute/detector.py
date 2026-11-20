"""Reroute's own scam detector: a small machine-learning model trained by this project.

It's a TF-IDF + logistic regression text classifier (scikit-learn), trained by
train_detector.py on the synthetic messages in detector_data.py. It runs in milliseconds on
any laptop and returns:
  - scam_probability (0-1)
  - label ("likely scam" / "unclear" / "looks normal")
  - the words that pushed the score up the most (so a person can see WHY)

It's one signal among many: the LLM sees its result as a tool, and the code guard won't let a
high-scoring message auto-clear. It never blocks or freezes anything on its own.
"""

import json
from functools import lru_cache
from pathlib import Path

MODEL_PATH = Path(__file__).with_name("models") / "scam_detector.joblib"
REPORT_PATH = Path(__file__).with_name("models") / "detector_report.json"
HIGH = 0.80   # at or above: "likely scam" (and the guard won't auto-clear)
LOW = 0.35    # below: "looks normal"


@lru_cache(maxsize=1)
def _model():
    try:
        import joblib
        return joblib.load(MODEL_PATH)
    except Exception:
        return None  # not trained yet, or scikit-learn missing: Reroute works without it


def available() -> bool:
    return _model() is not None


def _top_words(pipe, text: str, k: int = 4) -> list[str]:
    """Words and phrases in this message that pushed the scam score up the most."""
    try:
        vec, clf = pipe.named_steps["features"], pipe.named_steps["clf"]
        word_vec = dict(vec.transformer_list)["words"]
        x = word_vec.transform([text])
        names = word_vec.get_feature_names_out()
        n_words = len(names)
        coefs = clf.coef_[0][:n_words]  # the word features come first in the union
        contrib = [(coefs[i] * x[0, i], names[i]) for i in x.nonzero()[1]]
        return [w for c, w in sorted(contrib, reverse=True)[:k] if c > 0]
    except Exception:
        return []


def score(text: str | None) -> dict | None:
    """Score a message. Returns None when there's no message or no trained model."""
    text = (text or "").strip()
    if not text or text == "(no message attached)":
        return None
    pipe = _model()
    if pipe is None:
        return None
    p = float(pipe.predict_proba([text])[0][1])
    label = "likely scam" if p >= HIGH else ("looks normal" if p < LOW else "unclear")
    return {"scam_probability": round(p, 3), "label": label, "top_words": _top_words(pipe, text)}


def report() -> dict | None:
    try:
        return json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
