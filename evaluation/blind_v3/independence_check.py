"""Post-generation overlap check of blind v3 against the earlier datasets (run only after generation)."""

import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend/app/rag/evaluation"
EARLIER = {"historical": "cases.json", "v1": "generalization.json", "v2": "generalization_v2.json"}
UUID = re.compile(r"\{payment:[a-z0-9_]+\}|\{merchant:[a-z0-9_]+\}|[0-9a-f]{8}-[0-9a-f-]{27}")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = UUID.sub(" ID ", text)
    text = re.sub(r"\d+(?:[.,]\d+)?", " N ", text)
    return " ".join(re.findall(r"[a-zA-Z]+", text))


def jaccard(a: str, b: str) -> float:
    x, y = set(a.split()), set(b.split())
    return len(x & y) / len(x | y) if x | y else 1.0


def main(source: str, target: str) -> None:
    v3 = json.loads(Path(source).read_text(encoding="utf-8"))
    earlier = []
    for name, file in EARLIER.items():
        doc = json.loads((ROOT / file).read_text(encoding="utf-8"))
        for case in doc["cases"] if isinstance(doc, dict) else doc:
            earlier.append((name, case["id"], case["question"], normalize(case["question"])))
    rows = []
    for case in v3:
        norm = normalize(case["question"])
        best = max(
            ((SequenceMatcher(None, norm, e[3]).ratio(), jaccard(norm, e[3]), e) for e in earlier),
            key=lambda t: (t[0], t[1]),
        )
        ratio, jac, (name, cid, question, enorm) = best
        if case["question"].strip().lower() == question.strip().lower():
            verdict = "exact_duplicate"
        elif norm == enorm:
            verdict = "cosmetic_variant"
        elif ratio >= 0.85 or jac >= 0.8:
            verdict = "near_identical"
        elif ratio >= 0.7 or jac >= 0.6:
            verdict = "similar"
        else:
            verdict = "independent"
        rows.append(
            {
                "id": case["id"],
                "question": case["question"],
                "verdict": verdict,
                "ratio": round(ratio, 3),
                "jaccard": round(jac, 3),
                "closest": f"{name}:{cid}",
                "closest_question": question,
            }
        )
    internal = []
    for i, a in enumerate(v3):
        for b in v3[i + 1 :]:
            na, nb = normalize(a["question"]), normalize(b["question"])
            r = SequenceMatcher(None, na, nb).ratio()
            if na == nb or r >= 0.85:
                internal.append({"a": a["id"], "b": b["id"], "ratio": round(r, 3)})
    summary = {}
    for r in rows:
        summary[r["verdict"]] = summary.get(r["verdict"], 0) + 1
    Path(target).write_text(
        json.dumps(
            {"summary": summary, "internal_near_duplicates": internal, "cases": rows}, indent=1, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    print(summary, "internal:", internal)
    for r in sorted(rows, key=lambda r: -r["ratio"]):
        if r["verdict"] != "independent":
            print(
                f"{r['verdict']:16} {r['id']} {r['ratio']} {r['jaccard']} | {r['question']} || {r['closest']} | {r['closest_question']}"
            )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
