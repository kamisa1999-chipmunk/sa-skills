#!/usr/bin/env python3
"""Preview or publish approved SA markdown to Confluence.

Default is preview (no writes). Token is never printed.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

FALLBACKS: list[Path] = []
if os.environ.get("JIRA_WRITE_REPO_DIR"):
    FALLBACKS.append(Path(os.environ["JIRA_WRITE_REPO_DIR"]).expanduser())
# sa-skills/skills/sa-publish/scripts → sibling ../jira-write
FALLBACKS.append((Path(__file__).resolve().parents[3].parent / "jira-write").resolve())

HEADING_RE = re.compile(r"^###\s+(.+?)\s*$")
FIELD_RE = re.compile(
    r"^(Статус|Назначение|Операция|Родительская страница|Название|"
    r"pageId|Версия источника при загрузке|Репозиторий|Путь|"
    r"Тип|Шаблон|"
    r"Дата|Цель|URL/pageId/path|Версия после публикации|Commit)\s*:\s*(.*)$",
    re.I,
)


def find_scripts_dir() -> Path:
    cwd = Path.cwd().resolve()
    seen: list[Path] = []
    for candidate in [cwd, *cwd.parents, *FALLBACKS]:
        if candidate in seen:
            continue
        seen.append(candidate)
        for rel in ("jira/scripts", "scripts"):
            scripts = candidate / rel
            if (scripts / "confluence_client").is_dir():
                return scripts
    raise SystemExit(
        "Не найден confluence_client. Клонируй jira-write и задай "
        "JIRA_WRITE_REPO_DIR=/путь/к/jira-write"
    )


SCRIPTS_DIR = find_scripts_dir()
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from confluence_client import (  # noqa: E402
    ConfluenceClient,
    ConfluenceError,
    load_confluence_config,
)

PUBLISH_DIR = Path(__file__).resolve().parent
if str(PUBLISH_DIR) not in sys.path:
    sys.path.insert(0, str(PUBLISH_DIR))
import confluence_template as ctemplate  # noqa: E402


@dataclass
class PlanItem:
    index: int
    rel_path: str
    fields: dict[str, str] = field(default_factory=dict)
    raw: str = ""

    def get(self, *names: str) -> str:
        lower = {k.lower(): v for k, v in self.fields.items()}
        for name in names:
            value = lower.get(name.lower())
            if value:
                return value.strip()
        return ""

    @property
    def status(self) -> str:
        return self.get("Статус").lower()

    @property
    def target(self) -> str:
        return self.get("Назначение").lower()

    @property
    def operation(self) -> str:
        return self.get("Операция").lower()

    @property
    def page_id(self) -> str:
        raw = self.get("pageId")
        if raw.isdigit():
            return raw
        parent = self.get("Родительская страница")
        match = re.search(r"(\d{5,})", parent)
        return match.group(1) if match else ""

    @property
    def parent_id(self) -> str:
        parent = self.get("Родительская страница")
        match = re.search(r"(\d{5,})", parent)
        return match.group(1) if match else ""

    @property
    def title(self) -> str:
        return self.get("Название")

    @property
    def base_version(self) -> Optional[int]:
        raw = self.get("Версия источника при загрузке")
        if raw.isdigit():
            return int(raw)
        return None

    @property
    def spec_type(self) -> str:
        return ctemplate.infer_type(self.rel_path, self.get("Тип"))

    @property
    def template_id(self) -> str:
        raw = self.get("Шаблон")
        if raw.isdigit():
            return raw
        mapping = ctemplate.load_map()
        info = mapping.get(self.spec_type) or {}
        return str(info.get("templateId") or "")


def parse_plan(text: str) -> list[PlanItem]:
    chunks: list[tuple[str, str]] = []
    current_path = ""
    buf: list[str] = []
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match:
            if current_path:
                chunks.append((current_path, "\n".join(buf)))
            current_path = match.group(1).strip()
            buf = []
            continue
        if current_path:
            buf.append(line)
    if current_path:
        chunks.append((current_path, "\n".join(buf)))

    items: list[PlanItem] = []
    for i, (rel_path, body) in enumerate(chunks, start=1):
        fields: dict[str, str] = {}
        pending_key = ""
        pending_val: list[str] = []
        for line in body.splitlines():
            field_match = FIELD_RE.match(line.strip())
            if field_match:
                if pending_key:
                    fields[pending_key] = "\n".join(pending_val).strip()
                pending_key = field_match.group(1)
                pending_val = [field_match.group(2)]
            elif pending_key and line.strip():
                pending_val.append(line.strip())
        if pending_key:
            fields[pending_key] = "\n".join(pending_val).strip()
        items.append(PlanItem(index=i, rel_path=rel_path, fields=fields, raw=body))
    return items


def md_to_storage(markdown: str) -> str:
    """Pragmatic Markdown → Confluence storage. Not a full CommonMark parser."""
    lines = markdown.splitlines()
    if lines and lines[0].strip() == "---":
        end = None
        for i in range(1, min(len(lines), 40)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is not None:
            lines = lines[end + 1 :]

    html_parts: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            lang = html.escape(line[3:].strip())
            body_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body_lines.append(lines[i])
                i += 1
            code = "\n".join(body_lines).replace("]]>", "]]]]><![CDATA[>")
            html_parts.append(
                '<ac:structured-macro ac:name="code">'
                f'<ac:parameter ac:name="language">{lang}</ac:parameter>'
                f"<ac:plain-text-body><![CDATA[{code}]]></ac:plain-text-body>"
                "</ac:structured-macro>"
            )
            i += 1
            continue
        if re.match(r"^#{1,6}\s+", line):
            hashes, text = line.split(None, 1)
            level = min(len(hashes), 6)
            html_parts.append(f"<h{level}>{inline_md(text)}</h{level}>")
            i += 1
            continue
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(
            r"^\s*\|?\s*:?-{2,}", lines[i + 1]
        ):
            rows = [line]
            i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            html_parts.append(table_to_html(rows))
            continue
        if re.match(r"^\s*[-*]\s+", line):
            items: list[str] = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            lis = "".join(f"<li>{inline_md(item)}</li>" for item in items)
            html_parts.append(f"<ul>{lis}</ul>")
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            lis = "".join(f"<li>{inline_md(item)}</li>" for item in items)
            html_parts.append(f"<ol>{lis}</ol>")
            continue
        if not line.strip():
            i += 1
            continue
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not _starts_block(lines[i]):
            para.append(lines[i])
            i += 1
        html_parts.append(f"<p>{inline_md(' '.join(para))}</p>")
    return "\n".join(html_parts)


def _starts_block(line: str) -> bool:
    return bool(
        line.startswith("```")
        or re.match(r"^#{1,6}\s+", line)
        or line.strip().startswith("|")
        or re.match(r"^\s*[-*]\s+", line)
        or re.match(r"^\s*\d+\.\s+", line)
    )


def inline_md(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2">\1</a>',
        text,
    )
    return text


def table_to_html(rows: list[str]) -> str:
    parsed: list[list[str]] = []
    for row in rows:
        if re.match(r"^\s*\|?\s*:?-{2,}", row):
            continue
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        parsed.append(cells)
    if not parsed:
        return ""
    thead = "".join(f"<th>{inline_md(c)}</th>" for c in parsed[0])
    body = []
    for row in parsed[1:]:
        tds = "".join(f"<td>{inline_md(c)}</td>" for c in row)
        body.append(f"<tr>{tds}</tr>")
    return (
        f'<table class="wrapped"><tbody><tr>{thead}</tr>{"".join(body)}</tbody></table>'
    )


def load_questions_blocking(work: Path) -> list[str]:
    path = work / "questions.md"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=### )", text)
    blocking = []
    for block in blocks:
        status_m = re.search(r"Статус:\s*(.+)", block, re.I)
        type_m = re.search(r"Тип:\s*(.+)", block, re.I)
        title_m = re.search(r"###\s+\S+\s+—\s+(.+)", block)
        status = (status_m.group(1).strip().lower() if status_m else "")
        qtype = (type_m.group(1).strip().lower() if type_m else "")
        if "блокирующ" in qtype and status in {"открыт", "отложен", "требует внешнего уточнения"}:
            blocking.append(title_m.group(1).strip() if title_m else block[:80])
    return blocking


def select_items(items: list[PlanItem], only: Optional[str]) -> list[PlanItem]:
    if not only:
        return items
    wanted = {part.strip() for part in only.split(",") if part.strip()}
    selected = []
    for item in items:
        if str(item.index) in wanted or item.rel_path in wanted or Path(item.rel_path).name in wanted:
            selected.append(item)
    return selected


def get_page(client: ConfluenceClient, page_id: str) -> dict[str, Any]:
    return client.get(
        f"/rest/api/content/{page_id}",
        params={"expand": "version,space,ancestors,body.storage"},
    )


def preview_item(
    work: Path,
    item: PlanItem,
    client: Optional[ConfluenceClient],
    blocking: list[str],
) -> dict[str, Any]:
    file_path = work / item.rel_path
    result: dict[str, Any] = {
        "index": item.index,
        "file": item.rel_path,
        "status": item.get("Статус"),
        "target": item.get("Назначение"),
        "operation": item.get("Операция"),
        "ready": False,
        "skip_reason": "",
        "conflict": None,
    }
    if item.status != "утверждено":
        result["skip_reason"] = f"статус «{item.get('Статус') or '—'}»"
        return result
    if blocking:
        result["skip_reason"] = "есть блокирующие вопросы"
        return result
    if not file_path.is_file():
        result["skip_reason"] = f"файл не найден: {file_path}"
        return result
    if item.target not in {"confluence", "git"}:
        result["skip_reason"] = "цель публикации не определена"
        return result
    if item.operation not in {"создать", "обновить"}:
        result["skip_reason"] = "операция не понятна (нужно: создать / обновить)"
        return result
    if item.target == "git":
        result["ready"] = True
        result["git_repo"] = item.get("Репозиторий")
        result["git_path"] = item.get("Путь")
        result["note"] = "Git этим CLI не публикуется"
        return result
    if item.operation == "создать":
        if not item.parent_id or not item.title:
            result["skip_reason"] = "для создания нужны parent pageId и Название"
            return result
        result["parentId"] = item.parent_id
        result["title"] = item.title
        if not item.template_id:
            result["ready"] = False
            result["skip_reason"] = (
                "для создания нужен тип шаблона (http/grpc/feature/db/kafka/ui/admin)"
            )
            return result
        try:
            template = ctemplate.fetch_template(client, item.template_id)
        except Exception as exc:
            result["ready"] = False
            result["skip_reason"] = f"не удалось получить шаблон {item.template_id}: {exc}"
            return result
        result["ready"] = True
        result["templateId"] = template.get("templateId")
        result["templateName"] = template.get("name")
        result["templateSource"] = template.get("source")
        return result
    if not item.page_id:
        result["skip_reason"] = "для обновления нужен pageId"
        return result
    if client is None:
        result["skip_reason"] = "нет клиента Confluence"
        return result
    page = get_page(client, item.page_id)
    current = (page.get("version") or {}).get("number")
    result["pageId"] = item.page_id
    result["title"] = page.get("title") or item.title
    result["current_version"] = current
    result["base_version"] = item.base_version
    if item.base_version is not None and current is not None and int(current) != item.base_version:
        result["conflict"] = {
            "base": item.base_version,
            "current": current,
        }
        result["skip_reason"] = (
            f"локальная база: версия {item.base_version}; "
            f"текущая Confluence: версия {current}"
        )
        return result
    result["ready"] = True
    return result


def apply_confluence(
    work: Path,
    item: PlanItem,
    client: ConfluenceClient,
    config_url: str,
) -> dict[str, Any]:
    markdown = (work / item.rel_path).read_text(encoding="utf-8")
    if item.operation == "создать":
        parent = get_page(client, item.parent_id)
        space_key = (parent.get("space") or {}).get("key")
        template = ctemplate.fetch_template(client, item.template_id)
        extra_labels = []
        if item.spec_type == "http" and "admin" in item.rel_path.lower():
            extra_labels.append("admin")
        filled = ctemplate.fill_storage(template["body"], markdown, md_to_storage)
        created = ctemplate.create_page_from_template(
            client,
            parent_id=item.parent_id,
            title=item.title,
            space_key=space_key,
            template={**template, "body": filled["storage"]},
            extra_labels=extra_labels,
        )
        page_id = created.get("id")
        version = (created.get("version") or {}).get("number")
        return {
            "pageId": page_id,
            "title": created.get("title"),
            "version": version,
            "url": f"{config_url}/pages/viewpage.action?pageId={page_id}",
            "templateId": template.get("templateId"),
            "templateName": template.get("name"),
            "filled": {
                "details": filled["filled_details"],
                "expands": filled["filled_expands"],
                "blocks": filled["filled_blocks"],
                "headings": filled.get("filled_headings") or [],
            },
        }
    template = None
    if item.template_id:
        template = ctemplate.fetch_template(client, item.template_id)
    page = get_page(client, item.page_id)
    base = template["body"] if template and template.get("body") else (
        ((page.get("body") or {}).get("storage") or {}).get("value") or ""
    )
    filled = ctemplate.fill_storage(base, markdown, md_to_storage)
    updated = ctemplate.update_page_body(
        client, page, filled["storage"], title=item.title or page.get("title")
    )
    version = (updated.get("version") or {}).get("number")
    return {
        "pageId": item.page_id,
        "title": updated.get("title"),
        "version": version,
        "url": f"{config_url}/pages/viewpage.action?pageId={item.page_id}",
        "filled": {
            "details": filled["filled_details"],
            "expands": filled["filled_expands"],
            "blocks": filled["filled_blocks"],
        },
    }


def patch_plan_text(text: str, item: PlanItem, published: dict[str, Any]) -> str:
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    extra = (
        f"\nСтатус: опубликовано\n"
        f"Дата: {stamp}\n"
        f"Цель: Confluence\n"
        f"URL/pageId/path: {published.get('url')} (pageId={published.get('pageId')})\n"
        f"Версия после публикации: {published.get('version')}\n"
    )
    heading = f"### {item.rel_path}"
    parts = text.split(heading)
    if len(parts) < 2:
        return text
    rest = parts[1]
    next_h = re.search(r"\n### ", rest)
    body = rest[: next_h.start()] if next_h else rest
    tail = rest[next_h.start() :] if next_h else ""
    body = re.sub(r"(?im)^Статус:\s*.*$", "Статус: опубликовано", body, count=1)
    if "Версия после публикации:" not in body:
        body = body.rstrip() + extra + "\n"
    return parts[0] + heading + body + tail


def format_human(rows: list[dict[str, Any]]) -> str:
    ready = [r for r in rows if r.get("ready")]
    skipped = [r for r in rows if not r.get("ready")]
    lines = [f"К публикации готово {len(ready)} артефактов:\n"]
    for row in ready:
        lines.append(f"{row['index']}. {Path(row['file']).name}")
        lines.append(f"   → {row.get('target')}")
        if (row.get("target") or "").lower() == "git":
            lines.append(f"   → {row.get('git_path')}")
            lines.append(f"   → {row.get('operation')}")
        elif row.get("operation") == "создать":
            tmpl = row.get("templateName") or "шаблон"
            lines.append(f"   → создать из шаблона {tmpl}")
            lines.append(f"   → затем заполнить секции: {row.get('title')}")
        else:
            lines.append(
                f"   → обновить pageId={row.get('pageId')} "
                f"(версия {row.get('current_version')})"
            )
        lines.append("")
    if skipped:
        lines.append("Пропущено:")
        for row in skipped:
            reason = row.get("skip_reason") or "—"
            conflict = row.get("conflict")
            name = Path(row["file"]).name
            lines.append(f"- {name} — {reason}")
            if conflict:
                lines.append(
                    f"  Страница изменилась после начала работы. "
                    f"Локальная база: версия {conflict['base']}. "
                    f"Текущая Confluence: версия {conflict['current']}. "
                    f"Публикация этого файла остановлена."
                )
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")
    parser = argparse.ArgumentParser(
        description="Публикация SA-артефактов в Confluence (по умолчанию preview)"
    )
    parser.add_argument("--work", required=True, help="sa-work/<ISSUE-KEY>")
    parser.add_argument("--only", help="Номера из preview или пути файлов, через запятую")
    parser.add_argument("--apply", action="store_true", help="Реально создать/обновить страницы")
    parser.add_argument("--json", action="store_true", help="Машинный вывод")
    args = parser.parse_args()

    work = Path(args.work).expanduser().resolve()
    plan_path = work / "publish-plan.md"
    if not plan_path.is_file():
        raise SystemExit(f"Нет файла {plan_path}")

    items = parse_plan(plan_path.read_text(encoding="utf-8"))
    items = select_items(items, args.only)
    if args.only and not items:
        raise SystemExit("Ничего не совпало с --only")

    blocking = load_questions_blocking(work)
    need_confluence = any(item.target == "confluence" for item in items)
    rows: list[dict[str, Any]] = []
    published: list[dict[str, Any]] = []
    errors: list[str] = []
    config = None
    client_cm = None
    client = None
    if need_confluence:
        config = load_confluence_config()
        client_cm = ConfluenceClient(config)
        client = client_cm.__enter__()
    try:
        for item in items:
            row = preview_item(
                work,
                item,
                client if item.target == "confluence" else None,
                blocking,
            )
            rows.append(row)
            if args.apply and row.get("ready") and item.target == "confluence":
                try:
                    result = apply_confluence(work, item, client, config.url)
                    row["published"] = result
                    published.append({"file": item.rel_path, **result})
                    plan_path.write_text(
                        patch_plan_text(
                            plan_path.read_text(encoding="utf-8"), item, result
                        ),
                        encoding="utf-8",
                    )
                except ConfluenceError as exc:
                    errors.append(f"{item.rel_path}: {exc}")
                    row["error"] = str(exc)
    finally:
        if client_cm is not None:
            client_cm.__exit__(None, None, None)

    payload = {
        "work": str(work),
        "apply": bool(args.apply),
        "blocking_questions": blocking,
        "items": rows,
        "published": published,
        "errors": errors,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_human(rows))
        if args.apply:
            print(f"\nОпубликовано: {len(published)}")
            print(f"Ошибок: {len(errors)}")
            for err in errors:
                print(f"- {err}")
        else:
            print("\nЭто preview. Для записи добавь --apply после подтверждения.")


if __name__ == "__main__":
    main()
