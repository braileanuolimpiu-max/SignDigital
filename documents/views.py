import hashlib
import io
import json
import os
import smtplib
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .autoplace import analyze_document, can_autoplace
from .converters import PAGE_DPI, ConversionError, build_pages
from .forms import DocumentForm
from .models import AuditEvent, Document, Placeholder, Signer, delete_if_possible, delete_old_drafts
from .signing import (
    build_signed_pdf,
    has_signature,
    read_saved_overlays,
    save_overlays,
    send_decline_notice,
    send_invitations,
    send_reminders,
    send_signed_copy,
    signed_file_name,
)

EXPIRED_MESSAGE = 'Your document expired. Please upload it again.'
EMAIL_FAILED_MESSAGE = 'The emails could not be sent. Check the email settings and try again.'
AUTOPLACE_ONLY_MESSAGE = 'Auto Place works only on PDF and Word files.'
NOTHING_TO_SIGN_MESSAGE = 'Add your signature to the document before signing.'
REMIND_AGAIN_AFTER = timedelta(hours=1)
DECLINE_REASON_LENGTH = 500


class SendError(Exception):
    pass


def get_editable_document(request, uid):
    if not request.user.is_authenticated:
        return None

    return Document.objects.filter(uid=uid, owner=request.user, sent_at__isnull=True).first()


def person_text(user):
    name = user.get_full_name()
    if name == '':
        name = user.username
    return f'{name} ({user.email})'


def add_event(request, document, text):
    return AuditEvent.objects.create(
        document=document,
        text=text,
        ip=request.META.get('REMOTE_ADDR'),
    )


def get_my_signer(request, document):
    if request.user.email == '':
        return None
    return document.signers.filter(email__iexact=request.user.email).first()


def get_visible_document(request, uid):
    document = get_object_or_404(Document, uid=uid)

    if document.owner == request.user:
        return document
    if get_my_signer(request, document) is not None:
        return document

    raise Http404('Document not found')


def matches_filters(document, search, status):
    if search != '':
        text = document.filename + ' ' + document.sender_email
        if search.lower() not in text.lower():
            return False

    if status != '' and document.status != status:
        return False

    return True


def signer_is_allowed(request, signer):
    if not signer.document.require_login:
        return True
    if not request.user.is_authenticated:
        return False
    return request.user.email.lower() == signer.email.lower()


def is_valid_email(text):
    try:
        validate_email(text)
    except ValidationError:
        return False
    return True


@login_required
def homepage(request):
    delete_old_drafts()

    if request.method == 'POST':
        form = DocumentForm(request.POST, request.FILES)
        if form.is_valid():
            document = form.save(commit=False)
            document.owner = request.user
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
                delete_if_possible(document)
                messages.error(request, str(error))
                return redirect('homepage')

            document.original_hash = hashlib.sha256(read_file(document.file.path)).hexdigest()
            document.save()
            add_event(request, document, 'Uploaded by ' + person_text(request.user))
            return redirect('signature_workspace', uid=document.uid)
    else:
        form = DocumentForm()

    search = request.GET.get('search', '')
    search = search.strip()
    status = request.GET.get('status', '')
    filtering = search != '' or status != ''

    rows = []
    for document in request.user.documents.order_by('-created_at'):
        if document.status == 'draft':
            continue
        if not matches_filters(document, search, status):
            continue

        rows.append({
            'document': document,
            'signed': document.signed_count(),
            'total': document.signer_count(),
        })

    received = []
    waiting_for_me = 0
    if request.user.email != '':
        my_signers = Signer.objects.filter(email__iexact=request.user.email)
        for signer in my_signers.order_by('-document__sent_at'):
            if signer.signed_at is None and signer.document.status == 'waiting':
                waiting_for_me = waiting_for_me + 1
            if not matches_filters(signer.document, search, status):
                continue

            received.append({
                'document': signer.document,
                'signer': signer,
                'signed': signer.document.signed_count(),
                'total': signer.document.signer_count(),
            })

    show = request.GET.get('show')
    if show != 'received':
        show = 'mine'

    context = {
        'form': form,
        'rows': rows,
        'received': received,
        'waiting_for_me': waiting_for_me,
        'show': show,
        'search': search,
        'status': status,
        'filtering': filtering,
    }
    return render(request, 'dashboard.html', context)


