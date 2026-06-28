# src/cascade_parser.py
"""Cascade log pre-parsing.

Splits massive raw log files into manageable, overlapping chunks so they
can be routed to an LLM without exceeding context limits.
"""

from __future__ import annotations


def cascade_chunk_logs(
    raw_logs: str, max_lines: int = 150, overlap: int = 20
) -> list[str]:
    """Mandatory Cascade Methodology: Pre-parses massive log files into
    manageable, overlapping chunks for LLM processing.

    Args:
        raw_logs (str): The massive raw log string from GitHub.
        max_lines (int): Number of log lines per chunk.
        overlap (int): Number of lines to overlap between chunks to preserve
            context (e.g., split stack traces).

    Returns:
        list[str]: A list of string chunks ready for model routing.
    """
    if not raw_logs:
        return []

    if overlap >= max_lines:
        raise ValueError("Overlap must be strictly less than max_lines")

    lines = raw_logs.splitlines()
    chunks: list[str] = []

    # Sliding window approach for log chunking
    for i in range(0, len(lines), max_lines - overlap):
        chunk_lines = lines[i : i + max_lines]

        # Add chunk metadata for the LLM
        chunk_header = f"--- LOG CHUNK {len(chunks) + 1} ---\n"
        chunk_text = chunk_header + "\n".join(chunk_lines)
        chunks.append(chunk_text)

        # Break if we've reached the end of the logs
        if i + max_lines >= len(lines):
            break

    return chunks
