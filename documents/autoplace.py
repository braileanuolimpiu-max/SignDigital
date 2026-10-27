import os

import pymupdf

from .converters import PAGE_DPI

POINTS_PER_INCH = 72
SIGNATURE_KEYWORDS = ['signature', 'semnatura', 'semnătura', 'semnatură', 'semnătură', 'подпись']
TWO_WORD_LABELS = ['signed by', 'sign here', 'semnatura olografa', 'semnătura olografă', 'semnătură olografă']
# Words of a form field, never part of a name or a role
FIELD_WORDS = ['data', 'date', 'nr', 'no', 'loc', 'place', 'stampila', 'ștampila', 'stamp']
MIN_LINE_UNDERSCORES = 3
MAX_NAME_WORDS = 3
LINES_CHECKED_UNDER_LABEL = 4
LINE_HEIGHTS_CHECKED_ABOVE_LABEL = 6
NAME_INTRODUCERS = ['prin', 'by', 'name', 'nume', 'numele']
NAME_ENDINGS = ['–', '-', '—']
COMPANY_TYPES = ['srl', 's.r.l', 'sa', 's.a', 'sc', 'ii', 'llc', 'ltd', 'inc', 'gmbh']
COMPANY_INTRODUCERS = ['for', 'pentru']
QUOTES = '"\'“”„«»'
NAME_PUNCTUATION = ':,;'
EMPTY_SPOT_WIDTH_POINTS = 150
MIN_EMPTY_SPOT_WIDTH_POINTS = 60
PAGE_MARGIN_POINTS = 36
MAX_SPACE_ABOVE_POINTS = 60
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
    word = clean_word(text)
    return word in SIGNATURE_KEYWORDS or word in TWO_WORD_LABELS


def clean_name(texts):
    # "Ion Pop:" or "Ana Rusu," becomes "Ion Pop" and "Ana Rusu"
    name_words = []
    for text in texts:
        text = text.strip(NAME_PUNCTUATION)
        if text != '':
            name_words.append(text)
    return ' '.join(name_words)


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


def join_words(first, second):
    right = second['left'] + second['width']
    return {
        'text': first['text'] + ' ' + second['text'],
        'left': first['left'],
        'top': first['top'],
        'width': right - first['left'],
        'height': first['height'],
        'block': first['block'],
        'line': first['line'],
    }


