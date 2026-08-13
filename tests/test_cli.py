from pathlib import Path

from docify_scripts.cli import MarkdownStore, append_entry


def test_init_files_creates_markdown_files(tmp_path: Path) -> None:
    store = MarkdownStore(root=tmp_path)

    created = store.init_files()

    assert len(created) == 3
    assert (tmp_path / "md" / "tasks.md").exists()
    assert (tmp_path / "md" / "issues.md").exists()
    assert (tmp_path / "md" / "highlights.md").exists()


def test_append_task_with_content(tmp_path: Path) -> None:
    store = MarkdownStore(root=tmp_path)
    append_entry(store, "task", "整理文档", "补充截图")

    content = (tmp_path / "md" / "tasks.md").read_text(encoding="utf-8")
    assert "整理文档" in content
    assert "补充截图" in content


def test_append_issue_without_content(tmp_path: Path) -> None:
    store = MarkdownStore(root=tmp_path)
    append_entry(store, "issue", "目录加载慢", None)

    content = (tmp_path / "md" / "issues.md").read_text(encoding="utf-8")
    assert "目录加载慢" in content
