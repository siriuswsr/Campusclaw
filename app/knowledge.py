"""Knowledge file parsing — MVP: .txt and .md only."""
import os

ALLOWED_EXTENSIONS = {".txt", ".md"}


def parse_txt(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def parse_md(path: str) -> str:
    """Return the Markdown body verbatim.

    week04 spec: knowledge_entries.body_text MUST be the untouched upload —
    no preprocessing here (heading markers included). Any cleanup such as
    stripping URLs/emails or collapsing whitespace belongs to the optional
    `custom` chunking strategy and MUST NOT rewrite body_text.
    """
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def parse_file(path: str) -> str:
    """Parse file at path, returning plain text body. Raises ValueError on unsupported format."""
    _, ext = os.path.splitext(path)
    ext = ext.lower()
    if ext == ".txt":
        return parse_txt(path)
    if ext == ".md":
        return parse_md(path)
    raise ValueError(f"unsupported file type: {ext}")
