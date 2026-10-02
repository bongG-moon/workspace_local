"""Small stdlib desktop publisher; workers never touch Tk or persist tokens."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import queue
import threading
from urllib.parse import quote, quote_plus


CONFIG_KEYS = ("schema", "baseUrl", "projectId", "remoteName", "remoteUrl", "releaseTag",
               "title", "notes", "notesVersion", "allowedDownloadOrigins", "tokenKind")
DEFAULT_CONFIG = {"schema": 1, "baseUrl": "", "projectId": "", "remoteName": "intranet",
                  "remoteUrl": "", "releaseTag": "", "title": "", "notes": "",
                  "notesVersion": "", "allowedDownloadOrigins": [], "tokenKind": "auto"}
TOKEN_LABELS = {"auto": "자동 감지", "access": "액세스 토큰 (Personal / Project)",
                "deploy": "배포 토큰 (Deploy Token)", "job": "CI 작업 토큰 (Job Token)"}
ACTION_NAMES = {"load": "설정과 소스 확인", "inspect": "소스 확인", "save": "설정 저장",
                "restore": "이전 빌드 확인",
                "sdk-status": "SDK 확인", "sdk-import": "SDK 파일 확인", "sdk-download": "SDK 다운로드",
                "connection": "연결 확인", "preview": "반영할 소스 확인", "sync": "소스 반영",
                "build": "설치 파일 만들기", "publish": "게시와 다운로드 검증", "deploy": "사내 배포"}


def source_is_archive(source):
    return source.get("sourceKind") == "archive"


def source_identifier(source):
    return str(source.get("sourceId") or source.get("commit") or "")


def source_can_sync(source):
    return bool(source) and not source_is_archive(source) and source.get("canSync", True)


def sdk_status(repo_root):
    """A missing SDK must not hide the remaining settings or source."""
    from .sdk import inspect_sdk
    try:
        return inspect_sdk(repo_root)
    except ValueError as exc:
        return {"status": "invalid", "message": public_text(exc)}


def settings_only(value):
    """Only the public settings contract can reach save_config()."""
    return {key: deepcopy(value.get(key, DEFAULT_CONFIG[key])) for key in CONFIG_KEYS}


def build_identity(config):
    return json.dumps({key: value for key, value in settings_only(config).items()
                       if key not in {"title", "notes", "notesVersion", "tokenKind"}}, sort_keys=True, ensure_ascii=True)


def public_text(value, token=""):
    text = str(value)
    if token:
        for secret in {token, quote(token, safe=""), quote_plus(token)}:
            text = text.replace(secret, "[게시 토큰 숨김]")
    return "".join(character for character in text if character in "\n\t" or ord(character) >= 32)[:12000]


def public_result(value, token=""):
    if isinstance(value, str):
        return public_text(value, token) if token else value
    if isinstance(value, dict):
        return {key: public_result(item, token) for key, item in value.items()
                if str(key).lower() not in {"token", "password", "authorization", "private-token", "deploy-token", "job-token"}}
    if isinstance(value, (list, tuple)):
        return [public_result(item, token) for item in value]
    return value


class PublisherJobs:
    """One cancellable job and a bounded queue, independent of Tk for testing."""
    def __init__(self, repo_root, factory):
        self.repo_root, self.factory = Path(repo_root), factory
        self.events = queue.Queue(maxsize=512)
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._busy = False
        self._serial = 0
        self.thread = None

    @property
    def busy(self):
        with self._lock:
            return self._busy

    def _put(self, value):
        while True:
            try:
                self.events.put_nowait(value)
                return
            except queue.Full:
                try:
                    self.events.get_nowait()
                except queue.Empty:
                    pass

    def start(self, action, config=None, *, preview=None, build_result=None, token="", sdk_input=""):
        if action not in ACTION_NAMES:
            raise ValueError("실행할 단계를 확인해 주세요.")
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._serial += 1
            serial = self._serial
            self.cancel_event = threading.Event()
            cancel = self.cancel_event
        config = settings_only(config or DEFAULT_CONFIG)
        preview, build_result = deepcopy(preview), deepcopy(build_result)

        def worker():
            ready_build = None
            def emit(event):
                if not isinstance(event, dict):
                    return
                kind = event.get("kind", "info")
                self._put({"job": serial, "kind": kind if kind in {"progress", "info", "error"} else "info",
                           "message": public_text(event.get("message", ""), token)})
            try:
                publisher = self.factory(self.repo_root, emit=emit)
                if action == "load":
                    result = {"config": publisher.load_config(), "sdk": sdk_status(self.repo_root)}
                    try:
                        result["source"] = publisher.inspect_source()
                    except ValueError as exc:
                        # An edited source ZIP cannot be rebuilt, but verified
                        # artifacts from an earlier build can still be retried.
                        result["source"] = None
                        result["sourceNotice"] = public_text(exc)
                    try:
                        result["build"] = publisher.load_last_build(result["config"], cancel=cancel)
                    except ValueError as exc:
                        # A damaged or differently targeted build must not hide
                        # the saved settings or block rebuilding in the window.
                        result["build"] = None
                        result["buildNotice"] = public_text(exc)
                    try:
                        result["releaseNotes"] = publisher.release_notes()
                    except (ValueError, OSError) as exc:
                        result["notesNotice"] = public_text(exc)
                elif action == "inspect":
                    result = publisher.inspect_source()
                    if hasattr(publisher, "release_notes"):
                        try:
                            result = {**result, "releaseNotes": publisher.release_notes()}
                        except (ValueError, OSError) as exc:
                            result = {**result, "notesNotice": public_text(exc)}
                elif action == "restore":
                    result = {"build": publisher.load_last_build(config, cancel=cancel)}
                elif action == "sdk-status":
                    result = sdk_status(self.repo_root)
                elif action in {"sdk-import", "sdk-download"}:
                    from .sdk import import_sdk, download_sdk
                    if cancel.is_set():
                        raise ValueError("작업을 취소했습니다.")
                    if action == "sdk-import":
                        result = import_sdk(self.repo_root, sdk_input, cancel=cancel, emit=emit)
                    else:
                        result = download_sdk(self.repo_root, sdk_input or None, cancel=cancel, emit=emit)
                else:
                    if cancel.is_set():
                        raise ValueError("작업을 취소했습니다.")
                    config_saved = publisher.save_config(config)
                    clean = config_saved if isinstance(config_saved, dict) else config
                    if action == "save":
                        result = clean
                    elif action == "connection":
                        result = publisher.check_connection(clean, cancel=cancel)
                    elif action == "preview":
                        result = publisher.preview_sync(clean, cancel=cancel)
                    elif action == "sync":
                        result = publisher.sync(clean, preview, cancel=cancel)
                    elif action == "build":
                        result = publisher.build(clean, cancel=cancel)
                    else:
                        if not token:
                            raise ValueError("게시용 토큰을 입력해 주세요. 토큰은 저장하지 않습니다.")
                        if action == "deploy":
                            ready_build = publisher.prepare_deploy(clean, build_result=build_result, cancel=cancel)
                            self._put({"job": serial, "kind": "build-ready", "build": public_result(ready_build, token),
                                       "configIdentity": build_identity(config)})
                        elif not build_result:
                            raise ValueError("먼저 설치 파일을 만들어 주세요.")
                        result = publisher.publish(clean, ready_build or build_result, token,
                                                   token_kind=clean.get("tokenKind", "auto"), cancel=cancel)
                        if not isinstance(result, dict) or result.get("verified") is not True:
                            raise ValueError("게시 파일의 인증 없는 다운로드를 확인하지 못했습니다. 완료로 표시하지 않았습니다.")
                        if action == "deploy":
                            result = {"build": ready_build, "publication": result, "message": result.get("message", "")}
                # Results use the core's non-secret data contract; no token is
                # ever put into this queue, configuration, traceback, or log.
                outcome = {"job": serial, "kind": "done", "action": action, "result": public_result(result, token),
                           "configIdentity": build_identity(config)}
            except Exception as exc:
                outcome = {"job": serial, "kind": "failed", "action": action,
                           "message": public_text(exc, token) or "작업을 마치지 못했습니다. 설정과 로그를 확인해 주세요."}
                if ready_build is not None:
                    outcome.update(build=public_result(ready_build, token), configIdentity=build_identity(config))
            with self._lock:
                self._busy = False
            self._put(outcome)

        self.thread = threading.Thread(target=worker, name="workspace-publisher-" + action, daemon=False)
        self.thread.start()
        return True

    def cancel(self):
        if self.busy:
            self.cancel_event.set()
            return True
        return False

    def drain(self):
        result = []
        while len(result) < 512:
            try:
                result.append(self.events.get_nowait())
            except queue.Empty:
                break
        return result


class PublisherWindow:
    def __init__(self, root, repo_root, factory):
        import tkinter as tk
        from tkinter import ttk, messagebox, filedialog
        from tkinter.scrolledtext import ScrolledText

        self.root, self.repo_root = root, Path(repo_root)
        self.tk, self.ttk, self.messagebox, self.filedialog = tk, ttk, messagebox, filedialog
        self.jobs = PublisherJobs(repo_root, factory)
        self.active_action = None
        self.closing = False
        self.closed = False
        self.build_result = None
        self.build_config_identity = None
        self.build_restored = False
        self.source = {}
        self.source_notice = ""
        self.sdk = {}
        self.release_notes = {}
        self.notes_version = ""
        self.notes_loaded = False
        self.notes_edited = False
        self.loaded = False
        self._setting_notes = False
        self.mutable = []
        self.buttons = []
        self.fields = {key: tk.StringVar(value=DEFAULT_CONFIG[key]) for key in
                       ("baseUrl", "projectId", "remoteName", "remoteUrl", "releaseTag", "title")}
        self.origins, self.token = tk.StringVar(), tk.StringVar()
        self.sdk_url = tk.StringVar()
        self.sdk_label = tk.StringVar(value="SDK를 확인하고 있어요.")
        self.token_kind = tk.StringVar(value=TOKEN_LABELS["auto"])
        self.status = tk.StringVar(value="저장된 설정과 현재 소스를 확인하고 있어요.")
        self.source_label = tk.StringVar(value=str(self.repo_root))
        self.build_label = tk.StringVar(value="게시할 설치 파일이 없습니다. 먼저 빌드해 주세요.")
        root.title("Company Workspace · 사내 배포")
        root.geometry("900x760")
        root.minsize(720, 570)
        root.configure(background="#f2f4f8")
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#f2f4f8")
        style.configure("TLabel", background="#f2f4f8", foreground="#293142", font=("맑은 고딕", 10))
        style.configure("Title.TLabel", font=("맑은 고딕", 18, "bold"))
        style.configure("Note.TLabel", foreground="#626d7d", font=("맑은 고딕", 9))
        style.configure("TButton", font=("맑은 고딕", 10), padding=(12, 8))
        style.configure("Publish.TButton", foreground="#ffffff", background="#626ad7")
        style.map("Publish.TButton", background=[("disabled", "#9ca2c9"), ("active", "#565dc1")])
        canvas = tk.Canvas(root, background="#f2f4f8", highlightthickness=0)
        scrollbar = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        outer = ttk.Frame(canvas, padding=22)
        content = canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content, width=event.width))
        ttk.Label(outer, text="사내에 새 버전 배포하기", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="서버 주소, 프로젝트 ID, 게시용 토큰을 입력하면 설치 파일을 만들고 게시합니다.", style="Note.TLabel").pack(anchor="w", pady=(6, 13))
        ttk.Label(outer, textvariable=self.source_label, style="Note.TLabel", wraplength=840).pack(anchor="w", pady=(0, 12))

        def field(frame, row, label, variable, *, readonly=False):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 15), pady=7)
            entry = ttk.Entry(frame, textvariable=variable, state="readonly" if readonly else "normal")
            entry.grid(row=row, column=1, sticky="ew", pady=7)
            if not readonly:
                self.mutable.append(entry)
            return entry

        basics = ttk.Frame(outer)
        basics.pack(fill="x", pady=(0, 8))
        basics.columnconfigure(1, weight=1)
        self.server_entry = field(basics, 0, "GitLab 서버 주소 (HTTPS)", self.fields["baseUrl"])
        self.project_entry = field(basics, 1, "프로젝트 ID (숫자)", self.fields["projectId"])
        ttk.Label(basics, text="예: https://gitlab.company.example · 그룹/저장소 경로는 제외하고, 프로젝트 화면의 숫자 ID를 입력하세요.",
                  style="Note.TLabel", wraplength=730).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(basics, text="게시용 토큰").grid(row=3, column=0, sticky="w", padx=(0, 15), pady=7)
        self.token_entry = ttk.Entry(basics, textvariable=self.token, show="●")
        self.token_entry.grid(row=3, column=1, sticky="ew", pady=7)
        self.mutable.append(self.token_entry)
        ttk.Label(basics, text="Personal / Project Access Token 또는 Deploy Token을 자동으로 구분합니다. 토큰은 저장하지 않습니다.",
                  style="Note.TLabel", wraplength=730).grid(row=4, column=0, columnspan=2, sticky="w")

        self.sdk_summary = ttk.Label(outer, textvariable=self.sdk_label, style="Note.TLabel", wraplength=790)
        self.sdk_summary.pack(anchor="w", pady=(8, 0))
        self.sdk_section = ttk.Frame(outer)
        self.sdk_section.pack(fill="x")
        self.sdk_frame = sdk_page = ttk.Frame(self.sdk_section, padding=(0, 10))
        sdk_page.columnconfigure(1, weight=1)
        ttk.Label(sdk_page, text="처음 한 번 SDK 준비가 필요합니다. 허용된 네트워크에서 직접 다운로드하거나\n"
                  "사내에 준비된 WebView2 SDK 파일을 선택해 주세요.",
                  style="Note.TLabel", wraplength=730).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        field(sdk_page, 1, "사내 SDK 주소 (선택)", self.sdk_url)
        ttk.Label(sdk_page, text="비우면 지정된 NuGet 주소를 사용합니다. 다운로드는 아래 버튼을 누를 때만 시작합니다.",
                  style="Note.TLabel", wraplength=730).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 8))
        sdk_buttons = ttk.Frame(sdk_page)
        sdk_buttons.grid(row=3, column=0, columnspan=2, sticky="w")
        self._button(sdk_buttons, "SDK 다운로드", self.download_sdk).pack(side="left", padx=(0, 8))
        self._button(sdk_buttons, "SDK 파일 선택", self.select_sdk).pack(side="left", padx=(0, 8))
        self._button(sdk_buttons, "상태 다시 확인", lambda: self.start("sdk-status")).pack(side="left")

        release_section = ttk.Frame(outer)
        release_section.pack(fill="x", pady=(12, 0))
        self.notes_toggle = self._button(release_section, "변경 내용 보기 ▸", self.toggle_notes)
        self.notes_toggle.pack(anchor="w")
        self.notes_frame = release = ttk.Frame(release_section, padding=(0, 10))
        release.columnconfigure(1, weight=1)
        field(release, 0, "안내 제목", self.fields["title"])
        ttk.Label(release, text="업데이트 내용").grid(row=2, column=0, sticky="nw", padx=(0, 15), pady=7)
        self.notes = ScrolledText(release, height=5, wrap="word", font=("맑은 고딕", 10), relief="solid", borderwidth=1)
        self.notes.grid(row=2, column=1, sticky="ew", pady=7)
        self.mutable.append(self.notes)
        note_buttons = ttk.Frame(release)
        note_buttons.grid(row=3, column=0, columnspan=2, sticky="w")
        self._button(note_buttons, "원문 복원", self.restore_notes).pack(side="left", padx=(0, 8))
        self._button(note_buttons, "이력 보기", self.show_history).pack(side="left")

        advanced_section = ttk.Frame(outer)
        advanced_section.pack(fill="x", pady=(6, 0))
        self.advanced_toggle = self._button(advanced_section, "고급 설정 ▸", self.toggle_advanced)
        self.advanced_toggle.pack(anchor="w")
        self.advanced_frame = advanced = ttk.Frame(advanced_section, padding=(0, 10))
        advanced.columnconfigure(1, weight=1)
        field(advanced, 0, "추가 다운로드 서버", self.origins)
        ttk.Label(advanced, text="게시 토큰 종류").grid(row=1, column=0, sticky="w", padx=(0, 15), pady=7)
        self.token_selector = ttk.Combobox(advanced, textvariable=self.token_kind, state="readonly", width=31,
                                          values=tuple(TOKEN_LABELS.values()))
        self.token_selector.grid(row=1, column=1, sticky="w", pady=7)
        self.mutable.append(self.token_selector)
        self.git_frame = git_fields = ttk.Frame(advanced)
        git_fields.grid(row=2, column=0, columnspan=2, sticky="ew")
        git_fields.columnconfigure(1, weight=1)
        self.remote_entry = field(git_fields, 0, "소스 반영 주소 (SSH · Git만)", self.fields["remoteUrl"])
        field(git_fields, 1, "소스 목적지 이름", self.fields["remoteName"])
        field(git_fields, 2, "현재 소스의 버전 태그", self.fields["releaseTag"], readonly=True)
        self.sync_button = self._button(git_fields, "소스 반영 (Git)", lambda: self.start("preview"))
        self.sync_button.grid(row=3, column=0, columnspan=2, sticky="w", pady=7)
        save_bar = ttk.Frame(advanced)
        save_bar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=10)
        self._button(save_bar, "설정 저장", lambda: self.start("save")).pack(side="right")
        self._button(save_bar, "소스 다시 확인", lambda: self.start("inspect")).pack(side="left")
        self._button(save_bar, "이전 빌드 불러오기", lambda: self.start("restore")).pack(side="left", padx=(8, 0))
        legacy_actions = ttk.Frame(advanced)
        legacy_actions.grid(row=4, column=0, columnspan=2, sticky="w")
        self._button(legacy_actions, "연결 확인", lambda: self.start("connection")).pack(side="left", padx=(0, 8))
        self.build_button = self._button(legacy_actions, "빌드만 하기", lambda: self.start("build"))
        self.build_button.pack(side="left", padx=(0, 8))
        self.publish_button = self._button(legacy_actions, "기존 파일 게시", lambda: self.start("publish"))
        self.publish_button.pack(side="left", padx=(0, 8))
        self._button(legacy_actions, "SDK 다시 확인", lambda: self.start("sdk-status")).pack(side="left")

        ttk.Label(outer, textvariable=self.build_label, style="Note.TLabel", wraplength=790).pack(anchor="w", pady=(12, 10))
        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        self.deploy_button = self._button(actions, "배포하기", lambda: self.start("deploy"), style="Publish.TButton")
        self.deploy_button.pack(side="left")
        self.cancel_button = ttk.Button(actions, text="작업 취소", command=self.cancel)
        self.cancel_button.pack(side="right")
        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill="x", pady=(17, 9))
        ttk.Label(outer, textvariable=self.status, wraplength=840).pack(anchor="w", pady=(0, 10))
        ttk.Label(outer, text="진행 기록", style="Note.TLabel").pack(anchor="w", pady=(0, 5))
        self.log = ScrolledText(outer, height=6, wrap="word", font=("맑은 고딕", 9), state="disabled", relief="solid", borderwidth=1)
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("error", foreground="#9c4144")
        self.log.tag_config("info", foreground="#3d485b")
        for variable in [*self.fields.values(), self.origins, self.token_kind]:
            variable.trace_add("write", lambda *_: self.refresh_controls())
        self.fields["title"].trace_add("write", self.mark_notes_edited)
        self.notes.bind("<<Modified>>", self.mark_notes_edited)
        self.start("load")
        root.after(80, self.poll)

    def _button(self, parent, text, command, **options):
        button = self.ttk.Button(parent, text=text, command=command, **options)
        self.buttons.append(button)
        return button

    def config(self):
        return {"schema": 1, **{key: variable.get().strip() for key, variable in self.fields.items()},
                "notes": self.notes.get("1.0", "end-1c"),
                "notesVersion": self.notes_version,
                "allowedDownloadOrigins": [item.strip() for item in self.origins.get().replace("\n", ",").split(",") if item.strip()],
                "tokenKind": next((key for key, label in TOKEN_LABELS.items() if label == self.token_kind.get()), "auto")}

    def toggle_notes(self):
        opened = bool(self.notes_frame.winfo_manager())
        self.notes_frame.pack_forget() if opened else self.notes_frame.pack(fill="x")
        self.notes_toggle.configure(text="변경 내용 보기 ▸" if opened else "변경 내용 접기 ▾")

    def toggle_advanced(self):
        opened = bool(self.advanced_frame.winfo_manager())
        self.advanced_frame.pack_forget() if opened else self.advanced_frame.pack(fill="x")
        self.advanced_toggle.configure(text="고급 설정 ▸" if opened else "고급 설정 접기 ▾")

    def mark_notes_edited(self, *args):
        if args and hasattr(args[0], "widget") and not self.notes.edit_modified():
            return
        if not self._setting_notes and self.notes_loaded:
            self.notes_edited = True
        if self.notes.edit_modified():
            self.notes.edit_modified(False)

    def _set_notes(self, title, notes):
        self._setting_notes = True
        try:
            self.fields["title"].set(title)
            self.notes.configure(state="normal")
            self.notes.delete("1.0", "end")
            self.notes.insert("1.0", notes)
            self.notes.edit_modified(False)
        finally:
            self._setting_notes = False

    def _release(self, result, *, apply=False):
        data = result.get("releaseNotes")
        if isinstance(data, dict):
            self.release_notes = deepcopy(data)
            if apply and not self.notes_edited:
                self._set_notes(data.get("title", ""), data.get("notes", ""))
                self.notes_version = data.get("version", "")
        if result.get("notesNotice"):
            self.append_log("변경 내용 원문 확인 필요: " + result["notesNotice"], "error")

    def restore_notes(self):
        if not self.release_notes:
            self.status.set("이 버전의 변경 내용 원문을 확인하지 못했습니다. 현재 입력은 유지합니다.")
            return
        self._set_notes(self.release_notes.get("title", ""), self.release_notes.get("notes", ""))
        self.notes_version = self.release_notes.get("version", "")
        self.notes_edited = False
        self.status.set("이 버전에 포함된 변경 내용 원문을 복원했습니다.")

    def show_history(self):
        from tkinter.scrolledtext import ScrolledText
        window = self.tk.Toplevel(self.root)
        window.title("버전별 변경 이력")
        window.geometry("700x520")
        window.transient(self.root)
        contents = ScrolledText(window, wrap="word", font=("맑은 고딕", 10), padx=16, pady=16)
        contents.pack(fill="both", expand=True)
        rows = self.release_notes.get("history", [])
        text = "\n\n".join(str(row.get("version", "")) + " · " + str(row.get("title", "")) +
                           "\n" + str(row.get("notes", "")) for row in rows if isinstance(row, dict))
        contents.insert("1.0", text or "이 소스에 포함된 이전 버전 안내가 없습니다.")
        contents.configure(state="disabled")
        self.ttk.Button(window, text="닫기", command=window.destroy).pack(anchor="e", padx=16, pady=10)

    def append_log(self, message, kind="info"):
        message = public_text(message, self.token.get())
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n", kind if kind == "error" else "info")
        if int(self.log.index("end-1c").split(".")[0]) > 500:
            self.log.delete("1.0", "100.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def refresh_controls(self):
        busy = self.active_action is not None or self.closing
        for button in self.buttons:
            button.configure(state="disabled" if busy else "normal")
        for control in self.mutable:
            control.configure(state="disabled" if busy else "readonly" if control is self.token_selector else "normal")
        if not busy:
            self.sync_button.configure(state="normal" if source_can_sync(self.source) else "disabled")
            self.remote_entry.configure(state="normal" if source_can_sync(self.source) else "disabled")
            self.git_frame.grid() if source_can_sync(self.source) else self.git_frame.grid_remove()
            self.build_button.configure(state="normal" if self.source and self.sdk.get("status") == "ready" else "disabled")
            valid_build = self.build_result is not None and self.build_config_identity == build_identity(self.config())
            self.publish_button.configure(state="normal" if valid_build else "disabled")
            can_deploy = valid_build or bool(self.source and self.sdk.get("status") == "ready")
            self.deploy_button.configure(state="normal" if can_deploy else "disabled")
            if self.build_result is None:
                self.build_label.set("배포하기를 누르면 설치 파일 생성과 게시를 이어서 진행합니다.")
            else:
                built = self.build_result
                source_type = "소스 ZIP · SHA256" if source_is_archive(built) else "Git"
                label = f"게시할 설치 파일  {built.get('version', '')}   |   {source_type} {source_identifier(built)[:12]}"
                if self.build_restored:
                    label += "   |   이전 빌드 복원됨"
                if not valid_build:
                    label += "\n설정이 바뀌었습니다. 이전 빌드를 다시 확인하거나 새로 빌드해 주세요."
                elif self.source and source_identifier(built) != source_identifier(self.source):
                    label += "\n현재 소스와 다른 " + ("소스 ZIP" if source_is_archive(built) else "커밋") + "의 파일입니다. 위 버전으로 게시를 이어갑니다."
                self.build_label.set(label)
        self.cancel_button.configure(state="normal" if self.jobs.busy and not self.closing else "disabled")

    def select_sdk(self):
        if self.active_action is not None or self.closing:
            return
        path = self.filedialog.askopenfilename(parent=self.root, title="준비된 WebView2 SDK 선택",
                                              filetypes=(("NuGet SDK 파일", "*.nupkg"),))
        if path:
            self.start("sdk-import", sdk_input=path)

    def download_sdk(self):
        if self.active_action is not None or self.closing:
            return
        self.start("sdk-download", sdk_input=self.sdk_url.get().strip())

    def start(self, action, *, preview=None, sdk_input=""):
        if self.active_action is not None or self.closing:
            return
        if action in {"preview", "sync"} and not source_can_sync(self.source):
            self.status.set("Download ZIP에는 Git 이력이 없습니다. 소스 반영을 건너뛰고 빌드와 게시를 진행하세요.")
            return
        if action in {"build", "deploy"} and not self.source and not (action == "deploy" and self.build_result):
            self.status.set("현재 소스를 확인하지 못해 새로 빌드할 수 없습니다. 원본 ZIP을 복원하고 소스 다시 확인을 눌러 주세요.")
            return
        if action in {"build", "deploy"} and self.sdk.get("status") != "ready" and not (action == "deploy" and self.build_result):
            self.status.set("먼저 SDK 준비에서 파일을 선택하거나 다운로드해 주세요.")
            return
        config = self.config()
        token = self.token.get() if action in {"publish", "deploy"} else ""
        if action == "publish" and (not self.build_result or self.build_config_identity != build_identity(config)):
            self.status.set("현재 설정으로 먼저 빌드해 주세요.")
            return
        if action in {"publish", "deploy"} and not token.strip():
            self.status.set("게시용 토큰을 입력해 주세요. 토큰은 저장하지 않습니다.")
            self.token_entry.focus_set()
            return
        candidate = self.build_result if self.build_config_identity == build_identity(config) else None
        if self.jobs.start(action, config, preview=preview, build_result=candidate, token=token, sdk_input=sdk_input):
            self.active_action = action
            if action == "restore":
                self.build_result = None
                self.build_config_identity = None
            if action in {"publish", "deploy"}:
                self.token.set("")
            self.status.set(ACTION_NAMES[action] + " 중입니다…")
            self.append_log(ACTION_NAMES[action] + "을 시작합니다.")
            self.progress.start(12)
            self.refresh_controls()

    def _source(self, source, notice=""):
        self.source = source or {}
        self.source_notice = notice
        if notice:
            self.source_label.set("현재 소스 확인 필요 · " + public_text(notice) + "\n"
                                  "검증된 이전 빌드가 있으면 같은 파일로 게시를 다시 시도할 수 있습니다.")
            self.append_log("현재 소스 확인 필요: " + notice, "error")
            return
        version = self.source.get("version", "확인 전")
        commit = source_identifier(self.source)[:12]
        branch = self.source.get("branch", "확인 전")
        state = "반영하지 않은 변경 있음" if self.source.get("clean") is False else ""
        if source_is_archive(self.source):
            self.source_label.set(f"현재 소스  {version}   |   Download ZIP   |   SHA256 {commit}")
        else:
            self.source_label.set(f"현재 소스  {version}   |   {branch}   |   {commit}   {state}\n{self.repo_root}")
        self.fields["releaseTag"].set(self.source.get("releaseTag") or "")

    def _sdk(self, result):
        self.sdk = result or {}
        message = self.sdk.get("message") or ("SDK가 준비되었습니다." if self.sdk.get("status") == "ready" else "SDK 파일을 준비해 주세요.")
        ready = self.sdk.get("status") == "ready"
        self.sdk_label.set("SDK 준비됨" + (" · " + str(self.sdk.get("version")) if self.sdk.get("version") else "") if ready else public_text(message))
        self.sdk_frame.pack_forget() if ready else self.sdk_frame.pack(fill="x")

    def done(self, action, result, identity):
        result = result if isinstance(result, dict) else {}
        if action == "load":
            config = settings_only(result.get("config", {}))
            if not self.loaded:
                for key, variable in self.fields.items():
                    variable.set(config[key])
                self.origins.set(", ".join(config["allowedDownloadOrigins"]))
                kind = config["tokenKind"] if config["tokenKind"] in {"access", "job"} else "auto"
                self.token_kind.set(TOKEN_LABELS[kind])
                self._set_notes(config["title"], config["notes"])
                self.notes_version = config["notesVersion"]
                self._release(result, apply=not bool(config["notesVersion"] or config["title"] or config["notes"]))
                if self.release_notes:
                    self.notes_edited = (self.fields["title"].get(), self.notes.get("1.0", "end-1c")) != (
                        self.release_notes.get("title", ""), self.release_notes.get("notes", ""))
                self.notes_loaded = True
            else:
                self._release(result)
            self._source(result.get("source"), result.get("sourceNotice", ""))
            self._sdk(result.get("sdk"))
            if not self.loaded or build_identity(config) == build_identity(self.config()):
                self._restore_build(result, build_identity(self.config()))
            self.loaded = True
            return
        elif action == "restore":
            self._restore_build(result, identity)
            return
        elif action == "inspect":
            self._source(result)
            self._release(result, apply=not self.notes_edited)
        elif action.startswith("sdk-"):
            self._sdk(result)
        elif action == "preview":
            self.preview_dialog(result)
            return
        elif action == "build":
            self.build_result, self.build_config_identity = result, identity
            self.build_restored = False
            self.append_log(f"버전 {result.get('version', '')} · 파일 {len(result.get('files', []))}개\n{result.get('directory', '')}")
        elif action == "deploy":
            self.build_result, self.build_config_identity = result.get("build"), identity
        elif action == "sync":
            self.build_result = None
        message = result.get("message") or ACTION_NAMES[action] + "을 마쳤습니다."
        if action in {"publish", "deploy"}:
            message = "게시 완료 · 인증 없는 다운로드 검증까지 마쳤습니다."
        self.status.set(public_text(message))
        self.append_log(message)

    def _restore_build(self, result, identity):
        self.build_result = result.get("build")
        self.build_config_identity = identity if self.build_result is not None else None
        self.build_restored = self.build_result is not None
        if self.build_restored:
            self.append_log(f"이전 빌드 검증 완료 · 버전 {self.build_result.get('version', '')} · "
                            f"소스 {source_identifier(self.build_result)}\n{self.build_result.get('directory', '')}")
            message = "이전 빌드를 확인했습니다. 게시용 토큰을 입력하고 배포하기를 누르면 이어갑니다."
        elif result.get("buildNotice"):
            message = "이전 빌드를 복원하지 못했습니다. " + result["buildNotice"]
        elif self.source_notice:
            message = "설정을 불러왔습니다. 게시할 이전 빌드가 없어 원본 ZIP을 복원하고 소스를 다시 확인해 주세요."
        else:
            message = "배포 준비가 되었습니다. 서버 주소, 프로젝트 ID, 게시용 토큰을 입력해 주세요."
        self.status.set(public_text(message))
        self.append_log(message, "error" if result.get("buildNotice") else "info")

    def preview_dialog(self, preview):
        self.status.set("반영할 소스와 목적지를 확인해 주세요.")
        details = (f"목적지: {preview.get('remoteUrl', '')}\n"
                   f"브랜치: {preview.get('branch', '')}\n버전: {preview.get('version', '')}\n"
                   f"소스: {preview.get('commit', '')}\n태그: {preview.get('tag') or '없음 · 브랜치만 반영'}")
        summary = preview.get("summary")
        if summary:
            details += "\n\n" + ("\n".join(map(str, summary)) if isinstance(summary, list) else str(summary))
        window = self.tk.Toplevel(self.root)
        window.title("반영할 소스 확인")
        window.transient(self.root)
        window.resizable(False, False)
        area = self.ttk.Frame(window, padding=20)
        area.pack(fill="both", expand=True)
        self.ttk.Label(area, text="이 소스를 사내 저장소에 반영합니다.", font=("맑은 고딕", 12, "bold")).pack(anchor="w")
        self.ttk.Label(area, text=public_text(details, self.token.get()), wraplength=620).pack(anchor="w", pady=16)
        row = self.ttk.Frame(area)
        row.pack(fill="x")
        def dismiss():
            window.grab_release()
            window.destroy()
        def apply():
            dismiss()
            self.start("sync", preview=preview)
        self.ttk.Button(row, text="닫기", command=dismiss).pack(side="right")
        self.ttk.Button(row, text="이 소스 반영", command=apply).pack(side="right", padx=(0, 9))
        window.protocol("WM_DELETE_WINDOW", dismiss)
        window.bind("<Escape>", lambda _event: dismiss())
        window.grab_set()
        window.focus_set()

    def poll(self):
        if self.closed:
            return
        for event in self.jobs.drain():
            kind = event["kind"]
            if kind == "build-ready":
                self.build_result = event.get("build")
                self.build_config_identity = event.get("configIdentity")
                self.build_restored = False
                if self.build_result:
                    self.append_log("게시할 설치 파일을 확인했습니다.\n" + str(self.build_result.get("directory", "")))
            elif kind in {"done", "failed"}:
                self.active_action = None
                self.progress.stop()
                if self.closing:
                    self.destroy()
                    return
                if kind == "failed":
                    if event.get("build"):
                        self.build_result = event["build"]
                        self.build_config_identity = event.get("configIdentity")
                    if event["action"] == "inspect":
                        self._source(None, event["message"])
                    self.status.set(event["message"])
                    self.append_log(event["message"], "error")
                    if event["action"] == "deploy" and self.build_result:
                        self.append_log("완성된 설치 파일을 보관했습니다. 토큰을 다시 입력하고 배포하기를 눌러 같은 파일로 재시도하세요.")
                else:
                    self.done(event["action"], event.get("result"), event.get("configIdentity"))
                self.refresh_controls()
            else:
                self.append_log(event["message"], kind)
                if kind == "progress" and not self.closing:
                    self.status.set(event["message"])
        self.root.after(80, self.poll)

    def cancel(self):
        if self.jobs.cancel():
            self.status.set("취소를 요청했습니다. 현재 단계를 안전하게 마무리하고 있어요.")
            self.cancel_button.configure(state="disabled")

    def close(self):
        if self.closing or self.closed:
            return
        if self.active_action is not None:
            if not self.messagebox.askyesno("작업 취소 후 닫기", "진행 중인 작업을 취소하고 닫을까요?\n"
                "이미 반영하거나 게시한 내용은 유지합니다. 안전하게 멈춘 뒤 창을 닫습니다.", parent=self.root):
                return
            self.closing = True
            self.jobs.cancel()
            self.status.set("작업을 안전하게 멈춘 뒤 닫습니다. 잠시 기다려 주세요.")
            self.refresh_controls()
            return
        self.destroy()

    def destroy(self):
        self.token.set("")
        self.closed = True
        self.root.destroy()


def main(repo_root=None):
    try:
        import tkinter as tk
    except ImportError:
        print("이 Python에는 배포 창을 여는 tkinter가 없습니다. 터미널 방식으로도 진행할 수 있습니다.\n"
              "python -X utf8 Publish-Workspace.py --cli --help")
        return 1
    from .core import Publisher
    try:
        root = tk.Tk()
    except tk.TclError:
        print("이 환경에서 배포 창을 열지 못했습니다. 터미널 방식으로 진행할 수 있습니다.\n"
              "python -X utf8 Publish-Workspace.py --cli --help")
        return 1
    PublisherWindow(root, Path(repo_root) if repo_root else Path(__file__).resolve().parents[1], Publisher)
    root.mainloop()
    return 0
