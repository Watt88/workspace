# build_140_laser.py — v2
# Сборка /project1/laser140: игра «140» с экрана -> LAYU ABB06RGB через Helios.
# Параметры и операторы взяты из проверенного канона проекта LASER_RIG (TD 2025.33230):
#   Laser Device CHOP вместо устаревшего Helios DAC CHOP, Trace POP -> POP to CHOP (nextframe)
#   вместо Trace SOP (быстрее в ~10 раз), guard после Laser CHOP, ARM только человеком.
#
# Запуск: Text DAT в /project1 -> вставить файл -> ПКМ -> Run Script.
# Всё, что не нашлось, печатается в Textport как "[!] ...".
# Метки в комментариях: [V] проверено в LASER_RIG, [U] не проверено.
#
# Цепочка:
#   grab -> res 320x180 -> key_player / key_obst (Threshold TOP)
#   -> trace_* (Trace POP) -> pts_* (POP to CHOP, nextframe)
#   -> shapes (Script CHOP: приоритет игрока, бюджет, обход, углы, инверсия осей)
#   -> laser (Laser CHOP, source=chop) -> guard (Script CHOP) -> device (Laser Device CHOP, helios)
#   device_info (Info CHOP) + safety (Execute DAT: старт в Blackout, watchdog)
#
# БЕЗОПАСНОСТЬ: собирается с Arm=0, Blackout=1. Не ставить таймлайн на паузу при Arm —
# Helios бесконечно повторяет последний кадр, сторожа на паузе не работают. [V]

import td

ROOT = op('/project1')
NAME = 'laser140'

# Два Laser Device CHOP на одном Helios конфликтуют [V] — предупреждаем заранее.
_ldtype = getattr(td, 'laserdeviceCHOP', None)
if _ldtype is not None:
    for o in ROOT.findChildren(type=_ldtype):
        if not o.path.startswith(ROOT.path + '/' + NAME + '/'):
            print(f'[!] Уже есть Laser Device CHOP {o.path} (active={o.par.active.eval()}). '
                  'Активным держать только один; после смены нажать Scan.')

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
            print(f'[!] {o.name}.{k}={v!r}: {e}')


def setexpr(o, **kw):
    for k, e in kw.items():
        p = getattr(o.par, k, None)
        if p is None:
            print(f'[!] {o.name}: нет параметра "{k}" — выражение вручную: {e}')
            continue
        p.expr = e
        p.mode = ParMode.EXPRESSION


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


def scripted(typename, name, x, y, code, inp=None):
    o = mk(typename, name, x, y, inp)
    if o is None:
        return None
    cb = None
    try:
        cb = o.par.callbacks.eval()
    except Exception:
        pass
    if cb is None:
        cb = c.op(name + '_callbacks')
    if cb is None:
        cb = c.create(textDAT, name + '_callbacks')
        o.par.callbacks = cb
    cb.text = code
    cb.nodeX, cb.nodeY = o.nodeX, o.nodeY - 130
    return o


def add(page, kind, name, label, val, rng=None):
    getattr(page, 'append' + kind)(name, label=label)
    names, vals = ([name + s for s in 'rgb'], val) if kind == 'RGB' else ([name], [val])
    for n, v in zip(names, vals):
        p = getattr(c.par, n)
        p.default = v
        p.val = v
        if rng:
            p.normMin, p.normMax = rng


# ---------- Пульт: отдельные вкладки ----------
po = c.appendCustomPage('Output')
add(po, 'Toggle', 'Arm', 'ARM (только человек)', False)
add(po, 'Toggle', 'Blackout', 'Blackout', True)
add(po, 'Float', 'Ceiling', 'Потолок мощности', 0.135, (0, 1))   # 0.135 = «дом» [V]
add(po, 'Float', 'Gainr', 'Gain R', 1.0, (0, 1))
add(po, 'Float', 'Gaing', 'Gain G', 1.0, (0, 1))
add(po, 'Float', 'Gainb', 'Gain B', 1.0, (0, 1))
add(po, 'Int', 'Pps', 'Скорость, pps', 28000, (10000, 30000))  # 28k без потерь, 30k теряет [V]
add(po, 'Float', 'Size', 'Размер поля', 0.6, (0, 1))           # 0.5–0.7 для графики [V]
add(po, 'Toggle', 'Invertx', 'Инверсия X', True)                # у ABB06RGB инвертированы обе оси [V]
add(po, 'Toggle', 'Inverty', 'Инверсия Y', True)
add(po, 'Float', 'Offsetx', 'Сдвиг X', 0.0, (-0.5, 0.5))
add(po, 'Float', 'Offsety', 'Сдвиг Y', 0.0, (-0.5, 0.5))

