# EVAL

How the service was evaluated, what the numbers are, and what they do not show. Every number here comes from the runs saved in `evals/results/` (first run 24 Nov 2026; **re-run 25 Nov 2026 after the prompt change described in section 9**, which is what the tables below show; real OpenAI and Qdrant Cloud). Nothing is estimated unless it says "estimate".

## 1. Setup

| Item | Value |
|---|---|
| Corpus | 4 documents in `corpus/`: a PDF research paper (10 pages), a DOCX résumé (redacted, 7 sections), a PPTX business review (10 slides with speaker notes, fictional company), a Markdown README (9 sections). Sources: the paper is Mattmann et al., *Procedia Computer Science* 270 (2025) 1517-1526, open access (DOI 10.1016/j.procs.2025.09.273); the README is the owner's own project; the résumé is the owner's with name, contact details and file metadata removed; the deck is invented |
| Golden set | `evals/golden_set.json`: **42 questions**: 17 single, 6 reworded, 7 multi-part, 12 unanswerable (including near-miss questions that look answerable, such as "Q4 FY2025 revenue" when only Q3 FY25 is given) |
| Who wrote the labels | **Claude (an AI), not the owner.** The owner has not independently re-labelled them. Evidence spans are verbatim; `scripts/check_golden.py` proves every span matches a chunk of its source document in **both** configs |
| Split | stratified, seed 7: **dev** 12 answerable + 5 unanswerable; **test** 18 answerable + 7 unanswerable. The threshold is tuned on dev; results below are on **test** |
| Models | generator `gpt-6-luna` (reasoning effort low), judge `gpt-6.1-terra` (medium), embeddings `text-embedding-3-small`. No `temperature` (both chat models reject it), so a fresh run can differ slightly from this one; the disk cache makes a rerun with the same cache identical |
| Configs | **A** chunks of 1000 characters, overlap 200 (95 chunks); **B** 500 / 100 (166 chunks) |
| Pipeline under test | the real `AskService` (input guardrails, model screen, retrieval, threshold, generation, citation check), with a separate Qdrant collection and SQLite file per run |

**Metrics.** hit@k: a relevant chunk is in the top-5. recall@k: evidence spans covered ÷ spans (multi-part questions have two). MRR: 1 ÷ rank of the first relevant chunk. A chunk is *relevant* if it is from the source document and contains the evidence span (normalised), or at least 80% of the span's words appear in order inside a window of 1.5× the span length and every number in the span is present. Retrieval metrics use the raw top-5, before the threshold, on answerable questions only. **Faithfulness**: the judge extracts the claims in a non-refused answer, tags each `document` or `general`, and scores the `document` claims against the chunks the model was actually shown. **Correctness**: judge versus the reference answer on 0 / 0.5 / 1; an over-refused answerable question scores 0. **Unanswerable refused**: share of unanswerable questions refused. **Over-refusal**: share of answerable questions refused.

## 2. Results on the test split (the pre-declared winner rule)

The similarity threshold is tuned per config on dev (maximising balanced accuracy), then the config is judged on test.

| | Config A (1000/200) | Config B (500/100) |
|---|---|---|
| Tuned threshold | 0.404 | 0.414 |
| hit@5 | **0.944** (17/18; 95% CI 0.74-0.99) | 0.833 (15/18; CI 0.61-0.94) |
| recall@5 | **0.944** | 0.806 |
| MRR | **0.612** | 0.553 |
| Faithfulness | 1.000 | 1.000 |
| Correctness | 0.889 | 0.889 |
| Unanswerable refused | 1.000 (7/7) | 1.000 (7/7) |
| Over-refusal | 0.111 (2/18) | 0.111 (2/18) |
| Generation latency p50 / p95 | 2.5 s / 2.8 s | 2.4 s / 2.7 s |
| Tokens per question (screen + answer) | 1087 | 959 |

