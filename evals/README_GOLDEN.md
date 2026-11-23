# Building the golden set (owner task)

`python -m evals.run_eval` needs two things only you can provide:

1. **`corpus/`** — 3–5 documents of different kinds (PDF and DOCX; e.g. a policy/manual, a paper, a report with tables). Put them directly in `corpus/` (not in `corpus/fixtures/`, which is for edge-case files).
2. **`evals/golden_set.json`** — about 30 questions written by you (minimum 25): ≥10 unanswerable (include near-miss in-domain ones), ≥5 reworded, ≥5 multi-part.

Format (`questions` list; `split` is optional — if absent, a stratified ~40 % dev / 60 % test split is assigned with seed 7):

```json
{"questions": [
  {"id": "q01", "question": "How many days do customers have to return an item?", "type": "single",
   "answerable": true, "source_doc": "policy.pdf", "source_page": 1,
   "evidence": "within thirty days of purchase", "reference_answer": "Thirty days from purchase."},
  {"id": "q02", "question": "What is the refund policy for gift cards and shipping fees?", "type": "multi_part",
   "answerable": true, "source_doc": "policy.pdf", "source_page": 2,
   "evidence": ["gift cards are non-refundable", "shipping fees are refunded only for defective items"],
   "reference_answer": "..."},
  {"id": "q03", "question": "Does the company offer lifetime warranty?", "type": "unanswerable",
   "answerable": false, "source_doc": null, "source_page": null, "evidence": null, "reference_answer": null}
]}
```

Rules (PLAN.md §12):

- `type` is one of `single | reworded | multi_part | unanswerable`; `evidence` is a **verbatim** 10–20-word span from `source_doc` (a list for multi-part questions). Never use chunk ids.
- A retrieved chunk counts as relevant if it comes from `source_doc` and contains the span (after normalising case/whitespace/hyphenation), or at least 80 % of the span's words in order.
- Every span must match at least one chunk in **both** configs; check the first run's `hit_at_k` for answerable questions and fix spans that never match.
- State in EVAL.md that you wrote the labels.