def join_two_word_labels(line):
    # "Signed by" or "Semnătura olografă" are two words but one label
    joined = []
    position = 0
    while position < len(line):
        word = line[position]
        if position + 1 < len(line):
            next_word = line[position + 1]
            two_words = clean_word(word['text']) + ' ' + clean_word(next_word['text'])
            if two_words in TWO_WORD_LABELS:
                joined.append(join_words(word, next_word))
                position = position + 2
                continue
        joined.append(word)
        position = position + 1
    return joined


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
    # "Semnatura____" or "____/Ion" are one word, keep only the underscores
    text = word['text']
    if text.strip('_') == '':
        return word

    first_underscore = text.index('_')
    last_underscore = text.rindex('_')
    letter_width = word['width'] / len(text)
    return {
        'text': text[first_underscore:last_underscore + 1],
        'left': word['left'] + letter_width * first_underscore,
        'top': word['top'],
        'width': letter_width * (last_underscore + 1 - first_underscore),
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
            return underline_part_of(other)
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


def right_limit_of(line, position, page_width):
    for other in line[position + 1:]:
        if is_signature_word(other['text']):
            return other['left'] - other['height'] / 2
    return page_width - to_page_pixels(PAGE_MARGIN_POINTS)


def signature_box(keyword, underline, right_limit):
    if underline is not None:
        return {
            'left': underline['left'],
            'top': underline['top'],
            'width': underline['width'],
            'height': underline['height'],
        }

    gap = keyword['height'] / 2
    left = keyword['left'] + keyword['width'] + gap
    width = to_page_pixels(EMPTY_SPOT_WIDTH_POINTS)
    if left + width > right_limit:
        width = right_limit - left

    if width < to_page_pixels(MIN_EMPTY_SPOT_WIDTH_POINTS):
        return box_under_label(keyword, right_limit)

    return {
        'left': left,
        'top': keyword['top'],
        'width': width,
        'height': keyword['height'],
    }


def box_under_label(keyword, right_limit):
    # No room after the label (page edge or the next label), so sign on the line under it
    width = to_page_pixels(EMPTY_SPOT_WIDTH_POINTS)
    if keyword['left'] + width > right_limit:
        width = right_limit - keyword['left']
    if width < keyword['width']:
        width = keyword['width']

    return {
        'left': keyword['left'],
        'top': keyword['top'] + keyword['height'] * 2,
        'width': width,
        'height': keyword['height'],
    }


def space_above(page, box):
    # How tall the signature can be: from the line up to the closest text above it
    line_bottom = box['top'] + box['height']
    line_middle = box['top'] + box['height'] / 2
    space = to_page_pixels(MAX_SPACE_ABOVE_POINTS)

    for word in page['words']:
        word_bottom = word['top'] + word['height']
        if word_bottom > line_middle:
            continue
        if word['left'] + word['width'] <= box['left'] or word['left'] >= box['left'] + box['width']:
            continue
        if line_bottom - word_bottom < space:
            space = line_bottom - word_bottom

    return space


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


def column_of(line, position, page_width):
    keyword = line[position]
    start = 0
    end = page_width

    for other in line[:position]:
        if is_signature_word(other['text']):
            start = (other['left'] + keyword['left']) / 2
    for other in line[position + 1:]:
        if is_signature_word(other['text']):
            end = (keyword['left'] + other['left']) / 2
            break

    return start, end


def lines_above(page, keyword, column_start, column_end):
    highest_top = keyword['top'] - keyword['height'] * LINE_HEIGHTS_CHECKED_ABOVE_LABEL

    lines = {}
    for word in page['words']:
        if word['top'] >= keyword['top'] or word['top'] < highest_top:
            continue
        if word['left'] < column_start or word['left'] >= column_end:
            continue
        key = (word['block'], word['line'])
        if key not in lines:
            lines[key] = []
        lines[key].append(word)

    # The lowest line is the closest to the label, so it goes first
    above = list(lines.values())
    above.sort(key=top_of_line, reverse=True)
    return above


def top_of_line(words):
    return words[0]['top']


def looks_like_name(texts, min_words):
    if len(texts) < min_words or len(texts) > MAX_NAME_WORDS:
        return False

    for text in texts:
        if clean_word(text) in COMPANY_TYPES or clean_word(text) in COMPANY_INTRODUCERS:
            return False
        if clean_word(text) in FIELD_WORDS:
            return False
        letters = clean_word(text).replace('-', '')
        if not letters.isalpha():
            return False
        if not text[0].isupper():
            return False
    return True


def texts_before_dash(words):
    texts = []
    for word in words:
        if word['text'] in NAME_ENDINGS:
            break
        texts.append(word['text'])
    return texts


def person_in_line(words):
    texts = texts_before_dash(words)

    # "SUGAR SOFT SRL prin Rodislav Zaharadji" or "Name: Ana Pop"
    for index in range(len(texts)):
        if clean_word(texts[index]) in NAME_INTRODUCERS:
            after = texts[index + 1:]
            if looks_like_name(after, 1):
                return clean_name(after)

    # A line that is only a name, like "Corasevici Mihail"
    if looks_like_name(texts, 2):
        return clean_name(texts)
    return ''


def company_in_line(words):
    # 'SRL ” Sugar Software”' becomes "Sugar Software"
    is_company = False
    company_words = []

    for text in texts_before_dash(words):
        if clean_word(text) in NAME_INTRODUCERS:
            break
        if clean_word(text) in COMPANY_TYPES:
            is_company = True
            continue
        if clean_word(text) in COMPANY_INTRODUCERS:
            continue

        text = text.strip(QUOTES)
        if text == '':
            continue
        if not clean_word(text).replace('-', '').isalpha():
            return ''
        company_words.append(text)

    if not is_company or len(company_words) == 0:
        return ''
    return ' '.join(company_words)


def role_in_line(words):
    # A role alone on its line, like "ANGAJATORUL" or "Prestator"
    if len(words) != 1:
        return ''
    text = words[0]['text']
    # Another "Semnatura" label or a lowercase word is not a role
    if is_signature_word(text) or not text[0].isupper():
        return ''
    role = clean_word(text)
    if not role.isalpha() or role in FIELD_WORDS:
        return ''
    return role.capitalize()


def name_above(page, line, position):
    column_start, column_end = column_of(line, position, page['width'])
    above = lines_above(page, line[position], column_start, column_end)

    for words in above:
        name = person_in_line(words)
        if name != '':
            return name

    for words in above:
        name = company_in_line(words)
        if name != '':
            return name

    for words in above:
        name = role_in_line(words)
        if name != '':
            return name

    return ''


def name_after_slash(line, position):
    # "Semnatura ________ /Ion Pop/" has the name after the line
    texts = []
    for other in line[position + 1:]:
        if is_signature_word(other['text']):
            break
        texts.append(other['text'])

    rest = ' '.join(texts)
    if '/' not in rest:
        return ''

    name_texts = rest.split('/')[1].split()
    if looks_like_name(name_texts, 1):
        return clean_name(name_texts)
    return ''


def build_spot(page, line, position, name):
    keyword = line[position]
    underline = find_underline(line, position)
    right_limit = right_limit_of(line, position, page['width'])
    box = signature_box(keyword, underline, right_limit)
    if is_already_signed(page, keyword, box):
        return None
    box['space_above'] = space_above(page, box)

    return {
        'page_number': page['number'],
        'keyword': keyword,
        'name': name,
        'underline': underline,
        'box': box,
    }


def lines_of(page):
    lines = {}
    for word in page['words']:
        key = (word['block'], word['line'])
        if key not in lines:
            lines[key] = []
        lines[key].append(word)
    return list(lines.values())


def name_label_position(line):
    # "Braileanu Olimpiu:" with nothing after it is a place for that person to sign
    for position in range(len(line)):
        text = line[position]['text']
        if is_signature_word(text):
            return None
        if text.endswith(':'):
            texts = []
            for word in line[:position + 1]:
                texts.append(word['text'])
            if not looks_like_name(texts, 2):
                return None
            if is_followed_by_text(line, position):
                return None
            return position
    return None


def find_signature_spots(page):
    spots = []

    for line in lines_of(page):
        line = join_two_word_labels(line)

        for position in range(len(line)):
            if not is_signature_word(line[position]['text']):
                continue

            # "without signature and stamp" or "Signature: 0688..." are not places to sign
            if is_followed_by_text(line, position):
                continue

            name_words = name_before(line, position)
            if len(name_words) > MAX_NAME_WORDS:
                continue

            # "1. Signature" has words before it, but they are not a name
            name = ''
            if looks_like_name(name_words, 1):
                name = clean_name(name_words)
            if name == '':
                name = name_after_slash(line, position)
            if name == '':
                name = name_above(page, line, position)

            spot = build_spot(page, line, position, name)
            if spot is not None:
                spots.append(spot)

    return spots


def find_name_label_spots(page, signer_names):
    # Only names that already sign somewhere else, so "Invoice No:" is not a spot
    spots = []

    for line in lines_of(page):
        position = name_label_position(line)
        if position is None:
            continue

        texts = []
        for word in line[:position + 1]:
            texts.append(word['text'])
        name = clean_name(texts)
        if name not in signer_names:
            continue

        spot = build_spot(page, line, position, name)
        if spot is not None:
            spots.append(spot)

    return spots


def is_underline_only(line):
    for word in line:
        if word['text'].strip('_') != '':
            return False
    return True


def is_writing_space(line, underline_lines):
    # Several "______" lines one under the other are space to write, not to sign
    first = line[0]
    last = line[-1]
    for other in underline_lines:
        if other is line:
            continue
        other_first = other[0]
        other_last = other[-1]
        if abs(other_first['top'] - first['top']) < first['height'] / 2:
            continue
        if abs(other_first['top'] - first['top']) > first['height'] * 3:
            continue
        if other_first['left'] >= last['left'] + last['width']:
            continue
        if first['left'] >= other_last['left'] + other_last['width']:
            continue
        return True
    return False


def find_lone_underlines(page):
    # A "______" line with no label, often a place to sign with the name under it
    underline_lines = []
    for line in lines_of(page):
        if is_underline_only(line):
            underline_lines.append(line)

    spots = []
    for line in underline_lines:
        if is_writing_space(line, underline_lines):
            continue
        for position in range(len(line)):
            if not is_line_placeholder(line[position]['text']):
                continue
            spot = build_spot(page, line, position, '')
            if spot is not None:
                spots.append(spot)
    return spots


def analyze_document(document):
    pdf_path = pdf_path_of(document)
    if pdf_path is None or not os.path.exists(pdf_path):
        return {'spots': [], 'lone_underlines': []}

    pages = read_words(pdf_path)

    spots = []
    for page in pages:
        spots.extend(find_signature_spots(page))

    signer_names = []
    for spot in spots:
        if spot['name'] != '':
            signer_names.append(spot['name'])
    for page in pages:
        spots.extend(find_name_label_spots(page, signer_names))

    lone_underlines = []
    for page in pages:
        lone_underlines.extend(find_lone_underlines(page))

    # Only what the editor needs, not every word of every page
    return {'spots': spots, 'lone_underlines': lone_underlines}
