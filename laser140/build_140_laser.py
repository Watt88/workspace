# build_140_laser.py
# Сборка сети "140 -> лазер" в TouchDesigner (вариант 1: захват экрана + трассировка).
#
# Как запустить:
#   1) В TD создай Text DAT в /project1, вставь этот файл целиком.
#   2) ПКМ по DAT -> Run Script.
#   Появится компонент /project1/laser140 с готовой цепочкой.
#   Если какой-то параметр в твоей версии TD называется иначе — скрипт напишет
#   в Textport строку "[!] ..." и его нужно выставить руками.
#
# Цепочка:
#   Screen Grab TOP -> Resolution 320x180 -> два ключа (игрок / препятствия)
#   -> Trace SOP x2 -> Script SOP (упрощение, цвета, бюджет точек, приоритет игрока)
#   -> Transform SOP (подгонка в поле) -> Laser CHOP -> Helios DAC CHOP
#
# Helios создаётся ВЫКЛЮЧЕННЫМ. Включай вручную, когда проверишь картинку
# в превью Laser CHOP и направление луча.

import td

ROOT = op('/project1')
NAME = 'laser140'

if ROOT.op(NAME):
    ROOT.op(NAME).destroy()
c = ROOT.create(baseCOMP, NAME)
c.nodeX, c.nodeY = 0, 0


def setp(o, **kw):
    for k, v in kw.items():
        p = getattr(o.par, k, None)
        if p is None:
            print(f'[!] {o.name}: нет параметра "{k}" — выставь вручную: {v}')
            continue
        try:
            p.val = v
        except Exception as e:
            print(f'[!] {o.name}.{k}: {e}')


def mk(typename, name, x, y, inp=None):
    t = getattr(td, typename, None)
    if t is None:
        print(f'[!] Тип {typename} не найден в этой версии TD — создай "{name}" вручную')
        return None
    o = c.create(t, name)
    o.nodeX, o.nodeY = x * 220, -y * 160
    if inp is not None:
        inp.outputConnectors[0].connect(o)
    return o


# ---------- Пульт: кастомные параметры на компоненте ----------
pg = c.appendCustomPage('Laser')
pg.appendInt('Budget', label='Бюджет точек на кадр')
pg.appendInt('Step', label='Прореживание (каждая N-я точка)')
pg.appendInt('Minpts', label='Мин. точек в контуре')
pg.appendToggle('Playeronly', label='Только игрок')
pg.appendFloat('Playerthresh', label='Порог игрока (яркость >)')
pg.appendFloat('Obstthresh', label='Порог препятствий (яркость <)')
pg.appendRGB('Playercolor', label='Цвет игрока')
pg.appendRGB('Obstcolor', label='Цвет препятствий')

c.par.Budget = 800        # под ~25 kpps при ~30 к/с; подними/опусти по факту мерцания
c.par.Step = 2
c.par.Minpts = 6
c.par.Playeronly = False
c.par.Playerthresh = 0.85
c.par.Obstthresh = 0.15
c.par.Playercolorr, c.par.Playercolorg, c.par.Playercolorb = 1, 1, 1
c.par.Obstcolorr, c.par.Obstcolorg, c.par.Obstcolorb = 0, 0.8, 1

# ---------- Захват ----------
grab = mk('screengrabTOP', 'grab', 0, 0)
res = mk('resolutionTOP', 'res', 1, 0, grab)
if res:
    setp(res, outputresolution='custom', resolutionw=320, resolutionh=180)

# ---------- Ключи ----------
# Пороги подбираются под конкретные уровни 140: игрок светлый,
# препятствия тёмные. Если в каком-то мире иначе — меняй пороги/comparator.
kp = mk('thresholdTOP', 'key_player', 2, -1, res)
ko = mk('thresholdTOP', 'key_obst', 2, 1, res)
if kp:
    setp(kp, comparator='greater')
    kp.par.threshold.expr = "parent().par.Playerthresh"
if ko:
    setp(ko, comparator='less')
    ko.par.threshold.expr = "parent().par.Obstthresh"

# ---------- Трассировка ----------
tp = mk('traceSOP', 'trace_player', 3, -1)
to = mk('traceSOP', 'trace_obst', 3, 1)
if tp and kp:
    setp(tp, top=kp.path)
if to and ko:
    setp(to, top=ko.path)

# ---------- Script SOP: бюджет, приоритет, цвета ----------
CALLBACKS = r'''
# Вход 0 — игрок (всегда в приоритете), вход 1 — препятствия.
# Внутри слоя — сначала крупные контуры. Всё, что не влезает в бюджет, отбрасывается.

BLANK_COST = 8  # запас точек на гашение/переход между фигурами

def onCook(scriptOp):
    scriptOp.clear()
    P = parent().par
    budget = int(P.Budget)
    step = max(1, int(P.Step))
    minpts = max(3, int(P.Minpts))

    try:
        scriptOp.pointAttribs.create('Cd', (1.0, 1.0, 1.0, 1.0))
    except Exception:
        pass

    layers = []
    if len(scriptOp.inputs) > 0:
        layers.append((scriptOp.inputs[0], (P.Playercolorr.eval(), P.Playercolorg.eval(), P.Playercolorb.eval()), 0))
    if len(scriptOp.inputs) > 1 and not P.Playeronly.eval():
        layers.append((scriptOp.inputs[1], (P.Obstcolorr.eval(), P.Obstcolorg.eval(), P.Obstcolorb.eval()), 1))

    shapes = []
    for src, col, prio in layers:
        for prim in src.prims:
            pts = [(v.point.x, v.point.y) for v in prim][::step]
            if len(pts) < minpts:
                continue
            shapes.append((prio, -len(pts), pts, col))

    shapes.sort(key=lambda s: (s[0], s[1]))

    used = 0
    for prio, _, pts, col in shapes:
        cost = len(pts) + BLANK_COST
        if used + cost > budget:
            continue
        used += cost
        poly = scriptOp.appendPoly(len(pts), closed=True, addPoints=True)
        for i, (x, y) in enumerate(pts):
            p = poly[i].point
            p.x, p.y, p.z = x, y, 0.0
            p.Cd = (col[0], col[1], col[2], 1.0)
    return
'''

shapes = mk('scriptSOP', 'shapes', 4, 0)
if shapes:
    # Script SOP сам создаёт DAT shapes_callbacks — берём его, а не плодим второй.
    cb = shapes.par.callbacks.eval() or c.op('shapes_callbacks')
    if cb is None:
        cb = c.create(textDAT, 'shapes_callbacks')
        shapes.par.callbacks = cb
    cb.nodeX, cb.nodeY = 4 * 220, 300
    cb.text = CALLBACKS
    if tp:
        tp.outputConnectors[0].connect(shapes.inputConnectors[0])
    if to:
        to.outputConnectors[0].connect(shapes.inputConnectors[1])

# Подгонка в поле лазера: масштаб/сдвиг/отражение крути здесь.
fit = mk('transformSOP', 'fit', 5, 0, shapes)

# ---------- Лазер ----------
laser = mk('laserCHOP', 'laser', 6, 0)
if laser and fit:
    setp(laser, sop=fit.path)

helios = mk('heliosdacCHOP', 'helios', 7, 0, laser)
if helios:
    setp(helios, active=False)

print('laser140 собран. Проверь превью Laser CHOP, затем включи Active на helios.')
