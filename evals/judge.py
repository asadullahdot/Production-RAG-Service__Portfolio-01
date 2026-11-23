"""Own LLM-as-judge (PLAN.md §12): faithfulness over tagged claims, correctness 0/0.5/1, decline detection.

One judge call per answer returns JSON. The judge model differs from (and is larger than) the generator.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

JUDGE_SYSTEM = """You are a strict evaluator of answers produced by a document question-answering system.
You receive: the QUESTION, the numbered CONTEXT chunks the system saw, the SYSTEM ANSWER, and a REFERENCE ANSWER (may be 'NONE: the documents do not contain the answer').

Do the following and reply with ONE JSON object only, no prose:
1. "claims": split the SYSTEM ANSWER into atomic factual claims. For each give
   {"text": str, "kind": "document" | "general", "supported": true | false}.
   kind "document" = a claim that the answer presents as coming from the documents (usually cited like [1]).
   kind "general" = general knowledge or connective explanation not attributed to the documents.
   "supported" (only meaningful for kind "document") = true only if the CONTEXT chunks actually state or directly imply it.
2. "correctness": compare the SYSTEM ANSWER with the REFERENCE ANSWER. 1 = fully correct and complete, 0.5 = partly correct or incomplete, 0 = wrong, contradicting, or missing.
3. "declines": true if the SYSTEM ANSWER says the information is not available / cannot be answered from the documents.
Be literal. Do not reward fluent text that is not supported by the CONTEXT."""


@dataclass
class JudgeVerdict:
    claims: list[dict] = field(default_factory=list)
    correctness: float = 0.0
    declines: bool = False
    parse_error: bool = False

    @property
    def document_claims(self) -> list[dict]:
        return [c for c in self.claims if c.get("kind") == "document"]

    @property
    def faithfulness(self) -> float | None:
        """Share of document claims supported by the context; None when there are no document claims."""
        docs = self.document_claims
        if not docs:
            return None
        return sum(1 for c in docs if c.get("supported")) / len(docs)


def build_judge_prompt(question: str, chunks: list[dict], answer: str, reference: str | None) -> str:
    ctx = "\n\n".join(f"[{c['n']}] ({c['file_name']}, page {c['page']}) {c['text']}" for c in chunks) or "(no context was sent)"
    ref = reference if reference else "NONE: the documents do not contain the answer"
    return f"QUESTION:\n{question}\n\nCONTEXT:\n{ctx}\n\nSYSTEM ANSWER:\n{answer}\n\nREFERENCE ANSWER:\n{ref}"


def parse_verdict(text: str) -> JudgeVerdict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return JudgeVerdict(parse_error=True)
    try:
        data = json.loads(match.group(0))
        correctness = float(data.get("correctness", 0))
        correctness = min(1.0, max(0.0, round(correctness * 2) / 2))
        claims = [c for c in data.get("claims", []) if isinstance(c, dict)]
        return JudgeVerdict(claims=claims, correctness=correctness, declines=bool(data.get("declines", False)))
    except (ValueError, TypeError):
        return JudgeVerdict(parse_error=True)


async def judge_answer(client, question: str, chunks: list[dict], answer: str, reference: str | None) -> JudgeVerdict:
    """``client`` is a (cached) OpenAI wrapper exposing ``judge(system, user)``. One retry on unparseable output."""
    prompt = build_judge_prompt(question, chunks, answer, reference)
    verdict = JudgeVerdict(parse_error=True)
    for attempt in range(2):
        nudge = "" if attempt == 0 else "\n\nReply with the JSON object only."
        res = await client.judge(JUDGE_SYSTEM, prompt + nudge)
        verdict = parse_verdict(res.text)
        if not verdict.parse_error:
            break
    return verdict
