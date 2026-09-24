#!/usr/bin/env python3
"""Create a Confluence page from a BIZ space template, then fill sections.

Does not print tokens. Template chrome (info, details, linkgraph, plantuml)
is kept; placeholders and named blocks are replaced from Markdown.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

SKILL_DIR = Path(__file__).resolve().parent.parent
MAP_PATH = SKILL_DIR / "space-templates.json"
SNAPSHOT_DIR = SKILL_DIR / "templates"

TH_TO_META = (
    ("метод", ("метод", "method")),
    ("путь/url", ("путь", "url", "path")),
    ("описание", ("описание", "description")),
    ("swagger", ("swagger",)),
    ("proto", ("proto",)),
    ("примечание", ("примечание", "note")),
    ("структура бд", ("бд", "коллекция", "db")),
    ("структура топиков", ("топики", "kafka")),
    ("фичи", ("фичи", "features")),
    ("задачи", ("задачи", "jira")),
    ("команда", ("команда", "team")),
)

PATH_TYPE = (
    ("/http/", "http"),
    ("/grpc/", "grpc"),
    ("/kafka/", "kafka"),
    ("/db/", "db"),
    ("/ui/", "ui"),
    ("/admin/", "admin"),
    ("feature.md", "feature"),
)


def load_map() -> dict[str, Any]:
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


def infer_type(rel_path: str, explicit: str = "") -> str:
    if explicit:
        return explicit.strip().lower()
    lowered = rel_path.replace("\\", "/").lower()
    for needle, kind in PATH_TYPE:
        if needle in lowered:
            return kind
    return ""


def load_snapshot(template_id: str) -> Optional[dict[str, Any]]:
    path = SNAPSHOT_DIR / f"{template_id}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_template(client: Any, template_id: str) -> dict[str, Any]:
    try:
        data = client.get(f"/rest/experimental/template/{template_id}")
        body = ((data.get("body") or {}).get("storage") or {}).get("value") or ""
        labels = [
            item.get("name")
            for item in (data.get("labels") or [])
            if item.get("name")
        ]
        return {
            "templateId": str(data.get("templateId") or template_id),
            "name": data.get("name") or "",
            "body": body,
            "labels": labels,
            "source": "confluence",
        }
    except Exception:
        snap = load_snapshot(template_id)
        if not snap or not snap.get("body"):
            raise
        snap["source"] = "snapshot"
        return snap


SECTION_ALIASES = {
    "ответственный": "команда",
    "команда": "ответственный",
    "swagger url": "swagger",
    "swagger": "swagger url",
    "путь/url": "путь",
    "маппинг параметров": "маппинг параметров (опционально)",
    "маппинг параметров (опционально)": "маппинг параметров",
    "бизнес схема (user flow)": "бизнес схема",
    "схема взаимодействия между компонентами или слоями (front - plaid - magento - pi": "схема взаимодействия",
}


def parse_markdown(markdown: str) -> tuple[dict[str, str], dict[str, str]]:
    lines = markdown.splitlines()
    meta: dict[str, str] = {}
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, min(len(lines), 80)):
            if lines[i].strip() == "---":
                for raw in lines[1:i]:
                    if ":" not in raw:
                        continue
                    key, val = raw.split(":", 1)
                    meta[key.strip().lower()] = val.strip()
                start = i + 1
                break
    sections: dict[str, list[str]] = {}
    current = "_lead"
    sections[current] = []
    heading = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    for line in lines[start:]:
        match = heading.match(line)
        if match:
            current = _norm(match.group(2))
            sections.setdefault(current, [])
            continue
        sections[current].append(line)
    text_sections = {key: "\n".join(vals).strip() for key, vals in sections.items() if "".join(vals).strip()}
    return meta, text_sections


def _norm(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"[*#]", "", text)
    text = re.sub(r"\s*[-–—]\s*", "-", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _meta_value(meta: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        if meta.get(key):
            return meta[key]
    return ""


def _lookup(
    label: str,
    meta: dict[str, str],
    sections: dict[str, str],
) -> tuple[str, str]:
    """Return (section_or_meta_key, markdown_or_plain)."""
    candidates = [label]
    alias = SECTION_ALIASES.get(label)
    if alias:
        candidates.append(alias)
    for key, val in meta.items():
        if _norm(key) in candidates or label in _norm(key) or _norm(key) in label:
            if val:
                return key, val
    for key in candidates:
        if key in sections:
            return key, sections[key]
    for key, val in sections.items():
        if key in {"_lead"} or key.startswith("[draft]"):
            continue
        if label == key or label in key or key in label:
            return key, val
    for th_key, meta_keys in TH_TO_META:
        if th_key in label or label in th_key:
            value = _meta_value(meta, meta_keys)
            if value:
                return th_key, value
            for mk in meta_keys:
                if mk in sections:
                    return mk, sections[mk]
    return "", ""


def _collect_related(title: str, sections: dict[str, str]) -> tuple[str, list[str]]:
    keys: list[str] = []
    parts: list[str] = []
    prefix = title.split()[0] if title else ""
    for key, val in sections.items():
        if key in {"_lead"} or key.startswith("[draft]") or key.startswith("post ") or key.startswith("get "):
            continue
        related = key == title or (prefix in {"запрос", "ответ", "примеры"} and key.startswith(prefix))
        if related:
            keys.append(key)
            heading = "" if key == title else f"### {key}\n\n"
            parts.append(heading + val)
    return "\n\n".join(parts).strip(), keys


def fill_details_placeholders(
    storage: str,
    meta: dict[str, str],
    sections: dict[str, str],
    md_to_storage: Callable[[str], str],
) -> tuple[str, list[str], list[str]]:
    filled_labels: list[str] = []
    consumed: list[str] = []
    for start, end, block in reversed(iter_macros(storage, "details")):
        def repl_tr(match: re.Match[str]) -> str:
            row = match.group(0)
            if 'ac:name="linkgraph"' in row or 'ac:name="expand"' in row:
                return row
            th = re.search(r"<th\b[^>]*>(.*?)</th>", row, re.I | re.S)
            if not th:
                return row
            label = _norm(th.group(1))
            key, value = _lookup(label, meta, sections)
            if not value:
                return row
            inner = (
                md_to_storage(value)
                if "\n" in value or value.startswith("|") or "[" in value
                else _plain_to_storage(value)
            )
            if re.search(r"<ac:placeholder\b", row, re.I):
                new_row, n = re.subn(
                    r"<ac:placeholder\b[^>]*>.*?</ac:placeholder>",
                    inner,
                    row,
                    count=1,
                    flags=re.I | re.S,
                )
                if n:
                    filled_labels.append(label)
                    if key:
                        consumed.append(key)
                    return new_row
            new_row, n = re.subn(
                r"(<td\b[^>]*>)(.*?)(</td>)",
                rf"\1{inner}\3",
                row,
                count=1,
                flags=re.I | re.S,
            )
            if n:
                filled_labels.append(label)
                if key:
                    consumed.append(key)
                return new_row
            return row

        new_block = re.sub(r"<tr\b[^>]*>.*?</tr>", repl_tr, block, flags=re.I | re.S)
        storage = storage[:start] + new_block + storage[end:]
    return storage, filled_labels, consumed


def _plain_to_storage(value: str) -> str:
    if re.search(r"https?://", value) or value.startswith("CAT2-"):
        from html import escape

        return f"<p>{escape(value)}</p>"
    from html import escape

    return f"<p>{escape(value)}</p>"


def iter_macros(storage: str, name: str) -> list[tuple[int, int, str]]:
    needle = f'<ac:structured-macro ac:name="{name}"'
    found: list[tuple[int, int, str]] = []
    start = 0
    while True:
        i = storage.find(needle, start)
        if i < 0:
            break
        j = _macro_end(storage, i)
        if j < 0:
            break
        found.append((i, j, storage[i:j]))
        start = j
    return found


def _macro_end(storage: str, start: int) -> int:
    depth = 0
    pos = start
    open_tag = "<ac:structured-macro"
    close_tag = "</ac:structured-macro>"
    while pos < len(storage):
        nxt_open = storage.find(open_tag, pos)
        nxt_close = storage.find(close_tag, pos)
        if nxt_close < 0:
            return -1
        if nxt_open >= 0 and nxt_open < nxt_close:
            depth += 1
            pos = nxt_open + len(open_tag)
            continue
        depth -= 1
        pos = nxt_close + len(close_tag)
        if depth == 0:
            return pos
    return -1


def fill_expands(
    storage: str,
    sections: dict[str, str],
    md_to_storage: Callable[[str], str],
) -> tuple[str, list[str]]:
    filled: list[str] = []
    pieces: list[str] = []
    cursor = 0
    for start, end, block in iter_macros(storage, "expand"):
        pieces.append(storage[cursor:start])
        title_m = re.search(
            r'<ac:parameter ac:name="title">([^<]*)</ac:parameter>', block
        )
        title = _norm(title_m.group(1)) if title_m else ""
        body_md, keys = _collect_related(title, sections)
        if not body_md:
            body_md = _section_for(title, sections)
            keys = [title] if body_md else []
        if body_md:
            inner = md_to_storage(body_md)
            block = re.sub(
                r"(<ac:rich-text-body>)(.*?)(</ac:rich-text-body>)",
                rf"\1{inner}\3",
                block,
                count=1,
                flags=re.S,
            )
            filled.extend(keys or [title])
        pieces.append(block)
        cursor = end
    pieces.append(storage[cursor:])
    return "".join(pieces), filled


def fill_named_blocks(
    storage: str,
    sections: dict[str, str],
    md_to_storage: Callable[[str], str],
) -> tuple[str, list[str]]:
    filled: list[str] = []
    pattern = re.compile(
        r"(<p\b[^>]*>\s*<strong>(.*?)</strong>.*?</p>)"
        r"(\s*(?:<blockquote>.*?</blockquote>|<table\b.*?</table>))",
        re.I | re.S,
    )

    def repl(match: re.Match[str]) -> str:
        title = _norm(match.group(2))
        body_md, keys = _collect_related(title, sections)
        if not body_md:
            body_md = _section_for(title, sections)
            keys = [title] if body_md else []
        if not body_md:
            return match.group(0)
        filled.extend(keys)
        inner = md_to_storage(body_md)
        return match.group(1) + inner

    return pattern.sub(repl, storage), filled


def _section_for(title: str, sections: dict[str, str]) -> str:
    if title in sections:
        return sections[title]
    for key, val in sections.items():
        if key == "_lead":
            continue
        if title in key or key in title:
            return val
    return ""


def strip_create_hints(storage: str) -> str:
    for start, end, block in iter_macros(storage, "warning"):
        if "удали подсказку" in block.lower() or "обязательно добавить еще метку" in block.lower():
            storage = storage[:start] + storage[end:]
            break
    return storage


def set_method_heading(storage: str, meta: dict[str, str]) -> str:
    method = meta.get("метод") or meta.get("method") or ""
    path = meta.get("путь") or meta.get("url") or meta.get("path") or ""
    if not (method or path):
        return storage
    title = " ".join(part for part in (method, path) if part)
    storage = re.sub(
        r"(<h2>\s*<strong>)GET /URL(</strong>\s*</h2>)",
        rf"\1{title}\2",
        storage,
        count=1,
        flags=re.I,
    )
    return storage


def fill_heading_slots(
    storage: str,
    sections: dict[str, str],
    md_to_storage: Callable[[str], str],
) -> tuple[str, list[str]]:
    heading_re = re.compile(r"<h([1-3])\b[^>]*>(.*?)</h\1>", re.I | re.S)
    matches = list(heading_re.finditer(storage))
    filled: list[str] = []
    for i in range(len(matches) - 1, -1, -1):
        match = matches[i]
        title = _norm(match.group(2))
        body_md = _section_for(title, sections)
        if not body_md:
            continue
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(storage)
        chunk = storage[start:end]
        stripped = chunk.lstrip()
        if stripped.startswith("<ac:structured-macro"):
            continue
        inner = md_to_storage(body_md)
        ph = re.compile(
            r"^\s*(?:<p\b[^>]*>\s*(?:<span\b[^>]*>)?\s*<ac:placeholder\b[^>]*>"
            r".*?</ac:placeholder>\s*(?:</span>)?\s*</p>\s*)+",
            re.I | re.S,
        )
        ph_match = ph.match(chunk)
        stripped = chunk.lstrip()
        if stripped.startswith("<ac:structured-macro") and 'ac:name="details"' in stripped[:120]:
            continue
        if ph_match:
            chunk = inner + chunk[ph_match.end() :]
        else:
            chunk = inner + chunk
        storage = storage[:start] + chunk + storage[end:]
        filled.append(title)
    return storage, filled


def fill_storage(
    storage: str,
    markdown: str,
    md_to_storage: Callable[[str], str],
) -> dict[str, Any]:
    meta, sections = parse_markdown(markdown)
    storage = strip_create_hints(storage)
    storage = set_method_heading(storage, meta)
    storage, details, consumed_details = fill_details_placeholders(
        storage, meta, sections, md_to_storage
    )
    storage, expands = fill_expands(storage, sections, md_to_storage)
    storage, blocks = fill_named_blocks(storage, sections, md_to_storage)
    storage, headings = fill_heading_slots(storage, sections, md_to_storage)
    return {
        "storage": storage,
        "filled_details": details,
        "filled_expands": expands,
        "filled_blocks": blocks,
        "filled_headings": headings,
        "consumed": sorted(set(consumed_details + expands + blocks + headings)),
        "meta": meta,
    }


def create_page_from_template(
    client: Any,
    *,
    parent_id: str,
    title: str,
    space_key: str,
    template: dict[str, Any],
    extra_labels: Optional[list[str]] = None,
) -> dict[str, Any]:
    labels = list(template.get("labels") or [])
    for name in extra_labels or []:
        if name not in labels:
            labels.append(name)
    payload: dict[str, Any] = {
        "type": "page",
        "title": title,
        "space": {"key": space_key},
        "ancestors": [{"id": parent_id}],
        "body": {
            "storage": {
                "value": template["body"],
                "representation": "storage",
            }
        },
    }
    if labels:
        payload["metadata"] = {"labels": [{"name": name} for name in labels]}
    return client.post("/rest/api/content", json_body=payload)


def update_page_body(
    client: Any,
    page: dict[str, Any],
    storage: str,
    title: Optional[str] = None,
) -> dict[str, Any]:
    page_id = page["id"]
    current = int((page.get("version") or {}).get("number") or 1)
    return client.put(
        f"/rest/api/content/{page_id}",
        json_body={
            "id": page_id,
            "type": "page",
            "title": title or page.get("title"),
            "version": {"number": current + 1},
            "body": {
                "storage": {"value": storage, "representation": "storage"}
            },
        },
    )