@login_required
def signature_workspace(request, uid):
    document = get_editable_document(request, uid)
    if document is None:
        if Document.objects.filter(uid=uid, owner=request.user).exists():
            return redirect('document_detail', uid=uid)
        messages.warning(request, EXPIRED_MESSAGE)
        return redirect('homepage')

    pages = []
    for page in document.pages.all():
        url = reverse('editor_page_image', args=[document.uid, page.number])
        pages.append({'url': url, 'width': page.width, 'height': page.height})

    my_name = request.user.get_full_name()
    if my_name == '':
        my_name = request.user.username

    editor_data = {
        'my_name': my_name,
        'filename': document.filename,
        'dpi': PAGE_DPI,
        'mode': document.mode,
        'pages': pages,
    }
    context = {
        'uid': document.uid,
        'original_url': reverse('editor_original', args=[document.uid]),
        'mode': document.mode,
        'can_autoplace': can_autoplace(document),
        'editor_data': editor_data,
    }
    return render(request, 'editor.html', context)


def autoplace(request, uid):
    if request.method != 'POST':
        return HttpResponse(status=405)

    document = get_editable_document(request, uid)
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


def read_overlays(request, document):
    overlays = {}

    for page in document.pages.all():
        uploaded = request.FILES.get(f'overlay_{page.number}')
        if uploaded is None:
            raise SendError(f'The signatures of page {page.number} are missing.')

        if uploaded.content_type != 'image/png':
            raise SendError(f'The signatures of page {page.number} are not a PNG image.')

        overlays[page.number] = uploaded.read()

    return overlays


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


def send_for_signing(request, uid):
    if request.method != 'POST':
        return HttpResponse(status=405)

    document = get_editable_document(request, uid)
    if document is None:
        return JsonResponse({'error': EXPIRED_MESSAGE}, status=410)
    if document.mode != 'multi':
        return JsonResponse({'error': 'This document is not for multiple signing.'}, status=400)

    sender_email = request.user.email
    if not is_valid_email(sender_email):
        return JsonResponse({'error': 'Your account has no valid email.'}, status=400)

    try:
        placeholders = read_placeholders(request, document)
        page_images = read_page_images(request, document)
        overlays = read_overlays(request, document)
    except SendError as error:
        return JsonResponse({'error': str(error)}, status=400)

    save_page_images(page_images)
    save_overlays(document, overlays)

    try:
        with transaction.atomic():
            signer_count = create_signers(document, placeholders)
            document.sender_email = sender_email
            document.sent_at = timezone.now()
            document.require_login = request.POST.get('require_login') == 'yes'
            document.save()

            signer_texts = []
            for signer in document.signers.order_by('id'):
                signer_texts.append(f'{signer.name} ({signer.email})')
            text = 'Sent for signing by ' + person_text(request.user) + ' to ' + ', '.join(signer_texts)
            add_event(request, document, text)

            send_invitations(document)
    except (smtplib.SMTPException, OSError):
        return JsonResponse({'error': EMAIL_FAILED_MESSAGE}, status=500)

    if signer_count == 1:
        messages.success(request, f'"{document.filename}" was sent to 1 person.')
    else:
        messages.success(request, f'"{document.filename}" was sent to {signer_count} people.')
    return JsonResponse({'redirect': reverse('homepage')})


