"""Чтение цифр из генератора.

Оба шрифта интерфейса — пиксельные (растровая сетка, растянутая под размер окна), поэтому
символ восстанавливается обратно в свою сетку и сравнивается с образцом точно, без «похожести».

  * «45 из 250» — обычный шрифт Minecraft, цифра 5x7, оранжевый цвет;
  * «УРОВЕНЬ: 7» — шрифт интерфейса 5 строк в высоту, зелёный цвет. Его образцы есть не для
    всех цифр: недостающие бот доучивает сам (см. LevelReader) и хранит в glyphs.json.
"""
import json
import os

LO, MID, HI = 0.35, 0.5, 0.75


def orange_ink(c):
    r, g, b = c
    if r < 60 or b > 100 or r - b < 60 or not 0.45 <= g / r <= 0.85:
        return 0.0
    return r / 255


def green_ink(c):
    r, g, b = c
    if g < 60 or r > 0.5 * g or b > 0.5 * g:
        return 0.0
    return g / 255


def read_glyphs(img, rect, ink, nrows, pitch=None, per_run=False):
    """Символы заданного цвета внутри rect: список слов, слово — список ключей вида 'строка/строка/...'.

    nrows — высота шрифта в его собственных пикселях. Размер такого пикселя на экране (u)
    вычисляется по высоте надписи, так что масштаб окна значения не имеет.
    pitch — шаг моноширинного шрифта (символ + промежуток). Нужен, когда соседние символы
    из-за сглаживания сливаются в одно пятно: такое пятно режется на равные части.
    per_run — высота считается у каждого пятна отдельно: когда рядом с цифрами есть
    значок того же цвета (монетка), он не сбивает масштаб цифр.
    """
    x0, y0, x1, y1 = rect
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, img.w), min(y1, img.h)
    lvl = [[ink(img.px(x, y)) for x in range(x0, x1)] for y in range(y0, y1)]
    if not lvl or not lvl[0]:
        return []
    width = x1 - x0

    def extent(values):
        """Края надписи: середина между «точно краска» и «хоть немного краски» — так точнее при сглаживании."""
        lo = [i for i, v in enumerate(values) if v >= LO]
        hi = [i for i, v in enumerate(values) if v >= HI]
        if not hi:
            return None
        return (lo[0] + hi[0]) / 2, (lo[-1] + hi[-1]) / 2 + 1

    if not per_run:
        rows = extent([max(row) for row in lvl])
        if rows is None:
            return []
        top, bottom = rows
        u = (bottom - top) / nrows
        if u < 1.8:
            return []

    col_max = [max(lvl[j][i] for j in range(len(lvl))) for i in range(width)]
    runs, start = [], None
    for i in range(width + 1):
        on = i < width and col_max[i] >= LO
        if on and start is None:
            start = i
        elif not on and start is not None:
            runs.append((start, i))
            start = None

    words, prev_right = [], None
    for a, b in runs:
        if per_run:
            rows = extent([max(row[a:b]) for row in lvl])
            if rows is None:
                continue
            top, bottom = rows
            u = (bottom - top) / nrows
            if u < 1.8:
                continue
        ext = extent(col_max[a:b])
        if ext is None:
            continue
        left, right = a + ext[0], a + ext[1]
        if pitch:
            count = max(1, round(((right - left) / u + 1) / pitch))
            ncols = pitch - 1
            centers = [(left + right) / 2 + (i - (count - 1) / 2) * pitch * u for i in range(count)]
        else:
            ncols = max(1, round((right - left) / u))
            centers = [(left + right) / 2]
        if prev_right is None or left - prev_right > 2.2 * u:
            words.append([])
        for cx in centers:
            gx = cx - ncols * u / 2
            words[-1].append("/".join(
                "".join("#" if lvl[min(int(top + (r + .5) * u), len(lvl) - 1)]
                                   [min(max(int(gx + (c + .5) * u), 0), width - 1)] >= MID else "."
                        for c in range(ncols))
                for r in range(nrows)))
        prev_right = right
    return words


# ---------------------------------------------------------------- «45 из 250»

_VANILLA = {
    "0": ".###./#...#/#..##/#.#.#/##..#/#...#/.###.",
    "1": "..#../.##../..#../..#../..#../..#../#####",
    "2": ".###./#...#/....#/..##./.#.../#...#/#####",
    "3": ".###./#...#/....#/..##./....#/#...#/.###.",
    "4": "...##/..#.#/.#..#/#...#/#####/....#/....#",
    "5": "#####/#..../####./....#/....#/#...#/.###.",
    "6": "..##./.#.../#..../####./#...#/#...#/.###.",
    "7": "#####/#...#/....#/...#./..#../..#../..#..",
    "8": ".###./#...#/#...#/.###./#...#/#...#/.###.",
    "9": ".###./#...#/#...#/.####/....#/...#./.##..",
}


