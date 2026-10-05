"""Builds a sample EPUB (cover, nested TOC, footnotes, images) for manual testing."""
import random, struct, zlib, sys
from ebooklib import epub

def png(w, h, top, bottom):
    rows = b''
    for y in range(h):
        t = y / (h - 1)
        px = bytes(int(a + (b - a) * t) for a, b in zip(top, bottom))
        rows += b'\x00' + px * w
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))

random.seed(3)
WORDS = ("море ветер корабль капитан берег звезда ночь утро парус волна остров карта тайна путь дом письмо "
         "старик город огонь дорога память голос свет тень время небо песок сердце надежда страх буря").split()

def para(n):
    s = ' '.join(random.choice(WORDS) for _ in range(n))
    return s[0].upper() + s[1:] + '.'

book = epub.EpubBook()
book.set_identifier('sample-sea-1')
book.set_title('Хроники Северного моря')
book.set_language('ru')
book.add_author('Анна Тестова')
book.add_metadata('DC', 'description', 'Тестовая книга для проверки читалки: обложка, вложенное оглавление, сноски и иллюстрации.')
book.add_metadata('DC', 'publisher', 'reader3 samples')
book.add_metadata('DC', 'subject', 'Приключения')
book.set_cover('images/cover.png', png(400, 600, (30, 60, 110), (200, 110, 60)))
book.add_item(epub.EpubItem(uid='map', file_name='images/map.png', media_type='image/png',
                            content=png(600, 300, (220, 200, 160), (120, 150, 170))))

chapters = []
titles = ['Пролог', 'Глава 1. Письмо', 'Глава 2. Остров', 'Глава 3. Буря', 'Глава 4. Карта', 'Эпилог']
for i, title in enumerate(titles):
    c = epub.EpubHtml(title=title, file_name=f'text/ch{i}.xhtml', lang='ru')
    body = f'<h1 id="top">{title}</h1>'
    for j in range(24 if 0 < i < 5 else 6):
        if j == 3 and i == 1:
            body += f'<p>{para(30)} Сноска здесь<sup><a href="notes.xhtml#n1" id="r1">1</a></sup>.</p>'
        elif j == 8 and i == 2:
            body += '<figure><img src="../images/map.png" alt="Карта"/><figcaption>Карта острова</figcaption></figure>'
        elif j == 12 and 0 < i < 5:
            body += f'<h2 id="part{i}">Часть вторая</h2>'
        elif j == 5 and i == 3:
            body += f'<blockquote><p>{para(20)}</p></blockquote>'
        else:
            body += f'<p>{para(random.randint(40, 90))}</p>'
    c.content = body
    book.add_item(c)
    chapters.append(c)

notes = epub.EpubHtml(title='Примечания', file_name='text/notes.xhtml', lang='ru')
notes.content = '<h1>Примечания</h1><p id="n1"><a href="ch1.xhtml#r1">1</a> Пример сноски со ссылкой обратно.</p>'
book.add_item(notes)

book.toc = [epub.Link('text/ch0.xhtml', 'Пролог', 'p')] + [
    (epub.Section(titles[i], f'text/ch{i}.xhtml'),
     [epub.Link(f'text/ch{i}.xhtml#part{i}', 'Часть вторая', f'p{i}')]) for i in range(1, 5)
] + [epub.Link('text/ch5.xhtml', 'Эпилог', 'e'), epub.Link('text/notes.xhtml', 'Примечания', 'n')]
book.add_item(epub.EpubNcx())
book.add_item(epub.EpubNav())
book.spine = ['nav'] + chapters + [notes]
epub.write_epub(sys.argv[1] if len(sys.argv) > 1 else 'sample.epub', book)
