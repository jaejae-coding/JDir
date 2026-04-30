import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import os
import json
import ctypes
import string
import shutil
import subprocess
from pathlib import Path
from typing import Literal

LAUNCH_CWD = Path(os.getcwd())
CONFIG_FILE = Path.home() / ".claude" / "jdir_config.json"

EXEC_EXTENSIONS = frozenset({'.exe', '.bat', '.cmd', '.msi', '.com', '.ps1', '.py'})
DOC_EXTENSIONS  = frozenset({'.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                              '.hwp', '.hwpx', '.pdf', '.rtf', '.txt', '.csv', '.md'})

_FOCUS_CYCLE = ["entry-list", "prompt-input", "claude-btn", "start-input", "move-btn",
                "show-hidden-cb", "show-temp-cb"]

_CREATE_NEW_CONSOLE = 0x00000010

def _is_hidden_or_system(path: Path) -> bool:
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs == 0xFFFFFFFF:
            return False
        return bool(attrs & 0x6)  # HIDDEN=0x2 | SYSTEM=0x4
    except Exception:
        return False

def _is_temp_file(path: Path) -> bool:
    return path.name.startswith('~$') or path.suffix.lower() in ('.tmp', '.temp')


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


from rich.markup import escape as rich_escape
from textual.app import App, ComposeResult
from textual.widgets import ListView, ListItem, Label, Header, Footer, Input, Button, Static, Checkbox
from textual.containers import Horizontal, Grid
from textual.binding import Binding
from textual.screen import ModalScreen
from textual import on


class QuitConfirmScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "force_quit",   show=False),
        Binding("left",   "focus_quit",   show=False),
        Binding("right",  "focus_cancel", show=False),
    ]

    CSS = """
    QuitConfirmScreen { align: center middle; }
    #quit-dialog {
        grid-size: 2;
        grid-gutter: 1 2;
        grid-rows: 1fr 3;
        padding: 1 2;
        width: 40;
        height: 11;
        border: thick $background 80%;
        background: $surface;
    }
    #quit-dialog Label {
        column-span: 2;
        height: 1fr;
        width: 1fr;
        content-align: center middle;
    }
    #quit-btn   { width: 100%; border: tall $error; }
    #cancel-btn { width: 100%; }
    """

    def compose(self) -> ComposeResult:
        yield Grid(
            Label("JDir을 종료하시겠습니까?"),
            Button("종료", id="quit-btn", variant="error"),
            Button("취소", id="cancel-btn"),
            id="quit-dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#quit-btn").focus()

    def action_force_quit(self) -> None:
        self.app.exit()

    def action_focus_cancel(self) -> None:
        self.query_one("#cancel-btn").focus()

    def action_focus_quit(self) -> None:
        self.query_one("#quit-btn").focus()

    @on(Button.Pressed, "#quit-btn")
    def do_quit(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cancel-btn")
    def do_cancel(self) -> None:
        self.dismiss(False)


class ConfirmScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "cancel",        "취소", show=True),
        Binding("enter",  "confirm",       "확인", show=True),
        Binding("left",   "focus_confirm", show=False),
        Binding("right",  "focus_cancel",  show=False),
    ]

    CSS = """
    ConfirmScreen {
        align: center middle;
    }
    #dialog {
        grid-size: 2;
        grid-gutter: 1 2;
        grid-rows: 1fr 3;
        padding: 1 2;
        width: 60;
        height: 11;
        border: thick $background 80%;
        background: $surface;
    }
    #dialog Label {
        column-span: 2;
        height: 1fr;
        width: 1fr;
        content-align: center middle;
    }
    #confirm { width: 100%; border: tall $error; }
    #cancel  { width: 100%; }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        yield Grid(
            Label(self._message),
            Button("확인", id="confirm", variant="error"),
            Button("취소", id="cancel"),
            id="dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#confirm").focus()

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_focus_confirm(self) -> None:
        self.query_one("#confirm").focus()

    def action_focus_cancel(self) -> None:
        self.query_one("#cancel").focus()

    @on(Button.Pressed, "#confirm")
    def on_confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def on_cancel(self) -> None:
        self.dismiss(False)


class InputScreen(ModalScreen):
    BINDINGS = [Binding("escape", "cancel_input", show=False)]

    CSS = """
    InputScreen { align: center middle; }
    #input-dialog {
        grid-size: 2;
        grid-gutter: 1 2;
        grid-rows: 1fr 3 3;
        padding: 1 2;
        width: 52;
        height: 13;
        border: thick $background 80%;
        background: $surface;
    }
    #input-dialog Label {
        column-span: 2;
        height: 1fr;
        width: 1fr;
        content-align: center middle;
    }
    #input-field { column-span: 2; }
    #input-ok     { width: 100%; }
    #input-cancel { width: 100%; }
    """

    def __init__(self, title: str, default: str = "") -> None:
        super().__init__()
        self._title = title
        self._default = default

    def compose(self) -> ComposeResult:
        yield Grid(
            Label(self._title),
            Input(value=self._default, id="input-field"),
            Button("확인", id="input-ok", variant="primary"),
            Button("취소", id="input-cancel"),
            id="input-dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#input-field", Input).focus()

    @on(Input.Submitted, "#input-field")
    def on_submitted(self) -> None:
        self.dismiss(self.query_one("#input-field", Input).value.strip())

    @on(Button.Pressed, "#input-ok")
    def on_ok(self) -> None:
        self.dismiss(self.query_one("#input-field", Input).value.strip())

    @on(Button.Pressed, "#input-cancel")
    def on_cancel_btn(self) -> None:
        self.dismiss(None)

    def action_cancel_input(self) -> None:
        self.dismiss(None)


class EntryItem(ListItem):
    def __init__(self, path: Path | None, display: str, kind: str,
                 selected: bool = False, cut: bool = False) -> None:
        css_classes = []
        if kind == 'folder':
            css_classes.append('folder-item')
        if kind == 'exec':
            css_classes.append('exec-item')
        if selected:
            css_classes.append('item-selected')
        if cut:
            css_classes.append('item-cut')
        super().__init__(classes=" ".join(css_classes))
        self.entry_path = path
        self.kind = kind
        self._display = display
    def compose(self) -> ComposeResult:
        prefix = {
            'parent': '  ^  ',
            'drive':  ' [D] ',
            'exec':   ' [!] ',
            'doc':    ' [-] ',
            'file':   '     ',
        }.get(self.kind, '')

        if self.kind == 'folder':
            yield Label(f"  >  \\[{rich_escape(self._display)}]")
        else:
            yield Label(f"{prefix}{rich_escape(self._display)}")


class EntryListView(ListView):
    BINDINGS = [
        Binding("enter",      "activate",          "열기/이동", show=False),
        Binding("right",      "enter_item",        "하위폴더",  show=False),
        Binding("left",       "go_top",            "최상단",    show=False),
        Binding("space",      "toggle_select",     "선택",      show=False),
        Binding("shift+down", "shift_select_down", "범위선택",  show=False),
        Binding("shift+up",   "shift_select_up",   "범위선택",  show=False),
    ]

    def action_activate(self) -> None:
        self.app.activate_item()

    def action_enter_item(self) -> None:
        item = self.highlighted_child
        if isinstance(item, EntryItem) and item.kind in ('folder', 'drive'):
            self.app.navigate_to(item.entry_path)

    def action_go_top(self) -> None:
        item = self.highlighted_child
        if isinstance(item, EntryItem) and item.kind == 'parent':
            self.app.go_up()
        else:
            self.index = 0

    def action_cursor_up(self) -> None:
        self.app._reset_shift_session()
        super().action_cursor_up()

    def action_cursor_down(self) -> None:
        self.app._reset_shift_session()
        super().action_cursor_down()

    def action_toggle_select(self) -> None:
        self.app.toggle_select_current()

    def action_shift_select_down(self) -> None:
        self.app.shift_select(1)

    def action_shift_select_up(self) -> None:
        self.app.shift_select(-1)


class JDir(App):
    TITLE = "JDir"
    SUB_TITLE = "v0.6 (20260430)  ·  by JaeJae"

    CSS = """
    #top-bar {
        height: 3;
        background: $panel;
        border-bottom: solid $primary;
        align: left middle;
        padding: 0 1;
    }
    #claude-btn {
        width: 14;
        margin-right: 1;
    }
    #start-input {
        width: 1fr;
    }
    #move-btn {
        width: 5;
        margin-left: 1;
    }
    #prompt-bar {
        height: 3;
        background: $boost;
        border-bottom: solid $accent;
        align: left middle;
        padding: 0 1;
    }
    #prompt-prefix {
        color: $accent;
        width: auto;
        content-align: left middle;
        padding: 0 1;
    }
    #prompt-input {
        width: 1fr;
        border: none;
        background: $boost;
    }
    #filter-bar {
        height: 3;
        padding: 0 2;
        background: $panel;
        border-top: solid $primary;
        align: left middle;
    }
    #filter-bar Checkbox {
        margin-right: 3;
        background: $panel;
    }
    EntryListView {
        height: 1fr;
    }
    #clipboard-bar {
        height: 3;
        padding: 0 2;
        background: $surface;
        border-top: solid $primary;
        color: $text-muted;
        content-align: left middle;
    }
    EntryItem.folder-item Label {
        color: $success;
    }
    EntryItem.exec-item Label {
        color: $warning;
    }
    EntryItem.item-selected {
        background: $accent 25%;
    }
    EntryItem.item-cut Label {
        color: $text-muted;
        text-style: dim;
    }
    """

    BINDINGS = [
        Binding("tab",       "cycle_focus",       "탭이동",   show=True,  priority=True),
        Binding("ctrl+a",    "select_all",        "모두선택", show=True,  priority=True),
        Binding("ctrl+c",    "copy_items",        "복사",     show=True,  priority=True),
        Binding("ctrl+x",    "cut_items",         "잘라내기", show=True,  priority=True),
        Binding("ctrl+p",    "paste_items",       "붙여넣기", show=True,  priority=True),
        Binding("ctrl+d",    "delete_items",      "삭제",     show=True,  priority=True),
        Binding("ctrl+z",    "undo",              "취소",     show=True,  priority=True),
        Binding("f2",        "rename",            "이름변경", show=True),
        Binding("ctrl+n",    "new_folder",        "새폴더",   show=True),
        Binding("f5",        "refresh",           "새로고침", show=True),
        Binding("alt+left",  "nav_back",          "뒤로",     show=True),
        Binding("alt+right", "nav_forward",       "앞으로",   show=True),
        Binding("ctrl+r",    "focus_start_input", "시작폴더", show=False),
        Binding("escape",    "quit_confirm",      "종료"),
    ]

    def __init__(self, launch_cwd: Path) -> None:
        super().__init__()
        self.launch_cwd = launch_cwd
        saved = load_saved_start()
        self._current_path: Path | None = (
            Path(saved) if saved and Path(saved).is_dir() else launch_cwd
        )
        self._selected_paths: set[Path] = set()
        self._clipboard_paths: list[Path] = []
        self._clipboard_mode: Literal['copy', 'cut'] | None = None
        self._countdown_handle = None
        self._clipboard_countdown: int = 0
        self._shift_anchor_idx: int | None = None
        self._shift_baseline: dict[Path, bool] = {}
        self._shift_modified: set[Path] = set()
        self._nav_history: list[Path | None] = []
        self._nav_index: int = -1
        self._undo_stack: list[dict] = []
        self._show_hidden: bool = False
        self._show_temp: bool = False

    def compose(self) -> ComposeResult:
        saved = load_saved_start()
        placeholder = f"현재 지정된 시작 위치: {self.launch_cwd}"
        yield Header(show_clock=False)
        with Horizontal(id="top-bar"):
            yield Button("Claude 실행", id="claude-btn", variant="success")
            yield Input(value=saved or "", id="start-input", placeholder=placeholder)
            yield Button("이동", id="move-btn", variant="primary")
        with Horizontal(id="prompt-bar"):
            yield Static("PS > ", id="prompt-prefix")
            yield Input(placeholder="Enter: 새 PowerShell  |  명령어 입력 후 Enter: 명령 실행", id="prompt-input")
        yield EntryListView(id="entry-list")
        yield Static("클립보드: (없음)", id="clipboard-bar")
        with Horizontal(id="filter-bar"):
            yield Checkbox("숨김파일 표시", id="show-hidden-cb", value=False)
            yield Checkbox("임시파일 표시", id="show-temp-cb", value=False)
        yield Footer()

    def on_mount(self) -> None:
        self._push_nav(self._current_path)
        self._refresh_list(self._current_path)
        self.query_one(EntryListView).focus()

    def _get_active_paths(self) -> list[Path]:
        """선택된 항목 있으면 선택 목록, 없으면 현재 커서 항목."""
        if self._selected_paths:
            return list(self._selected_paths)
        lv = self.query_one(EntryListView)
        item = lv.highlighted_child
        if isinstance(item, EntryItem) and item.entry_path and item.kind not in ('parent', 'drive'):
            return [item.entry_path]
        return []

    def _push_nav(self, path: Path | None) -> None:
        self._nav_history = self._nav_history[:self._nav_index + 1]
        if self._nav_history and self._nav_history[-1] == path:
            return
        self._nav_history.append(path)
        self._nav_index = len(self._nav_history) - 1

    def _reset_shift_session(self) -> None:
        self._shift_anchor_idx = None
        self._shift_baseline.clear()
        self._shift_modified.clear()

    def _cancel_countdown(self) -> None:
        if self._countdown_handle is not None:
            self._countdown_handle.stop()
            self._countdown_handle = None
        self._clipboard_countdown = 0

    def _start_clipboard_countdown(self) -> None:
        self._cancel_countdown()
        self._clipboard_countdown = 10
        self._update_clipboard_bar()
        self._countdown_handle = self.set_interval(1.0, self._tick_countdown)

    def _tick_countdown(self) -> None:
        self._clipboard_countdown -= 1
        if self._clipboard_countdown <= 0:
            self._cancel_countdown()
            self._clipboard_paths = []
            self._clipboard_mode = None
            self._update_clipboard_bar()
        else:
            self._update_clipboard_bar()

    def _update_clipboard_bar(self) -> None:
        bar = self.query_one("#clipboard-bar", Static)
        parts = []
        if self._selected_paths:
            parts.append(f"{len(self._selected_paths)}개 선택")
        if self._clipboard_paths:
            mode_str = "복사" if self._clipboard_mode == 'copy' else "잘라내기"
            names = ", ".join(p.name for p in self._clipboard_paths[:3])
            if len(self._clipboard_paths) > 3:
                names += f" 외 {len(self._clipboard_paths) - 3}개"
            countdown_str = f" ({self._clipboard_countdown}초 후 삭제)" if self._clipboard_countdown > 0 else ""
            parts.append(f"클립보드 [{mode_str}]: {names}{countdown_str}")
        bar.update("  |  ".join(parts) if parts else "클립보드: (없음)")

    def _refresh_list(self, path: Path | None, select: Path | None = None) -> None:
        lv = self.query_one(EntryListView)
        lv.clear()

        # 프롬프트 접두사 업데이트
        try:
            path_str = str(path) if path else "내 PC"
            self.query_one("#prompt-prefix", Static).update(f"PS {path_str}> ")
        except Exception:
            pass

        entries_meta: list[tuple[Path | None, str]] = []
        cut_set = set(self._clipboard_paths) if self._clipboard_mode == 'cut' else set()

        if path is None:
            for drive in get_drives():
                lv.append(EntryItem(drive, str(drive), 'drive'))
                entries_meta.append((drive, 'drive'))
        else:
            if path.parent != path:
                parent_label = f".. ({path.parent.name or str(path.parent)})"
                lv.append(EntryItem(path.parent, parent_label, 'parent'))
                entries_meta.append((path.parent, 'parent'))
            else:
                lv.append(EntryItem(None, ".. (내 PC)", 'parent'))
                entries_meta.append((None, 'parent'))

            try:
                raw = list(path.iterdir())
            except (PermissionError, OSError):
                raw = []

            if not self._show_hidden:
                raw = [p for p in raw if not _is_hidden_or_system(p)]
            if not self._show_temp:
                raw = [p for p in raw if not _is_temp_file(p)]

            folders = sorted([p for p in raw if p.is_dir()], key=lambda x: x.name.lower())
            execs   = sorted([p for p in raw if p.is_file() and p.suffix.lower() in EXEC_EXTENSIONS], key=lambda x: x.name.lower())
            docs    = sorted([p for p in raw if p.is_file() and p.suffix.lower() in DOC_EXTENSIONS],  key=lambda x: x.name.lower())
            others  = sorted([p for p in raw if p.is_file()
                               and p.suffix.lower() not in EXEC_EXTENSIONS
                               and p.suffix.lower() not in DOC_EXTENSIONS], key=lambda x: x.name.lower())

            for p in folders:
                lv.append(EntryItem(p, p.name, 'folder', p in self._selected_paths, p in cut_set))
                entries_meta.append((p, 'folder'))
            for p in execs:
                lv.append(EntryItem(p, p.name, 'exec', p in self._selected_paths, p in cut_set))
                entries_meta.append((p, 'exec'))
            for p in docs:
                lv.append(EntryItem(p, p.name, 'doc', p in self._selected_paths, p in cut_set))
                entries_meta.append((p, 'doc'))
            for p in others:
                lv.append(EntryItem(p, p.name, 'file', p in self._selected_paths, p in cut_set))
                entries_meta.append((p, 'file'))

        target_index = 0
        if select is not None:
            for i, (p, _) in enumerate(entries_meta):
                if p == select:
                    target_index = i
                    break

        self._current_path = path
        self.call_after_refresh(lambda idx=target_index: setattr(lv, "index", idx))

    def navigate_to(self, path: Path | None) -> None:
        self._selected_paths.clear()
        self._reset_shift_session()
        self._push_nav(path)
        self._current_path = path
        self._refresh_list(path)

    def go_up(self) -> None:
        if self._current_path is None:
            return
        parent = self._current_path.parent
        if parent != self._current_path:
            prev = self._current_path
            self._selected_paths.clear()
            self._reset_shift_session()
            self._push_nav(parent)
            self._current_path = parent
            self._refresh_list(parent, select=prev)
        else:
            prev = self._current_path
            self._selected_paths.clear()
            self._reset_shift_session()
            self._push_nav(None)
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

    def action_select_all(self) -> None:
        if self._current_path is None:
            return
        lv = self.query_one(EntryListView)
        self._selected_paths.clear()
        self._reset_shift_session()
        for child in lv.children:
            if isinstance(child, EntryItem) and child.entry_path and child.kind not in ('parent', 'drive'):
                self._selected_paths.add(child.entry_path)
        self._refresh_list(self._current_path)
        self._update_clipboard_bar()
        self.notify(f"{len(self._selected_paths)}개 선택됨", timeout=1)

    def toggle_select_current(self) -> None:
        self._reset_shift_session()
        lv = self.query_one(EntryListView)
        item = lv.highlighted_child
        if not (isinstance(item, EntryItem) and item.entry_path and item.kind not in ('parent', 'drive')):
            return
        self._selected_paths ^= {item.entry_path}
        self._refresh_list(self._current_path, select=item.entry_path)
        self._update_clipboard_bar()

    def shift_select(self, direction: int) -> None:
        lv = self.query_one(EntryListView)
        if lv.index is None:
            return
        children = list(lv.children)
        current_idx = lv.index
        new_idx = current_idx + direction
        if not (0 <= new_idx < len(children)):
            return

        # 세션 시작: 앵커와 베이스라인 스냅샷 저장
        if self._shift_anchor_idx is None:
            self._shift_anchor_idx = current_idx
            self._shift_baseline = {
                child.entry_path: (child.entry_path in self._selected_paths)
                for child in children
                if isinstance(child, EntryItem) and child.entry_path
                and child.kind not in ('parent', 'drive')
            }

        anchor = self._shift_anchor_idx
        lo, hi = min(anchor, new_idx), max(anchor, new_idx)

        # 새 범위에 포함된 selectable 항목 집합
        new_modified: set[Path] = set()
        for i in range(lo, hi + 1):
            item = children[i]
            if isinstance(item, EntryItem) and item.entry_path and item.kind not in ('parent', 'drive'):
                new_modified.add(item.entry_path)

        # 새로 범위에 들어온 항목: 베이스라인 반전
        for path in new_modified - self._shift_modified:
            if self._shift_baseline.get(path, False):
                self._selected_paths.discard(path)
            else:
                self._selected_paths.add(path)

        # 범위에서 빠진 항목: 베이스라인으로 복원
        for path in self._shift_modified - new_modified:
            if self._shift_baseline.get(path, False):
                self._selected_paths.add(path)
            else:
                self._selected_paths.discard(path)

        self._shift_modified = new_modified

        new_item = children[new_idx]
        select_path = new_item.entry_path if isinstance(new_item, EntryItem) else None
        self._refresh_list(self._current_path, select=select_path)
        self._update_clipboard_bar()

    def action_copy_items(self) -> None:
        paths = self._get_active_paths()
        if not paths:
            return
        self._reset_shift_session()
        self._cancel_countdown()
        self._clipboard_paths = paths
        self._clipboard_mode = 'copy'
        self._update_clipboard_bar()
        self._refresh_list(self._current_path)
        self.notify(f"{len(paths)}개 복사 준비", timeout=1)

    def action_cut_items(self) -> None:
        paths = self._get_active_paths()
        if not paths:
            return
        self._reset_shift_session()
        self._cancel_countdown()
        self._clipboard_paths = paths
        self._clipboard_mode = 'cut'
        self._update_clipboard_bar()
        self._refresh_list(self._current_path)
        self.notify(f"{len(paths)}개 잘라내기 준비", timeout=1)

    def action_paste_items(self) -> None:
        if not self._clipboard_paths or self._current_path is None:
            return
        self._reset_shift_session()
        errors = []
        created: list[tuple[Path, Path]] = []
        paste_mode = self._clipboard_mode
        for src in self._clipboard_paths:
            dst = self._current_path / src.name
            try:
                if dst.exists():
                    dst = self._current_path / (src.stem + "_copy" + src.suffix)
                if paste_mode == 'copy':
                    if src.is_dir():
                        shutil.copytree(str(src), str(dst))
                    else:
                        shutil.copy2(str(src), str(dst))
                else:
                    shutil.move(str(src), str(dst))
                created.append((src, dst))
            except Exception as e:
                errors.append(f"{src.name}: {e}")

        if created:
            self._undo_stack.append({
                'type': 'paste',
                'mode': paste_mode,
                'folder': self._current_path,
                'items': created,
            })

        if paste_mode == 'cut':
            self._clipboard_paths = []
            self._clipboard_mode = None
            self._cancel_countdown()
            self._update_clipboard_bar()
        else:
            self._start_clipboard_countdown()

        self._selected_paths.clear()
        self._refresh_list(self._current_path)

        if errors:
            self.notify("오류: " + " / ".join(errors), severity="error", timeout=5)
        else:
            self.notify("붙여넣기 완료", timeout=2)

    def action_nav_back(self) -> None:
        if self._nav_index <= 0:
            return
        self._nav_index -= 1
        path = self._nav_history[self._nav_index]
        self._selected_paths.clear()
        self._reset_shift_session()
        self._current_path = path
        self._refresh_list(path)

    def action_nav_forward(self) -> None:
        if self._nav_index >= len(self._nav_history) - 1:
            return
        self._nav_index += 1
        path = self._nav_history[self._nav_index]
        self._selected_paths.clear()
        self._reset_shift_session()
        self._current_path = path
        self._refresh_list(path)

    def action_undo(self) -> None:
        if not self._undo_stack:
            self.notify("되돌릴 작업이 없습니다.", timeout=2)
            return
        op = self._undo_stack.pop()
        errors = []
        if op['mode'] == 'copy':
            for _src, dst in op['items']:
                try:
                    if dst.is_dir():
                        shutil.rmtree(str(dst))
                    elif dst.exists():
                        os.remove(str(dst))
                except Exception as e:
                    errors.append(f"{dst.name}: {e}")
        else:
            for src, dst in op['items']:
                try:
                    shutil.move(str(dst), str(src))
                except Exception as e:
                    errors.append(f"{dst.name}: {e}")

        folder = op['folder']
        if folder and folder.is_dir():
            self._selected_paths.clear()
            self._reset_shift_session()
            self._push_nav(folder)
            self._current_path = folder
            self._refresh_list(folder)

        if errors:
            self.notify("취소 오류: " + " / ".join(errors), severity="error", timeout=5)
        else:
            mode_str = "복사" if op['mode'] == 'copy' else "이동"
            self.notify(f"{mode_str} 취소 완료", timeout=2)

    def action_delete_items(self) -> None:
        paths = self._get_active_paths()
        if not paths:
            return
        self._reset_shift_session()
        names = ", ".join(p.name for p in paths[:2])
        if len(paths) > 2:
            names += f" 외 {len(paths) - 2}개"

        def do_delete(confirmed: bool) -> None:
            if not confirmed:
                return
            errors = []
            for p in paths:
                try:
                    if p.is_dir():
                        shutil.rmtree(str(p))
                    else:
                        os.remove(str(p))
                except Exception as e:
                    errors.append(f"{p.name}: {e}")
            self._selected_paths -= set(paths)
            self._clipboard_paths = [cp for cp in self._clipboard_paths if cp not in paths]
            if not self._clipboard_paths:
                self._clipboard_mode = None
                self._cancel_countdown()
            self._update_clipboard_bar()
            self._refresh_list(self._current_path)
            if errors:
                self.notify("삭제 오류: " + " / ".join(errors), severity="error", timeout=5)
            else:
                self.notify(f"{len(paths)}개 삭제 완료", timeout=2)

        self.push_screen(ConfirmScreen(f"삭제하시겠습니까?\n{names}"), do_delete)

    def action_rename(self) -> None:
        lv = self.query_one(EntryListView)
        item = lv.highlighted_child
        if not isinstance(item, EntryItem) or not item.entry_path or item.kind in ('parent', 'drive'):
            return
        old_path = item.entry_path

        def do_rename(new_name: str | None) -> None:
            if not new_name or new_name == old_path.name:
                return
            new_path = old_path.parent / new_name
            if new_path.exists() and new_name.lower() != old_path.name.lower():
                self.notify(f"이미 존재합니다: {new_name}", severity="error")
                return
            try:
                old_path.rename(new_path)
                self._refresh_list(self._current_path, select=new_path)
                self.notify("이름 변경 완료", timeout=2)
            except Exception as e:
                self.notify(f"이름 변경 오류: {e}", severity="error")

        self.push_screen(InputScreen("이름 변경", old_path.name), do_rename)

    def action_new_folder(self) -> None:
        if self._current_path is None:
            self.notify("드라이브를 먼저 선택하세요.", severity="warning")
            return

        def do_create(name: str | None) -> None:
            if not name:
                return
            new_path = self._current_path / name
            if new_path.exists():
                self.notify(f"이미 존재합니다: {name}", severity="error")
                return
            try:
                new_path.mkdir()
                self._refresh_list(self._current_path, select=new_path)
                self.notify(f"폴더 생성: {name}", timeout=2)
            except Exception as e:
                self.notify(f"폴더 생성 오류: {e}", severity="error")

        self.push_screen(InputScreen("새 폴더 이름"), do_create)

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
        self._selected_paths.clear()
        self._reset_shift_session()
        self._push_nav(self._current_path)
        self._refresh_list(self._current_path)
        self.query_one(EntryListView).focus()
        self.notify(f"이동: {self._current_path.name or str(self._current_path)}", timeout=2)

    @on(Input.Submitted, "#start-input")
    def on_start_submitted(self) -> None:
        self._apply_start()

    @on(Input.Submitted, "#prompt-input")
    def on_prompt_submitted(self) -> None:
        inp = self.query_one("#prompt-input", Input)
        command = inp.value.strip()
        inp.value = ""
        self._launch_pwsh(command)

    @on(Button.Pressed, "#move-btn")
    def on_move_btn(self) -> None:
        self._apply_start()

    @on(Button.Pressed, "#claude-btn")
    def on_claude_btn(self) -> None:
        self._launch_claude()

    @on(Checkbox.Changed, "#show-hidden-cb")
    def on_show_hidden_changed(self, event: Checkbox.Changed) -> None:
        self._show_hidden = event.value
        self._refresh_list(self._current_path)

    @on(Checkbox.Changed, "#show-temp-cb")
    def on_show_temp_changed(self, event: Checkbox.Changed) -> None:
        self._show_temp = event.value
        self._refresh_list(self._current_path)

    def _launch_pwsh(self, command: str = "") -> None:
        cwd = str(self._current_path) if self._current_path else str(Path.home())
        for exe in ("pwsh", "powershell"):
            try:
                args = [exe, "-NoExit"] + (["-Command", command] if command else [])
                subprocess.Popen(args, cwd=cwd, creationflags=_CREATE_NEW_CONSOLE)
                return
            except FileNotFoundError:
                continue
        self.notify("PowerShell을 찾을 수 없습니다.", severity="error")

    def action_refresh(self) -> None:
        self._refresh_list(self._current_path)
        self.notify("새로고침", timeout=1)

    def action_focus_start_input(self) -> None:
        self.query_one("#start-input", Input).focus()

    def action_quit_confirm(self) -> None:
        if self._selected_paths:
            self._selected_paths.clear()
            self._reset_shift_session()
            self._cancel_countdown()
            self._clipboard_paths = []
            self._clipboard_mode = None
            self._update_clipboard_bar()
            self._refresh_list(self._current_path)
            self.notify("선택 해제", timeout=1)
        elif self._clipboard_paths:
            self._cancel_countdown()
            self._clipboard_paths = []
            self._clipboard_mode = None
            self._update_clipboard_bar()
            self._refresh_list(self._current_path)
            self.notify("클립보드를 비웠습니다.", timeout=1)
        else:
            def handle(confirmed: bool) -> None:
                if confirmed:
                    self.exit()
            self.push_screen(QuitConfirmScreen(), handle)


if __name__ == "__main__":
    app = JDir(LAUNCH_CWD)
    result = app.run()
    if result is not None:
        print(f"\n  claude 실행: {result}\n")
        subprocess.run(["claude"], cwd=str(result))