ps = c.appendCustomPage('Shapes')
add(ps, 'Toggle', 'Playeronly', 'Только игрок', False)
add(ps, 'Float', 'Playerthresh', 'Порог игрока (яркость >)', 0.85, (0, 1))
add(ps, 'Float', 'Obstthresh', 'Порог препятствий (яркость <)', 0.15, (0, 1))
add(ps, 'RGB', 'Playercolor', 'Цвет игрока', (1.0, 1.0, 1.0))
add(ps, 'RGB', 'Obstcolor', 'Цвет препятствий', (0.0, 0.8, 1.0))
add(ps, 'Float', 'Playermin', 'Мин. размер игрока', 0.2, (0, 0.5))  # защита Layu от мелких фигур [V]
add(ps, 'Float', 'Minhz', 'Мин. частота обхода, Гц', 30, (15, 60))  # ниже ~30 Гц видно мерцание [V]
add(ps, 'Int', 'Step', 'Прореживание (каждая N-я)', 2, (1, 8))
add(ps, 'Int', 'Minpts', 'Мин. точек в контуре', 6, (3, 50))
add(ps, 'Float', 'Cornerdeg', 'Угол, с которого точка — угол', 35, (10, 90))  # 30–35° [U]
add(ps, 'Float', 'Blankcost', 'Сэмплов на перелёт', 12, (0, 50))   # [U] оценка
add(ps, 'Float', 'Cornercost', 'Сэмплов на угол', 4, (0, 20))      # ~maxcornerhold·pps [U]

pg = c.appendCustomPage('Guard')
add(pg, 'Toggle', 'Guardon', 'Guard включён', True)
add(pg, 'Float', 'Capint', 'Потолок цвета в guard', 1.0, (0, 1))
add(pg, 'Float', 'Zonex', 'Зона |x| <', 0.95, (0, 1))
add(pg, 'Float', 'Zoney', 'Зона |y| <', 0.95, (0, 1))
add(pg, 'Float', 'Minsize', 'Мин. размер кадра', 0.1, (0, 0.5))   # 0.04 ронял защиту Layu [V]
add(pg, 'Float', 'Maxstep', 'Макс. шаг горящего луча', 0.03, (0, 0.2))
add(pg, 'Int', 'Maxdwell', 'Макс. стоянка, сэмплов', 12, (2, 100))

# ---------- Захват и ключи ----------
grab = mk('screengrabTOP', 'grab', 0, 0)
res = mk('resolutionTOP', 'res', 1, 0, grab)
if res:
    setp(res, outputresolution='custom', resolutionw=320, resolutionh=180)

kp = mk('thresholdTOP', 'key_player', 2, -1, res)
ko = mk('thresholdTOP', 'key_obst', 2, 1, res)
if kp:
    setp(kp, comparator='greater')          # [U] имя меню
    setexpr(kp, threshold='parent().par.Playerthresh')   # [V]
if ko:
    setp(ko, comparator='less')             # [U]
    setexpr(ko, threshold='parent().par.Obstthresh')

# ---------- Трассировка: Trace POP -> POP to CHOP (nextframe) ----------
# Laser CHOP, читающий POP напрямую, тормозит до сотен мс; через POP to CHOP nextframe — 0.5 мс [V].
# Диапазон ±0.5 по X, по Y — с учётом 16:9.
pts = {}
for key, kop, row in (('player', kp, -1), ('obst', ko, 1)):
    tr = mk('tracePOP', 'trace_' + key, 3, row)
    if tr and kop:
        setp(tr, top=kop.path, threshold=0.5, rerangep=True,
             tolow0=-0.5, tohigh0=0.5, tolow1=-0.28125, tohigh1=0.28125)   # [V] имена
    p2c = mk('poptoCHOP', 'pts_' + key, 4, row)
    if p2c and tr:
        setp(p2c, pop=tr.path, downloadtype='nextframe', extract='points')  # [V]
    pts[key] = p2c

