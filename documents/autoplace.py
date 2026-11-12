import os

import pymupdf

from .converters import PAGE_DPI

POINTS_PER_INCH = 72
AUTOPLACE_KINDS = ['pdf', 'word']
SIGNATURE_KEYWORDS = ['signature', 'semnatura', 'semnătura', 'semnatură', 'semnătură', 'подпись']
TWO_WORD_LABELS = ['signed by', 'sign here', 'semnatura olografa', 'semnătura olografă', 'semnătură olografă']
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
THIN_LINE_POINTS = 2
MIN_DRAWN_LINE_POINTS = 40
MAX_LONE_LINE_POINTS = 300
MAX_LABEL_GAP_POINTS = 72
DRAWN_LINE_BOX_POINTS = 12
FRAME_GAP_POINTS = 3
FRAME_PADDING_POINTS = 3
MIN_ROOM_POINTS = 15
MAX_BOX_HEIGHT_POINTS = 200
SIGNED_ANNOTATION_TYPES = [pymupdf.PDF_ANNOT_STAMP, pymupdf.PDF_ANNOT_INK]


def can_autoplace(document):
    return document.kind in AUTOPLACE_KINDS


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


def make_segment(x0, y0, x1, y1):
    return {
        'x0': min(x0, x1),
        'y0': min(y0, y1),
        'x1': max(x0, x1),
        'y1': max(y0, y1),
    }


def segments_of(item):
    if item[0] == 'l':
        start = item[1]
        end = item[2]
        return [make_segment(start.x, start.y, end.x, end.y)]

    if item[0] == 're':
        rect = item[1]
        if rect.width <= THIN_LINE_POINTS or rect.height <= THIN_LINE_POINTS:
            return [make_segment(rect.x0, rect.y0, rect.x1, rect.y1)]
        top = make_segment(rect.x0, rect.y0, rect.x1, rect.y0)
        bottom = make_segment(rect.x0, rect.y1, rect.x1, rect.y1)
        left = make_segment(rect.x0, rect.y0, rect.x0, rect.y1)
        right = make_segment(rect.x1, rect.y0, rect.x1, rect.y1)
        return [top, bottom, left, right]

    return []


def add_to_lines(lines, segment):
    for line in lines:
        same_height = abs(line['y0'] - segment['y0']) <= 1
        touching = segment['x0'] <= line['x1'] + 1 and segment['x1'] >= line['x0'] - 1
        if same_height and touching:
            line['x0'] = min(line['x0'], segment['x0'])
            line['x1'] = max(line['x1'], segment['x1'])
            return
    lines.append(segment)


def touches_vertical(line, vertical_lines):
    for vertical in vertical_lines:
        if vertical['x0'] < line['x0'] - THIN_LINE_POINTS:
            continue
        if vertical['x0'] > line['x1'] + THIN_LINE_POINTS:
            continue
        if vertical['y0'] > line['y0'] + THIN_LINE_POINTS:
            continue
        if vertical['y1'] < line['y0'] - THIN_LINE_POINTS:
            continue
        return True
    return False


def read_lines(page):
    horizontal_lines = []
    vertical_lines = []

    for drawing in page.get_drawings():
        for item in drawing['items']:
            for segment in segments_of(item):
                if segment['y1'] - segment['y0'] <= THIN_LINE_POINTS:
                    add_to_lines(horizontal_lines, segment)
                elif segment['x1'] - segment['x0'] <= THIN_LINE_POINTS:
                    vertical_lines.append(segment)

    return {'horizontal': horizontal_lines, 'vertical': vertical_lines}


def drawn_lines_of(lines):
    drawn_lines = []
    for line in lines['horizontal']:
        if line['x1'] - line['x0'] < MIN_DRAWN_LINE_POINTS:
            continue
        if touches_vertical(line, lines['vertical']):
            continue
        drawn_lines.append({
            'left': to_page_pixels(line['x0']),
            'width': to_page_pixels(line['x1'] - line['x0']),
            'y': to_page_pixels(line['y0']),
        })
    return drawn_lines


