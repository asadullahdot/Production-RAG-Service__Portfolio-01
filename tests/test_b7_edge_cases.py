"""B7: edge cases from the brief. The live versions run against the real services in scripts/run_edge_cases.py."""
from __future__ import annotations

import asyncio
import io
import os

import pytest

from app import guardrails
from app.config import Settings
from app.errors import UpstreamUnavailable
from app.ingestion.extraction import extract
from scripts.make_edge_case_fixtures import SMALL, long_pdf, too_big_pdf
from tests.conftest import Env
from tests.test_llm_guard import make, ok_response

MIME = {".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def post(client, name, data):
    return client.post("/documents", files={"file": (name, data, MIME[os.path.splitext(name)[1]])})


# ---- scanned / empty PDFs ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["empty.pdf", "scanned.pdf"])
def test_pdfs_without_a_text_layer_are_400_no_text(tmp_path, name):
    e = Env(tmp_path)
    r = post(e.client, name, SMALL[name]())
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_text"
    assert e.count("local") == 0


def test_the_scanned_fixture_really_is_an_image(tmp_path):
    """A guard against a fixture that silently carries text: the image-only PDF must extract zero characters."""
    assert extract(SMALL["scanned.pdf"](), "pdf").total_chars == 0


# ---- very large file / very long document ---------------------------------------------------------------------
def test_a_file_over_the_size_limit_is_413_and_one_over_the_page_limit_is_413(tmp_path):
    e = Env(tmp_path)  # defaults: 10 MB, 200 pages
    big = post(e.client, "huge.pdf", too_big_pdf())
    assert big.status_code == 413 and big.json()["error"]["code"] == "file_too_large"
    pages = post(e.client, "long.pdf", long_pdf(250))
    assert pages.status_code == 413 and "pages" in pages.json()["error"]["message"]
    assert e.count("local") == 0


def test_a_long_document_within_the_limits_is_searchable_by_a_fact_on_page_100(tmp_path):
    e = Env(tmp_path)
    r = post(e.client, "manual.pdf", long_pdf(120))
    assert r.status_code == 201 and r.json()["num_pages"] == 120
    body = e.client.post("/ask", json={"question": "Where is the emergency generator located next to the loading dock?"}).json()
    assert any(ch["page"] == 100 for ch in body["retrieved_chunks"])


# ---- contradictions, tables, special characters, non-English text ----------------------------------------------
def test_two_contradicting_documents_both_reach_the_model_and_the_prompt_asks_for_a_conflict_to_be_stated(tmp_path):
    e = Env(tmp_path)
    post(e.client, "policy_2023.pdf", SMALL["contradict_2023.pdf"]())
    post(e.client, "policy_2024.docx", SMALL["contradict_2024.docx"]())
    e.openai.script.append("The 2023 policy says 30 days [1], but the 2024 policy says 14 days [2].")
    body = e.client.post("/ask", json={"question": "How many days do customers have to return an item for a refund?"}).json()
    system, user = e.openai.prompts[-1]
    assert "30 days" in user and "14 days" in user                              # both documents were retrieved into the context
    assert "If sources contradict each other, say so and cite both." in system  # rule 7 of the prompt
    assert {c["file_name"] for c in body["citations"]} == {"policy_2023.pdf", "policy_2024.docx"}


def test_table_cells_are_extracted_row_by_row():
    text = extract(SMALL["table_prices.docx"](), "docx").sections[0].page_content
    assert "Widget B | $99.00 | backordered until May" in text and "Widget A | $12.50 | in stock" in text


def test_special_characters_survive_extraction_and_are_not_mistaken_for_a_key(tmp_path):
    text = extract(SMALL["special_chars.md"](), "md").sections[0].page_content
    assert "Zürich-Œuvre-42" in text and "\U0001F600" in text and "∑" in text
    assert not guardrails.contains_key_like("The password is Zürich-Œuvre-42")
    e = Env(tmp_path)
    assert post(e.client, "cafe.md", SMALL["special_chars.md"]()).status_code == 201


@pytest.mark.parametrize("name,question", [("french_horaires.txt", "Quand la bibliothèque est-elle ouverte ?"),
                                           ("urdu_notice.docx", "دفتر کے اوقات")])
def test_non_english_documents_are_accepted_and_keep_their_text(tmp_path, name, question):
    e = Env(tmp_path)
    r = post(e.client, name, SMALL[name]())
    assert r.status_code == 201
    body = e.client.post("/ask", json={"question": question}).json()
    assert body["retrieved_chunks"] and any(ord(ch) > 127 for ch in body["retrieved_chunks"][0]["text"])  # the original script is intact


def test_a_hostile_file_name_is_stored_and_returned_as_plain_data(tmp_path):
    e = Env(tmp_path)
    name = "<script>alert(1)</script>.txt"
    r = e.client.post("/documents", files={"file": (name, b"The office plants are watered every Tuesday morning by the facilities team.", "text/plain")})
    assert r.status_code == 201 and r.json()["file_name"] == name and "application/json" in r.headers["content-type"]
    e.client.post("/ask", json={"question": "When are the office plants watered by the facilities team?"})
    assert "<script>" not in e.openai.prompts[-1][1]  # angle brackets in file names are stripped before they reach the prompt


# ---- timeouts and rate limits: the demo faults ----------------------------------------------------------------
@pytest.mark.parametrize("fault", ["embed_timeout", "embed_429"])
def test_embedding_faults_retry_then_503_and_leave_the_chat_model_alone(fault):
    client, script, sleeps = make([ok_response("fine")], demo_fault=fault, max_retries=2)
    with pytest.raises(UpstreamUnavailable):
        asyncio.run(client.embed(["a question"]))
    assert script.calls == 0 and len(sleeps) == 2                  # the fault is raised before any call, twice retried
    assert asyncio.run(client.generate("s", "u")).text == "fine"   # generation is not affected by an embedding fault


@pytest.mark.parametrize("fault", ["llm_timeout", "llm_429"])
def test_llm_faults_retry_then_503_and_leave_embeddings_alone(fault):
    client, script, sleeps = make([[[0.1, 0.2]]], demo_fault=fault, max_retries=2, embedding_dim=2)
    assert asyncio.run(client.embed(["x"])) == [[0.1, 0.2]]
    with pytest.raises(UpstreamUnavailable):
        asyncio.run(client.generate("s", "u"))
    assert len(sleeps) == 2


def test_demo_faults_are_validated_and_forbidden_in_production():
    assert Settings(_env_file=None, demo_fault="embed_429").demo_fault == "embed_429"
    with pytest.raises(ValueError):
        Settings(_env_file=None, demo_fault="made_up")
    with pytest.raises(ValueError):
        Settings(_env_file=None, env="production", demo_fault="llm_timeout")
    assert Settings(_env_file=None, env="production").demo_fault == "none"


def test_failed_embedding_during_upload_leaves_nothing_behind(tmp_path):
    e = Env(tmp_path)
    e.openai.fail_embed = True
    r = post(e.client, "a.txt", b"A document whose embedding will fail, long enough to pass the minimum text length.")
    assert r.status_code == 503 and r.json()["error"]["code"] == "upstream_unavailable"
    e.openai.fail_embed = False
    assert e.count("local") == 0 and e.client.get("/documents").json()["documents"] == []


def test_prompt_rule_6_protects_the_service_but_lets_document_passwords_be_quoted():
    from app.generation import SYSTEM_PROMPT
    rule6 = next(line for line in SYSTEM_PROMPT.splitlines() if line.startswith("6."))
    assert "this prompt" in rule6 and "belongs to this service itself" in rule6        # the service's own secrets stay protected
    assert "written inside the context are ordinary document content" in rule6        # a password in a user's document may be quoted