# ---------- shapes: приоритет игрока, бюджет, обход, углы ----------
SHAPES = r'''
# Вход 0 — игрок (всегда рисуется), вход 1 — препятствия (от крупных к мелким, пока влезает).
# Бюджет = Pps / Minhz выходных сэмплов на один обход.
# Laser CHOP внутри считает на 192 кГц: путь за выходной сэмпл = stepsize*192000/pps.
# Выход: x y r g b id lascorner — формат CHOP-входа Laser CHOP.
import numpy as np

CX = ('p0', 'P0', 'P(0)', 'tx', 'x')
CY = ('p1', 'P1', 'P(1)', 'ty', 'y')
CID = ('lsidx', 'id', 'primid', 'prim')
JUMP = 3.0 / 320.0          # запасной разрез контуров: разрыв > 3 px
_warned = set()


def _warn(msg):
    if msg not in _warned:
        _warned.add(msg)
        print('[laser140/shapes] ' + msg)


def _chan(inp, names):
    for n in names:
        ch = inp.chan(n)
        if ch is not None:
            return np.asarray(ch.vals, dtype=np.float64)
    return None


def _strips(inp):
    if inp is None or inp.numSamples < 2:
        return []
    x, y = _chan(inp, CX), _chan(inp, CY)
    if x is None or y is None:
        _warn('нет X/Y во входе %s: %s' % (inp.name, [ch.name for ch in inp.chans()]))
        return []
    pts = np.column_stack([x, y])
    ids = _chan(inp, CID)
    if ids is not None:
        cut = np.flatnonzero(np.diff(ids) != 0) + 1
    else:
        _warn('нет id/lsidx во входе %s — режу контуры по разрывам' % inp.name)
        cut = np.flatnonzero(np.hypot(*np.diff(pts, axis=0).T) > JUMP) + 1
    return np.split(pts, cut)


def _length(s):
    cl = np.vstack([s, s[:1]])
    return float(np.hypot(*np.diff(cl, axis=0).T).sum())


def _corners(s, deg):
    v1 = s - np.roll(s, 1, 0)
    v2 = np.roll(s, -1, 0) - s
    den = np.maximum(np.hypot(*v1.T) * np.hypot(*v2.T), 1e-12)
    return ((v1 * v2).sum(1) / den < np.cos(np.radians(deg))).astype(np.float64)


def _grow(shapes, minsize):
    # мелкие фигуры роняют защиту Layu — увеличиваем игрока вокруг центра
    allp = np.vstack(shapes)
    lo, hi = allp.min(0), allp.max(0)
    ext = float((hi - lo).max())
    if ext <= 0 or ext >= minsize:
        return shapes
    cen = (lo + hi) / 2
    return [(s - cen) * (minsize / ext) + cen for s in shapes]


def onCook(scriptOp):
    scriptOp.clear()
    scriptOp.isTimeSlice = False
    P = parent().par
    lz = op('laser')
    pps = max(1000.0, float(P.Pps))
    budget = pps / max(1.0, float(P.Minhz))
    stepsize = float(lz.par.stepsize) if lz is not None else 0.00125
    per = max(1e-6, stepsize * 192000.0 / pps)
    step = max(1, int(P.Step))
    minpts = max(3, int(P.Minpts))
    deg = float(P.Cornerdeg)

    ins = scriptOp.inputs
    layers = []
    if len(ins) > 0:
        layers.append((0, ins[0], (P.Playercolorr.eval(), P.Playercolorg.eval(), P.Playercolorb.eval())))
    if len(ins) > 1 and not P.Playeronly.eval():
        layers.append((1, ins[1], (P.Obstcolorr.eval(), P.Obstcolorg.eval(), P.Obstcolorb.eval())))

    cand = []
    for prio, inp, col in layers:
        shapes = []
        for s in _strips(inp):
            if len(s) < minpts:
                continue
            if len(s) > 2 and np.allclose(s[0], s[-1]):
                s = s[:-1]
            s = s[::step]
            if len(s) >= 3:
                shapes.append(s)
        if prio == 0 and shapes:
            shapes = _grow(shapes, float(P.Playermin))
        for s in shapes:
            cand.append((prio, -_length(s), s, col))
    cand.sort(key=lambda t: (t[0], t[1]))

    used = 0.0
    chosen = []
    for prio, negl, s, col in cand:
        corn = _corners(s, deg)
        cost = -negl / per + float(P.Blankcost) + float(P.Cornercost) * corn.sum()
        if prio > 0 and used + cost > budget:
            continue
        used += cost
        chosen.append((prio, s, corn, col))

    # Обход: игрок первым, дальше жадно ближайший; старт контура — ближайшая к лучу точка.
    pos = np.zeros(2)
    out = []
    for group in (0, 1):
        pool = [t for t in chosen if t[0] == group]
        while pool:
            best = None
            for i, (_, s, _, _) in enumerate(pool):
                d = np.hypot(*(s - pos).T)
                j = int(d.argmin())
                if best is None or d[j] < best[0]:
                    best = (d[j], i, j)
            _, i, j = best
            _, s, corn, col = pool.pop(i)
            s = np.roll(s, -j, 0)
            corn = np.roll(corn, -j)
            s = np.vstack([s, s[:1]])          # замыкаем контур
            corn = np.r_[corn, corn[:1]]
            out.append((s, corn, col))
            pos = s[-1]

    if out:
        xy = np.vstack([s for s, _, _ in out])
        lc = np.concatenate([cn for _, cn, _ in out])
        ids = np.concatenate([np.full(len(s), k, np.float64) for k, (s, _, _) in enumerate(out)])
        rgb = np.vstack([np.tile(col, (len(s), 1)) for s, _, col in out])
    else:                                       # пусто — одна тёмная точка в центре
        xy = np.zeros((1, 2))
        lc = np.zeros(1)
        ids = np.zeros(1)
        rgb = np.zeros((1, 3))

    x = xy[:, 0] * (-1.0 if P.Invertx else 1.0) + float(P.Offsetx)
    y = xy[:, 1] * (-1.0 if P.Inverty else 1.0) + float(P.Offsety)

    scriptOp.numSamples = len(x)
    for name, arr in (('x', x), ('y', y), ('r', rgb[:, 0]), ('g', rgb[:, 1]), ('b', rgb[:, 2]),
                      ('id', ids), ('lascorner', lc)):
        scriptOp.appendChan(name).vals = arr.tolist()
    return
'''

