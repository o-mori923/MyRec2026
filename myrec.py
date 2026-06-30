"""
MyRec 2026 - 作業記録アプリ
  ・tkinter で実装（標準ライブラリのみ）
  ・記録ファイル: YYYYMMDD.txt (Shift-JIS)
  ・履歴ファイル: history.csv (UTF-8 BOM付き)
  ・設定ファイル: config.txt (MyRec V0.16 互換形式)
  ・集計出力:    YYYY_MM_summary.csv (横持ち、時間単位)
"""
import csv
import os
import re
import sys
import tkinter as tk
import winsound
from collections import defaultdict
from datetime import datetime, timedelta
from tkinter import messagebox, ttk

# ---------------------------------------------------------------------------
# パス定数
# ---------------------------------------------------------------------------
# PyInstaller --onefile 時は exe と同フォルダをデータ保存先にする
if getattr(sys, "frozen", False):
    DATA_DIR = os.path.dirname(sys.executable)
else:
    DATA_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(DATA_DIR, "config.txt")
HISTORY_FILE = os.path.join(DATA_DIR, "history.csv")
RECORD_ENCODING = "shift_jis"


def _resource(filename: str) -> str:
    """バンドル資源（アイコン等）のパスを返す。
    PyInstaller 内部は sys._MEIPASS、スクリプト実行時はスクリプトフォルダ。
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
_CONFIG_KEYS = (
    "interval", "beep", "record_empty", "check_interval", "inactive_popup"
)
_CONFIG_DEFAULTS = {"interval": 10, "beep": 1, "record_empty": 1,
                    "check_interval": 10, "inactive_popup": 0}


def load_config() -> dict:
    cfg = dict(_CONFIG_DEFAULTS)
    if not os.path.exists(CONFIG_FILE):
        return cfg
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            lines = [line.rstrip() for line in f]
    except OSError:
        return cfg

    ki = 0
    i = 0
    while i < len(lines) and ki < len(_CONFIG_KEYS):
        if lines[i].startswith("//"):
            i += 1
            if i < len(lines):
                try:
                    cfg[_CONFIG_KEYS[ki]] = int(lines[i])
                except ValueError:
                    pass
                ki += 1
        i += 1

    if cfg["interval"] <= 0:
        cfg["interval"] = 10
    return cfg


# ---------------------------------------------------------------------------
# 履歴
# ---------------------------------------------------------------------------

def load_history() -> list[dict]:
    """history.csv を読み込み、使用回数の降順で返す。"""
    if not os.path.exists(HISTORY_FILE):
        return []
    rows: list[dict] = []
    try:
        with open(HISTORY_FILE, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                rows.append({
                    "task": row["作業内容"],
                    "last_used": row["最終使用日"],
                    "count": int(row.get("使用回数", 1)),
                })
    except (OSError, KeyError, ValueError):
        return []
    rows.sort(key=lambda r: r["count"], reverse=True)
    return rows


def save_history(history: list[dict]) -> None:
    with open(HISTORY_FILE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["作業内容", "最終使用日", "使用回数"])
        w.writeheader()
        for item in history:
            w.writerow({"作業内容": item["task"],
                        "最終使用日": item["last_used"],
                        "使用回数": item["count"]})


def update_history(task: str, history: list[dict]) -> None:
    """履歴にタスクを追加・更新する（history をインプレース変更）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    for item in history:
        if item["task"] == task:
            item["count"] += 1
            item["last_used"] = today
            return
    history.append({"task": task, "last_used": today, "count": 1})


# ---------------------------------------------------------------------------
# 記録ファイル
# ---------------------------------------------------------------------------

def _today_filepath() -> str:
    return os.path.join(DATA_DIR, datetime.now().strftime("%Y%m%d") + ".txt")


def ensure_today_file() -> str:
    """今日の記録ファイルがなければヘッダ付きで作成し、パスを返す。"""
    fp = _today_filepath()
    if not os.path.exists(fp):
        header = "■" + datetime.now().strftime("%Y-%m-%d") + "\r\n"
        with open(fp, "w", encoding=RECORD_ENCODING) as f:
            f.write(header)
    return fp


def append_record(start: datetime, end: datetime, task: str) -> None:
    """記録を今日のファイルに追記する。"""
    fp = ensure_today_file()
    line = f"[{start.strftime('%H:%M')}-{end.strftime('%H:%M')}]:{task}\r\n"
    with open(fp, "a", encoding=RECORD_ENCODING) as f:
        f.write(line)


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------

def _calc_minutes(start_str: str, end_str: str) -> int:
    try:
        s = datetime.strptime(start_str, "%H:%M")
        e = datetime.strptime(end_str, "%H:%M")
        if e < s:
            e += timedelta(days=1)
        return int((e - s).total_seconds() / 60)
    except ValueError:
        return 0