**Winner by the fixed rule: Config A.** The rule: primary metric hit@k and MRR on test; a difference counts only if at least 2 questions change net. Hit@k differs by 2 questions (q18 and q19: A found them, B did not) and A wins MRR on 5 questions against 2, with 11 ties. The result is real but small: with 18 answerable test questions the confidence intervals overlap, and one question moves a rate by 0.056. On dev the two configs were level on hit@k (1.000 both). Retrieval does not depend on the prompt, so hit@k, recall@k and MRR are identical to the first run; only the answer-side numbers moved (see section 9).

**By document** (all 30 answerable questions, dev and test): hit@k A / B was 1.00 / 1.00 on the deck (13), the README (5) and the résumé (5), and **0.86 / 0.57** on the paper (7). All of B's loss is on the PDF.

**Latency** is the time of the answer-generation call only (`generation_ms`); it excludes the guardrail screen, embedding, Qdrant search and any retry, so end-to-end latency is higher (in manual runs the whole request took about 3-6 s). **Cost** (estimate from the token counts and the published prices): the 42 asks cost about $0.004 per config on the generator side; judge and ingestion cost were not tracked.

## 3. The threshold finding (diagnostic, run after seeing the results)

The dev-tuned thresholds (0.40-0.41) are not reliable. Balanced accuracy on dev peaks at only 0.62 (A) and 0.66 (B), against 0.50 for "no threshold", because near-miss unanswerable questions score as high as real ones: top similarity of the 12 unanswerable questions ranged 0.10-0.70 (median 0.51), while the 30 answerable ones ranged 0.34-0.80 (median 0.54). A similarity threshold cannot tell "Q4 FY2025 revenue" from "Q3 revenue".

What actually refuses near-miss questions is the model (prompt rule 4). To see the effect, both configs were re-run at a **fixed threshold of 0.15** (`--fixed-threshold 0.15`, saved separately, not the winner-rule result):

| Test split | A at 0.404 (tuned) | **A at 0.15** | B at 0.414 (tuned) | B at 0.15 |
|---|---|---|---|---|
| Correctness | 0.889 | **0.944** | 0.889 | 0.889 |
| Over-refusal | 0.111 | **0.056** (1/18; CI 0.01-0.26) | 0.111 | 0.111 |
| Unanswerable refused | 1.000 | 1.000 | 1.000 | 1.000 |

All 7 unanswerable test questions were still refused at 0.15, so the higher threshold bought nothing here and cost answerable questions (A dev: q14 and q23 fell under 0.404 and were refused as `no_relevant_context`). **The shipped default is therefore 0.15**, as set during manual testing, and the rule-tuned value is reported only for transparency. The threshold still has one job: very low scores (off-topic questions scored 0.00-0.13) never reach the model, which saves a call. This conclusion rests on 25 test questions on one corpus.

## 4. Failure analysis

| Kind | Questions | What happened |
|---|---|---|
| Retrieval miss: incidental sentence | q20, both configs (test) | "On which GPU were the models trained?" The answer is one clause in a limitations paragraph; dense retrieval ranked other chunks higher (top score 0.39-0.45), so the chunk with the span was not in the top 5. Hybrid or keyword search would likely help |
| Retrieval miss: chunk split | q18, q19, Config B only (test) | The evidence sentence is cut across a 500-character chunk boundary. The splitter breaks at line ends and builds overlap only from whole lines: on the PDF all 55 neighbouring pairs of A overlap but only 22 of 109 pairs of B do (20%). The same split lowered B's partial recall on q23 and q25 (0.50) |
| Over-refusal by the model | q09 (both, dev), q11 (A, test, tuned threshold only), q41 (both, dev); q05 (test) was refused in the first run and answered correctly in the re-run | The right chunk was retrieved (top scores 0.44-0.55) but the model answered `NOT_IN_DOCUMENTS`. The two reworded questions used synonyms ("big-business clients", "AI model") for words in the text. The same cash-on-hand fact was answered correctly in a live run with different wording, so these refusals are sensitive to phrasing, and with no `temperature` available the same question can flip between runs (q05 did, in both configs) |
| Threshold refusal | q14, q23 (A, dev) | top score below 0.404 (0.34, 0.38), refused as `no_relevant_context`; fixed by the lower threshold |
| Wrong answer | q10 (A, dev) | the evidence chunk was retrieved but the answer cited a neighbouring README sentence (the leak scanner) instead of "the API answers 422 and stores nothing"; correctness 0. Config B answered correctly |
| Unsupported claim | none found | the judge scored every answered question faithful (1.000). See section 5: this is a ceiling, not proof, but the judge does catch invented claims when they are planted |
| Bad citation | not measured | invalid citation numbers are removed by code and logged; no rate was computed |