def export_signed(request, uid):
    if request.method != 'POST':
        return HttpResponse(status=405)

    document = get_editable_document(request, uid)
    if document is None:
        return JsonResponse({'error': EXPIRED_MESSAGE}, status=410)

    try:
        overlays = read_overlays(request, document)
    except SendError as error:
        return JsonResponse({'error': str(error)}, status=400)

    if document.mode == 'solo' and not has_signature(overlays):
        return JsonResponse({'error': NOTHING_TO_SIGN_MESSAGE}, status=400)

    save_overlays(document, overlays)
    if document.mode == 'solo':
        document.finished_at = timezone.now()
        document.save()
        add_event(request, document, 'Signed by ' + person_text(request.user))

    pdf = build_signed_pdf(document, overlays)
    return HttpResponse(pdf, content_type='application/pdf')


def show_finished(request, title, message):
    context = {
        'title': title,
        'message': message,
    }
    if request.user.is_authenticated:
        context['link'] = reverse('homepage') + '?show=received'
        context['link_text'] = 'Back to my documents'
    return render(request, 'sign_finished.html', context)


def sign_document(request, token):
    signer = Signer.objects.filter(token=token).first()
    if signer is None:
        return show_finished(
            request,
            'This link is no longer active',
            'The document was deleted by the sender, or the link is wrong.',
        )

    document = signer.document

    if document.status == 'cancelled':
        return show_finished(
            request,
            'This document was cancelled',
            'The sender cancelled it, so it cannot be signed anymore.',
        )

    if document.status == 'declined':
        return show_finished(
            request,
            'This document was declined',
            'Someone declined to sign it, so it cannot be signed anymore.',
        )

    if not signer_is_allowed(request, signer):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        return show_finished(
            request,
            'This document is for someone else',
            f'Only the account with the email {signer.email} can sign it. Log out and log in with that account.',
        )

    if signer.signed_at is not None:
        return show_finished(
            request,
            'You already signed',
            'A signed copy was sent to your email.',
        )

    if request.method == 'POST' and request.POST.get('action') == 'decline':
        reason = request.POST.get('reason', '')
        reason = reason.strip()
        reason = reason[:DECLINE_REASON_LENGTH]

        signer.declined_at = timezone.now()
        signer.decline_reason = reason
        signer.save()
        document.declined_at = signer.declined_at
        document.save()

        text = f'Declined by {signer.name} ({signer.email})'
        if reason != '':
            text = text + '. Reason: ' + reason
        add_event(request, document, text)

        try:
            send_decline_notice(document, signer)
        except (smtplib.SMTPException, OSError):
            pass

        return show_finished(
            request,
            'You declined to sign',
            'The sender was told. Nobody can sign this document anymore.',
        )

    if request.method == 'POST':
        signer.signed_at = timezone.now()
        signer.save()
        event = add_event(request, document, f'Signed by {signer.name} ({signer.email})')

        try:
            send_signed_copy(document, signer)
        except (smtplib.SMTPException, OSError):
            signer.signed_at = None
            signer.save()
            event.delete()
            return show_finished(request, 'Something went wrong', EMAIL_FAILED_MESSAGE)

        if document.everyone_signed():
            document.finished_at = timezone.now()
            document.save()
            add_event(request, document, 'Signed by everyone')

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
        url = reverse('sign_page_image', args=[signer.token, page.number])
        pages.append({'url': url, 'boxes': boxes})

    opened_text = f'Opened by {signer.name} ({signer.email})'
    if not document.events.filter(text=opened_text).exists():
        add_event(request, document, opened_text)

    context = {
        'document': document,
        'signer': signer,
        'pages': pages,
    }
    return render(request, 'sign.html', context)


@login_required
def document_detail(request, uid):
    document = get_visible_document(request, uid)
    if document.status == 'draft':
        return redirect('homepage')

    is_owner = document.owner == request.user
    if is_owner:
        back_url = reverse('homepage')
    else:
        back_url = reverse('homepage') + '?show=received'

    context = {
        'document': document,
        'is_owner': is_owner,
        'my_signer': get_my_signer(request, document),
        'back_url': back_url,
        'signers': document.signers.order_by('id'),
        'signed': document.signed_count(),
        'total': document.signer_count(),
    }
    return render(request, 'document_detail.html', context)


