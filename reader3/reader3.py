"""
Parses an EPUB file into a structured object that can be used to serve the book via a web interface.

Usage:
    uv run reader3.py book.epub            # adds the book to ./library
    uv run reader3.py book.epub --out DIR  # custom library directory
"""

import os
import re
import pickle
import shutil
import posixpath
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime
from urllib.parse import unquote

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, Comment

LIBRARY_DIR = os.environ.get("READER3_LIBRARY", "library")
FORMAT_VERSION = "3.1"

# --- Data structures ---

@dataclass
class ChapterContent:
    """
    Represents a physical file in the EPUB (Spine Item).
    A single file might contain multiple logical chapters (TOC entries).
    """
    id: str           # Internal ID (e.g., 'item_1')
    href: str         # Filename (e.g., 'part01.html')
    title: str        # Title from the TOC, first heading, or a fallback
    content: str      # Cleaned HTML with rewritten image paths and internal links
    text: str         # Plain text for search/LLM context
    order: int        # Linear reading order
    words: int = 0    # Word count, used for progress and time estimates


@dataclass
class TOCEntry:
    """Represents a logical entry in the navigation sidebar."""
    title: str
    href: str         # original href (e.g., 'part01.html#chapter1')
    file_href: str    # just the filename (e.g., 'part01.html')
    anchor: str       # just the anchor (e.g., 'chapter1'), empty if none
    children: List['TOCEntry'] = field(default_factory=list)


@dataclass
class BookMetadata:
    """Metadata"""
    title: str
    language: str
    authors: List[str] = field(default_factory=list)
    description: Optional[str] = None
    publisher: Optional[str] = None
    date: Optional[str] = None
    identifiers: List[str] = field(default_factory=list)
    subjects: List[str] = field(default_factory=list)


@dataclass
class Book:
    """The Master Object to be pickled."""
    metadata: BookMetadata
    spine: List[ChapterContent]  # The actual content (linear files)
    toc: List[TOCEntry]          # The navigation tree
    images: Dict[str, str]       # Map: original_path -> local_path

    # Meta info
    source_file: str
    processed_at: str
    version: str = FORMAT_VERSION
    cover: Optional[str] = None  # Relative path of the cover image, e.g. 'images/cover.jpg'


# --- Utilities ---

DROP_TAGS = ['script', 'style', 'iframe', 'video', 'audio', 'nav', 'form', 'button',
             'input', 'select', 'textarea', 'link', 'meta', 'object', 'embed', 'base']


def clean_html_content(soup: BeautifulSoup) -> BeautifulSoup:

    # Remove dangerous/useless tags
    for tag in soup(DROP_TAGS):
        tag.decompose()

    # Remove HTML comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    # Drop event handlers, javascript: urls and inline styles (they fight the reader themes)
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            value = tag.attrs[attr]
            if attr.startswith('on') or attr == 'style':
                del tag.attrs[attr]
            elif isinstance(value, str) and value.strip().lower().startswith('javascript:'):
                del tag.attrs[attr]

    return soup


def extract_plain_text(soup: BeautifulSoup) -> str:
    """Extract clean text for LLM/Search usage."""
    text = soup.get_text(separator=' ')
    # Collapse whitespace
    return ' '.join(text.split())


def split_href(href: str):
    href = unquote(href or '')
    if '#' in href:
        file_part, anchor = href.split('#', 1)
        return file_part, anchor
    return href, ""


def parse_toc_recursive(toc_list, depth=0) -> List[TOCEntry]:
    """
    Recursively parses the TOC structure from ebooklib.
    """
    result = []

    for item in toc_list:
        # ebooklib TOC items are either `Link` objects or tuples (Section, [Children])
        if isinstance(item, tuple):
            section, children = item
            file_href, anchor = split_href(section.href)
            entry = TOCEntry(
                title=section.title,
                href=section.href or "",
                file_href=file_href,
                anchor=anchor,
                children=parse_toc_recursive(children, depth + 1)
            )
            result.append(entry)
        elif isinstance(item, (epub.Link, epub.Section)):
            # Note: ebooklib sometimes returns direct Section objects without children
            file_href, anchor = split_href(item.href)
            result.append(TOCEntry(title=item.title, href=item.href or "", file_href=file_href, anchor=anchor))

    return result


def flatten_toc(entries: List[TOCEntry]) -> List[TOCEntry]:
    out = []
    for e in entries:
        out.append(e)
        out.extend(flatten_toc(e.children))
    return out


