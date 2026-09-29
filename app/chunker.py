"""Text chunking strategies: auto, custom, hierarchy.

All strategies return Chunk dataclasses with (index, char_start, char_end, text).

IMPORTANT — spec constraints:
  - body_text MUST remain the original upload (never rewritten).
  - Offsets are relative to the post-preprocessing text for custom strategy.
  - When called via reindex endpoint: delete old chunks + old vectors FIRST,
    then re-chunk + re-embed + re-write.
"""
import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Chunk:
    index: int          # 0..N-1 within this material
    char_start: int     # in the post-preprocessing text (or original for auto/hierarchy)
    char_end: int       # exclusive
    text: str


# ---------------------------------------------------------------------------
# Strategy dispatch
# ---------------------------------------------------------------------------

def chunk(text: str, strategy: Optional[str] = None, options: Optional[dict] = None) -> List[Chunk]:
    """Dispatch to the appropriate strategy. 'auto' is the default when
    strategy is None or unknown, or when custom fields are provided with a
    non-custom strategy (spec line 9: auto MUST ignore caller-provided lengths)."""
    if strategy == "custom":
        return _chunk_custom(text, options or {})
    if strategy == "hierarchy":
        return _chunk_hierarchy(text, options or {})
    # default: auto (covers None, "auto", "hierarchy" fallback, unknown strings)
    return _chunk_auto(text)


# ---------------------------------------------------------------------------
# auto — max 800 chars, overlap 80 chars, prefer breaks at paragraph/newline/period
# ---------------------------------------------------------------------------

AUTO_MAX = 800
AUTO_OVERLAP = 80

_BREAK_RE = re.compile(r"\n\n|\n|[。！？.!?]")


def _find_next_break(text: str, from_idx: int, max_end: int) -> int:
    """Return the end offset of the window starting at from_idx, preferring the
    LAST break point within [from_idx, max_end] so the window fills up to
    max_len before breaking (spec: 最大 800 字窗口 + 优先在空行/换行/句号断开)."""
    segment = text[from_idx:max_end]
    if not segment:
        return max_end
    # Priority: paragraph break, then newline, then sentence-ending punctuation.
    # Within the chosen priority take the LAST occurrence, so the window is filled
    # as much as possible instead of breaking at the first punctuation found.
    for pat in (r"\n\n", r"\n", r"[。！？.!?]"):
        matches = list(re.finditer(pat, segment))
        if matches:
            return from_idx + matches[-1].end()
    # No break found → hard truncate at the window end
    return max_end


def _chunk_auto(text: str, max_len: int = AUTO_MAX, overlap: int = AUTO_OVERLAP) -> List[Chunk]:
    if not text.strip():
        return []
    chunks: List[Chunk] = []
    idx = 0
    pos = 0
    n = len(text)
    while pos < n:
        limit = min(pos + max_len, n)
        if limit >= n:
            # Remaining text fits inside the window → emit it whole, no break needed.
            end = n
        else:
            end = _find_next_break(text, pos, limit)
            if end - pos <= overlap:
                # The break sits too close to the window start (e.g. a lone blank
                # line near the top). Honouring it would leave less than `overlap`
                # of new text, so the next step would rewind into the same region
                # and degenerate into 1-char slivers. Hard-truncate at max_len.
                end = limit
        chunk_text = text[pos:end]
        if chunk_text.strip():
            chunks.append(Chunk(index=idx, char_start=pos, char_end=end, text=chunk_text))
            idx += 1
        if end >= n:
            break
        pos = max(end - overlap, pos + 1)
    return chunks


# ---------------------------------------------------------------------------
# custom — configurable max_len, overlap%, optional preprocessing
# ---------------------------------------------------------------------------