shapes = scripted('scriptCHOP', 'shapes', 5, 0, SHAPES)
if shapes:
    if pts.get('player'):
        pts['player'].outputConnectors[0].connect(shapes.inputConnectors[0])
    if pts.get('obst'):
        pts['obst'].outputConnectors[0].connect(shapes.inputConnectors[1])

# ---------- Laser CHOP: канон LASER_RIG [V] ----------
laser = mk('laserCHOP', 'laser', 6, 0)
if laser and shapes:
    setp(laser, source='chop', chop=shapes.path,
         stepsize=0.00125, bstepsize=0.005,          # bstepsize 0.02 давал хвост после прыжка [V]
         mincornerhold=0.05, maxcornerhold=0.2, closedoverlap=0.15,
         preblankon=0.07, postblankon=0.15, preblankoff=0.4, postblankoff=0.03,   # мс
         colordelay=0.1, interpcolors=True,           # 0.10–0.14 мс, замер камерой [V]
         updatemethod='alldrawn', startpulse=False)  # startpulse даёт цвет −1 [V]
    setexpr(laser, outputrate='parent().par.Pps',
            xscale='parent().par.Size', yscale='parent().par.Size')

# ---------- guard: последний рубеж перед устройством ----------
GUARD = r'''
# Laser CHOP отдаёт временные срезы (pps/fps сэмплов), не целые фигуры — краёв кадра не гасим. [V]
# rate и numSamples копируем: по rate входа устройство выбирает pps. [V]
import numpy as np


def onCook(scriptOp):
    scriptOp.clear()
    if not scriptOp.inputs:
        return
    inp = scriptOp.inputs[0]
    P = parent().par
    n = inp.numSamples
    scriptOp.rate = inp.rate
    scriptOp.numSamples = n
    if n == 0:
        return

    def g(name):
        ch = inp.chan(name)
        return np.asarray(ch.vals, dtype=np.float64) if ch is not None else np.zeros(n)

    x, y = g('x'), g('y')
    rgb = np.stack([g('r'), g('g'), g('b')])

    if P.Guardon:
        bad = ~(np.isfinite(x) & np.isfinite(y))     # NaN/Inf: гасим, держим позицию
        x[bad] = 0.0
        y[bad] = 0.0
        rgb[~np.isfinite(rgb)] = 0.0
        rgb[:, bad] = 0.0
        np.clip(rgb, 0.0, float(P.Capint), out=rgb)
        rgb[:, (np.abs(x) > float(P.Zonex)) | (np.abs(y) > float(P.Zoney))] = 0.0
        lit = rgb.max(0) > 1e-3
        # стоячий горящий луч
        same = np.r_[False, (np.diff(x) == 0) & (np.diff(y) == 0)]
        grp = np.cumsum(~same)
        runlen = np.bincount(grp)[grp]
        rgb[:, (runlen > int(P.Maxdwell)) & lit] = 0.0
        # рывок горящего луча
        stepd = np.r_[0.0, np.hypot(np.diff(x), np.diff(y))]
        rgb[:, (stepd > float(P.Maxstep)) & lit & np.r_[False, lit[:-1]]] = 0.0
        # одиночная точка / слишком мелкая картинка (защита Layu)
        lit = rgb.max(0) > 1e-3
        if lit.sum() <= 2:
            rgb[:] = 0.0
        elif max(np.ptp(x[lit]), np.ptp(y[lit])) < float(P.Minsize):
            rgb[:] = 0.0

    for name, arr in (('x', x), ('y', y), ('r', rgb[0]), ('g', rgb[1]), ('b', rgb[2])):
        scriptOp.appendChan(name).vals = arr.tolist()
    return
'''
guard = scripted('scriptCHOP', 'guard', 7, 0, GUARD, laser)