def extract_metadata_robust(book_obj) -> BookMetadata:
    """
    Extracts metadata handling both single and list values.
    """
    def get_list(key):
        data = book_obj.get_metadata('DC', key)
        return [x[0] for x in data if x and x[0]] if data else []

    def get_one(key):
        data = book_obj.get_metadata('DC', key)
        return data[0][0] if data else None

    description = get_one('description')
    if description:
        description = BeautifulSoup(description, 'html.parser').get_text(' ').strip()

    return BookMetadata(
        title=get_one('title') or "Untitled",
        language=get_one('language') or "en",
        authors=get_list('creator'),
        description=description,
        publisher=get_one('publisher'),
        date=get_one('date'),
        identifiers=get_list('identifier'),
        subjects=get_list('subject')
    )


def find_cover_item(book_obj):
    """Best effort cover lookup: EPUB3 cover-image, EPUB2 <meta name="cover">, then a filename guess."""
    for item in book_obj.get_items():
        if item.get_type() == ebooklib.ITEM_COVER:
            return item
        if 'cover-image' in (getattr(item, 'properties', None) or []):
            return item

    for _, attrs in book_obj.get_metadata('OPF', 'cover') or []:
        cover_id = (attrs or {}).get('content')
        if cover_id:
            item = book_obj.get_item_with_id(cover_id)
            if item is not None and item.get_type() in (ebooklib.ITEM_IMAGE, ebooklib.ITEM_COVER):
                return item

    for item in book_obj.get_items_of_type(ebooklib.ITEM_IMAGE):
        if 'cover' in item.get_name().lower():
            return item
    return None


def is_nav_document(item) -> bool:
    return isinstance(item, epub.EpubNav) or 'nav' in (getattr(item, 'properties', None) or [])


# --- Main Conversion Logic ---

def process_epub(epub_path: str, output_dir: str) -> Book:

    # 1. Load Book
    print(f"Loading {epub_path}...")
    book = epub.read_epub(epub_path, options={'ignore_ncx': False})

    # 2. Extract Metadata
    metadata = extract_metadata_robust(book)

    # 3. Prepare Output Directories
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    images_dir = os.path.join(output_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)

    # 4. Extract Images & Build Map
    print("Extracting images...")
    image_map = {}  # Key: internal_path, Value: local_relative_path
    used_names = set()

    for item in book.get_items():
        if item.get_type() in (ebooklib.ITEM_IMAGE, ebooklib.ITEM_COVER):
            # Normalize filename
            original_fname = os.path.basename(item.get_name())
            # Sanitize filename for OS
            safe_fname = "".join([c for c in original_fname if c.isalnum() or c in '._-']).strip() or "image"
            base, ext = os.path.splitext(safe_fname)
            n = 1
            while safe_fname in used_names:
                n += 1
                safe_fname = f"{base}_{n}{ext}"
            used_names.add(safe_fname)

            # Save to disk
            with open(os.path.join(images_dir, safe_fname), 'wb') as f:
                f.write(item.get_content())

            # Map keys: We try both the full internal path and just the basename
            # to be robust against messy HTML src attributes
            rel_path = f"images/{safe_fname}"
            image_map[item.get_name()] = rel_path
            image_map.setdefault(original_fname, rel_path)

    cover_item = find_cover_item(book)
    cover = image_map.get(cover_item.get_name()) if cover_item else None

    # 5. Process TOC
    print("Parsing Table of Contents...")
    toc_structure = parse_toc_recursive(book.toc)

    # 6. Process Content (Spine-based to preserve HTML validity)
    print("Processing chapters...")
    parsed = []  # (item, soup)

    # We iterate over the spine (linear reading order)
    for spine_item in book.spine:
        item_id = spine_item[0] if isinstance(spine_item, tuple) else spine_item
        item = book.get_item_with_id(item_id)
        if not item or item.get_type() != ebooklib.ITEM_DOCUMENT or is_nav_document(item):
            continue

        raw_content = item.get_content().decode('utf-8', errors='ignore')
        soup = BeautifulSoup(raw_content, 'html.parser')
        base_dir = posixpath.dirname(item.get_name())

        # A. Fix Images (<img src> and SVG <image href>)
        for img in soup.find_all(['img', 'image']):
            for attr in ('src', 'href', 'xlink:href'):
                src = img.get(attr)
                if not src:
                    continue
                # Decode URL (part01/image%201.jpg -> part01/image 1.jpg)
                src_decoded = unquote(src)
                full = posixpath.normpath(posixpath.join(base_dir, src_decoded))
                filename = os.path.basename(src_decoded)
                local = image_map.get(full) or image_map.get(src_decoded) or image_map.get(filename)
                if local:
                    img[attr] = local
            if img.name == 'img':
                img['loading'] = 'lazy'

        # B. Clean HTML
        soup = clean_html_content(soup)
        text = extract_plain_text(soup)

        # Skip documents that have neither text nor pictures (blank pages, empty wrappers)
        if not text and not soup.find(['img', 'image', 'svg']):
            continue
        parsed.append((item, soup, text))

    # Map every document path to its index in our (filtered) spine
    href_to_index = {item.get_name(): i for i, (item, _, _) in enumerate(parsed)}

    # Titles: first TOC entry pointing to the file, otherwise the first heading
    toc_titles = {}
    for entry in flatten_toc(toc_structure):
        toc_titles.setdefault(entry.file_href, entry.title)

    spine_chapters = []
    for i, (item, soup, text) in enumerate(parsed):
        base_dir = posixpath.dirname(item.get_name())

        # C. Rewrite internal links so the reader can navigate between chapters
        for a in soup.find_all('a', href=True):
            href = a['href']
            if re.match(r'^[a-z][a-z0-9+.-]*:', href, re.I):
                a['target'] = '_blank'
                a['rel'] = 'noopener noreferrer'
                continue
            file_part, anchor = split_href(href)
            target = i if not file_part else href_to_index.get(
                posixpath.normpath(posixpath.join(base_dir, file_part)))
            if target is None:
                del a['href']
                continue
            a['href'] = '#'
            a['data-ch'] = str(target)
            if anchor:
                a['data-anchor'] = anchor

        title = toc_titles.get(item.get_name())
        if not title:
            heading = soup.find(['h1', 'h2', 'h3'])
            title = ' '.join(heading.get_text(' ').split()) if heading else ''
        title = title or f"Section {i + 1}"

        # D. Extract Body Content only
        body = soup.find('body')
        final_html = "".join(str(x) for x in body.contents) if body else str(soup)

        spine_chapters.append(ChapterContent(
            id=item.get_id(),
            href=item.get_name(),  # Important: This links TOC to Content
            title=title,
            content=final_html,
            text=text,
            order=i,
            words=len(text.split()),
        ))

    if not toc_structure:
        print("Warning: Empty TOC, building fallback from Spine...")
        toc_structure = [TOCEntry(title=c.title, href=c.href, file_href=c.href, anchor="") for c in spine_chapters]

    # 7. Final Assembly
    return Book(
        metadata=metadata,
        spine=spine_chapters,
        toc=toc_structure,
        images=image_map,
        source_file=os.path.basename(epub_path),
        processed_at=datetime.now().isoformat(),
        cover=cover,
    )


