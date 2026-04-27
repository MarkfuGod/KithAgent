"""Deterministic text chunking for local RAG."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def chunk_text(
    text: str,
    *,
    path: str = "",
    chunk_size_chars: int = 1600,
    chunk_overlap_chars: int = 250,
) -> list[dict[str, Any]]:
    """Split text into overlapping, line-aware chunks.

    The chunker favors predictable local behavior over clever parsing. It
    preserves line numbers for citations and never emits empty chunks.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []

    chunk_size_chars = max(400, int(chunk_size_chars or 1600))
    chunk_overlap_chars = max(0, min(int(chunk_overlap_chars or 0), chunk_size_chars // 2))

    lines = text.split("\n")
    chunks: list[dict[str, Any]] = []
    start_idx = 0

    while start_idx < len(lines):
        char_count = 0
        end_idx = start_idx
        while end_idx < len(lines):
            next_len = len(lines[end_idx]) + 1
            if end_idx > start_idx and char_count + next_len > chunk_size_chars:
                break
            char_count += next_len
            end_idx += 1

        content = "\n".join(lines[start_idx:end_idx]).strip()
        if content:
            chunks.append({
                "chunk_index": len(chunks),
                "start_line": start_idx + 1,
                "end_line": end_idx,
                "content": content,
                "metadata": {
                    "path_name": Path(path).name if path else "",
                    "heading": _nearest_heading(lines, start_idx),
                },
            })

        if end_idx >= len(lines):
            break

        overlap_count = 0
        next_start = end_idx
        while next_start > start_idx and overlap_count < chunk_overlap_chars:
            next_start -= 1
            overlap_count += len(lines[next_start]) + 1
        start_idx = max(next_start, start_idx + 1)

    return chunks


def _nearest_heading(lines: list[str], start_idx: int) -> str:
    for idx in range(start_idx, max(-1, start_idx - 30), -1):
        line = lines[idx].strip()
        if line.startswith("#"):
            return line[:120]
    return ""