## 5. Judge reliability

The judge (`gpt-6.1-terra`) was compared with labels from Claude (an AI, **not** the owner or an independent human) on 44 answers: 28 real answers drawn by a stratified sample from the first run (`scripts/judge_reliability.py --sample`; the sample was not redrawn after the re-run) and 16 **control answers** written to be wrong or right by construction (`scripts/judge_controls.py`: a wrong number, a wrong entity, swapped values, an opposite answer, an invented extra claim, half of a two-part answer, and four correct paraphrases). Controls were needed because the system answers almost everything correctly: only 1 of the 28 real answers was wrong, which says little about whether the judge detects errors. Binarisation: judge correct = correctness 1.0; judge faithful = faithfulness 1.0.

| | n | Agreement (95% CI) | Cohen's kappa |
|---|---|---|---|
| Correctness, real answers | 28 | 100% (0.88-1.00) | 1.00 (only one wrong answer) |
| Correctness, controls | 16 | 87.5% (0.64-0.97) | 0.71 |
| **Correctness, combined** | **44** | **95.5% (0.85-0.99)** | **0.89** |
| Faithfulness, real answers | 28 | 100% | not meaningful (every label is "faithful") |
| Faithfulness, controls | 16 | 100% (0.81-1.00) | 1.00 |
| **Faithfulness, combined** | **44** | **100% (0.92-1.00)** | **1.00** |

The target was at least 80% agreement; it was met. The judge flagged all 8 wrong-number, wrong-entity, swapped and opposite controls as wrong (correctness 0) and accepted all 4 correct paraphrases. Its only disagreements with the labels are 2 of 16 controls: for a correct answer with an invented extra detail, the judge gave correctness 0.5 (the label said correct, the judge penalised the invention). Half answers got 0.5 and counted as not fully correct, matching the labels.

**Caveats.** The labeller is an AI and the judge is the same vendor family, so shared blind spots are possible; the controls are synthetic and easier than real subtle errors; derived arithmetic ("0.81 percentage points lower") was labelled faithful because it follows from the chunks, and a stricter reading would differ. A human re-label of the sample is the single most useful improvement.

The 16 controls were run through the judge again on 25 Nov: every label matched the first run, and the combined agreement is still 95.5% (kappa 0.89 for correctness, 1.00 for faithfulness).

## 6. Limitations

- The golden set, its labels and the judge check were produced by Claude; the owner has not independently verified them.
- Small samples: 18 answerable and 7 unanswerable test questions on 4 documents. Differences of one or two questions are within noise, and "measured on this corpus" is the most that can be claimed; the threshold and chunk size are not shown to be optimal elsewhere.
- Dev has only 5 unanswerable questions, which is why the tuned thresholds are unstable.
- Faithfulness is at its ceiling (1.000) for both configs, so it did not separate them.
- Models take no `temperature`; fresh runs can differ. `evals/results/cache/` is git-ignored, so a rerun from a clean checkout makes new calls and may not reproduce every figure exactly.
- The optional DeepEval cross-check was **not run**.
- The embedding model, vector database and generator were chosen from documentation and price and were not benchmarked against alternatives.

## 7. Top 3 lessons learned

