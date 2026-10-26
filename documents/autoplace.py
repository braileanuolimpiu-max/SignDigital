import os

import pymupdf

from .converters import PAGE_DPI

POINTS_PER_INCH = 72
SIGNATURE_KEYWORDS = ['signature', 'semnatura', 'semnătura']
MIN_LINE_UNDERSCORES = 3
MAX_NAME_WORDS = 3
LINES_CHECKED_UNDER_LABEL = 4
EMPTY_SPOT_WIDTH_POINTS = 150
SIGNED_ANNOTATION_TYPES = [pymupdf.PDF_ANNOT_STAMP, pymupdf.PDF_ANNOT_INK]


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
    text = text.strip(':.,;()[]_')
    return text


def is_signature_word(text):
    return clean_word(text) in SIGNATURE_KEYWORDS


def is_line_placeholder(text):
    return text.count('_') >= MIN_LINE_UNDERSCORES


def box_of(rect):
    return {
        'left': to_page_pixels(rect.x0),
        'top': to_page_pixels(rect.y0),
        'width': to_page_pixels(rect.width),
        'height': to_page_pixels(rect.height),
    }


def boxes_overlap(first, second):
    if first['left'] + first['width'] <= second['left']:
        return False
    if second['left'] + second['width'] <= first['left']:
        return False
    if first['top'] + first['height'] <= second['top']:
        return False
    if second['top'] + second['height'] <= first['top']:
        return False
    return True


def read_signed_areas(page):
    signed_areas = []

    for widget in page.widgets():
        if widget.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE and widget.is_signed:
            signed_areas.append(box_of(widget.rect))

    for annotation in page.annots():
        if annotation.type[0] in SIGNED_ANNOTATION_TYPES:
            signed_areas.append(box_of(annotation.rect))

    return signed_areas


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
                'signed_areas': read_signed_areas(page),
            })

    return pages


def words_on_same_line(words, word):
    same_line = []
    for other in words:
        if other['block'] == word['block'] and other['line'] == word['line']:
            same_line.append(other)
    return same_line


def name_before(line, position):
    name_words = []
    for other in line[:position]:
        if is_signature_word(other['text']) or is_line_placeholder(other['text']):
            # Everything before belongs to another signer on the same line
            name_words = []
        else:
            name_words.append(other['text'])
    return name_words


def underline_part_of(word):
    text = word['text']
    first_underscore = text.index('_')
    skipped_width = word['width'] * first_underscore / len(text)
    return {
        'text': text[first_underscore:],
        'left': word['left'] + skipped_width,
        'top': word['top'],
        'width': word['width'] - skipped_width,
        'height': word['height'],
        'block': word['block'],
        'line': word['line'],
    }


def find_underline(line, position):
    word = line[position]
    if is_line_placeholder(word['text']):
        return underline_part_of(word)

    for other in line[position + 1:]:
        if is_line_placeholder(other['text']):
            return other
        if clean_word(other['text']) != '':
            return None
    return None


def is_followed_by_text(line, position):
    word = line[position]
    if is_line_placeholder(word['text']):
        return False

    for other in line[position + 1:]:
        if is_line_placeholder(other['text']) or is_signature_word(other['text']):
            return False
        if clean_word(other['text']) != '':
            return True
    return False


def signature_box(keyword, underline):
    if underline is not None:
        return {
            'left': underline['left'],
            'top': underline['top'],
            'width': underline['width'],
            'height': underline['height'],
        }

    gap = keyword['height'] / 2
    return {
        'left': keyword['left'] + keyword['width'] + gap,
        'top': keyword['top'],
        'width': to_page_pixels(EMPTY_SPOT_WIDTH_POINTS),
        'height': keyword['height'],
    }


def area_under_label(keyword, box):
    right = box['left'] + box['width']
    return {
        'left': keyword['left'],
        'top': keyword['top'],
        'width': right - keyword['left'],
        'height': keyword['height'] * LINES_CHECKED_UNDER_LABEL,
    }


def is_already_signed(page, keyword, box):
    under_label = area_under_label(keyword, box)
    for area in page['signed_areas']:
        if boxes_overlap(area, under_label):
            return True
    return False


def find_signature_spots(page):
    spots = []

    for word in page['words']:
        if not is_signature_word(word['text']):
            continue

        line = words_on_same_line(page['words'], word)
        position = line.index(word)

        # "without signature and stamp" or "Signature: 0688..." are not places to sign
        if is_followed_by_text(line, position):
            continue

        name_words = name_before(line, position)
        if len(name_words) > MAX_NAME_WORDS:
            continue

        underline = find_underline(line, position)
        box = signature_box(word, underline)
        if is_already_signed(page, word, box):
            continue

        spots.append({
            'page_number': page['number'],
            'keyword': word,
            'name': ' '.join(name_words),
            'underline': underline,
            'box': box,
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
            if not is_signature_word(word['text']):
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
