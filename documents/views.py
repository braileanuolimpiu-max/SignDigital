import json
import os
import smtplib

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .autoplace import analyze_document, can_autoplace
from .converters import PAGE_DPI, ConversionError, build_pages
from .forms import DocumentForm
from .models import Document, Placeholder, Signer, purge_abandoned_documents
from .signing import send_invitations, send_signed_copy

EXPIRED_MESSAGE = 'Your document expired. Please upload it again.'
EMAIL_FAILED_MESSAGE = 'The emails could not be sent. Check the email settings and try again.'
AUTOPLACE_ONLY_MESSAGE = 'Auto Place works only on PDF and Word files.'


class SendError(Exception):
    pass


def get_active_document(request):
    uid = request.session.get('active_doc_uid')
    if not uid:
        return None
    return Document.objects.filter(uid=uid).first()


def discard_active_document(request):
    document = get_active_document(request)
    if document:
        document.delete_with_files()
    request.session.pop('active_doc_uid', None)


def is_valid_email(text):
    try:
        validate_email(text)
    except ValidationError:
        return False
    return True


def homepage(request):
    purge_abandoned_documents()

    if request.method == 'POST':
        form = DocumentForm(request.POST, request.FILES)
        if form.is_valid():
            discard_active_document(request)

            document = form.save(commit=False)
            uploaded_name = form.cleaned_data['file'].name
            name, extension = os.path.splitext(uploaded_name)
            document.filename = name[:50]
            if request.POST.get('mode') == 'multi':
                document.mode = 'multi'
            else:
                document.mode = 'solo'
            document.save()

            try:
                build_pages(document)
            except ConversionError as error:
                document.delete_with_files()
                messages.error(request, str(error))
                return redirect('homepage')

            request.session['active_doc_uid'] = str(document.uid)
            return redirect('signature_workspace', uid=document.uid)
    else:
        form = DocumentForm()

    return render(request, 'homepage.html', {'form': form})


def signature_workspace(request, uid):
    if str(uid) != request.session.get('active_doc_uid'):
        return redirect('homepage')

    document = get_active_document(request)
    if document is None:
        messages.warning(request, EXPIRED_MESSAGE)
        return redirect('homepage')

    document.mark_seen()

    pages = []
    for page in document.pages.all():
        pages.append({'url': page.image.url, 'width': page.width, 'height': page.height})

    editor_data = {
        'filename': document.filename,
        'dpi': PAGE_DPI,
        'mode': document.mode,
        'pages': pages,
    }
    context = {
        'original_url': document.file.url,
        'mode': document.mode,
        'can_autoplace': can_autoplace(document),
        'editor_data': editor_data,
    }
    return render(request, 'editor.html', context)


@csrf_exempt
def user_left_page(request):
    if request.method != 'POST':
        return HttpResponse(status=405)

    document = get_active_document(request)
    if document:
        document.mark_left()
    return HttpResponse(status=204)


@csrf_exempt
def heartbeat(request):
    if request.method != 'POST':
        return HttpResponse(status=405)

    document = get_active_document(request)
    if document is None:
        messages.warning(request, EXPIRED_MESSAGE)
        return HttpResponse(status=410)

    document.mark_seen()
    return HttpResponse(status=204)


def autoplace(request):
    if request.method != 'POST':
        return HttpResponse(status=405)

    document = get_active_document(request)
    if document is None:
        return JsonResponse({'error': EXPIRED_MESSAGE}, status=410)
    if not can_autoplace(document):
        return JsonResponse({'error': AUTOPLACE_ONLY_MESSAGE}, status=400)

    return JsonResponse(analyze_document(document))


def read_placeholders(request, document):
    try:
        items = json.loads(request.POST.get('placeholders', ''))
    except json.JSONDecodeError:
        raise SendError('The placeholders could not be read.')

    if len(items) == 0:
        raise SendError('Add at least one placeholder before sending.')

    page_count = document.pages.count()
    placeholders = []

    for item in items:
        name = str(item.get('name', ''))
        name = name.strip()

        email = str(item.get('email', ''))
        email = email.strip()
        email = email.lower()

        color = str(item.get('color', ''))

        if name == '':
            raise SendError('Every placeholder needs a name.')
        if not is_valid_email(email):
            raise SendError(f'"{email}" is not a valid email.')
        if len(color) != 7 or not color.startswith('#'):
            raise SendError('A placeholder has a wrong color.')

        try:
            page_number = int(item['page_number'])
            left = float(item['left'])
            top = float(item['top'])
            width = float(item['width'])
            height = float(item['height'])
            font_size = float(item['font_size'])
        except (KeyError, TypeError, ValueError):
            raise SendError('A placeholder has a wrong position.')

        if page_number < 1 or page_number > page_count:
            raise SendError('A placeholder is on a page that does not exist.')

        placeholders.append({
            'name': name[:100],
            'email': email,
            'color': color,
            'page_number': page_number,
            'left': left,
            'top': top,
            'width': width,
            'height': height,
            'font_size': font_size,
        })

    return placeholders


