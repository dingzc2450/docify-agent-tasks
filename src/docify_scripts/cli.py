from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_FILES = {
    "task": ("tasks.md", "# Tasks\n\n"),
    "issue": ("issues.md", "# Docify Issues\n\n"),
    "highlight": ("highlights.md", "# Key Highlights\n\n"),
}


@dataclass(frozen=True)
class MarkdownStore:
    root: Path

    @property
    def md_root(self) -> Path:
        return self.root / "md"

    def init_files(self) -> list[Path]:
        self.md_root.mkdir(parents=True, exist_ok=True)
        created = []
        for filename, header in DEFAULT_FILES.values():
            path = self.md_root / filename
            if not path.exists():
                path.write_text(header, encoding="utf-8")
                created.append(path)
        return created

    def file_for(self, item_type: str) -> Path:
        filename, _ = DEFAULT_FILES[item_type]
        path = self.md_root / filename
        if not path.exists():
            self.init_files()
        return path


def append_entry(store: MarkdownStore, item_type: str, title: str, content: str | None) -> Path:
    path = store.file_for(item_type)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    if item_type == "task":
        line = f"- [ ] {timestamp} | {title}\n"
        if content:
            line += f"  - {content}\n"
    else:
        line = f"- **{timestamp} | {title}**"
        if content:
            line += f": {content}"
        line += "\n"

    with path.open("a", encoding="utf-8") as f:
        f.write(line)
    return path


def list_entries(store: MarkdownStore, item_type: str) -> str:
    path = store.file_for(item_type)
    return path.read_text(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Docify markdown tasks, issues, and highlights")
    parser.add_argument("--root", default=".", help="Project root path (default: current directory)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialize markdown management files")

    add_parser = subparsers.add_parser("add", help="Add a task/issue/highlight entry")
    add_parser.add_argument("type", choices=DEFAULT_FILES.keys())
    add_parser.add_argument("title", help="Entry title")
    add_parser.add_argument("--content", default=None, help="Optional detailed content")

    list_parser = subparsers.add_parser("list", help="Print one markdown file content")
    list_parser.add_argument("type", choices=DEFAULT_FILES.keys())

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    store = MarkdownStore(root=Path(args.root).resolve())

    if args.command == "init":
        created = store.init_files()
        if created:
            print("Created files:")
            for file in created:
                print(f"- {file}")
        else:
            print("All markdown files already exist.")
        return

    if args.command == "add":
        path = append_entry(store, args.type, args.title, args.content)
        print(f"Added to {path}")
        return

    if args.command == "list":
        print(list_entries(store, args.type), end="")
        return


if __name__ == "__main__":
    main()