def in_page_pixels(segments):
    converted = []
    for segment in segments:
        converted.append({
            'x0': to_page_pixels(segment['x0']),
            'y0': to_page_pixels(segment['y0']),
            'x1': to_page_pixels(segment['x1']),
            'y1': to_page_pixels(segment['y1']),
        })
    return converted


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

            lines = read_lines(page)
            pages.append({
                'number': page.number + 1,
                'width': to_page_pixels(page.rect.width),
                'height': to_page_pixels(page.rect.height),
                'words': words,
                'signed_areas': read_signed_areas(page),
                'drawn_lines': drawn_lines_of(lines),
                'horizontal_lines': in_page_pixels(lines['horizontal']),
                'vertical_lines': in_page_pixels(lines['vertical']),
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
    joined = []
    position = 0
    while position < len(line):
        word = line[position]
        two_words = ''
        if position + 1 < len(line):
            next_word = line[position + 1]
            two_words = clean_word(word['text']) + ' ' + clean_word(next_word['text'])

        if two_words in TWO_WORD_LABELS:
            joined.append(join_words(word, next_word))
            position = position + 2
        else:
            joined.append(word)
            position = position + 1
    return joined


def name_before(line, position):
    name_words = []
    for other in line[:position]:
        if is_signature_word(other['text']) or is_line_placeholder(other['text']):
            name_words = []
        else:
            name_words.append(other['text'])
    return name_words


def underline_part_of(word):
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

    for index in range(len(texts)):
        if clean_word(texts[index]) in NAME_INTRODUCERS:
            after = texts[index + 1:]
            if looks_like_name(after, 1):
                return clean_name(after)

    if looks_like_name(texts, 2):
        return clean_name(texts)
    return ''


def company_in_line(words):
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
    if len(words) != 1:
        return ''
    text = words[0]['text']
    if is_signature_word(text) or not text[0].isupper():
        return ''
    role = clean_word(text)
    if not role.isalpha() or role in FIELD_WORDS:
        return ''
    return role.capitalize()


def name_above(page, line, position):
    column_start, column_end = column_of(line, position, page['width'])

    frame = frame_around_word(page, line[position])
    if frame is not None:
        if frame['left'] > column_start:
            column_start = frame['left']
        if frame['right'] < column_end:
            column_end = frame['right']

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
    texts = []
    for other in line[position + 1:]:
        if is_signature_word(other['text']):
            break
        texts.append(other['text'])

    rest = ' '.join(texts)
    if '/' not in rest:
        return ''

    parts = rest.split('/')
    after_slash = parts[1]
    name_texts = after_slash.split()
    if looks_like_name(name_texts, 1):
        return clean_name(name_texts)
    return ''


def drawn_line_after(page, keyword, right_limit):
    keyword_right = keyword['left'] + keyword['width']
    keyword_middle = keyword['top'] + keyword['height'] / 2
    keyword_bottom = keyword['top'] + keyword['height']
    farthest_left = keyword_right + to_page_pixels(MAX_LABEL_GAP_POINTS)

    for drawn in page['drawn_lines']:
        if drawn['y'] < keyword_middle or drawn['y'] > keyword_bottom + keyword['height'] / 2:
            continue
        if drawn['left'] < keyword_right - keyword['height'] / 2:
            continue
        if drawn['left'] > farthest_left or drawn['left'] >= right_limit:
            continue

        width = drawn['width']
        if drawn['left'] + width > right_limit:
            width = right_limit - drawn['left']
        return {
            'left': drawn['left'],
            'top': drawn['y'] - keyword['height'],
            'width': width,
            'height': keyword['height'],
        }
    return None


def closest_line_left(vertical_lines, x, y):
    closest = None
    for line in vertical_lines:
        if line['y0'] <= y <= line['y1'] and line['x0'] < x:
            if closest is None or line['x0'] > closest['x0']:
                closest = line
    return closest


def closest_line_right(vertical_lines, x, y):
    closest = None
    for line in vertical_lines:
        if line['y0'] <= y <= line['y1'] and line['x0'] > x:
            if closest is None or line['x0'] < closest['x0']:
                closest = line
    return closest


def closest_line_above(horizontal_lines, x, y):
    closest = None
    for line in horizontal_lines:
        if line['x0'] <= x <= line['x1'] and line['y0'] < y:
            if closest is None or line['y0'] > closest['y0']:
                closest = line
    return closest


def closest_line_below(horizontal_lines, x, y):
    closest = None
    for line in horizontal_lines:
        if line['x0'] <= x <= line['x1'] and line['y0'] > y:
            if closest is None or line['y0'] < closest['y0']:
                closest = line
    return closest


def frame_around(page, x, y):
    left = closest_line_left(page['vertical_lines'], x, y)
    right = closest_line_right(page['vertical_lines'], x, y)
    top = closest_line_above(page['horizontal_lines'], x, y)
    bottom = closest_line_below(page['horizontal_lines'], x, y)
    if left is None or right is None or top is None or bottom is None:
        return None

    gap = to_page_pixels(FRAME_GAP_POINTS)
    for side in [left, right]:
        if side['y0'] > top['y0'] + gap or side['y1'] < bottom['y0'] - gap:
            return None
    for side in [top, bottom]:
        if side['x0'] > left['x0'] + gap or side['x1'] < right['x0'] - gap:
            return None

    return {
        'left': left['x0'],
        'right': right['x0'],
        'top': top['y0'],
        'bottom': bottom['y0'],
    }


def frame_around_word(page, word):
    middle_x = word['left'] + word['width'] / 2
    middle_y = word['top'] + word['height'] / 2
    return frame_around(page, middle_x, middle_y)


def has_words_inside(page, frame):
    for word in page['words']:
        middle_x = word['left'] + word['width'] / 2
        middle_y = word['top'] + word['height'] / 2
        if frame['left'] < middle_x < frame['right'] and frame['top'] < middle_y < frame['bottom']:
            return True
    return False


def box_in_frame(left, right, top, bottom):
    padding = to_page_pixels(FRAME_PADDING_POINTS)
    height = to_page_pixels(DRAWN_LINE_BOX_POINTS)
    return {
        'left': left + padding,
        'top': bottom - padding - height,
        'width': right - left - padding * 2,
        'height': height,
        'space_above': bottom - top - padding * 2,
    }


def empty_box_next_to(page, keyword, right_limit):
    keyword_right = keyword['left'] + keyword['width']
    keyword_middle = keyword['top'] + keyword['height'] / 2
    keyword_center = keyword['left'] + keyword['width'] / 2
    keyword_bottom = keyword['top'] + keyword['height']
    gap = to_page_pixels(FRAME_GAP_POINTS)

    points = []
    for vertical in page['vertical_lines']:
        if vertical['y0'] > keyword_middle or vertical['y1'] < keyword_middle:
            continue
        if vertical['x0'] < keyword_right - keyword['height'] / 2:
            continue
        if vertical['x0'] > keyword_right + to_page_pixels(MAX_LABEL_GAP_POINTS):
            continue
        if vertical['x0'] >= right_limit:
            continue
        points.append([vertical['x0'] + gap, keyword_middle])
    for horizontal in page['horizontal_lines']:
        if horizontal['x0'] > keyword_center or horizontal['x1'] < keyword_center:
            continue
        if horizontal['y0'] < keyword_bottom - keyword['height'] / 2:
            continue
        if horizontal['y0'] > keyword_bottom + keyword['height'] * 2:
            continue
        points.append([keyword_center, horizontal['y0'] + gap])

    for point in points:
        frame = frame_around(page, point[0], point[1])
        if frame is None or has_words_inside(page, frame):
            continue
        width = frame['right'] - frame['left']
        height = frame['bottom'] - frame['top']
        if width < to_page_pixels(MIN_EMPTY_SPOT_WIDTH_POINTS):
            continue
        if height < to_page_pixels(MIN_ROOM_POINTS) or height > to_page_pixels(MAX_BOX_HEIGHT_POINTS):
            continue
        return box_in_frame(frame['left'], frame['right'], frame['top'], frame['bottom'])
    return None


def room_under_label(page, keyword, frame):
    if frame is None:
        return None

    keyword_bottom = keyword['top'] + keyword['height']
    room_bottom = frame['bottom']
    for word in page['words']:
        if word['top'] < keyword_bottom - keyword['height'] / 2:
            continue
        if word['left'] < frame['left'] or word['left'] > frame['right']:
            continue
        if word['top'] < room_bottom:
            room_bottom = word['top']

    if room_bottom - keyword_bottom < to_page_pixels(MIN_ROOM_POINTS):
        return None

    right = keyword['left'] + to_page_pixels(EMPTY_SPOT_WIDTH_POINTS)
    if right > frame['right']:
        right = frame['right']
    return box_in_frame(keyword['left'], right, keyword_bottom, room_bottom)


def build_spot(page, line, position, name):
    keyword = line[position]
    right_limit = right_limit_of(line, position, page['width'])

    frame = frame_around_word(page, keyword)
    if frame is not None and frame['right'] < right_limit:
        right_limit = frame['right'] - to_page_pixels(FRAME_PADDING_POINTS)

    underline = find_underline(line, position)
    if underline is None:
        underline = drawn_line_after(page, keyword, right_limit)

    box = None
    if underline is None:
        box = empty_box_next_to(page, keyword, right_limit)
    if underline is None and box is None:
        box = room_under_label(page, keyword, frame)
    if box is None:
        box = signature_box(keyword, underline, right_limit)

    if is_already_signed(page, keyword, box):
        return None
    if 'space_above' not in box:
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

            if is_followed_by_text(line, position):
                continue

            name_words = name_before(line, position)
            if len(name_words) > MAX_NAME_WORDS:
                continue

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


def is_alone_on_its_row(page, drawn):
    for word in page['words']:
        word_bottom = word['top'] + word['height']
        if word['top'] < drawn['y'] and drawn['y'] < word_bottom + word['height'] / 2:
            return False
    return True


def is_drawn_writing_space(drawn, drawn_lines):
    box_height = to_page_pixels(DRAWN_LINE_BOX_POINTS)
    for other in drawn_lines:
        distance = abs(other['y'] - drawn['y'])
        if distance < 1 or distance > box_height * 3:
            continue
        if other['left'] >= drawn['left'] + drawn['width']:
            continue
        if drawn['left'] >= other['left'] + other['width']:
            continue
        return True
    return False


def find_lone_drawn_lines(page):
    spots = []
    box_height = to_page_pixels(DRAWN_LINE_BOX_POINTS)

    for drawn in page['drawn_lines']:
        if drawn['width'] > to_page_pixels(MAX_LONE_LINE_POINTS):
            continue
        if not is_alone_on_its_row(page, drawn):
            continue
        if is_drawn_writing_space(drawn, page['drawn_lines']):
            continue

        underline = {
            'left': drawn['left'],
            'top': drawn['y'] - box_height,
            'width': drawn['width'],
            'height': box_height,
        }
        box = {
            'left': drawn['left'],
            'top': drawn['y'] - box_height,
            'width': drawn['width'],
            'height': box_height,
        }
        if is_already_signed(page, underline, box):
            continue
        box['space_above'] = space_above(page, box)

        spots.append({
            'page_number': page['number'],
            'keyword': underline,
            'name': '',
            'underline': underline,
            'box': box,
        })
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
        lone_underlines.extend(find_lone_drawn_lines(page))

    return {'spots': spots, 'lone_underlines': lone_underlines}