# ---------- Laser Device CHOP (Helios) [V] ----------
device = mk('laserdeviceCHOP', 'device', 8, 0, guard)
if device:
    setp(device, type='helios', device='helios0', queuetime=0.25)
    # ARM — только через параметр компонента; blackout — через гейны (выключение active
    # крашило TD 2025). Писатель гейнов один — это выражение, руками не трогать. [V]
    setexpr(device, active='parent().par.Arm')
    for ch, gp in (('redscale', 'Gainr'), ('greenscale', 'Gaing'), ('bluescale', 'Gainb')):
        setexpr(device, **{ch: f'parent().par.Ceiling * parent().par.{gp} * (0 if parent().par.Blackout else 1)'})
    try:
        print('Helios:', device.par.device.menuLabels)
    except Exception:
        pass

info = mk('infoCHOP', 'device_info', 8, 1)
if info and device:
    setp(info, op=device.path)

# ---------- safety: старт в Blackout + watchdog ----------
SAFETY = r'''
# На старте/создании: Arm=0, Blackout=1.
# Каждые 6 кадров при Arm: guard не кукается дольше GRACE или Helios отключён -> DISARM.
# На паузе таймлайна это НЕ работает — не паузить при Arm.
GRACE = 1.5
_armed_at = [None]


def _safe(reason=None):
    p = parent().par
    p.Arm = 0
    p.Blackout = 1
    if reason:
        print('[laser140] DISARM: ' + reason)


def onStart():
    _safe()
    return


def onCreate():
    _safe()
    return


def onFrameStart(frame):
    p = parent().par
    if not p.Arm.eval():
        _armed_at[0] = None
        return
    now = absTime.seconds
    if _armed_at[0] is None:
        _armed_at[0] = now
    if frame % 6 or now - _armed_at[0] < GRACE:
        return
    g = op('guard')
    if g is None or absTime.frame - g.cookAbsFrame > project.cookRate * GRACE:
        _safe('guard не кукается')
        return
    if not p.Guardon.eval():
        print('[laser140] внимание: Arm при выключенном Guard')
    info = op('device_info')
    ch = info.chan('connected') if info is not None else None
    if ch is not None and ch[0] < 0.5:
        _safe('Helios не подключён')
    return
'''
safety = c.create(executeDAT, 'safety')
safety.nodeX, safety.nodeY = 8 * 220, 300
safety.text = SAFETY
setp(safety, active=True, start=True, create=True, framestart=True)   # [U] имена toggles

c.par.Arm = 0
c.par.Blackout = 1

print('laser140 собран (Arm=0, Blackout=1). Проверь превью laser/guard и device_info, '
      'затем: Blackout=0, Arm=1. Если ничего не светит — Active 0 -> Scan -> Active 1.')
