"""Автокопалка OneBlock: копает блок, следит за шкалой в генераторе и прокачивает уровень.

  python bot.py overlay [--mode bg|fg]   окно с уровнем и прогрессом; бот включается/выключается по HOME
  python bot.py run     [--mode bg|fg]   рабочий цикл в консоли (F9 — пауза, F10 — стоп)
  python bot.py test    [--mode bg|fg]   пошаговая проверка: копание, открытие генератора, диалог, закрытие
  python bot.py shot                     снимок окна + что бот на нём видит (ничего не нажимает)
  python bot.py calibrate                заново указать точки мышкой (если поменялся размер окна)

--level N — подсказать текущий уровень, если бот не может его прочитать (нужно один раз:
незнакомые цифры уровня он запоминает в glyphs.json).
"""
import argparse
import json
import os
import sys
import time

import ocr
import win
from vision import Vision, MAIN, DIALOG, OTHER

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
GLYPHS = os.path.join(HERE, "glyphs.json")
LOG_FILE = None


class Stop(Exception):
    pass


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    if LOG_FILE:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


class Bot:
    def __init__(self, cfg, mode, shots=False, stop_event=None, on_status=None, level=None, console_keys=True):
        self.cfg = cfg
        self.bg = mode == "bg"
        self.stop_event = stop_event
        self.on_status = on_status
        self.console_keys = console_keys
        self.hwnd, title, cls = win.find_window(cfg["window_title"])
        self.io = win.PostInput(self.hwnd) if self.bg else win.SendInput_(self.hwnd)
        self.shots_dir = os.path.join(HERE, "shots") if shots else None
        if self.shots_dir:
            os.makedirs(self.shots_dir, exist_ok=True)
        self.shot_n = 0
        self.method = "printwindow"
        self.v = Vision(cfg, win.client_size(self.hwnd))
        self.levels = ocr.LevelReader(GLYPHS)
        self.level = level
        self.total = None
        self.coins = None
        self.cost = None          # цена прокачки текущего уровня
        self._cost_tried = False  # на этом уровне уже заглядывали в диалог за ценой
        self.holding = False
        self.pause_total = 0.0
        self._f9 = False
        self._shutting = False
        self._last_cursor = win.cursor_pos()
        self._warned_focus = False
        self.log(f"Окно: «{title}» ({cls}), клиентская область {win.client_size(self.hwnd)}, режим {mode}")
        if tuple(win.client_size(self.hwnd)) != tuple(cfg["ref_size"]):
            self.log(f"ВНИМАНИЕ: размер окна отличается от калибровочного {cfg['ref_size']} — "
                     "точки пересчитаны пропорционально, проверь их через «shot» или сделай «calibrate».")

    def log(self, msg):
        log(msg)
        self.report(msg=msg)

    def report(self, **fields):
        if self.on_status:
            self.on_status(fields)

    # ------------------------------------------------------------ время, пауза, стоп

    def clock(self):
        """Время без учёта пауз."""
        return time.monotonic() - self.pause_total

    def sleep(self, sec):
        end = self.clock() + sec
        while True:
            self.poll()
            left = end - self.clock()
            if left <= 0:
                return
            time.sleep(min(0.015, left))

    def poll(self):
        if not win.IsWindow(self.hwnd):
            raise Stop("окно игры закрыто")
        if self._shutting:
            return
        if self.stop_event is not None and self.stop_event.is_set():
            raise Stop("выключен")
        if self.console_keys:
            if win.key_down(win.VK_F10):
                raise Stop("остановлено по F10")
            f9 = win.key_down(win.VK_F9)
            if f9 and not self._f9:
                self._wait_while("пауза (F9 — продолжить, F10 — стоп)", lambda: not self._f9_pressed())
            self._f9 = f9
        if self.bg:
            self._guard_cursor()
        elif win.GetForegroundWindow() != self.hwnd:
            self._wait_while("окно игры не активно — жду, пока вернёшься в игру",
                             lambda: win.GetForegroundWindow() != self.hwnd, settle=1.0)

    def _f9_pressed(self):
        f9 = win.key_down(win.VK_F9)
        pressed = f9 and not self._f9
        self._f9 = f9
        return pressed

    def _wait_while(self, why, cond, settle=0.2):
        """Отпускает ЛКМ и ждёт, пока cond() истинно; это время не считается временем копания."""
        t0 = time.monotonic()
        was_holding = self.holding
        self.io.left_up()
        self.log(why)
        self.report(state="пауза")
        self._f9 = True
        while cond():
            if not win.IsWindow(self.hwnd) or (self.console_keys and win.key_down(win.VK_F10)) \
                    or (self.stop_event is not None and self.stop_event.is_set()):
                raise Stop("остановлено")
            time.sleep(0.03)
        time.sleep(settle)
        self.log("продолжаю")
        if was_holding:
            self.io.left_down()
            self.report(state="копаю", t_mine=time.monotonic())
        self.pause_total += time.monotonic() - t0

    def _guard_cursor(self):
        """В фоне игра при открытии/закрытии GUI дёргает системный курсор в центр своего окна.

        Если курсор внезапно оказался ровно там — возвращаем его, откуда он прыгнул.
        """
        cur = win.cursor_pos()
        if win.GetForegroundWindow() != self.hwnd:
            w, h = win.client_size(self.hwnd)
            cx, cy = win.client_to_screen(self.hwnd, w // 2, h // 2)
            lx, ly = self._last_cursor
            if abs(cur[0] - cx) <= 3 and abs(cur[1] - cy) <= 3 and (abs(lx - cx) > 40 or abs(ly - cy) > 40):
                win.SetCursorPos(lx, ly)
                return
        self._last_cursor = cur

    # ------------------------------------------------------------ зрение

    def look(self, tag=""):
        img = win.capture(self.hwnd, self.method)
        if img.is_blank() and self.method == "printwindow":
            if self.bg:
                raise Stop("захват окна даёт чёрную картинку — фоновый режим на этой системе не сработает, "
                           "попробуй --mode fg")
            self.log("PrintWindow даёт чёрную картинку, переключаюсь на захват с экрана")
            self.method = "screen"
            img = win.capture(self.hwnd, self.method)
        self.v = Vision(self.cfg, (img.w, img.h))
        state = self.v.state(img)
        if self.shots_dir:
            self.shot_n += 1
            img.save_png(os.path.join(self.shots_dir, f"{self.shot_n:04d}_{tag or 'look'}_{state}.png"))
        return state, img

    def wait_state(self, wanted, timeout, tag=""):
        end = self.clock() + timeout
        while True:
            state, img = self.look(tag)
            if state in wanted or self.clock() >= end:
                return state, img
            self.sleep(0.15)

    def read_info(self, img):
        """Шкала, «N из M» и уровень с открытого генератора. Числа сверяются со шкалой."""
        frac = self.v.progress(img)
        count = total = None
        nums = ocr.read_numbers(img, self.v.rect("progress_rect"))
        if len(nums) >= 2 and nums[-1] > 0 and abs(min(nums[-2] / nums[-1], 1.0) - frac) <= 0.03:
            count, total = nums[-2], nums[-1]
        elif self.total:
            count, total = round(frac * self.total), self.total
        self.total = total
        level = self.levels.read(img, self.v.rect("level_rect"), self.v.rect("next_level_rect"), hint=self.level)
        if level:
            self.level = level
        coins = self.v.coins(img, MAIN)
        if coins is not None:
            self.coins = coins
            self.report(coins=coins)
        self.report(level=self.level, count=count, total=total, frac=frac, t_read=time.monotonic())
        return frac

    def read_cost(self, img):
        """Цена с кнопки «Прокачать за N» в открытом диалоге."""
        cost = ocr.read_cost(img, self.v.rect("cost_rect"), self.levels.known)
        if cost:
            if cost != self.cost:
                self.log(f"прокачка стоит {cost} монет")
            self.cost = cost
            self.report(cost=cost)
        else:
            path = os.path.join(HERE, "dialog_debug.png")
            img.save_png(path)
            self.log("не смог прочитать цену прокачки — снимок диалога сохранён в dialog_debug.png")
        return cost

    def peek_cost(self):
        """Генератор открыт: заглянуть в диалог прокачки за ценой и вернуться. Ничего не покупает."""
        self._cost_tried = True
        self.io.click(*self.v.pt("upgrade_btn"))
        state, _ = self.wait_state({DIALOG}, 2.5, "цена")
        if state != DIALOG:
            return
        self.sleep(0.3)
        state, img = self.look("цена")
        if state == DIALOG:
            self.read_cost(img)
        self.press_esc()
        self.wait_state({MAIN, OTHER}, 2.0, "после_цены")

    # ------------------------------------------------------------ действия

    def press(self):
        # После закрытия GUI игра игнорирует зажатую ЛКМ, пока её хоть раз не отпустят.
        self.io.left_up()
        self.sleep(0.15)
        self.io.left_down()
        self.holding = True

    def release(self):
        self.io.left_up()
        self.holding = False
        self.sleep(0.15)

    def mine(self, sec):
        self.press()
        self.report(state="копаю", t_mine=time.monotonic())
        end = self.clock() + sec
        reclick = self.cfg["reclick_interval"]
        while self.clock() < end:
            self.sleep(min(reclick or 1.0, max(0.0, end - self.clock())))
            if reclick and self.holding:
                # Повторное «нажатие» не сбивает копание блока, зато восстанавливает зажатие,
                # если игра его сбросила, и бьёт моба, вставшего перед блоком.
                self.io.left_down()
        self.release()

    def press_esc(self):
        fake = self.bg and self.cfg["bg_fake_focus"] and win.GetForegroundWindow() != self.hwnd
        if fake:
            self.io.fake_focus(True)
            self.sleep(0.05)
        self.io.esc()
        self.sleep(0.35)
        if fake:
            self.io.fake_focus(False)
            self.sleep(0.05)
            if win.GetForegroundWindow() == self.hwnd and not self._warned_focus:
                self._warned_focus = True
                self.log("ВНИМАНИЕ: при закрытии GUI окно игры вышло на передний план (так делает сама игра).")

    def open_gui(self):
        """ПКМ по блоку -> ждём генератор. Возвращает снимок с генератором или None."""
        self.report(state="смотрю генератор")
        for _ in range(3):
            state, img = self.look("до_пкм")
            if state == DIALOG:
                self.press_esc()
                continue
            if state != MAIN:
                self.io.right_click()
                state, img = self.wait_state({MAIN}, 2.5, "жду_генератор")
            if state == MAIN:
                self.sleep(0.3)
                state, img = self.look("генератор")
                if state == MAIN:
                    return img
        return None

    def close_gui(self):
        for _ in range(4):
            state, _ = self.look("закрытие")
            if state == OTHER:
                return True
            self.press_esc()
        return self.look("закрытие")[0] == OTHER

    def upgrade(self):
        """Генератор открыт, шкала полная: «Прокачать уровень» -> «Прокачать за монеты»."""
        self.report(state="прокачиваю")
        self.io.click(*self.v.pt("upgrade_btn"))
        state, _ = self.wait_state({DIALOG}, 2.5, "жду_диалог")
        if state != DIALOG:
            self.log("диалог прокачки не открылся")
            return False
        self.sleep(0.3)
        state, img = self.look("диалог")
        if state == DIALOG:
            self.read_cost(img)
        coins_before = self.coins
        self.io.click(*self.v.pt("confirm_btn"))
        state, img = self.wait_state({MAIN, OTHER}, 3.0, "после_подтверждения")
        if state == DIALOG:
            self.log("диалог не закрылся — кнопка не сработала (не хватает монет?)")
            return False
        if state == OTHER:
            img = self.open_gui()
            if img is None:
                return False
        self.sleep(0.4)
        state, img = self.look("проверка_уровня")
        if state != MAIN or self.v.progress(img) >= 0.9:
            return False
        if self.level:
            self.level += 1
        self.total = None
        self.coins = None
        self.read_info(img)
        if coins_before and self.coins and coins_before > self.coins:
            spent = coins_before - self.coins
            self.log(f"списано {spent} монет" + (f" (на кнопке было {self.cost})" if self.cost and self.cost != spent else ""))
            self.report(last_cost=spent)
        # Цена следующей прокачки другая — узнаем её при следующем заходе в генератор.
        self.cost, self._cost_tried = None, False
        self.report(cost=None)
        return True

    def shutdown(self):
        """Выключение: закрыть генератор, если он открыт, и отпустить кнопки."""
        self._shutting = True
        try:
            if win.IsWindow(self.hwnd):
                self.io.left_up()
                if self.look("выключение")[0] != OTHER:
                    self.close_gui()
        except Exception as e:
            log(f"при выключении: {e}")
        finally:
            if win.IsWindow(self.hwnd):
                self.io.left_up()
            self.report(state="выключен")

    # ------------------------------------------------------------ сценарии

    def prepare(self):
        if self.bg:
            if win.IsIconic(self.hwnd):
                raise Stop("окно игры свёрнуто — разверни его и перекрой другими окнами, сворачивать нельзя")
        elif win.GetForegroundWindow() != self.hwnd:
            self._wait_while("переключись в окно игры — начну, как только оно станет активным",
                             lambda: win.GetForegroundWindow() != self.hwnd, settle=1.0)
        state, _ = self.look("старт")
        self.log(f"Сейчас на экране: {state}")

    def plan(self, prev, now, frac):
        """Через сколько секунд копания заглянуть в генератор, когда шкала заполнится и скорость (доля/с)."""
        if prev and frac > prev[1] and now > prev[0]:
            rate = (frac - prev[1]) / (now - prev[0])
            eta = (1 - frac) / rate
            # Целимся чуть ПОЗЖЕ заполнения: лишние блоки не вредят, а лишние заходы в GUI — потеря времени.
            return min(max(eta * 1.05 + 1, 3), self.cfg["max_check"]), eta, rate
        return self.cfg["first_check"], None, None

    def run(self, max_levels=0, minutes=0):
        self.prepare()
        started = time.monotonic()
        levels = open_fails = stuck = idle = 0
        prev = None
        interval = 0
        while True:
            if minutes and time.monotonic() - started > minutes * 60:
                raise Stop(f"вышло время ({minutes} мин), прокачано уровней: {levels}")
            if interval > 0:
                self.mine(interval)
            img = self.open_gui()
            if img is None:
                open_fails += 1
                self.log(f"генератор не открылся ({open_fails}/5)")
                self.close_gui()
                if open_fails >= 5:
                    raise Stop("генератор не открывается по ПКМ — персонаж смотрит на блок?")
                interval = 5
                continue
            open_fails = 0
            frac, now = self.read_info(img), self.clock()

            if frac >= self.cfg["full_threshold"]:
                self.log("шкала заполнена — прокачиваю")
                if self.upgrade():
                    levels += 1
                    stuck = 0
                    self.log(f"уровень прокачан{f' до {self.level}' if self.level else ''} "
                             f"(за этот запуск: {levels})")
                    prev, interval = (self.clock(), 0.0), self.cfg["first_check"]
                    if max_levels and levels >= max_levels:
                        self.close_gui()
                        raise Stop(f"готово: прокачано уровней — {levels}")
                else:
                    stuck += 1
                    if stuck >= 3:
                        self.close_gui()
                        raise Stop("шкала полная, но прокачка не проходит 3 раза подряд — "
                                   "скорее всего не хватает монет")
                    interval = 10
            else:
                if self.cost is None and not self._cost_tried:
                    self.peek_cost()
                if prev and frac <= prev[1] and interval >= self.cfg["first_check"] - 1:
                    idle += 1
                    self.log(f"прогресс не растёт ({idle}/3) — блок не копается?")
                    if idle >= 3:
                        self.close_gui()
                        raise Stop("прогресс стоит на месте: мешает моб, сломался инструмент или сбился прицел")
                else:
                    idle = 0
                interval, eta, rate = self.plan(prev, now, frac)
                prev = (now, frac)
                if rate and self.total:
                    self.report(rate=rate * self.total)
                eta_txt = f", до заполнения ~{eta:.0f} с" if eta is not None else ""
                self.log(f"прогресс {frac * 100:.1f}%{eta_txt}; копаю {interval:.0f} с")

            if not self.close_gui():
                raise Stop("не получается закрыть GUI (сохрани снимки через --shots и посмотри, что на экране)")

    def test(self):
        """Проверка всех действий без траты монет: кнопку подтверждения в диалоге не нажимает."""
        results = []

        def check(name, ok, note=""):
            results.append((name, ok))
            self.log(f"[{'OK' if ok else 'НЕ РАБОТАЕТ'}] {name}{' — ' + note if note else ''}")

        self.prepare()
        check("закрыть GUI, если открыт", self.close_gui())

        img = self.open_gui()
        check("ПКМ открывает генератор", img is not None)
        if img is None:
            return results
        f0 = self.read_info(img)
        self.log(f"шкала: {f0 * 100:.1f}%, уровень: {self.level or '?'}, блоков: {self.total and round(f0 * self.total)} "
                 f"из {self.total or '?'}")

        self.io.click(*self.v.pt("upgrade_btn"))
        state, _ = self.wait_state({DIALOG}, 2.5, "тест_диалог")
        check("клик по «Прокачать уровень» открывает диалог", state == DIALOG)
        if state == DIALOG:
            self.sleep(0.3)
            cost = self.read_cost(self.look("тест_цена")[1])
            check("цена прокачки читается", bool(cost), f"{cost} монет, на счету {self.coins}" if cost else "")
            self.press_esc()
            self.log(f"после Esc в диалоге на экране: {self.look('тест_esc')[0]}")
        check("Esc закрывает GUI", self.close_gui())

        self.mine(8)
        img = self.open_gui()
        f1 = self.read_info(img) if img else f0
        check("копание после закрытия GUI идёт", f1 > f0 or f1 >= self.cfg["full_threshold"],
              f"шкала {f0 * 100:.1f}% -> {f1 * 100:.1f}%")
        check("GUI закрыт", self.close_gui())

        self.mine(8)
        img = self.open_gui()
        f2 = self.read_info(img) if img else f1
        check("копание идёт и во втором цикле", f2 > f1 or f2 >= self.cfg["full_threshold"],
              f"шкала {f1 * 100:.1f}% -> {f2 * 100:.1f}%")
        check("GUI закрыт", self.close_gui())
        return results


# ---------------------------------------------------------------- команды

def cmd_shot(cfg, args):
    hwnd, title, cls = win.find_window(cfg["window_title"])
    img = win.capture(hwnd, "printwindow")
    method = "printwindow"
    if img.is_blank():
        method = "screen"
        img = win.capture(hwnd, "screen")
    v = Vision(cfg, (img.w, img.h))
    state = v.state(img)
    print(f"Окно: «{title}» ({cls}), {img.w}x{img.h}, захват: {method}"
          f"{' — в фоне работать не будет' if method == 'screen' else ''}")
    print(f"Состояние: {state}")
    print(f"Шкала: {v.progress(img) * 100:.1f}%")
    print(f"Монеты: {v.coins(img, state)}")
    if state == DIALOG:
        print(f"Цена прокачки: {ocr.read_cost(img, v.rect('cost_rect'), ocr.LevelReader(GLYPHS).known)}")
    if state == MAIN:
        level = ocr.LevelReader(GLYPHS).read(img, v.rect("level_rect"), v.rect("next_level_rect"), hint=args.level)
        print(f"Уровень: {level or '? (не прочитан — запусти с --level N)'}")
        print(f"Числа «N из M»: {ocr.read_numbers(img, v.rect('progress_rect')) or 'не прочитаны'}")
    for name in ("main_anchors", "dialog_anchors"):
        for p in cfg[name]:
            print(f"  {name} {v.pt(p)} = {img.px(*v.pt(p))}")
    img.save_png(os.path.join(HERE, "shot.png"))
    for name, color in (("upgrade_btn", (255, 0, 0)), ("confirm_btn", (255, 0, 255)),
                        ("bar_left", (0, 128, 255)), ("bar_right", (0, 128, 255))):
        img.mark(*v.pt(name), rgb=color)
    for name in ("main_anchors", "dialog_anchors"):
        for p in cfg[name]:
            img.mark(*v.pt(p), rgb=(255, 255, 0), size=6)
    print("Сохранено: shot.png и shot_marked.png (крестики — точки, которыми пользуется бот)")
    img.save_png(os.path.join(HERE, "shot_marked.png"))


def cmd_calibrate(cfg, _args):
    hwnd, _, _ = win.find_window(cfg["window_title"])
    size = win.client_size(hwnd)
    kx, ky = size[0] / cfg["ref_size"][0], size[1] / cfg["ref_size"][1]
    steps = [("upgrade_btn", "открой генератор и наведи курсор на центр кнопки «ПРОКАЧАТЬ УРОВЕНЬ»"),
             ("bar_left", "наведи на ЛЕВЫЙ край шкалы прогресса (внутри рамки, по центру высоты)"),
             ("bar_right", "наведи на ПРАВЫЙ край шкалы прогресса (внутри рамки)"),
             ("confirm_btn", "нажми «ПРОКАЧАТЬ УРОВЕНЬ» и наведи на центр кнопки «ПРОКАЧАТЬ ЗА … монет» "
                             "(НЕ на розовую кнопку за гемы)")]
    for key, text in steps:
        print(f"{text}, затем нажми F8")
        while win.key_down(win.VK_F8):
            time.sleep(0.02)
        while not win.key_down(win.VK_F8):
            time.sleep(0.02)
        cfg[key] = list(win.screen_to_client(hwnd, *win.cursor_pos()))
        print(f"  {key} = {cfg[key]}")
    for name in ("main_anchors", "dialog_anchors"):
        cfg[name] = [[round(x * kx), round(y * ky)] for x, y in cfg[name]]
    for name in ("progress_rect", "level_rect", "next_level_rect", "coins_hud_rect", "coins_gui_rect", "cost_rect"):
        x0, y0, x1, y1 = cfg[name]
        cfg[name] = [round(x0 * kx), round(y0 * ky), round(x1 * kx), round(y1 * ky)]
    cfg["ref_size"] = list(size)
    save_config(cfg)
    print("config.json обновлён. Проверь результат: python bot.py shot")


def cmd_bot(cfg, args):
    bot = Bot(cfg, args.mode, args.shots, level=args.level)
    try:
        if args.cmd == "test":
            results = bot.test()
            bad = [name for name, ok in results if not ok]
            log("ИТОГ: всё работает" if not bad else "ИТОГ: не работает — " + "; ".join(bad))
        else:
            log("Старт. F9 — пауза, F10 — стоп.")
            bot.run(args.levels, args.minutes)
    except Stop as e:
        log(f"СТОП: {e}")
    except KeyboardInterrupt:
        log("СТОП: Ctrl+C")
    finally:
        bot.shutdown()


def cmd_overlay(cfg, args):
    global LOG_FILE
    LOG_FILE = os.path.join(HERE, "bot.log")
    import overlay
    overlay.Overlay(cfg, args.mode, args.level).run()


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream:
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("shot")
    sp.add_argument("--level", type=int, help="подсказать текущий уровень")
    sub.add_parser("calibrate")
    for name in ("overlay", "test", "run"):
        sp = sub.add_parser(name)
        sp.add_argument("--mode", choices=("bg", "fg"), default="bg",
                        help="bg — фоновый ввод (окно игры перекрыто другими), fg — обычный (игра активна)")
        sp.add_argument("--level", type=int, help="подсказать текущий уровень, если бот не может его прочитать")
        if name != "overlay":
            sp.add_argument("--shots", action="store_true", help="сохранять каждый снимок в папку shots/")
        if name == "run":
            sp.add_argument("--levels", type=int, default=0, help="остановиться после N прокачек (0 — без лимита)")
            sp.add_argument("--minutes", type=float, default=0, help="остановиться через M минут (0 — без лимита)")
    args = p.parse_args()
    cfg = load_config()
    try:
        {"shot": cmd_shot, "calibrate": cmd_calibrate, "overlay": cmd_overlay}.get(args.cmd, cmd_bot)(cfg, args)
    except RuntimeError as e:
        print(f"Ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
