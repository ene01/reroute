"""Train Reroute's own scam detector and test it honestly.

    python train_detector.py

1. Builds the synthetic training set (detector_data.py).
2. Holds back 20% of it, trains on the other 80%, and measures accuracy on the held-back part.
3. Separately tests on the demo scenarios' messages from seed_data.py, which it never trained on.
4. Retrains on all the synthetic data and saves models/scam_detector.joblib plus a report.
"""

import json
from datetime import datetime

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, precision_score, recall_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import FeatureUnion, Pipeline

import detector
import detector_data
import seed_data

# Demo scenarios with a clear right answer (1 = scam / attack, 0 = normal). The three
# "uncertain slang" cases have no single right label, so they're left out of the score.
SCENARIO_LABELS = {"T003": 1, "T006": 1, "T013": 1, "T014": 1, "T004": 0, "T005": 0, "T011": 0, "T016": 0}


def make_pipeline() -> Pipeline:
    return Pipeline([
        ("features", FeatureUnion([
            ("words", TfidfVectorizer(lowercase=True, ngram_range=(1, 2), min_df=1, sublinear_tf=True,
                                      stop_words="english")),
            ("chars", TfidfVectorizer(lowercase=True, analyzer="char_wb", ngram_range=(3, 5), min_df=2,
                                      sublinear_tf=True)),
        ])),
        ("clf", LogisticRegression(C=4.0, max_iter=2000, class_weight="balanced")),
    ])


def main() -> None:
    data = detector_data.build_dataset()
    texts, labels = [t for t, _ in data], [y for _, y in data]
    print(f"Training set: {len(texts)} synthetic messages ({sum(labels)} scam, {len(labels) - sum(labels)} normal)")

    x_tr, x_te, y_tr, y_te = train_test_split(texts, labels, test_size=0.2, random_state=42, stratify=labels)
    pipe = make_pipeline().fit(x_tr, y_tr)
    pred = pipe.predict(x_te)
    held_out = {"messages": len(x_te), "accuracy": round(accuracy_score(y_te, pred), 3),
                "precision": round(precision_score(y_te, pred), 3), "recall": round(recall_score(y_te, pred), 3)}
    print(f"Held-out test (never seen in training): {held_out}")

    # Final model: all synthetic data.
    final = make_pipeline().fit(texts, labels)
    detector.MODEL_PATH.parent.mkdir(exist_ok=True)
    joblib.dump(final, detector.MODEL_PATH)
    detector._model.cache_clear()

    # Fair test on the demo scenarios (not in the training data).
    rows = {r[0]: r for r in seed_data.TRANSACTIONS}
    scenario_results, correct = [], 0
    for txn_id, truth in SCENARIO_LABELS.items():
        s = detector.score(rows[txn_id][8])
        guess = 1 if s and s["scam_probability"] >= 0.5 else 0
        correct += guess == truth
        scenario_results.append({"txn_id": txn_id, "truth": "scam" if truth else "normal",
                                 "scam_probability": s["scam_probability"] if s else None,
                                 "label": s["label"] if s else None, "top_words": s["top_words"] if s else []})
        print(f"  {txn_id}  truth={'scam  ' if truth else 'normal'}  p={s['scam_probability'] if s else '-':<6}  "
              f"{s['label'] if s else '':<13} {', '.join(s['top_words']) if s else ''}")
    print(f"Demo scenarios: {correct}/{len(SCENARIO_LABELS)} correct")

    report = {"trained_at": datetime.now().isoformat(timespec="seconds"), "training_messages": len(texts),
              "model": "TF-IDF (words 1-2 + characters 3-5) + logistic regression", "held_out_test": held_out,
              "demo_scenarios": {"correct": correct, "total": len(SCENARIO_LABELS), "results": scenario_results}}
    detector.REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved {detector.MODEL_PATH.name} and {detector.REPORT_PATH.name}")


if __name__ == "__main__":
    main()
