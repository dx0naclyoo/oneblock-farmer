"""Распознавание состояния по пикселям: открыт ли генератор, открыт ли диалог, насколько заполнена шкала."""
import ocr

MAIN, DIALOG, OTHER = "генератор", "диалог", "игра"


def is_green(c):
    """Ярко-зелёный интерфейса: полоса под вкладками, заливка шкалы прогресса."""
    r, g, b = c
    return g >= 140 and g - max(r, b) >= 60


def is_magenta(c):
    """Розовая плашка «Быстрая прокачка за гемы» — есть только в диалоге прокачки."""
    r, g, b = c
    return r >= 170 and b >= 170 and min(r, b) - g >= 70


class Vision:
    def __init__(self, cfg, size):
        self.cfg = cfg
        self.kx = size[0] / cfg["ref_size"][0]
        self.ky = size[1] / cfg["ref_size"][1]

    def pt(self, name_or_xy):
        x, y = self.cfg[name_or_xy] if isinstance(name_or_xy, str) else name_or_xy
        return round(x * self.kx), round(y * self.ky)

    def rect(self, name):
        x0, y0, x1, y1 = self.cfg[name]
        return round(x0 * self.kx), round(y0 * self.ky), round(x1 * self.kx), round(y1 * self.ky)

    def state(self, img):
        if all(is_magenta(img.px(*self.pt(p))) for p in self.cfg["dialog_anchors"]):
            return DIALOG
        if all(is_green(img.px(*self.pt(p))) for p in self.cfg["main_anchors"]):
            return MAIN
        return OTHER

    def coins(self, img, state=None):
        """Баланс монет: в игре — счётчик слева вверху, в генераторе — его шапка, в диалоге не виден."""
        state = state or self.state(img)
        if state == DIALOG:
            return None
        return ocr.read_amount(img, self.rect("coins_gui_rect" if state == MAIN else "coins_hud_rect"))

    def progress(self, img):
        """Доля заполнения шкалы 0..1: длина зелёной заливки от левого края шкалы."""
        x0, y = self.pt("bar_left")
        x1, _ = self.pt("bar_right")
        best = 0
        for dy in (-2, 0, 2):
            run, gap = 0, 0
            for x in range(x0, x1 + 1):
                if is_green(img.px(x, y + dy)):
                    run, gap = x - x0 + 1, 0
                else:
                    gap += 1
                    if gap > 3:
                        break
            best = max(best, run)
        return best / (x1 - x0 + 1)