def _chunk_custom(text: str, options: dict) -> List[Chunk]:
    # 1) Preprocessing (only affects chunking input; original body_text untouched)
    work = text
    if options.get("remove_urls", False):
        work = re.sub(r"https?://\S+", "", work)
    if options.get("remove_emails", False):
        work = re.sub(r"\S+@\S+\.\S+", "", work)
    if options.get("collapse_whitespace", False):
        work = re.sub(r"\s{2,}", " ", work).strip()

    # 2) Validate parameters with defaults from spec
    max_len = int(options.get("max_len", 800))
    max_len = max(100, min(2000, max_len))       # clamp [100, 2000]
    overlap_pct = float(options.get("overlap_pct", 0))
    overlap_pct = max(0.0, min(0.5, overlap_pct))  # clamp [0%, 50%]
    overlap_chars = int(max_len * overlap_pct)

    # 3) Split by delimiters (user-provided or default)
    delimiter = options.get("delimiter", None)
    if delimiter:
        # User-provided delimiter (single char/string)
        parts = [p for p in work.split(delimiter) if p.strip()]
    else:
        # Default: paragraph → newline → sentence
        parts = []
        for para in re.split(r"\n\n", work):
            for line in re.split(r"\n", para):
                pieces = re.split(r"(?<=[。！？.!?])\s*", line)
                parts.extend(p for p in pieces if p.strip())

    # 4) Assemble windows respecting max_len
    chunks: List[Chunk] = []
    current_parts: List[str] = []
    current_len = 0

    def flush(parts_list, start_offset):
        nonlocal idx
        if not parts_list:
            return start_offset
        joined = " ".join(parts_list)
        chunks.append(Chunk(index=idx, char_start=start_offset,
                            char_end=start_offset + len(joined), text=joined))
        idx += 1
        # overlap: keep last N chars of the joined window
        if overlap_chars > 0:
            last_text = joined[-overlap_chars:]
            # Find which parts contributed to the overlap region
            remaining = joined[:len(joined) - overlap_chars]
            # drop parts until removing them would drop into overlap zone
            kept = []
            acc = 0
            for p in parts_list:
                if acc + len(p) + (1 if kept else 0) <= len(remaining):
                    kept.append(p)
                    acc += len(p) + (1 if len(kept) > 1 else 0)
                else:
                    break
            if kept and len(remaining) > 0:
                return start_offset + acc
        return start_offset + len(joined)

    idx = 0
    pos = 0   # offset in post-preprocessing text
    pending = ""
    for part in parts:
        plen = len(part) + (1 if current_parts else 0)
        if current_len + plen <= max_len:
            current_parts.append(part)
            current_len += plen
        else:
            # Need to flush — but first check if part itself > max_len (force truncate)
            if plen - (1 if current_parts else 0) > max_len:
                # Truncate this oversized part alone
                if current_parts:
                    pos = flush(current_parts, pos)
                    current_parts = []
                    current_len = 0
                # Hard truncate into max_len windows (spec: 无断点处按最大长度强制截断).
                # Must repeat — a single slice would leave an oversized tail.
                rem = part
                while len(rem) > max_len:
                    chunks.append(Chunk(index=idx, char_start=pos,
                                        char_end=pos + max_len, text=rem[:max_len]))
                    idx += 1
                    pos += max_len
                    rem = rem[max_len:]
                # Keep the tail (<= max_len) as the next pending part
                current_parts = [rem] if rem else []
                current_len = len(rem)
            else:
                pos = flush(current_parts, pos)
                current_parts = [part]
                current_len = plen

    if current_parts:
        flush(current_parts, pos)

    return chunks


# ---------------------------------------------------------------------------
# hierarchy — Markdown #/##/### heading-based; chapters > max_len → auto-windows
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)


def _chunk_hierarchy(text: str, options: Optional[dict] = None) -> List[Chunk]:
    # Find all headings
    headings = [(m.start(), len(m.group(1)), m.group(2)) for m in _HEADING_RE.finditer(text)]

    if not headings:
        # No Markdown headings → auto fallback
        return _chunk_auto(text)

    # Split into chapters — each starts at a heading position, ends at next heading or EOF
    chapters: List[tuple[int, str]] = []  # (start_offset, chapter_text_including_heading)
    for i, (hstart, hlevel, htitle) in enumerate(headings):
        # chapter starts at hstart and ends at next heading start
        if i + 1 < len(headings):
            cend = headings[i + 1][0]
        else:
            cend = len(text)
        chapter_text = text[hstart:cend].rstrip("\n")
        chapters.append((hstart, chapter_text))

    chunks: List[Chunk] = []
    idx = 0

    for cstart, ctext in chapters:
        if len(ctext) <= AUTO_MAX:
            # Chapter fits in one chunk → include heading prefix
            chunks.append(Chunk(index=idx, char_start=cstart,
                                char_end=cstart + len(ctext), text=ctext))
            idx += 1
        else:
            # Chapter too long → split by auto windows (preserve heading at top of each window)
            heading_line = _HEADING_RE.search(ctext).group(0) + "\n\n"
            header_len = len(heading_line)
            body = ctext[_HEADING_RE.search(ctext).end():]  # body after heading

            body_chunks = _chunk_auto(body)
            for bc in body_chunks:
                # Prefix each window chunk with the chapter heading
                full_text = heading_line + bc.text
                chunks.append(Chunk(
                    index=idx,
                    char_start=cstart + header_len + bc.char_start,
                    char_end=cstart + header_len + bc.char_end,
                    text=full_text,
                ))
                idx += 1

    return chunks
