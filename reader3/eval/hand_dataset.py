"""Hand-written evaluation questions for the two small test books (written after reading them in full).

    uv run python eval/hand_dataset.py      (checks every gold quote against the book and writes eval/out/hand.json)

Each answerable item: book, loc (gold page; `also` lists other pages that contain the answer), q (Russian), q_en (what a good agent
would search for, in the book's language), quote (verbatim), answer (gold, Russian). Negatives: questions the book cannot answer."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import notebook  # noqa: E402

LIB = os.environ.get("EVAL_LIB", r"C:\WORK\PROJECTS\reader3\testlib")
POLY = "Polyrhythms_data"
FIFTHS = "The_Ultimate_Guide_to__The_Circle_of_Fifths_data"


def A(book, loc, q, q_en, quote, answer, also=()):
    return {"book": book, "lang": "en", "kind_book": "pdf", "loc": loc, "also": list(also), "q": q, "q_en": q_en, "quote": quote,
            "answer": answer, "kind": "hand"}


ANSWERABLE = [
    A(POLY, 5, "Какую общественную роль, по словам автора, играют полиритмы в африканской культуре?", "polyrhythm African culture social purpose community",
      "a musical symbol of the rich relationships between individuals", "Они символ отношений между людьми, из которых складывается культурное единство общины."),
    A(POLY, 11, "Является ли соотношение 4:1 полиритмом и почему?", "polyrhythm common divisor other than 1 not a polyrhythm",
      "two rhythms will only be considered a polyrhythm if they have no common divisor other than 1", "Нет: у 4 и 1 нет контрастирующих долей, делитель 1 общий; в упражнении ответ «не полиритм».", also=[13]),
    A(POLY, 13, "Какую фразу предлагают проговаривать, чтобы отработать ритм «три на два»?", "phrase Hot Cup of Tea practice 3:2 polyrhythm",
      "you can use the phrase Hot Cup of Tea to practice 3:2 polyrhythms", "Фразу «Hot Cup of Tea»."),
    A(POLY, 15, "Сколько долей занимает полный цикл полиритма 3:4 и как это вычислить?", "lowest common multiple LCM 3:4 full cycle 12 beats",
      "The LCM is 12, meaning that 12 beats represent a full cycle of the polyrhythm", "12 долей; нужно найти наименьшее общее кратное чисел X и Y."),
    A(POLY, 16, "Чему равен полный цикл полиритма 4:5?", "LCM of 4 and 5 is 20 beats full cycle 4:5",
      "The lowest common multiple (LCM) of 4 and 5 is 20", "20 долей."),
    A(POLY, 17, "На какой темп советуют ставить метроном новичкам, изучающим полиритмы?", "set metronome 60 beats per minute start slow",
      "set your metronome to 60 beats per minute", "На 60 ударов в минуту, начиная медленно."),
    A(POLY, 7, "Что происходит на отметке 1:22 в песне Nine Inch Nails «La Mer»?", "La Mer at 1:22 drums kick in four beats against three piano",
      "the drums kick in, playing four beats against the three beats of the piano", "Вступают барабаны: четыре доли против трёх у фортепиано."),
    A(POLY, 7, "Как в Африке называют гемиолу, повторяющуюся на протяжении всей песни?", "hemiola African music cross rhythm systematic rhythm base of a piece",
      "cross rhythm, or a systematic rhythm that is the base of a piece", "Кросс-ритм: систематический ритм, на котором держится вся пьеса."),
    A(POLY, 18, "Какую функцию приложения PolyNome автор особенно хвалит?", "PolyNome app Practice Log feature track progress",
      "Especially wonderful is the app's Practice Log feature", "Журнал практики: какие ритмы, в каком темпе и сколько играл, плюс графики и отчёты."),
    A(POLY, 19, "Как пианисту или гитаристу потренироваться играть полиритм?", "piano guitar practice polyrhythm beat on instrument humming tapping clapping different rhythm",
      "Try playing a beat on your instrument, whether it's on one note or a simple melodic motif, while humming, tapping, or clapping overtop in a different rhythm",
      "Играть ровный бит на инструменте и одновременно напевать, отстукивать или хлопать другой ритм."),
    A(POLY, 8, "Что обозначают X и Y в записи полиритма X:Y?", "polyrhythmic formula X:Y Y basic pulse X counter rhythm",
      "Y is the basic pulse, over which the counter rhythm will be played, and X is the counter rhythm", "Y: основной пульс; X: контр-ритм, играемый поверх него."),
    A(POLY, 9, "Почему в ритме 3:2 триоли приходится играть быстрее дуолей?", "3:2 triplets played faster than duplets arrive on beat one at the same time",
      "the triplets will be played faster than the duplets, in order to arrive on beat one at the same time", "Обе фигуры занимают одно и то же время, и первые ноты должны совпасть на первой доле."),
    A(FIFTHS, 5, "Кто и когда придумал круг квинт?", "Circle of Fifths invented Nikolay Diletsky 1670s Grammatika",
      "Russian composer and music theorist Nikolay Diletsky set this whole wheel rolling in the late 1670's", "Николай Дилецкий, русский композитор и теоретик, в конце 1670-х годов."),
    A(FIFTHS, 8, "На каком ладу гитары струна делится пополам и где получается квинта?", "guitar 12th fret splits string in half 7th fret fifth",
      "when you put your finger on the 12th fret of a guitar, you're splitting that string in half", "На 12-м ладу струна делится пополам (2:1), а квинта получается на 7-м ладу."),
    A(FIFTHS, 8, "Сколько полутонов в чистой квинте?", "perfect fifth seven semitones counting up",
      "the fifth can be found by counting seven semitones up", "Семь полутонов."),
    A(FIFTHS, 14, "В каком порядке добавляются диезы при движении по кругу квинт по часовой стрелке?", "order sharps added clockwise F-C-G-D-A-E-B",
      "new sharps are added in the order F-C-G-D-A-E-B", "Фа, до, соль, ре, ля, ми, си (F-C-G-D-A-E-B)."),
    A(FIFTHS, 18, "Как быстро найти параллельный минор данной мажорной тональности по кругу квинт?", "relative minor circle of fifths move three positions clockwise",
      "simply move three positions clockwise around the to find the relative minor", "Сдвинуться на три позиции по часовой стрелке (от до мажор: соль, ре, ля: параллельный минор ля)."),
    A(FIFTHS, 19, "Какой лад получается, если сжать семь соседних нот круга квинт в гамму?", "seven adjacent notes in the circle squish to a scale Lydian mode",
      "If you squish any seven adjacent notes in the Circle down to a scale, you wind up with the Lydian mode", "Лидийский лад."),
    A(FIFTHS, 27, "Как найти септиму доминантсептаккорда с помощью круга квинт?", "dominant seventh chord find the seventh count two steps counterclockwise",
      "Simply count two steps counterclockwise from the key in which you're building the chord to give you the seventh", "Отсчитать два шага против часовой стрелки от тоники."),
    A(FIFTHS, 28, "Из каких нот состоит доминантсептаккорд от фа-диез?", "dominant seventh chord in F# notes F# A# C# E",
      "your dominant seventh chord in F# will contain the notes F#, A#, C#, and E", "Фа-диез, ля-диез, до-диез и ми."),
    A(FIFTHS, 31, "Как по кругу квинт найти IV и V ступени любой тональности?", "chord IV and V circle of fifths letter to the left right of tonic",
      "The letter to the left is IV, and the letter to the right is V", "Слева от тоники IV ступень, справа V (в до мажоре IV это фа, V это соль)."),
    A(FIFTHS, 33, "Сколько нот из семи совпадает в гаммах соседних по кругу тональностей?", "adjacent keys circle of fifths six out of seven notes in common",
      "Two keys that are adjacent to each other in the circle have six out of seven notes in their scales in common", "Шесть из семи."),
    A(FIFTHS, 23, "Сколько полутонов отделяют большую терцию от тоники в мажорном аккорде?", "major chord major third counting up four semitones",
      "The major third interval is found simply by counting up four semitones", "Четыре полутона (два целых тона)."),
    A(FIFTHS, 15, "Что такое энгармонизм и как он связан с тональностями до-диез мажор и ре-бемоль мажор?", "enharmonic equivalence C sharp major identical D flat major",
      "C♯ major will be identical to D♭ major", "Энгармоническое равенство: до-диез мажор звучит так же, как ре-бемоль мажор, а записывать его бемолями проще."),
    A(FIFTHS, 7, "С какой частотой колеблется нота ля в примере автора и какова частота ля октавой выше?", "note A vibrating at 440 Hz octave 880 Hz 2:1 ratio",
      "this note (A) is vibrating at 440 Hz", "440 Гц; октавой выше 880 Гц (отношение 2:1)."),
]

NEGATIVES = [
    {"book": POLY, "q": "Какой рецепт борща приводит автор?"},
    {"book": POLY, "q": "Сколько стоит подписка на приложение PolyNome?"},
    {"book": POLY, "q": "Какую модель барабанной установки автор советует купить новичку?"},
    {"book": POLY, "q": "В каком году была основана компания Musical U?"},
    {"book": FIFTHS, "q": "Какую гитару Fender автор рекомендует для изучения круга квинт?"},
    {"book": FIFTHS, "q": "Кто написал вальс «Амурские волны»?"},
    {"book": FIFTHS, "q": "Как автор предлагает настраивать гитару в открытом строе DADGAD?"},
    {"book": FIFTHS, "q": "Какой кофе пьёт автор, когда сочиняет песни?"},
]


def main():
    out = os.path.join(HERE, "out")
    os.makedirs(out, exist_ok=True)
    books = {b: notebook.make_sources([(b, "A")], lambda x: os.path.join(LIB, x))[0] for b in (POLY, FIFTHS)}
    bad = []
    for it in ANSWERABLE:
        v = notebook.verify_quote(books[it["book"]], it["loc"], it["quote"])
        if v["status"] != "ok":
            bad.append((it["book"][:12], it["loc"], v, it["quote"][:50]))
    if bad:
        for b in bad:
            print("GOLD QUOTE NOT ON PAGE:", b)
        sys.exit(1)
    negs = [{**n, "lang": "en", "kind": "neg_hand"} for n in NEGATIVES]
    with open(os.path.join(out, "hand.json"), "w", encoding="utf-8") as f:
        json.dump({"answerable": ANSWERABLE, "negatives": negs}, f, ensure_ascii=False, indent=1)
    print("ok:", len(ANSWERABLE), "answerable,", len(negs), "negatives")


if __name__ == "__main__":
    main()