def save_to_pickle(book: Book, output_dir: str):
    p_path = os.path.join(output_dir, 'book.pkl')
    with open(p_path, 'wb') as f:
        pickle.dump(book, f)
    print(f"Saved structured data to {p_path}")


def slugify(name: str) -> str:
    slug = re.sub(r'[^\w.-]+', '_', name, flags=re.UNICODE).strip('._')
    return slug[:80] or "book"


def import_epub(epub_file: str, library_dir: str = LIBRARY_DIR, book_id: Optional[str] = None) -> (str, Book):
    """Processes an EPUB into `<library_dir>/<book_id>` and keeps a copy of the original file."""
    os.makedirs(library_dir, exist_ok=True)
    if book_id is None:
        book_id = slugify(os.path.splitext(os.path.basename(epub_file))[0]) + "_data"
    out_dir = os.path.join(library_dir, book_id)

    book_obj = process_epub(epub_file, out_dir)
    save_to_pickle(book_obj, out_dir)
    if os.path.abspath(epub_file) != os.path.abspath(os.path.join(out_dir, 'book.epub')):
        shutil.copyfile(epub_file, os.path.join(out_dir, 'book.epub'))
    return book_id, book_obj


# --- CLI ---

if __name__ == "__main__":
    import argparse
    # Pickle through the importable module, not __main__, so the server can load the books
    import reader3 as _module
    import_epub = _module.import_epub

    parser = argparse.ArgumentParser(description="Add EPUB books to the reader3 library.")
    parser.add_argument("epub", nargs="+", help="EPUB file(s)")
    parser.add_argument("--out", default=LIBRARY_DIR, help=f"library directory (default: {LIBRARY_DIR})")
    args = parser.parse_args()

    for epub_file in args.epub:
        if not os.path.exists(epub_file):
            parser.error(f"File not found: {epub_file}")
        book_id, book_obj = import_epub(epub_file, args.out)
        print("\n--- Summary ---")
        print(f"ID: {book_id}")
        print(f"Title: {book_obj.metadata.title}")
        print(f"Authors: {', '.join(book_obj.metadata.authors)}")
        print(f"Chapters: {len(book_obj.spine)}")
        print(f"TOC Root Items: {len(book_obj.toc)}")
        print(f"Images extracted: {len(set(book_obj.images.values()))}")
        print(f"Cover: {book_obj.cover or '-'}\n")
