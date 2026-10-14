"""Полупрозрачное окно с уровнем и прогрессом; бот включается и выключается горячей клавишей."""
import threading
import time
import tkinter as tk
import traceback

import win
from bot import Bot, Stop, log, save_config
from vision import Vision

KEYS = {"home": 0x24, "end": 0x23, "insert": 0x2D, "delete": 0x2E, "pageup": 0x21, "pagedown": 0x22,
        "pause": 0x13, "scrolllock": 0x91, **{f"f{i}": 0x6F + i for i in range(1, 13)}}

BG, FG, MUTED = "#16181d", "#eceff4", "#8b919c"
GREEN, GOLD, TRACK, PIN_BG, CLOSE_HOVER = "#55ff55", "#ffaa00", "#2a2e36", "#3d4350", "#ff6b6b"
WIDTH = 250


def money(n):
    return "—" if n is None else f"{n:,}".replace(",", " ")


class Overlay:
    def __init__(self, cfg, mode, level=None):
        self.cfg, self.mode = cfg, mode
        key = cfg.get("hotkey", "home").lower()
        if key not in KEYS:
            raise RuntimeError(f"hotkey «{key}» не поддерживается, можно: {', '.join(KEYS)}")
        self.key_name, self.vk = key.upper(), KEYS[key]
        self.status = {"state": "выключен", "level": level}
        self.lock = threading.Lock()
        self.worker = None
        self.stop_event = None
        self.bot = None
        self.pinned = True  # при каждом запуске окно закреплено поверх всех
        cfg.pop("overlay_pinned", None)
        self._key_was_down = win.key_down(self.vk)
        self._drag = None
        self._build()

    # ------------------------------------------------------------ окно

    def _build(self):
        r = self.root = tk.Tk()
        r.title("Farmer HUD")
        r.overrideredirect(True)
        r.configure(bg=BG)
        r.attributes("-alpha", self.cfg.get("overlay_alpha", 0.85))
        r.attributes("-topmost", self.pinned)
        x, y = self.cfg.get("overlay_pos", [40, 120])
        r.geometry(f"+{x}+{y}")
        r.report_callback_exception = lambda *exc: log("".join(traceback.format_exception(*exc)))

        head = tk.Frame(r, bg=BG)
        head.pack(fill="x", padx=(12, 6), pady=(8, 0))
        self.lbl_level = tk.Label(head, text="? уровень", font=("Segoe UI Semibold", 15), fg=FG, bg=BG)
        self.lbl_level.pack(side="left")
        self.btn_close = tk.Label(head, text="✕", font=("Segoe UI", 11), fg=MUTED, bg=BG, cursor="hand2", padx=4)
        self.btn_close.pack(side="right")
        self.btn_pin = tk.Label(head, text="📎", font=("Segoe UI Emoji", 11), bg=BG, cursor="hand2", padx=4)
        self.btn_pin.pack(side="right")

        self.lbl_count = tk.Label(r, text="— / —", font=("Consolas", 14, "bold"), fg=GOLD, bg=BG, anchor="w")
        self.lbl_count.pack(fill="x", padx=12)
        self.bar = tk.Canvas(r, height=6, width=WIDTH - 24, bg=TRACK, highlightthickness=0)
        self.bar.pack(padx=12, pady=(4, 6))
        self.bar_fill = self.bar.create_rectangle(0, 0, 0, 6, fill=GREEN, width=0)
        money_row = tk.Frame(r, bg=BG)
        money_row.pack(fill="x", padx=12, pady=(0, 4))
        self.lbl_coins = tk.Label(money_row, text="монеты: —", font=("Segoe UI", 10), fg=GOLD, bg=BG)
        self.lbl_coins.pack(side="left")
        self.lbl_cost = tk.Label(money_row, text="", font=("Segoe UI", 10), fg=MUTED, bg=BG)
        self.lbl_cost.pack(side="right")
        self.lbl_state = tk.Label(r, font=("Segoe UI", 9), fg=MUTED, bg=BG, anchor="w")
        self.lbl_state.pack(fill="x", padx=12)
        self.lbl_msg = tk.Label(r, font=("Segoe UI", 8), fg=MUTED, bg=BG, anchor="w", justify="left",
                                wraplength=WIDTH - 24)
        self.lbl_msg.pack(fill="x", padx=12, pady=(0, 8))

        self.btn_close.bind("<Button-1>", lambda e: self.close())
        self.btn_close.bind("<Enter>", lambda e: self.btn_close.config(fg=CLOSE_HOVER))
        self.btn_close.bind("<Leave>", lambda e: self.btn_close.config(fg=MUTED))
        self.btn_pin.bind("<Button-1>", lambda e: self.toggle_pin())
        self._paint_pin()
        self.lbl_level.bind("<Double-Button-1>", lambda e: self.ask_level())
        for w in (r, head, self.lbl_level, self.lbl_count, self.bar, money_row, self.lbl_coins, self.lbl_cost,
                  self.lbl_state, self.lbl_msg):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)

    def _paint_pin(self):
        # Скрепка рисуется цветным эмодзи, поэтому состояние показываем подложкой.
        self.btn_pin.config(bg=PIN_BG if self.pinned else BG)

    def ask_level(self):
        """Двойной клик по уровню — ввести его вручную, если бот не смог прочитать."""
        from tkinter import simpledialog
        with self.lock:
            current = self.status.get("level")
        level = simpledialog.askinteger("Уровень", "Текущий уровень генератора:", parent=self.root,
                                        initialvalue=current, minvalue=1, maxvalue=100000)
        if level:
            self.set_status({"level": level})
            if self.bot:
                self.bot.level = level

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.root.attributes("-topmost", self.pinned)
        self._apply_topmost()
        self._paint_pin()

    def _apply_topmost(self):
        """Ставит окно поверх всех (или снимает), не забирая фокус у текущего окна."""
        hwnd = int(self.root.wm_frame(), 16)
        win.SetWindowPos(hwnd, win.HWND_TOPMOST if self.pinned else win.HWND_NOTOPMOST, 0, 0, 0, 0,
                         win.SWP_NOMOVE | win.SWP_NOSIZE | win.SWP_NOACTIVATE)

    def _keep_on_top(self):
        # Некоторые программы (полноэкранные видео, другие «поверх всех» окна) могут перекрыть оверлей —
        # пока он закреплён, раз в секунду возвращаем его наверх.
        if self.pinned:
            self._apply_topmost()
        self.root.after(1000, self._keep_on_top)

    def _drag_start(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _drag_move(self, e):
        if self._drag:
            self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _drag_end(self, _e):
        if self._drag:
            self._drag = None
            self.cfg["overlay_pos"] = [self.root.winfo_x(), self.root.winfo_y()]
            save_config(self.cfg)

    def _refresh(self):
        with self.lock:
            s = dict(self.status)
        running = self.running()
        level, count, total, frac = s.get("level"), s.get("count"), s.get("total"), s.get("frac")

        approx = False
        if running and s.get("state") == "копаю" and count is not None and total and s.get("rate"):
            # Между заходами в генератор досчитываем по средней скорости копания.
            est = min(total, int(count + s["rate"] * (time.monotonic() - s.get("t_mine", time.monotonic()))))
            approx, count = est != count, est
        if total and count is not None:
            text, part = f"{'~' if approx else ''}{count} / {total}", count / total
        elif frac is not None:
            text, part = f"{frac * 100:.0f}%", frac
        else:
            text, part = "— / —", 0

        self.lbl_level.config(text=f"{level} уровень" if level else "? уровень")
        self.lbl_count.config(text=text)
        coins, cost, last = s.get("coins"), s.get("cost"), s.get("last_cost")
        self.lbl_coins.config(text=f"монеты: {money(coins)}")
        if cost:
            enough = coins is None or coins >= cost
            self.lbl_cost.config(text=f"прокачка: {money(cost)}", fg=GREEN if enough else CLOSE_HOVER)
        elif last:
            self.lbl_cost.config(text=f"прошлая прокачка: {money(last)}", fg=MUTED)
        else:
            self.lbl_cost.config(text="прокачка: ?", fg=MUTED)
        self.bar.coords(self.bar_fill, 0, 0, round((WIDTH - 24) * min(part, 1.0)), 6)
        if running:
            self.lbl_state.config(text=f"● {s.get('state', 'работает')}  ·  {self.key_name} — выключить", fg=GREEN)
        else:
            self.lbl_state.config(text=f"○ выключен  ·  {self.key_name} — включить", fg=MUTED)
        self.lbl_msg.config(text=s.get("msg", ""))
        self.root.after(200, self._refresh)

    def _tick_hotkey(self):
        down = win.key_down(self.vk)
        if down and not self._key_was_down:
            self.toggle()
        self._key_was_down = down
        self.root.after(30, self._tick_hotkey)

    # ------------------------------------------------------------ бот

    def set_status(self, fields):
        """Вызывается из потока бота — только меняет словарь, сам Tk трогает лишь главный поток."""
        with self.lock:
            self.status.update(fields)

    def _watch_coins(self):
        """Фоновый поток: раз в 2 с снимает окно игры и читает баланс. Ничего в игру не нажимает,
        поэтому монеты обновляются и когда бот выключен."""
        hwnd = None
        while True:
            try:
                if hwnd is None or not win.IsWindow(hwnd):
                    hwnd = win.find_window(self.cfg["window_title"])[0]
                img = win.capture(hwnd)
                coins = Vision(self.cfg, (img.w, img.h)).coins(img)
                if coins is not None:
                    self.set_status({"coins": coins})
            except Exception:
                hwnd = None  # игра закрыта или свёрнута — попробуем позже
            time.sleep(2)

    def running(self):
        return self.worker is not None and self.worker.is_alive()

    def toggle(self):
        if self.running():
            if not self.stop_event.is_set():
                self.stop_event.set()
                self.set_status({"msg": "выключаю…"})
            return
        self.stop_event = threading.Event()
        self.set_status({"state": "запуск", "msg": ""})
        self.worker = threading.Thread(target=self._work, args=(self.stop_event,), daemon=True)
        self.worker.start()

    def _work(self, stop_event):
        bot = None
        try:
            with self.lock:
                level = self.status.get("level")
            bot = self.bot = Bot(self.cfg, self.mode, stop_event=stop_event, on_status=self.set_status,
                                 level=level, console_keys=False)
            log(f"Старт ({self.key_name} — выключить)")
            bot.run()
        except Stop as e:
            msg = f"выключен ({self.key_name})" if stop_event.is_set() else f"стоп: {e}"
            log(f"СТОП: {e}")
            self.set_status({"msg": msg})
        except Exception as e:
            log(traceback.format_exc())
            self.set_status({"msg": f"ошибка: {e}"})
        finally:
            if bot:
                bot.shutdown()
            self.bot = None
            self.set_status({"state": "выключен"})

    def close(self):
        if self.running():
            self.stop_event.set()
            self.set_status({"msg": "выключаю бота перед закрытием…"})
            deadline = time.monotonic() + 8

            def wait():
                if self.running() and time.monotonic() < deadline:
                    self.root.after(100, wait)
                else:
                    self.root.destroy()
            wait()
        else:
            self.root.destroy()

    def run(self):
        log(f"Оверлей запущен, режим {self.mode}; {self.key_name} — включить/выключить бота")
        self._refresh()
        self._tick_hotkey()
        self.root.update_idletasks()
        self._keep_on_top()
        threading.Thread(target=self._watch_coins, daemon=True).start()
        self.root.mainloop()
