import os
import shutil
import subprocess

from django.conf import settings
from pdf2image import convert_from_path, pdfinfo_from_path
from pdf2image.exceptions import PDFPageCountError, PDFSyntaxError
from PIL import Image, ImageOps

from .models import Page

PAGE_DPI = 150
MAX_IMAGE_SIDE = 2500
WORD_TIMEOUT_SECONDS = 120


class ConversionError(Exception):
    pass


def word_to_pdf(word_path, output_dir):
    if not os.path.exists(settings.LIBREOFFICE_PATH):
        raise ConversionError('Word files are not supported on this server.')

    profile_dir = os.path.join(output_dir, 'libreoffice_profile')
    profile_url = 'file:///' + profile_dir.replace('\\', '/')

    command = [
        settings.LIBREOFFICE_PATH,
        '-env:UserInstallation=' + profile_url,
        '--headless',
        '--convert-to', 'pdf',
        '--outdir', output_dir,
        word_path,
    ]

    try:
        subprocess.run(command, capture_output=True, timeout=WORD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        shutil.rmtree(profile_dir, ignore_errors=True)
        raise ConversionError('The Word file took too long to convert.')

    shutil.rmtree(profile_dir, ignore_errors=True)

    pdf_path = os.path.join(output_dir, 'original.pdf')
    if not os.path.exists(pdf_path):
        raise ConversionError('The Word file could not be converted.')
    return pdf_path


def pdf_to_pages(pdf_path, pages_dir):
    try:
        info = pdfinfo_from_path(pdf_path, poppler_path=settings.POPPLER_PATH)
    except (PDFPageCountError, PDFSyntaxError):
        raise ConversionError('The PDF is damaged or password protected.')

    page_count = info['Pages']
    if page_count > settings.MAX_PAGES:
        raise ConversionError(f'The document has {page_count} pages, the limit is {settings.MAX_PAGES}.')

    image_paths = convert_from_path(
        pdf_path,
        dpi=PAGE_DPI,
        fmt='jpeg',
        output_folder=pages_dir,
        output_file='page',
        paths_only=True,
        poppler_path=settings.POPPLER_PATH,
    )
    return image_paths


def image_to_pages(image_path, pages_dir):
    page_path = os.path.join(pages_dir, 'page-1.jpg')

    try:
        original = Image.open(image_path)
        image = ImageOps.exif_transpose(original)
        image = image.convert('RGBA')
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))

        page = Image.new('RGB', image.size, 'white')
        page.paste(image, mask=image)
        page.save(page_path, 'JPEG', quality=90)
        original.close()
    except OSError:
        raise ConversionError('The image is damaged.')

    return [page_path]


def build_pages(document):
    pages_dir = os.path.join(document.folder_path, 'pages')
    os.makedirs(pages_dir, exist_ok=True)

    if document.kind == 'word':
        pdf_path = word_to_pdf(document.file.path, document.folder_path)
        image_paths = pdf_to_pages(pdf_path, pages_dir)
    elif document.kind == 'pdf':
        image_paths = pdf_to_pages(document.file.path, pages_dir)
    else:
        image_paths = image_to_pages(document.file.path, pages_dir)

    number = 1
    for image_path in image_paths:
        image = Image.open(image_path)
        width = image.width
        height = image.height
        image.close()

        relative_path = os.path.relpath(image_path, settings.MEDIA_ROOT)
        relative_path = relative_path.replace('\\', '/')

        Page.objects.create(
            document=document,
            number=number,
            image=relative_path,
            width=width,
            height=height,
        )
        number = number + 1
