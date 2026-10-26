"""Watch an existing batch without submitting or restarting transcription."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.table import Table

from b2t.i18n import tr


class MonitorConsole(Console):
    @property
    def is_dumb_terminal(self) -> bool:
        # Wrappers may set TERM=dumb despite forwarding cursor controls.
        return False


def latest_tasks(connection: sqlite3.Connection, sources: list[str]) -> list[sqlite3.Row | None]:
    return [connection.execute(
        """
        SELECT t.*, (SELECT detail_json FROM task_progress_events
                     WHERE task_id = t.id ORDER BY id DESC LIMIT 1) AS detail_json,
                    (SELECT created_at FROM task_progress_events
                     WHERE task_id = t.id ORDER BY id DESC LIMIT 1) AS updated_at
        FROM tasks t WHERE source_input = ? ORDER BY created_at DESC, rowid DESC LIMIT 1
        """, (source,),
    ).fetchone() for source in sources]


def task_table(sources: list[str], tasks: list[sqlite3.Row | None]) -> Table:
    table = Table(title="转写进度（只读监看，Ctrl+C 退出）")
    for title in ("文件", "状态", "总进度", "已识别 / 总时长", "设备", "最近更新"):
        table.add_column(title)
    for source, task in zip(sources, tasks):
        if task is None:
            table.add_row(Path(source).name, "未找到任务", "", "", "", "")
            continue
        detail = json.loads(task["detail_json"] or "{}")
        duration = ""
        if "processed_seconds" in detail and "total_seconds" in detail:
            duration = " / ".join(str(timedelta(seconds=round(detail[key])))
                                  for key in ("processed_seconds", "total_seconds"))
        table.add_row(Path(source).name, tr("zh-CN", f"progress_stage_{task['current_stage']}"),
                      f"{task['progress_percent']:.1%}", duration, detail.get("device", ""),
                      (task["updated_at"] or "").split("T")[-1][:8])
    return table


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True, help="批量任务的输入清单，每行一个路径或链接")
    parser.add_argument("--workspace", type=Path, default=Path(".b2t"))
    parser.add_argument("--once", action="store_true", help="仅显示一次后退出")
    args = parser.parse_args()
    sources = list(dict.fromkeys(line.strip() for line in args.file.read_text(encoding="utf-8").splitlines()
                                if line.strip()))
    if not sources:
        parser.error("任务清单为空")
    # This is an interactive monitor, even when a command wrapper uses pipes.
    console = Console() if args.once else MonitorConsole(force_terminal=True, force_interactive=True, color_system=None)
    with closing(sqlite3.connect(args.workspace.joinpath("app.db").resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        tasks = latest_tasks(connection, sources)
        if args.once:
            console.print(task_table(sources, tasks))
            return 0
        try:
            with Live(task_table(sources, tasks), console=console, auto_refresh=False,
                      redirect_stdout=False, redirect_stderr=False) as live:
                while any(task is not None and task["status"] in {"queued", "running"} for task in tasks):
                    time.sleep(1)
                    updated = latest_tasks(connection, sources)
                    if updated == tasks:
                        continue
                    tasks = updated
                    live.update(task_table(sources, tasks), refresh=True)
        except KeyboardInterrupt:
            return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
