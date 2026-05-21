import re
from config import MAX_PAGES_PER_CHUNK

# Matches both standard headers:  ## PAGE N ##
# and annotated headers:           ## PAGE N -- FORM_8130-3 ##
_PAGE_HEADER_RE = re.compile(
    r"#{64}\n##\s+PAGE\s+(\d+)(?:\s*--[^#]*)?\s*##\n#{64}",
    re.MULTILINE,
)


def parse_pages(raw_text: str) -> list[tuple[int, str]]:
    """Return list of (page_number, page_text) for all pages in the document."""
    matches = list(_PAGE_HEADER_RE.finditer(raw_text))
    if not matches:
        return []

    pages = []
    for i, match in enumerate(matches):
        page_number = int(match.group(1))
        block_start = match.start()
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
        page_text = raw_text[block_start:block_end]
        pages.append((page_number, page_text))

    return pages


def build_chunks(
    pages: list[tuple[int, str]], max_pages: int = MAX_PAGES_PER_CHUNK
) -> list[dict]:
    """Group pages into chunks. Each chunk dict has: chunk_index, start_page, end_page, text."""
    if not pages:
        return []

    chunks = []
    chunk_index = 0

    for i in range(0, len(pages), max_pages):
        slice_ = pages[i : i + max_pages]
        start_page = slice_[0][0]
        end_page = slice_[-1][0]
        text = "".join(page_text for _, page_text in slice_)
        chunks.append(
            {
                "chunk_index": chunk_index,
                "start_page": start_page,
                "end_page": end_page,
                "text": text,
            }
        )
        chunk_index += 1

    return chunks