def read_page_images(request, document):
    page_images = {}

    for page in document.pages.all():
        uploaded = request.FILES.get(f'page_{page.number}')
        if uploaded is None:
            raise SendError(f'Page {page.number} is missing.')

        if uploaded.content_type != 'image/jpeg':
            raise SendError(f'Page {page.number} is not a JPEG image.')

        page_images[page] = uploaded

    return page_images


def save_page_images(page_images):
    for page in page_images:
        uploaded = page_images[page]
        with open(page.image.path, 'wb') as file:
            for chunk in uploaded.chunks():
                file.write(chunk)


def create_signers(document, placeholders):
    signers_by_email = {}

    for item in placeholders:
        email = item['email']
        if email not in signers_by_email:
            signers_by_email[email] = Signer.objects.create(
                document=document,
                name=item['name'],
                email=email,
            )

        Placeholder.objects.create(
            document=document,
            signer=signers_by_email[email],
            page_number=item['page_number'],
            left=item['left'],
            top=item['top'],
            width=item['width'],
            height=item['height'],
            font_size=item['font_size'],
            color=item['color'],
        )

    return len(signers_by_email)


def send_for_signing(request):
    if request.method != 'POST':
        return HttpResponse(status=405)

    document = get_active_document(request)
    if document is None:
        return JsonResponse({'error': EXPIRED_MESSAGE}, status=410)
    if document.mode != 'multi':
        return JsonResponse({'error': 'This document is not for multiple signing.'}, status=400)

    sender_email = request.POST.get('sender_email', '')
    sender_email = sender_email.strip()
    sender_email = sender_email.lower()
    if not is_valid_email(sender_email):
        return JsonResponse({'error': 'Write a valid email for yourself.'}, status=400)

    try:
        placeholders = read_placeholders(request, document)
        page_images = read_page_images(request, document)
    except SendError as error:
        return JsonResponse({'error': str(error)}, status=400)

    save_page_images(page_images)

    try:
        with transaction.atomic():
            signer_count = create_signers(document, placeholders)
            document.sender_email = sender_email
            document.sent_at = timezone.now()
            document.save()
            send_invitations(document)
    except (smtplib.SMTPException, OSError):
        return JsonResponse({'error': EMAIL_FAILED_MESSAGE}, status=500)

    request.session.pop('active_doc_uid', None)

    if signer_count == 1:
        messages.success(request, f'"{document.filename}" was sent to 1 person.')
    else:
        messages.success(request, f'"{document.filename}" was sent to {signer_count} people.')
    return JsonResponse({'redirect': reverse('homepage')})


def show_finished(request, title, message):
    return render(request, 'sign_finished.html', {'title': title, 'message': message})


def sign_document(request, token):
    signer = Signer.objects.filter(token=token).first()
    if signer is None:
        return show_finished(
            request,
            'This link is no longer active',
            'The document was already signed by everyone, or it expired.',
        )

    document = signer.document

    if signer.signed_at is not None:
        return show_finished(
            request,
            'You already signed',
            'A signed copy was sent to your email.',
        )

    if request.method == 'POST':
        signer.signed_at = timezone.now()
        signer.save()

        try:
            send_signed_copy(document, signer)
        except (smtplib.SMTPException, OSError):
            signer.signed_at = None
            signer.save()
            return show_finished(request, 'Something went wrong', EMAIL_FAILED_MESSAGE)

        if document.everyone_signed():
            document.delete_with_files()

        return show_finished(
            request,
            'Thank you for signing',
            'A signed copy of the document was sent to your email.',
        )

    pages = []
    for page in document.pages.all():
        boxes = []
        for placeholder in document.placeholders.filter(page_number=page.number):
            if placeholder.signer == signer:
                state = 'mine'
                label = signer.name
            elif placeholder.signer.signed_at is not None:
                state = 'signed'
                label = placeholder.signer.name
            else:
                state = 'waiting'
                label = 'Waiting for ' + placeholder.signer.name

            boxes.append({
                'state': state,
                'label': label,
                'color': placeholder.color,
                'left': placeholder.left / page.width * 100,
                'top': placeholder.top / page.height * 100,
                'width': placeholder.width / page.width * 100,
                'height': placeholder.height / page.height * 100,
                'font_size': placeholder.font_size / page.width * 100,
            })
        pages.append({'url': page.image.url, 'boxes': boxes})

    context = {
        'document': document,
        'signer': signer,
        'pages': pages,
    }
    return render(request, 'sign.html', context)
