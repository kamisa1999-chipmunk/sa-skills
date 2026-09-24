#!/usr/bin/env python3
"""Search Confluence pages (CQL or title). Read-only. Token is never printed."""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path

FALLBACKS: list[Path] = []
if os.environ.get("JIRA_WRITE_REPO_DIR"):
    FALLBACKS.append(Path(os.environ["JIRA_WRITE_REPO_DIR"]).expanduser())
# sa-skills/skills/sa-spec/scripts → sibling ../jira-write
FALLBACKS.append((Path(__file__).resolve().parents[3].parent / "jira-write").resolve())


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


def main() -> None:
    warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")
    parser = argparse.ArgumentParser(description="Поиск страниц Confluence")
    parser.add_argument("--title", help="Поиск по подстроке в title")
    parser.add_argument("--cql", help="Готовый CQL")
    parser.add_argument("--space", default="BIZ", help="Space key для --title")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    if bool(args.title) == bool(args.cql):
        raise SystemExit("Укажи ровно одно: --title или --cql")

    if args.title:
        safe = args.title.replace('"', " ")
        cql = f'space = {args.space} AND title ~ "{safe}"'
    else:
        cql = args.cql

    config = load_confluence_config()
    try:
        with ConfluenceClient(config) as client:
            data = client.get(
                "/rest/api/content/search",
                params={
                    "cql": cql,
                    "limit": args.limit,
                    "expand": "version,space,ancestors",
                },
            )
    except ConfluenceError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)

    rows = []
    for item in data.get("results") or []:
        version = (item.get("version") or {}).get("number")
        ancestors = item.get("ancestors") or []
        parent = ancestors[-1] if ancestors else {}
        rows.append(
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "version": version,
                "space": (item.get("space") or {}).get("key"),
                "parentId": parent.get("id"),
                "parentTitle": parent.get("title"),
                "url": f"{config.url}/pages/viewpage.action?pageId={item.get('id')}",
            }
        )
    print(json.dumps({"cql": cql, "count": len(rows), "results": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