1. **A similarity threshold is a coarse filter, not a refusal mechanism.** Tuned on a small dev set it landed where it hurt answerable questions and added nothing for unanswerable ones, because the model's own refusal rule did that job. We kept a low threshold only to skip hopeless calls.
2. **Chunk boundaries decide retrieval quality more than chunk size alone.** Config B lost on the PDF because its overlap rarely applied to long line-broken text, cutting evidence sentences in two. Measuring the real overlap, not just the configured one, explained the result.
3. **The evaluation and manual testing found what the unit tests could not.** The 233 offline tests passed while real questions were refused (threshold too high), speaker notes were being ignored (an unseen PPTX extraction gap, which also turned one "unanswerable" label into a wrong one), and attack prompts slipped past patterns. Labels written by a tool need a script that checks them against every config, and a human check.

## 8. Reproduce

```text
python scripts/check_golden.py                                  # validates the golden set against both configs
python -m evals.run_eval --config all                           # the winner-rule run -> evals/results/latest.json
python -m evals.run_eval --config all --fixed-threshold 0.15    # the diagnostic run (section 3)
python scripts/judge_reliability.py --sample                    # draws the stratified sample
python scripts/judge_controls.py                                # runs the 16 control answers through the judge
python scripts/judge_reliability.py --score                     # agreement, kappa, confidence intervals
```

Needs `OPENAI_API_KEY` and a Qdrant cluster in `.env`; the cache in `evals/results/cache/` makes a rerun cheap. Saved results: `evals/results/results_*.json`, `latest.json`, `judge_sample.json`, `judge_controls.json`, `judge_reliability.json`; labels: `evals/judge_labels.json`.

## 9. What changed after the first run (bonus features B5 to B7)

**The prompt changed, so the evaluation was re-run.** Edge-case testing (B7) found that prompt rule 6 ("never reveal any key or secret") made the model refuse to quote a password that was written in the user's own document (3 of 6 phrasings refused). Rule 6 now protects only this service's own secrets and says passwords and codes inside the context are ordinary content (0 of 9 refused afterwards). The evaluation cache key includes the prompt version, so the full run (`--config all`), the fixed-0.15 diagnostic and the 16 judge controls were repeated with the new prompt on 25 Nov 2026. Result: retrieval metrics and the winner (A, hit@k by 2 questions, MRR 5 to 2) are identical; faithfulness is 1.000 as before; correctness and over-refusal on test each moved by one question for both configs (0.833 to 0.889 and 0.167 to 0.111), because q05 was answered instead of refused. One question is inside the run-to-run noise of a model without `temperature`, so **this is not evidence that the new prompt is better**, only that it did not make these numbers worse. The prompt text in `DESIGN.md` is the new one.

**Other measurements made after the main evaluation** (each is in PLAN.md section 16 with its method):

| Feature | Measurement | Result |
|---|---|---|
| B6 document guardrails | 11 poisoned documents x 3 questions plus 7 clean documents, real stack (`scripts/run_poisoned_eval.py`) | before hardening 2 of 33 answers repeated the planted claim; after, **0 of 33**, all 33 still correct; scanner flagged 11 of 11 poisoned documents (in-sample: patterns written after seeing them) and 0 of 7 clean ones |
| B6 held-out check | 6 unseen attack phrasings, 5 tricky benign sentences (scanner only) | 2 of 6 attacks flagged; 2 of 5 benign sentences wrongly flagged on first pass, 0 of 5 after two over-generic pattern parts were removed (no longer clean held-out). The scanner is a warning, not the protection |
| B7 edge cases | 27 cases from the brief against the real services (`scripts/run_edge_cases.py`) | **27 of 27 passed**; upstream failures give 503 after 2 bounded retries in 1.4-4.1 s |
| B5 file types | unit tests per type; the deck with speaker notes is part of the golden set (q39-q42) | PPTX, TXT and MD accepted; the notes gap was found and fixed during this evaluation |

These are single runs on small fixtures, not repeated measurements.
