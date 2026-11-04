import os

import pymupdf

from .converters import PAGE_DPI

POINTS_PER_INCH = 72
SIGNATURE_KEYWORDS = ['signature', 'semnatura', 'semnătura']
MIN_LINE_UNDERSCORES = 3


def pdf_path_of(document):
    if document.kind == 'pdf':
        return document.file.path
    if document.kind == 'word':
        return os.path.join(document.folder_path, 'original.pdf')
    return None


def to_page_pixels(points):
    return points * PAGE_DPI / POINTS_PER_INCH


def clean_word(text):
    text = text.lower()
    text = text.strip(':.,;()[]')
    return text


def is_signature_word(text):
    return clean_word(text) in SIGNATURE_KEYWORDS


def is_line_placeholder(text):
    return text.count('_') >= MIN_LINE_UNDERSCORES


def read_words(pdf_path):
    pages = []

    with pymupdf.open(pdf_path) as pdf:
        for page in pdf:
            words = []
            for x0, y0, x1, y1, text, block, line, number in page.get_text('words'):
                words.append({
                    'text': text,
                    'left': to_page_pixels(x0),
                    'top': to_page_pixels(y0),
                    'width': to_page_pixels(x1 - x0),
                    'height': to_page_pixels(y1 - y0),
                    'block': block,
                    'line': line,
                })

            pages.append({
                'number': page.number + 1,
                'width': to_page_pixels(page.rect.width),
                'height': to_page_pixels(page.rect.height),
                'words': words,
            })

    return pages


def words_on_same_line(words, word):
    same_line = []
    for other in words:
        if other['block'] == word['block'] and other['line'] == word['line']:
            same_line.append(other)
    return same_line


def find_signature_spots(page):
    spots = []

    for word in page['words']:
        if not is_signature_word(word['text']):
            continue

        line = words_on_same_line(page['words'], word)
        position = line.index(word)

        name_words = []
        for other in line[:position]:
            name_words.append(other['text'])

        underline = None
        for other in line[position + 1:]:
            if is_line_placeholder(other['text']):
                underline = other
                break

        spots.append({
            'page_number': page['number'],
            'keyword': word,
            'name': ' '.join(name_words),
            'underline': underline,
        })

    return spots


def find_lone_underlines(page, spots):
    used_underlines = []
    for spot in spots:
        if spot['underline'] is not None:
            used_underlines.append(spot['underline'])

    lone_underlines = []
    for word in page['words']:
        if is_line_placeholder(word['text']) and word not in used_underlines:
            lone_underlines.append({'page_number': page['number'], 'underline': word})
    return lone_underlines


def analyze_document(document):
    pdf_path = pdf_path_of(document)
    if pdf_path is None or not os.path.exists(pdf_path):
        return {'pages': [], 'spots': [], 'lone_underlines': []}

    pages = read_words(pdf_path)

    spots = []
    lone_underlines = []
    for page in pages:
        page_spots = find_signature_spots(page)
        spots.extend(page_spots)
        lone_underlines.extend(find_lone_underlines(page, page_spots))

    return {'pages': pages, 'spots': spots, 'lone_underlines': lone_underlines}