def aggregate_month(year: int, month: int) -> str | None:
    """指定年月の集計CSVを生成してパスを返す。データなしなら None。"""
    prefix = f"{year}{month:02d}"

    files = sorted(
        f for f in os.listdir(DATA_DIR)
        if f.startswith(prefix) and f.endswith(".txt")
        and re.match(r"^\d{8}\.txt$", f)
    )

    daily: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for fn in files:
        fp = os.path.join(DATA_DIR, fn)
        content = None
        for enc in ("shift_jis", "cp932", "utf-8"):
            try:
                with open(fp, encoding=enc) as f:
                    content = f.read()
                break
            except (OSError, UnicodeDecodeError):
                continue
        if not content:
            continue

        m = re.search(rf"■{year}-{month:02d}-(\d+)", content)
        if not m:
            continue
        date_str = f"{year}-{month:02d}-{int(m.group(1)):02d}"

        pattern = r"\[(\d{2}:\d{2})-(\d{2}:\d{2})\]:([^\n\r]*)"
        for s, e, t in re.findall(pattern, content):
            t = t.strip()
            if not t:
                continue
            mins = _calc_minutes(s, e)
            if mins > 0:
                daily[date_str][t] += mins

    if not daily:
        return None

    all_tasks = sorted({t for d in daily.values() for t in d})
    out_path = os.path.join(DATA_DIR, f"{year}_{month:02d}_summary.csv")

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["日付"] + all_tasks)
        for date in sorted(daily):
            row = [date] + [
                round(daily[date].get(t, 0) / 60, 2) for t in all_tasks
            ]
            w.writerow(row)

    return out_path


# ---------------------------------------------------------------------------
# アプリ本体
# ---------------------------------------------------------------------------

class MyRecApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg = load_config()
        self.history = load_history()

        # ポップアップ管理
        self.last_record_time: datetime = datetime.now()
        self.popup_end_time: datetime | None = None   # 最新ポップアップの発生時刻
        self.responded: bool = True                   # 前回ポップアップに応答済みか
        self._last_popup_abs_min: int = -1            # 二重発火防止（0〜1439）

        self._build_ui()
        self._set_record_ui_enabled(True)
        # 起動直後に最小化
        self.root.after(200, self.root.iconify)
        # タイマー開始
        self._tick()

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.root.title("MyRec 2026")
        self.root.resizable(False, False)
        # [X] ボタン・タスクバー右クリック→確認ダイアログ付き終了
        self.root.protocol("WM_DELETE_WINDOW", self._on_quit)
        # ウィンドウ・タスクバーアイコンを設定
        ico = _resource("myrec.ico")
        if os.path.exists(ico):
            self.root.iconbitmap(ico)

        # メニューバー
        menubar = tk.Menu(self.root)
        app_menu = tk.Menu(menubar, tearoff=0)
        app_menu.add_command(label="終了", command=self._on_quit)
        menubar.add_cascade(label="メニュー", menu=app_menu)
        self.root.config(menu=menubar)

        frame = tk.Frame(self.root, padx=8, pady=6)
        frame.pack()

        # 時間帯ラベル
        self.lbl_time = tk.Label(frame, text="--:---:--", width=13,
                                 relief=tk.SUNKEN, anchor="center")
        self.lbl_time.grid(row=0, column=0, padx=4, pady=4)

        # ボタン群
        self.btn_record = tk.Button(frame, text="記録", width=6,
                                    command=self._on_record,
                                    state=tk.DISABLED)
        self.btn_record.grid(row=0, column=1, padx=2, pady=4)
        tk.Button(frame, text="開く", width=6,
                  command=self._on_open
                  ).grid(row=0, column=2, padx=2, pady=4)
        tk.Button(frame, text="集計", width=6,
                  command=self._on_aggregate
                  ).grid(row=0, column=3, padx=2, pady=4)

        # 作業内容入力（コンボボックス）
        self.combo_var = tk.StringVar()
        self.combo = ttk.Combobox(frame, textvariable=self.combo_var, width=42,
                                  state="disabled")
        self.combo.grid(row=1, column=0, columnspan=4,
                        padx=4, pady=(0, 4), sticky="ew")
        self.combo.bind("<Return>", lambda _e: self._on_record())
        self._refresh_combo()

    def _refresh_combo(self) -> None:
        self.combo["values"] = [h["task"] for h in self.history]

    def _set_record_ui_enabled(self, enabled: bool, *, focus: bool = False) -> None:
        self.btn_record.config(state=tk.NORMAL if enabled else tk.DISABLED)
        self.combo.config(state="normal" if enabled else "disabled")
        if enabled and focus:
            self.combo.focus_set()

    # ------------------------------------------------------------------
    # タイマー（ポップアップ制御）
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        try:
            now = datetime.now()
            interval = self.cfg["interval"]
            abs_min = now.hour * 60 + now.minute

            if interval > 0 and now.minute % interval == 0:
                if abs_min != self._last_popup_abs_min:
                    self._last_popup_abs_min = abs_min
                    self._do_popup(now)
        finally:
            # 例外発生時も必ず再スケジュールしタイマーループを維持する
            self.root.after(self.cfg["check_interval"] * 1000, self._tick)

    def _do_popup(self, now: datetime) -> None:
        # 前回ポップアップへの無応答 → 自動記録
        if self.popup_end_time is not None and not self.responded:
            if self.cfg["record_empty"]:
                append_record(self.last_record_time, self.popup_end_time, "")
            self.last_record_time = self.popup_end_time

        # 今回のポップアップ情報を更新
        self.popup_end_time = now
        self.responded = False

        # ラベル更新
        start_s = self.last_record_time.strftime("%H:%M")
        end_s = now.strftime("%H:%M")
        self.lbl_time.config(text=f"{start_s}-{end_s}")

        # ビープ
        if self.cfg["beep"]:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)

        # ウィンドウを前面に（Windowsのフォーカス盗む防止を回避する topmost トリック）
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.root.update()
        self.root.attributes("-topmost", False)
        if self.cfg["inactive_popup"] == 0:
            self.root.focus_force()

        # 記録UIを有効化
        self._set_record_ui_enabled(True, focus=True)

    # ------------------------------------------------------------------
    # ボタンハンドラ
    # ------------------------------------------------------------------

    def _on_record(self) -> None:
        task = self.combo_var.get().strip()
        # 終了時刻: ポップアップ発生時刻（ない場合は現在時刻）
        end = (self.popup_end_time
               if self.popup_end_time is not None
               else datetime.now())

        append_record(self.last_record_time, end, task)

        if task:
            update_history(task, self.history)
            save_history(self.history)
            self._refresh_combo()

        self.last_record_time = end
        self.popup_end_time = None
        self.responded = True

        # 記録UIを無効化
        self._set_record_ui_enabled(False)
        self.root.iconify()

    def _on_open(self) -> None:
        fp = ensure_today_file()
        os.startfile(fp)

    def _on_aggregate(self) -> None:
        """年月選択ダイアログを開いて集計を実行する。"""
        now = datetime.now()
        dlg = tk.Toplevel(self.root)
        dlg.title("集計対象の選択")
        dlg.resizable(False, False)
        dlg.grab_set()  # モーダル

        tk.Label(dlg, text="年:").grid(
            row=0, column=0, padx=(12, 4), pady=12, sticky="e")
        year_var = tk.IntVar(value=now.year)
        tk.Spinbox(dlg, from_=2000, to=2099, width=6,
                   textvariable=year_var).grid(
            row=0, column=1, padx=4, pady=12)

        tk.Label(dlg, text="月:").grid(
            row=0, column=2, padx=(12, 4), pady=12, sticky="e")
        month_var = tk.IntVar(value=now.month)
        tk.Spinbox(dlg, from_=1, to=12, width=4,
                   textvariable=month_var).grid(
            row=0, column=3, padx=(4, 12), pady=12)

        def _run() -> None:
            try:
                y = int(year_var.get())
                mo = int(month_var.get())
            except (ValueError, tk.TclError):
                messagebox.showerror(
                    "入力エラー", "年月を正しく入力してください。", parent=dlg)
                return
            if not (1 <= mo <= 12):
                messagebox.showerror(
                    "入力エラー", "月は 1〜12 で入力してください。", parent=dlg)
                return
            dlg.destroy()
            out = aggregate_month(y, mo)
            if out:
                messagebox.showinfo(
                    "集計完了",
                    f"{y}年{mo}月の集計CSVを出力しました:\n{out}")
                os.startfile(out)
            else:
                messagebox.showwarning(
                    "集計",
                    f"{y}年{mo}月の記録データが見つかりませんでした。")

        btn_frame = tk.Frame(dlg)
        btn_frame.grid(row=1, column=0, columnspan=4, pady=(0, 12))
        tk.Button(btn_frame, text="集計実行", width=10,
                  command=_run).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_frame, text="キャンセル", width=10,
                  command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def _on_quit(self) -> None:
        self.root.deiconify()
        if messagebox.askyesno("終了", "MyRec 2026 を終了しますか？"):
            self.root.destroy()


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    root = tk.Tk()
    MyRecApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
