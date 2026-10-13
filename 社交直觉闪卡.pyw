# -*- coding: utf-8 -*-
"""社交直觉闪卡：双击本文件即可打开，不需要浏览器，也不需要命令行参数。"""

import sys
import traceback
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# 让程序自己的模块和随程序一起分发的 FSRS 库都可以被导入
sys.path.insert(0, str(BASE_DIR / "vendor"))
sys.path.insert(0, str(BASE_DIR))


def _show_crash(detail: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("社交直觉闪卡 启动失败", f"程序启动时出错。\n详细信息已写入 data\\last_error.log：\n\n{detail[-1200:]}")
        root.destroy()
    except Exception:
        pass


def run() -> None:
    from app.ui import main

    main()


if __name__ == "__main__":
    try:
        run()
    except Exception:  # noqa: BLE001 - .pyw 没有控制台，必须自己留下错误记录
        text = traceback.format_exc()
        try:
            log_dir = BASE_DIR / "data"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "last_error.log").write_text(text, encoding="utf-8")
        except Exception:
            pass
        _show_crash(text)
        raise SystemExit(1)
