import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import os
import json
import ctypes
import string
import subprocess
from pathlib import Path

LAUNCH_CWD = Path(os.getcwd())
CONFIG_FILE = Path.home() / ".claude" / "jdir_config.json"

EXEC_EXTENSIONS = frozenset({'.exe', '.bat', '.cmd', '.msi', '.com', '.ps1'})
DOC_EXTENSIONS  = frozenset({'.doc', '.docx', '.ppt', '.pptx', '.txt', '.csv', '.md'})

_FOCUS_CYCLE = ["entry-list", "claude-btn", "start-input", "move-btn"]


def get_drives() -> list[Path]:
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for letter in string.ascii_uppercase:
        if bitmask & 1:
            p = Path(f"{letter}:\\")
            if p.exists():
                drives.append(p)
        bitmask >>= 1
    return drives


def load_saved_start() -> str | None:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("start") or None
        except Exception:
            pass
    return None


def save_start(path: str) -> None:
    CONFIG_FILE.write_text(
        json.dumps({"start": path}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def clear_saved_start() -> None:
    if CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")


from textual.app import App, ComposeResult
from textual.widgets import ListView, ListItem, Label, Header, Footer, Input, Button, Static
from textual.containers import Horizontal
from textual.binding import Binding
from textual import on


class EntryItem(ListItem):
    def __init__(self, path: Path | None, display: str, kind: str) -> None:
        super().__init__()
        self.entry_path = path
        self.kind = kind   # 'parent' | 'drive' | 'folder' | 'exec' | 'doc'
        self._display = display

    def compose(self) -> ComposeResult:
        prefix = {
            'parent': '  ^  ',
            'drive':  ' [D] ',
            'folder': '  >  ',
            'exec':   ' [!] ',
            'doc':    ' [-] ',
        }.get(self.kind, '     ')
        yield Label(f"{prefix}{self._display}")


class EntryListView(ListView):
    BINDINGS = [
        Binding("enter", "activate",    "열기/이동", show=False),
        Binding("right", "enter_item",  "하위폴더",  show=False),
        Binding("left",  "go_top",      "최상단",    show=False),
    ]

    def action_activate(self) -> None:
        self.app.activate_item()

    def action_enter_item(self) -> None:
        item = self.highlighted_child
        if isinstance(item, EntryItem) and item.kind in ('folder', 'drive'):
            self.app.navigate_to(item.entry_path)

    def action_go_top(self) -> None:
        self.index = 0


class JDir(App):
    TITLE = "JDir"
    SUB_TITLE = "v0.1  ·  by JaeJae"

    CSS = """
    #top-bar {
        height: 3;
        background: $panel;
        border-bottom: solid $primary;
        align: left middle;
        padding: 0 1;
    }
    #start-label {
        width: auto;
        color: $text-muted;
        padding: 0 1;
    }
    #start-input {
        width: 1fr;
    }
    #move-btn {
        width: 6;
        margin-left: 1;
    }
    #claude-btn {
        width: 14;
        margin-left: 1;
    }
    #current-path-bar {
        height: 2;
        padding: 0 2;
        background: $boost;
        border-bottom: solid $accent;
        color: $accent;
        content-align: left middle;
    }
    EntryListView {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("tab",    "cycle_focus",       "탭 이동",     show=True,  priority=True),
        Binding("ctrl+r", "focus_start_input", "시작폴더 변경"),
        Binding("escape", "app.quit",          "종료"),
    ]

    def __init__(self, launch_cwd: Path) -> None:
        super().__init__()
        self.launch_cwd = launch_cwd
        saved = load_saved_start()
        self._current_path: Path | None = (
            Path(saved) if saved and Path(saved).is_dir() else launch_cwd
        )

    def compose(self) -> ComposeResult:
        saved = load_saved_start()
        placeholder = f"비워두면 실행 위치 ({self.launch_cwd})"
        yield Header(show_clock=False)
        with Horizontal(id="top-bar"):
            yield Label("시작:", id="start-label")
            yield Input(value=saved or "", id="start-input", placeholder=placeholder)
            yield Button("이동", id="move-btn", variant="primary")
            yield Button("Claude 실행", id="claude-btn", variant="success")
        yield Static("", id="current-path-bar")
        yield EntryListView(id="entry-list")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_list(self._current_path)
        self.query_one(EntryListView).focus()

    def _refresh_list(self, path: Path | None, select: Path | None = None) -> None:
        lv = self.query_one(EntryListView)
        path_bar = self.query_one("#current-path-bar", Static)
        lv.clear()

        entries_meta: list[tuple[Path | None, str]] = []

        if path is None:
            path_bar.update("  [내 PC]")
            for drive in get_drives():
                lv.append(EntryItem(drive, str(drive), 'drive'))
                entries_meta.append((drive, 'drive'))
        else:
            path_bar.update(f"  {path}")

            if path.parent != path:
                parent_label = f".. ({path.parent.name or str(path.parent)})"
                lv.append(EntryItem(path.parent, parent_label, 'parent'))
                entries_meta.append((path.parent, 'parent'))
            else:
                # 드라이브 루트 → 가상 루트(내 PC)로 이동
                lv.append(EntryItem(None, ".. (내 PC)", 'parent'))
                entries_meta.append((None, 'parent'))

            try:
                raw = list(path.iterdir())
            except PermissionError:
                raw = []

            folders = sorted(
                [p for p in raw if p.is_dir()],
                key=lambda x: x.name.lower()
            )
            execs = sorted(
                [p for p in raw if p.is_file() and p.suffix.lower() in EXEC_EXTENSIONS],
                key=lambda x: x.name.lower()
            )
            docs = sorted(
                [p for p in raw if p.is_file() and p.suffix.lower() in DOC_EXTENSIONS],
                key=lambda x: x.name.lower()
            )

            for p in folders:
                lv.append(EntryItem(p, p.name, 'folder'))
                entries_meta.append((p, 'folder'))
            for p in execs:
                lv.append(EntryItem(p, p.name, 'exec'))
                entries_meta.append((p, 'exec'))
            for p in docs:
                lv.append(EntryItem(p, p.name, 'doc'))
                entries_meta.append((p, 'doc'))

        target_index = 0
        if select is not None:
            for i, (p, _) in enumerate(entries_meta):
                if p == select:
                    target_index = i
                    break

        self._current_path = path
        self.call_after_refresh(lambda idx=target_index: setattr(lv, "index", idx))

    def navigate_to(self, path: Path | None) -> None:
        self._current_path = path
        self._refresh_list(path)

    def go_up(self) -> None:
        if self._current_path is None:
            return
        parent = self._current_path.parent
        if parent != self._current_path:
            prev = self._current_path
            self._current_path = parent
            self._refresh_list(parent, select=prev)
        else:
            # 드라이브 루트에서 가상 루트로
            prev = self._current_path
            self._current_path = None
            self._refresh_list(None, select=prev)

    def activate_item(self) -> None:
        lv = self.query_one(EntryListView)
        item = lv.highlighted_child
        if not isinstance(item, EntryItem):
            return
        if item.kind == 'parent':
            self.go_up()
        elif item.kind in ('folder', 'drive'):
            self.navigate_to(item.entry_path)
        elif item.kind in ('exec', 'doc'):
            try:
                os.startfile(str(item.entry_path))
            except Exception as e:
                self.notify(f"열기 실패: {e}", severity="error")

    def action_cycle_focus(self) -> None:
        focused = self.focused
        current_id = focused.id if focused else None
        if current_id in _FOCUS_CYCLE:
            idx = (_FOCUS_CYCLE.index(current_id) + 1) % len(_FOCUS_CYCLE)
        else:
            idx = 0
        self.query_one(f"#{_FOCUS_CYCLE[idx]}").focus()

    def _launch_claude(self) -> None:
        if self._current_path is None:
            self.notify("드라이브를 먼저 선택하세요.", severity="warning")
            return
        self.exit(self._current_path)

    def _apply_start(self) -> None:
        raw = self.query_one("#start-input", Input).value.strip()
        if raw:
            p = Path(raw)
            if not p.is_dir():
                self.notify("폴더를 찾을 수 없습니다.", severity="error")
                return
            save_start(str(p))
            self._current_path = p
        else:
            clear_saved_start()
            self._current_path = self.launch_cwd
        self._refresh_list(self._current_path)
        self.query_one(EntryListView).focus()
        self.notify(f"이동: {self._current_path.name or str(self._current_path)}", timeout=2)

    @on(Input.Submitted, "#start-input")
    def on_start_submitted(self) -> None:
        self._apply_start()

    @on(Button.Pressed, "#move-btn")
    def on_move_btn(self) -> None:
        self._apply_start()

    @on(Button.Pressed, "#claude-btn")
    def on_claude_btn(self) -> None:
        self._launch_claude()

    def action_focus_start_input(self) -> None:
        self.query_one("#start-input", Input).focus()


if __name__ == "__main__":
    app = JDir(LAUNCH_CWD)
    result = app.run()
    if result is not None:
        print(f"\n  claude 실행: {result}\n")
        subprocess.run(["claude"], cwd=str(result))