@login_required
@xframe_options_sameorigin
def document_pdf(request, uid):
    document = get_visible_document(request, uid)
    if document.status == 'draft':
        return redirect('homepage')

    pdf = build_signed_pdf(document, read_saved_overlays(document))
    download = request.GET.get('download') == '1'
    return FileResponse(
        io.BytesIO(pdf),
        content_type='application/pdf',
        as_attachment=download,
        filename=signed_file_name(document, document.status == 'signed'),
    )


@login_required
def send_reminder(request, uid):
    if request.method != 'POST':
        return HttpResponse(status=405)

    document = get_object_or_404(Document, uid=uid, owner=request.user)

    if document.status != 'waiting':
        messages.error(request, 'This document is not waiting for anyone to sign.')
        return redirect('document_detail', uid=uid)

    if document.reminded_at is not None:
        next_reminder = document.reminded_at + REMIND_AGAIN_AFTER
        if timezone.now() < next_reminder:
            messages.error(request, 'You already sent a reminder in the last hour. Try again later.')
            return redirect('document_detail', uid=uid)

    try:
        count = send_reminders(document)
    except (smtplib.SMTPException, OSError):
        messages.error(request, EMAIL_FAILED_MESSAGE)
        return redirect('document_detail', uid=uid)

    document.reminded_at = timezone.now()
    document.save()
    if count == 1:
        add_event(request, document, 'Reminder sent to 1 person who did not sign yet')
    else:
        add_event(request, document, f'Reminder sent to {count} people who did not sign yet')

    if count == 1:
        messages.success(request, 'A reminder was sent to 1 person.')
    else:
        messages.success(request, f'A reminder was sent to {count} people.')
    return redirect('document_detail', uid=uid)


def read_file(path):
    with open(path, 'rb') as file:
        return file.read()


def page_image_response(document, number):
    page = document.pages.filter(number=number).first()
    if page is None:
        raise Http404('Page not found')
    return HttpResponse(read_file(page.image.path), content_type='image/jpeg')


@login_required
def editor_page_image(request, uid, number):
    document = get_object_or_404(Document, uid=uid, owner=request.user)
    return page_image_response(document, number)


@login_required
def editor_original(request, uid):
    document = get_object_or_404(Document, uid=uid, owner=request.user)
    name, extension = os.path.splitext(document.file.name)
    return FileResponse(
        io.BytesIO(read_file(document.file.path)),
        filename=document.filename + extension,
    )


def sign_page_image(request, token, number):
    signer = get_object_or_404(Signer, token=token)
    if signer.document.status == 'cancelled':
        raise Http404('Document cancelled')
    if signer.document.status == 'declined':
        raise Http404('Document declined')
    if not signer_is_allowed(request, signer):
        raise Http404('Not allowed')
    return page_image_response(signer.document, number)


@login_required
def cancel_document(request, uid):
    if request.method != 'POST':
        return HttpResponse(status=405)

    document = get_object_or_404(Document, uid=uid, owner=request.user)

    if document.status != 'waiting':
        messages.error(request, 'Only a document that is waiting for signatures can be cancelled.')
        return redirect('document_detail', uid=uid)

    document.cancelled_at = timezone.now()
    document.save()
    add_event(request, document, 'Cancelled by ' + person_text(request.user))
    messages.success(request, 'The document was cancelled. Its signing links do not work anymore.')
    return redirect('document_detail', uid=uid)


@login_required
def delete_document(request, uid):
    if request.method != 'POST':
        return HttpResponse(status=405)

    document = get_object_or_404(Document, uid=uid, owner=request.user)
    filename = document.filename

    try:
        document.delete_with_files()
    except OSError:
        messages.error(request, 'The document could not be deleted right now. Try again in a minute.')
        return redirect('document_detail', uid=uid)

    messages.success(request, f'"{filename}" was deleted.')
    return redirect('homepage')
