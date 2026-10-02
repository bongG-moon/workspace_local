"""Small stdlib desktop publisher; workers never touch Tk or persist tokens."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import queue
import threading
from urllib.parse import quote, quote_plus


CONFIG_KEYS = ("schema", "baseUrl", "projectId", "remoteName", "remoteUrl", "releaseTag",
               "title", "notes", "allowedDownloadOrigins", "tokenKind")
DEFAULT_CONFIG = {"schema": 1, "baseUrl": "", "projectId": "", "remoteName": "intranet",
                  "remoteUrl": "", "releaseTag": "", "title": "", "notes": "",
                  "allowedDownloadOrigins": [], "tokenKind": "deploy"}
ACTION_NAMES = {"load": "설정과 소스 확인", "inspect": "소스 확인", "save": "설정 저장",
                "restore": "이전 빌드 확인",
                "connection": "연결 확인", "preview": "반영할 소스 확인", "sync": "소스 반영",
                "build": "설치 파일 만들기", "publish": "게시와 다운로드 검증"}


def settings_only(value):
    """Only the public settings contract can reach save_config()."""
    return {key: deepcopy(value.get(key, DEFAULT_CONFIG[key])) for key in CONFIG_KEYS}


def build_identity(config):
    return json.dumps({key: value for key, value in settings_only(config).items()
                       if key not in {"title", "notes", "tokenKind"}}, sort_keys=True, ensure_ascii=True)


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
                if str(key).lower() not in {"token", "password", "authorization", "deploy-token", "job-token"}}
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

    def start(self, action, config=None, *, preview=None, build_result=None, token=""):
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
            def emit(event):
                if not isinstance(event, dict):
                    return
                kind = event.get("kind", "info")
                self._put({"job": serial, "kind": kind if kind in {"progress", "info", "error"} else "info",
                           "message": public_text(event.get("message", ""), token)})
            try:
                publisher = self.factory(self.repo_root, emit=emit)
                if action == "load":
                    result = {"config": publisher.load_config(), "source": publisher.inspect_source()}
                    try:
                        result["build"] = publisher.load_last_build(result["config"], cancel=cancel)
                    except ValueError as exc:
                        # A damaged or differently targeted build must not hide
                        # the saved settings or block rebuilding in the window.
                        result["build"] = None
                        result["buildNotice"] = public_text(exc)
                elif action == "inspect":
                    result = publisher.inspect_source()
                elif action == "restore":
                    result = {"build": publisher.load_last_build(config, cancel=cancel)}
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
                        if not build_result:
                            raise ValueError("먼저 설치 파일을 만들어 주세요.")
                        result = publisher.publish(clean, build_result, token,
                                                   token_kind=clean.get("tokenKind", "deploy"), cancel=cancel)
                        if not isinstance(result, dict) or result.get("verified") is not True:
                            raise ValueError("게시 파일의 인증 없는 다운로드를 확인하지 못했습니다. 완료로 표시하지 않았습니다.")
                # Results use the core's non-secret data contract; no token is
                # ever put into this queue, configuration, traceback, or log.
                outcome = {"job": serial, "kind": "done", "action": action, "result": public_result(result, token),
                           "configIdentity": build_identity(config)}
            except Exception as exc:
                outcome = {"job": serial, "kind": "failed", "action": action,
                           "message": public_text(exc, token) or "작업을 마치지 못했습니다. 설정과 로그를 확인해 주세요."}
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
        from tkinter import ttk, messagebox
        from tkinter.scrolledtext import ScrolledText

        self.root, self.repo_root = root, Path(repo_root)
        self.tk, self.ttk, self.messagebox = tk, ttk, messagebox
        self.jobs = PublisherJobs(repo_root, factory)
        self.active_action = None
        self.closing = False
        self.closed = False
        self.build_result = None
        self.build_config_identity = None
        self.build_restored = False
        self.source = {}
        self.mutable = []
        self.buttons = []
        self.fields = {key: tk.StringVar(value=DEFAULT_CONFIG[key]) for key in
                       ("baseUrl", "projectId", "remoteName", "remoteUrl", "releaseTag", "title")}
        self.origins, self.token = tk.StringVar(), tk.StringVar()
        self.token_kind = tk.StringVar(value="배포 토큰 (Deploy Token)")
        self.status = tk.StringVar(value="저장된 설정과 현재 소스를 확인하고 있어요.")
        self.source_label = tk.StringVar(value=str(self.repo_root))
        self.build_label = tk.StringVar(value="게시할 설치 파일이 없습니다. 먼저 빌드해 주세요.")
        root.title("Company Workspace · 사내 배포")
        root.geometry("920x850")
        root.minsize(760, 680)
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
        outer = ttk.Frame(root, padding=22)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="사내에 새 버전 배포하기", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="연결 확인 → 소스 반영 → 설치 파일 만들기 → 게시", style="Note.TLabel").pack(anchor="w", pady=(6, 13))
        ttk.Label(outer, textvariable=self.source_label, style="Note.TLabel", wraplength=840).pack(anchor="w", pady=(0, 12))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="x")
        settings, release, advanced = (ttk.Frame(notebook, padding=16) for _ in range(3))
        notebook.add(settings, text="배포 서버 설정")
        notebook.add(release, text="이번 버전 안내")
        notebook.add(advanced, text="추가 설정")
        for frame in (settings, release, advanced):
            frame.columnconfigure(1, weight=1)

        def field(frame, row, label, variable, *, readonly=False):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 15), pady=7)
            entry = ttk.Entry(frame, textvariable=variable, state="readonly" if readonly else "normal")
            entry.grid(row=row, column=1, sticky="ew", pady=7)
            if not readonly:
                self.mutable.append(entry)
            return entry

        field(settings, 0, "GitLab 웹 주소", self.fields["baseUrl"])
        field(settings, 1, "프로젝트 ID", self.fields["projectId"])
        field(settings, 2, "소스 반영 주소 (SSH)", self.fields["remoteUrl"])
        ttk.Label(settings, text="프로젝트의 웹 주소와 프로젝트 ID, SSH 복제 주소를 입력해 주세요.\n"
                  "설정은 이 폴더의 build/publisher/config.json에만 저장하며 소스에 포함하지 않습니다.",
                  style="Note.TLabel", wraplength=730).grid(row=3, column=0, columnspan=2, sticky="w", pady=(7, 0))
        field(release, 0, "현재 소스의 버전 태그", self.fields["releaseTag"], readonly=True)
        field(release, 1, "안내 제목", self.fields["title"])
        ttk.Label(release, text="업데이트 내용").grid(row=2, column=0, sticky="nw", padx=(0, 15), pady=7)
        self.notes = ScrolledText(release, height=5, wrap="word", font=("맑은 고딕", 10), relief="solid", borderwidth=1)
        self.notes.grid(row=2, column=1, sticky="ew", pady=7)
        self.mutable.append(self.notes)
        field(advanced, 0, "소스 목적지 이름", self.fields["remoteName"])
        field(advanced, 1, "추가 다운로드 서버", self.origins)
        ttk.Label(advanced, text="다운로드가 별도 저장소를 사용하는 경우에만 https://주소를 쉼표로 구분해 입력하세요.\n"
                  "개인·프로젝트 액세스 토큰 대신 게시 전용 배포 토큰 또는 CI 작업 토큰을 사용합니다.",
                  style="Note.TLabel", wraplength=730).grid(row=2, column=0, columnspan=2, sticky="w", pady=7)
        save_bar = ttk.Frame(outer)
        save_bar.pack(fill="x", pady=10)
        self._button(save_bar, "설정 저장", lambda: self.start("save")).pack(side="right")
        self._button(save_bar, "소스 다시 확인", lambda: self.start("inspect")).pack(side="left")
        self._button(save_bar, "이전 빌드 불러오기", lambda: self.start("restore")).pack(side="left", padx=(8, 0))
        ttk.Label(outer, textvariable=self.build_label, style="Note.TLabel", wraplength=840).pack(anchor="w", pady=(0, 10))

        token_frame = ttk.Frame(outer)
        token_frame.pack(fill="x", pady=(0, 5))
        ttk.Label(token_frame, text="게시용 토큰").pack(side="left", padx=(0, 12))
        self.token_entry = ttk.Entry(token_frame, textvariable=self.token, show="●")
        self.token_entry.pack(side="left", fill="x", expand=True)
        self.mutable.append(self.token_entry)
        self.token_selector = ttk.Combobox(token_frame, textvariable=self.token_kind, state="readonly", width=25,
                                          values=("배포 토큰 (Deploy Token)", "CI 작업 토큰 (Job Token)"))
        self.token_selector.pack(side="left", padx=(10, 0))
        self.mutable.append(self.token_selector)
        ttk.Label(outer, text="토큰은 게시할 때만 사용하며 파일·설정·로그에 저장하지 않습니다. 게시 시도 후 입력칸을 비웁니다.",
                  style="Note.TLabel").pack(anchor="w", pady=(0, 14))

        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        for label, action in (("1  연결 확인", "connection"), ("2  소스 반영", "preview"), ("3  빌드", "build")):
            self._button(actions, label, lambda action=action: self.start(action)).pack(side="left", padx=(0, 8))
        self.publish_button = self._button(actions, "4  게시", lambda: self.start("publish"), style="Publish.TButton")
        self.publish_button.pack(side="left", padx=(0, 8))
        self.cancel_button = ttk.Button(actions, text="작업 취소", command=self.cancel)
        self.cancel_button.pack(side="right")
        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill="x", pady=(17, 9))
        ttk.Label(outer, textvariable=self.status, wraplength=840).pack(anchor="w", pady=(0, 10))
        ttk.Label(outer, text="진행 기록", style="Note.TLabel").pack(anchor="w", pady=(0, 5))
        self.log = ScrolledText(outer, height=8, wrap="word", font=("맑은 고딕", 9), state="disabled", relief="solid", borderwidth=1)
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("error", foreground="#9c4144")
        self.log.tag_config("info", foreground="#3d485b")
        for variable in [*self.fields.values(), self.origins, self.token_kind]:
            variable.trace_add("write", lambda *_: self.refresh_controls())
        self.start("load")
        root.after(80, self.poll)

    def _button(self, parent, text, command, **options):
        button = self.ttk.Button(parent, text=text, command=command, **options)
        self.buttons.append(button)
        return button

    def config(self):
        return {"schema": 1, **{key: variable.get().strip() for key, variable in self.fields.items()},
                "notes": self.notes.get("1.0", "end-1c"),
                "allowedDownloadOrigins": [item.strip() for item in self.origins.get().replace("\n", ",").split(",") if item.strip()],
                "tokenKind": "job" if self.token_kind.get().startswith("CI") else "deploy"}

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
            valid_build = self.build_result is not None and self.build_config_identity == build_identity(self.config())
            self.publish_button.configure(state="normal" if valid_build else "disabled")
            if self.build_result is None:
                self.build_label.set("게시할 설치 파일이 없습니다. 먼저 빌드하거나 이전 빌드를 불러와 주세요.")
            else:
                built = self.build_result
                label = f"게시할 설치 파일  {built.get('version', '')}   |   {str(built.get('commit', ''))[:12]}"
                if self.build_restored:
                    label += "   |   이전 빌드 복원됨"
                if not valid_build:
                    label += "\n설정이 바뀌었습니다. 이전 빌드를 다시 확인하거나 새로 빌드해 주세요."
                elif built.get("commit") != self.source.get("commit"):
                    label += "\n현재 소스와 다른 커밋의 파일입니다. 위 버전으로 게시를 이어갑니다."
                self.build_label.set(label)
        self.cancel_button.configure(state="normal" if self.jobs.busy and not self.closing else "disabled")

    def start(self, action, *, preview=None):
        if self.active_action is not None or self.closing:
            return
        config = self.config()
        token = self.token.get() if action == "publish" else ""
        if action == "publish" and (not self.build_result or self.build_config_identity != build_identity(config)):
            self.status.set("현재 설정으로 먼저 빌드해 주세요.")
            return
        if action == "publish" and not token.strip():
            self.status.set("게시용 토큰을 입력해 주세요. 토큰은 저장하지 않습니다.")
            self.token_entry.focus_set()
            return
        if self.jobs.start(action, config, preview=preview, build_result=self.build_result, token=token):
            self.active_action = action
            if action == "restore":
                self.build_result = None
                self.build_config_identity = None
            if action == "publish":
                self.token.set("")
            self.status.set(ACTION_NAMES[action] + " 중입니다…")
            self.append_log(ACTION_NAMES[action] + "을 시작합니다.")
            self.progress.start(12)
            self.refresh_controls()

    def _source(self, source):
        self.source = source or {}
        version = self.source.get("version", "확인 전")
        commit = str(self.source.get("commit", ""))[:12]
        branch = self.source.get("branch", "확인 전")
        state = "반영하지 않은 변경 있음" if self.source.get("clean") is False else ""
        self.source_label.set(f"현재 소스  {version}   |   {branch}   |   {commit}   {state}\n{self.repo_root}")
        self.fields["releaseTag"].set(self.source.get("releaseTag") or "")

    def done(self, action, result, identity):
        result = result if isinstance(result, dict) else {}
        if action == "load":
            config = settings_only(result.get("config", {}))
            for key, variable in self.fields.items():
                variable.set(config[key])
            self.origins.set(", ".join(config["allowedDownloadOrigins"]))
            self.token_kind.set("CI 작업 토큰 (Job Token)" if config["tokenKind"] == "job" else "배포 토큰 (Deploy Token)")
            self.notes.configure(state="normal")
            self.notes.delete("1.0", "end")
            self.notes.insert("1.0", config["notes"])
            self._source(result.get("source"))
            self._restore_build(result, build_identity(self.config()))
            return
        elif action == "restore":
            self._restore_build(result, identity)
            return
        elif action == "inspect":
            self._source(result)
            self.build_result = None
        elif action == "preview":
            self.preview_dialog(result)
            return
        elif action == "build":
            self.build_result, self.build_config_identity = result, identity
            self.build_restored = False
            self.append_log(f"버전 {result.get('version', '')} · 파일 {len(result.get('files', []))}개\n{result.get('directory', '')}")
        elif action == "sync":
            self.build_result = None
        message = result.get("message") or ACTION_NAMES[action] + "을 마쳤습니다."
        if action == "publish":
            message = "게시 완료 · 인증 없는 다운로드 검증까지 마쳤습니다."
        self.status.set(public_text(message))
        self.append_log(message)

    def _restore_build(self, result, identity):
        self.build_result = result.get("build")
        self.build_config_identity = identity if self.build_result is not None else None
        self.build_restored = self.build_result is not None
        if self.build_restored:
            self.append_log(f"이전 빌드 검증 완료 · 버전 {self.build_result.get('version', '')} · "
                            f"소스 {self.build_result.get('commit', '')}\n{self.build_result.get('directory', '')}")
            message = "이전 빌드를 확인했습니다. 게시용 토큰을 입력하고 게시하면 같은 파일로 이어갑니다."
        elif result.get("buildNotice"):
            message = "이전 빌드를 복원하지 못했습니다. " + result["buildNotice"]
        else:
            message = "설정과 소스를 확인했습니다. 저장된 이전 빌드가 없어 먼저 빌드해 주세요."
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
            if kind in {"done", "failed"}:
                self.active_action = None
                self.progress.stop()
                if self.closing:
                    self.destroy()
                    return
                if kind == "failed":
                    self.status.set(event["message"])
                    self.append_log(event["message"], "error")
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
    import tkinter as tk
    from .core import Publisher
    root = tk.Tk()
    PublisherWindow(root, Path(repo_root) if repo_root else Path(__file__).resolve().parents[1], Publisher)
    root.mainloop()
    return 0
