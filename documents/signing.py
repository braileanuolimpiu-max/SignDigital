import io
import os
from email.utils import formataddr

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.urls import reverse
import pymupdf

from .autoplace import pdf_path_of
from .converters import PAGE_DPI, image_on_white_page

SIGNATURE_FONT_FILE = os.path.join(settings.BASE_DIR, 'documents', 'fonts', 'MrDeHaviland-Regular.ttf')
SIGNATURE_FONT_NAME = 'signature'
POINTS_PER_INCH = 72


def image_to_pdf(image_path):
    image = image_on_white_page(image_path)
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=90)

    pdf = pymupdf.open()
    width = image.width * POINTS_PER_INCH / PAGE_DPI
    height = image.height * POINTS_PER_INCH / PAGE_DPI
    pdf_page = pdf.new_page(width=width, height=height)
    pdf_page.insert_image(pdf_page.rect, stream=buffer.getvalue())
    return pdf


def open_original_pdf(document):
    if document.kind == 'image':
        return image_to_pdf(document.file.path)
    return pymupdf.open(pdf_path_of(document))


def overlay_path(document, page_number):
    return os.path.join(document.folder_path, 'overlays', f'page_{page_number}.png')


def save_overlays(document, overlays):
    os.makedirs(os.path.join(document.folder_path, 'overlays'), exist_ok=True)
    for page_number in overlays:
        with open(overlay_path(document, page_number), 'wb') as file:
            file.write(overlays[page_number])


def read_saved_overlays(document):
    overlays = {}
    for page in document.pages.all():
        path = overlay_path(document, page.number)
        if os.path.exists(path):
            with open(path, 'rb') as file:
                overlays[page.number] = file.read()
    return overlays


def add_overlay(pdf_page, overlay):
    rect = pdf_page.rect * pdf_page.derotation_matrix
    pdf_page.insert_image(rect, stream=overlay, rotate=pdf_page.rotation)


def color_of(hex_color):
    red = int(hex_color[1:3], 16) / 255
    green = int(hex_color[3:5], 16) / 255
    blue = int(hex_color[5:7], 16) / 255
    return (red, green, blue)


def write_signature(pdf_page, placeholder, page):
    scale = pdf_page.rect.width / page.width
    font_size = placeholder.font_size * scale
    name = placeholder.signer.name

    font = pymupdf.Font(fontfile=SIGNATURE_FONT_FILE)
    text_width = font.text_length(name, fontsize=font_size)
    middle_of_letters = (font.ascender + font.descender) / 2 * font_size

    center_x = (placeholder.left + placeholder.width / 2) * scale
    center_y = (placeholder.top + placeholder.height / 2) * scale
    start = pymupdf.Point(center_x - text_width / 2, center_y + middle_of_letters)

    pdf_page.insert_text(
        start * pdf_page.derotation_matrix,
        name,
        fontfile=SIGNATURE_FONT_FILE,
        fontname=SIGNATURE_FONT_NAME,
        fontsize=font_size,
        color=color_of(placeholder.color),
        rotate=pdf_page.rotation,
    )


def build_signed_pdf(document, overlays):
    pdf = open_original_pdf(document)

    for page in document.pages.all():
        pdf_page = pdf[page.number - 1]

        if page.number in overlays:
            add_overlay(pdf_page, overlays[page.number])

        signed_placeholders = document.placeholders.filter(
            page_number=page.number,
            signer__signed_at__isnull=False,
        )
        for placeholder in signed_placeholders:
            write_signature(pdf_page, placeholder, page)

    data = pdf.tobytes(garbage=3, deflate=True)
    pdf.close()
    return data


def from_address(who):
    display_name = f'{who} via SignElectric'
    return formataddr((display_name, settings.DEFAULT_FROM_EMAIL))


def sign_link(signer):
    return settings.SITE_URL + reverse('sign_document', args=[signer.token])


def send_invitations(document):
    for signer in document.signers.all():
        context = {
            'document': document,
            'signer': signer,
            'link': sign_link(signer),
            'placeholder_count': signer.placeholders.count(),
        }
        html = render_to_string('emails/invitation.html', context)

        email = EmailMessage(
            subject=f'Please sign "{document.filename}"',
            body=html,
            from_email=from_address(document.sender_email),
            to=[signer.email],
            reply_to=[document.sender_email],
        )
        email.content_subtype = 'html'
        email.send()


def send_signed_copy(document, signer):
    pdf = build_signed_pdf(document, read_saved_overlays(document))

    recipients = [document.sender_email]
    for someone in document.signers.filter(signed_at__isnull=False):
        if someone.email not in recipients:
            recipients.append(someone.email)

    finished = document.everyone_signed()
    if finished:
        subject = f'"{document.filename}" is signed by everyone'
    else:
        subject = f'{signer.name} signed "{document.filename}"'

    context = {
        'document': document,
        'signer': signer,
        'finished': finished,
        'waiting': document.signers.filter(signed_at__isnull=True),
    }
    html = render_to_string('emails/signed_copy.html', context)

    email = EmailMessage(
        subject=subject,
        body=html,
        from_email=from_address(signer.name),
        to=recipients,
    )
    email.content_subtype = 'html'
    email.attach(f'{document.filename}.pdf', pdf, 'application/pdf')
    email.send()
