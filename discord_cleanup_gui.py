"""
discord_cleanup.py のTkinter GUIラッパー。
========================================
コアのAPIロジックは discord_cleanup.py のものをそのまま再利用し、
このファイルは入力フォーム・実行ボタン・ログ表示だけを担当する。

実行:
    python discord_cleanup_gui.py
"""

import queue
import sys
import threading
import types
from tkinter import (
    Tk, StringVar, BooleanVar, IntVar, N, S, E, W, END, DISABLED, NORMAL,
    messagebox,
)
from tkinter import ttk, scrolledtext

import discord_cleanup as core


class QueueWriter:
    """print()の出力をスレッドセーフにキューへ流し込むためのstdout代替。"""

    def __init__(self, q):
        self.q = q

    def write(self, text):
        if text:
            self.q.put(text)

    def flush(self):
        pass


class App:
    def __init__(self, root):
        self.root = root
        root.title("Discord Cleanup")
        root.geometry("760x600")

        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.stop_requested = False

        self._build_form()
        self._build_log_area()
        self._poll_log_queue()

    # ---------- UI構築 ----------

    def _build_form(self):
        frame = ttk.Frame(self.root, padding=10)
        frame.grid(row=0, column=0, sticky=(N, S, E, W))
        self.root.columnconfigure(0, weight=1)

        row = 0
        ttk.Label(frame, text="Discordトークン:").grid(row=row, column=0, sticky=W)
        self.token_var = StringVar()
        self.token_entry = ttk.Entry(frame, textvariable=self.token_var, show="*", width=55)
        self.token_entry.grid(row=row, column=1, columnspan=2, sticky=(E, W), padx=5)
        self.show_token_var = BooleanVar(value=False)
        ttk.Checkbutton(
            frame, text="表示", variable=self.show_token_var, command=self._toggle_token_visibility
        ).grid(row=row, column=3, sticky=W)
        row += 1

        ttk.Label(
            frame,
            text="※ パスワード変更・全デバイスログアウト・2FA設定後に新しく取得したトークンを使用してください",
            foreground="#a04000",
        ).grid(row=row, column=0, columnspan=4, sticky=W, pady=(0, 8))
        row += 1

        ttk.Label(frame, text="開始日 (YYYY-MM-DD, 任意/UTC):").grid(row=row, column=0, sticky=W)
        self.start_var = StringVar()
        ttk.Entry(frame, textvariable=self.start_var, width=20).grid(row=row, column=1, sticky=W, padx=5)
        row += 1

        ttk.Label(frame, text="終了日 (YYYY-MM-DD, 任意/UTC):").grid(row=row, column=0, sticky=W)
        self.end_var = StringVar()
        ttk.Entry(frame, textvariable=self.end_var, width=20).grid(row=row, column=1, sticky=W, padx=5)
        row += 1

        ttk.Label(frame, text="対象範囲:").grid(row=row, column=0, sticky=W)
        self.scope_var = StringVar(value="all")
        scope_frame = ttk.Frame(frame)
        scope_frame.grid(row=row, column=1, columnspan=2, sticky=W, padx=5)
        ttk.Radiobutton(scope_frame, text="サーバー+DM", variable=self.scope_var, value="all").pack(
            side="left", padx=(0, 10)
        )
        ttk.Radiobutton(scope_frame, text="サーバーのみ", variable=self.scope_var, value="guilds").pack(
            side="left", padx=(0, 10)
        )
        ttk.Radiobutton(scope_frame, text="DMのみ", variable=self.scope_var, value="dms").pack(side="left")
        row += 1

        ttk.Label(frame, text="繰り返し回数(passes):").grid(row=row, column=0, sticky=W)
        self.passes_var = IntVar(value=1)
        ttk.Spinbox(frame, from_=1, to=10, textvariable=self.passes_var, width=5).grid(
            row=row, column=1, sticky=W, padx=5
        )
        row += 1

        ttk.Label(frame, text="パス間待機秒数:").grid(row=row, column=0, sticky=W)
        self.wait_var = IntVar(value=30)
        ttk.Spinbox(frame, from_=0, to=300, textvariable=self.wait_var, width=5).grid(
            row=row, column=1, sticky=W, padx=5
        )
        row += 1

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=row, column=0, columnspan=4, pady=10, sticky=W)
        self.list_btn = ttk.Button(btn_frame, text="一覧表示のみ (--list)", command=self.run_list)
        self.list_btn.grid(row=0, column=0, padx=(0, 8))
        self.delete_btn = ttk.Button(btn_frame, text="削除を実行 (--delete)", command=self.run_delete)
        self.delete_btn.grid(row=0, column=1, padx=(0, 8))
        self.clear_btn = ttk.Button(btn_frame, text="ログをクリア", command=self.clear_log)
        self.clear_btn.grid(row=0, column=2)

        self.status_var = StringVar(value="待機中")
        ttk.Label(frame, textvariable=self.status_var, foreground="#0060c0").grid(
            row=row + 1, column=0, columnspan=4, sticky=W
        )

        for c in range(4):
            frame.columnconfigure(c, weight=1 if c == 1 else 0)

    def _build_log_area(self):
        self.log_widget = scrolledtext.ScrolledText(self.root, state=DISABLED, wrap="word")
        self.log_widget.grid(row=1, column=0, sticky=(N, S, E, W), padx=10, pady=(0, 10))
        self.root.rowconfigure(1, weight=1)

    def _toggle_token_visibility(self):
        self.token_entry.config(show="" if self.show_token_var.get() else "*")

    # ---------- ログ表示 ----------

    def _poll_log_queue(self):
        try:
            while True:
                text = self.log_queue.get_nowait()
                self.log_widget.config(state=NORMAL)
                self.log_widget.insert(END, text)
                self.log_widget.see(END)
                self.log_widget.config(state=DISABLED)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)

    def clear_log(self):
        self.log_widget.config(state=NORMAL)
        self.log_widget.delete("1.0", END)
        self.log_widget.config(state=DISABLED)

    # ---------- 実行 ----------

    def run_list(self):
        self._run(is_delete=False)

    def run_delete(self):
        if not messagebox.askyesno(
            "確認",
            "実際にメッセージ/イベントを削除します。よろしいですか?\n"
            "(先に「一覧表示のみ」で対象を確認することを推奨します)",
        ):
            return
        self._run(is_delete=True)

    def _run(self, is_delete):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning("実行中", "既に処理を実行中です。完了までお待ちください。")
            return

        token = self.token_var.get().strip()
        if not token:
            messagebox.showerror("エラー", "Discordトークンを入力してください。")
            return

        args = types.SimpleNamespace(
            list=not is_delete,
            delete=is_delete,
            start=self.start_var.get().strip() or None,
            end=self.end_var.get().strip() or None,
            scope=self.scope_var.get(),
        )
        passes = self.passes_var.get()
        wait_between = self.wait_var.get()

        self.list_btn.config(state=DISABLED)
        self.delete_btn.config(state=DISABLED)
        self.status_var.set("実行中...")

        self.worker_thread = threading.Thread(
            target=self._worker, args=(token, args, passes, wait_between), daemon=True
        )
        self.worker_thread.start()

    def _worker(self, token, args, passes, wait_between):
        old_stdout = sys.stdout
        sys.stdout = QueueWriter(self.log_queue)
        try:
            me = core.get_me(token)
            self.log_queue.put(f"アカウント: {me['username']} (id={me['id']})\n")

            grand_deleted_m = grand_deleted_e = 0
            for pass_no in range(1, passes + 1):
                found_m, deleted_m, found_e, deleted_e = core.run_once(token, args, pass_no, passes)
                grand_deleted_m += deleted_m
                grand_deleted_e += deleted_e
                if pass_no < passes and args.delete:
                    if found_m == 0 and found_e == 0:
                        self.log_queue.put("このパスでは何も見つかりませんでした。以降のパスは省略します。\n")
                        break
                    self.log_queue.put(f"次のパスまで{wait_between}秒待機します...\n")
                    core.time.sleep(wait_between)

            if passes > 1 and args.delete:
                self.log_queue.put(
                    f"\n===== 全パス合計 =====\n"
                    f"削除したメッセージ合計: {grand_deleted_m} 件\n"
                    f"削除したイベント合計: {grand_deleted_e} 件\n"
                )
            self.log_queue.put("\n完了しました。\n")
        except Exception as e:
            self.log_queue.put(f"\nエラーが発生しました: {e}\n")
        finally:
            sys.stdout = old_stdout
            self.root.after(0, self._on_worker_done)

    def _on_worker_done(self):
        self.list_btn.config(state=NORMAL)
        self.delete_btn.config(state=NORMAL)
        self.status_var.set("待機中")


def main():
    root = Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
