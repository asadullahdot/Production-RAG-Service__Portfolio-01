"""Thin wrapper over ``langchain-text-splitters`` (PLAN.md §3). Splits per page; never across pages."""
from __future__ import annotations

from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.ingestion.extraction import Section


@dataclass
class ChunkDraft:
    chunk_idx: int
    page: int
    text: str


def chunk_sections(sections: list[Section], chunk_size: int, chunk_overlap: int) -> list[ChunkDraft]:
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    drafts: list[ChunkDraft] = []
    for section in sections:
        if not section.text.strip():
            continue
        for piece in splitter.split_text(section.text):
            piece = piece.strip()
            if piece:
                drafts.append(ChunkDraft(chunk_idx=len(drafts), page=section.page, text=piece))
    return drafts
