import io
import os
from email.utils import formataddr

from django.conf import settings
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from PIL import Image, ImageDraw, ImageFont

from .converters import PAGE_DPI

FONTS_FOLDER = os.path.join(settings.BASE_DIR, 'documents', 'fonts')

SIGNING_FONTS = {
    'CourierNew': 'LiberationMono-Regular.ttf',
    'TimesNewRoman': 'LiberationSerif-Regular.ttf',
    'SansSerif': 'LiberationSans-Regular.ttf',
    'Arial': 'LiberationSans-Regular.ttf',
    'MonsieurLaDoulaise': 'MonsieurLaDoulaise-Regular.ttf',
    'MrDeHaviland': 'MrDeHaviland-Regular.ttf',
    'LuxuriousScript': 'LuxuriousScript-Regular.ttf',
}


def draw_signature(image, placeholder):
    font_file = os.path.join(FONTS_FOLDER, SIGNING_FONTS[placeholder.font_key])
    font = ImageFont.truetype(font_file, round(placeholder.font_size))

    center_x = placeholder.left + placeholder.width / 2
    center_y = placeholder.top + placeholder.height / 2

    draw = ImageDraw.Draw(image)
    draw.text((center_x, center_y), placeholder.signer.name, font=font, fill=placeholder.color, anchor='mm')


def build_signed_pdf(document):
    page_images = []

    for page in document.pages.all():
        image = Image.open(page.image.path)
        image = image.convert('RGB')

        signed_placeholders = document.placeholders.filter(
            page_number=page.number,
            signer__signed_at__isnull=False,
        )
        for placeholder in signed_placeholders:
            draw_signature(image, placeholder)

        page_images.append(image)

    buffer = io.BytesIO()
    first_page = page_images[0]
    other_pages = page_images[1:]
    first_page.save(buffer, 'PDF', resolution=PAGE_DPI, save_all=True, append_images=other_pages)
    return buffer.getvalue()


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
        text = render_to_string('emails/invitation.txt', context)
        html = render_to_string('emails/invitation.html', context)

        email = EmailMultiAlternatives(
            subject=f'Please sign "{document.filename}"',
            body=text,
            from_email=from_address(document.sender_email),
            to=[signer.email],
            reply_to=[document.sender_email],
        )
        email.attach_alternative(html, 'text/html')
        email.send()


def send_signed_copy(document, signer):
    pdf = build_signed_pdf(document)

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
    text = render_to_string('emails/signed_copy.txt', context)

    email = EmailMessage(
        subject=subject,
        body=text,
        from_email=from_address(signer.name),
        to=recipients,
    )
    email.attach(f'{document.filename}.pdf', pdf, 'application/pdf')
    email.send()