def _vanilla_digit(key):
    if len(key) != len(_VANILLA["0"]):
        return None
    dist = sorted((sum(a != b for a, b in zip(key, tpl)), d) for d, tpl in _VANILLA.items())
    # Шрифт в клиенте слегка сглажен, поэтому допускаем пару отличий, но только при явном лидере.
    if dist[0][0] <= 2 and dist[1][0] - dist[0][0] >= 2:
        return dist[0][1]
    return None


def _digit_words(img, rect, per_run=False):
    """Оранжевые слова в rect: строка цифр для числового слова, None для всего остального."""
    out = []
    for word in read_glyphs(img, rect, orange_ink, 7, pitch=6, per_run=per_run):
        digits = [_vanilla_digit(k) for k in word]
        out.append("".join(digits) if digits and all(digits) else None)
    return out


def read_numbers(img, rect):
    """Все числа оранжевого цвета в rect, слева направо. «1 000» с пробелом-разделителем склеивается не будет."""
    return [int(w) for w in _digit_words(img, rect) if w]


def join_groups(groups):
    """['6', '781'] -> 6781: число, разбитое пробелами по разрядам."""
    if not groups or not 1 <= len(groups[0]) <= 3 or any(len(g) != 3 for g in groups[1:]):
        return None
    return int("".join(groups))


def read_amount(img, rect):
    """Сумма вида «6 781»: в rect должно быть только это число."""
    words = _digit_words(img, rect)
    return join_groups(words) if words and all(words) else None


def read_cost(img, rect, ui_known):
    """Цена на кнопке «Прокачать за N»: цифры среди белого текста и значка монеты.

    Каким шрифтом нарисована цена, по скриншоту не видно, поэтому пробуем оба.
    """
    amount = join_groups([w for w in _digit_words(img, rect, per_run=True) if w])
    if amount:
        return amount
    # Значок монеты стоит вплотную к цифрам и попадает в то же «слово» — режем слово на нём.
    groups = []
    for word in read_glyphs(img, rect, orange_ink, 5, per_run=True):
        current = ""
        for key in word + [None]:
            digit = ui_known.get(key)
            if digit:
                current += digit
            elif current:
                groups.append(current)
                current = ""
    return join_groups(groups)


# ---------------------------------------------------------------- «УРОВЕНЬ: 7»

# Образцы сняты со скриншотов генератора (заголовок, вкладки, цена в диалоге прокачки).
_UI_SEED = {
    ".###./#...#/#.#.#/#...#/.###.": "0",
    ".#./##./.#./.#./###": "1",
    "####./....#/.###./#..../#####": "2",
    "####./....#/.###./....#/####.": "3",
    "..##./.#.#./#..#./#####/...#.": "4",
    "####./#..../####./....#/####.": "5",
    ".###./#..../####./#...#/.###.": "6",
    "#####/....#/...#./..#../..#..": "7",
    ".###./#...#/.###./#...#/.###.": "8",
}


class LevelReader:
    """Читает номер уровня и сам доучивает незнакомые цифры.

    В генераторе всегда видны два числа: текущий уровень и следующий (текущий + 1). Если одно из
    них прочитано, второе известно заранее — и его незнакомые символы можно подписать и запомнить.
    """

    def __init__(self, path):
        self.path = path
        self.known = dict(_UI_SEED)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.known.update(json.load(f))

    def _decode(self, word):
        digits = [self.known.get(k) for k in word]
        return int("".join(digits)) if digits and all(digits) else None

    def _learn(self, word, value):
        text = str(value)
        if len(word) != len(text):
            return
        new = {k: d for k, d in zip(word, text) if k not in self.known}
        if any(self.known.get(k, d) != d for k, d in zip(word, text)) or not new:
            return
        self.known.update(new)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.known, f, ensure_ascii=False, indent=1)

    def read(self, img, cur_rect, next_rect, hint=None):
        """Текущий уровень или None. hint — уровень, известный из прошлого чтения или заданный вручную."""
        cur_words = read_glyphs(img, cur_rect, green_ink, 5)
        next_words = read_glyphs(img, next_rect, green_ink, 5)
        cur_word = cur_words[-1] if cur_words else []
        next_word = next_words[-1] if next_words else []
        cur, nxt = self._decode(cur_word), self._decode(next_word)
        if cur is None and nxt is not None:
            cur = nxt - 1
        if cur is None and hint is not None and len(cur_word) == len(str(hint)):
            cur = hint
        if cur is not None:
            self._learn(cur_word, cur)
            if next_word:
                self._learn(next_word, cur + 1)
        return cur
