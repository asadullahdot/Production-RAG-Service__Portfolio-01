"""Retrieval metrics, evidence-span matching, threshold tuning and statistics (PLAN.md §12).

Labels are evidence spans (document + page + verbatim text), never chunk ids, so two chunking
configurations can be compared fairly.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Iterable, Sequence


def normalise(text: str) -> str:
    """NFKC, lowercase, drop soft hyphens, undo hyphenation line breaks, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text).replace("­", "")
    text = re.sub(r"-\s*\n\s*", "", text)
    return re.sub(r"\s+", " ", text).lower().strip()


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", normalise(text))


def span_matches(chunk_text: str, span: str, min_ratio: float = 0.8) -> bool:
    """True if the normalised span is contained in the chunk, or ≥ ``min_ratio`` of its words appear in order
    inside the chunk (so a span cut by a chunk boundary still counts for the chunk holding most of it)."""
    span_n, chunk_n = normalise(span), normalise(chunk_text)
    if not span_n:
        return False
    if span_n in chunk_n:
        return True
    span_w, chunk_w = _words(span), _words(chunk_text)
    if not span_w:
        return False
    # longest in-order (subsequence) overlap, computed greedily per span word over the chunk words
    i = 0
    matched = 0
    for w in span_w:
        try:
            j = chunk_w.index(w, i)
        except ValueError:
            continue
        matched += 1
        i = j + 1
    return matched / len(span_w) >= min_ratio


def as_spans(evidence) -> list[str]:
    if evidence is None:
        return []
    return [evidence] if isinstance(evidence, str) else list(evidence)


def chunk_relevant(chunk: dict, source_doc: str, spans: Sequence[str]) -> bool:
    """A retrieved chunk is relevant iff it is from ``source_doc`` and matches any evidence span."""
    return chunk["file_name"] == source_doc and any(span_matches(chunk["text"], s) for s in spans)


def hit_at_k(relevance: Sequence[bool]) -> float:
    return 1.0 if any(relevance) else 0.0


def mrr(relevance: Sequence[bool]) -> float:
    for rank, rel in enumerate(relevance, start=1):
        if rel:
            return 1.0 / rank
    return 0.0


def span_coverage(chunks: Sequence[dict], source_doc: str, spans: Sequence[str]) -> tuple[float, bool]:
    """(recall@k = spans covered ÷ total spans, all spans covered)."""
    if not spans:
        return 0.0, False
    covered = sum(
        1 for s in spans
        if any(c["file_name"] == source_doc and span_matches(c["text"], s) for c in chunks))
    return covered / len(spans), covered == len(spans)


def mean(values: Iterable[float]) -> float | None:
    vals = list(values)
    return sum(vals) / len(vals) if vals else None


def percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def tune_threshold(top_scores: Sequence[tuple[float, bool]]) -> tuple[float, list[tuple[float, float]]]:
    """Pick the threshold maximising balanced accuracy on dev (ties → the lower threshold).

    ``top_scores`` is (best similarity of the question, answerable). Answerable is "kept" when its best
    score ≥ t; unanswerable is "correctly refused" when its best score < t.
    Returns (threshold, curve of (t, balanced_accuracy)).
    """
    ans = [s for s, a in top_scores if a]
    una = [s for s, a in top_scores if not a]
    uniq = sorted({round(s, 4) for s, _ in top_scores})
    # candidate thresholds are midpoints between adjacent observed scores (plus 0), so the chosen value sits
    # between the classes instead of on the edge of one dev example
    candidates = sorted({0.0} | {round((lo + hi) / 2, 4) for lo, hi in zip(uniq, uniq[1:])})
    curve = []
    for t in candidates:
        kept = sum(1 for s in ans if s >= t) / len(ans) if ans else 1.0
        refused = sum(1 for s in una if s < t) / len(una) if una else 1.0
        curve.append((t, (kept + refused) / 2))
    best = max(curve, key=lambda x: (round(x[1], 6), -x[0]))
    return best[0], curve


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def cohen_kappa(a: Sequence, b: Sequence) -> float:
    """Cohen's kappa for two equal-length label sequences."""
    n = len(a)
    if n == 0 or n != len(b):
        return 0.0
    labels = set(a) | set(b)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = sum((sum(1 for x in a if x == l) / n) * (sum(1 for y in b if y == l) / n) for l in labels)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def assign_split(questions: list[dict], dev_fraction: float = 0.4, seed: int = 7) -> None:
    """Stratified (answerable / unanswerable) dev/test split, deterministic for a given seed. Mutates in place."""
    import random
    rng = random.Random(seed)
    for answerable in (True, False):
        group = sorted((q for q in questions if bool(q["answerable"]) == answerable), key=lambda q: q["id"])
        rng.shuffle(group)
        n_dev = max(1, round(len(group) * dev_fraction)) if group else 0
        for i, q in enumerate(group):
            q["split"] = "dev" if i < n_dev else "test"
