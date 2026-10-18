import os
import shutil
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

DOCUMENTS_DIRECTORY = 'documents'
ALLOWED_EXTENSIONS = ['pdf', 'doc', 'docx', 'png', 'jpg', 'jpeg']
LEFT_GRACE_PERIOD = timedelta(minutes=2)
ABANDONED_AFTER = timedelta(minutes=10)
SIGNING_EXPIRES_AFTER = timedelta(days=30)


def document_upload_path(instance, filename):
    name, extension = os.path.splitext(filename)
    folder = os.path.join(DOCUMENTS_DIRECTORY, str(instance.uid))
    return os.path.join(folder, 'original' + extension.lower())


class Document(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    file = models.FileField(
        upload_to=document_upload_path,
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_EXTENSIONS)],
        max_length=500,
    )
    filename = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(default=timezone.now)
    left_at = models.DateTimeField(null=True, blank=True)
    mode = models.CharField(max_length=10, default='solo')
    sender_email = models.EmailField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.filename

    @property
    def kind(self):
        name, extension = os.path.splitext(self.file.name)
        if extension == '.pdf':
            return 'pdf'
        if extension in ('.doc', '.docx'):
            return 'word'
        return 'image'

    @property
    def folder_path(self):
        return os.path.join(settings.MEDIA_ROOT, DOCUMENTS_DIRECTORY, str(self.uid))

    def mark_seen(self):
        self.last_seen = timezone.now()
        self.left_at = None
        self.save(update_fields=['last_seen', 'left_at'])

    def mark_left(self):
        self.left_at = timezone.now()
        self.save(update_fields=['left_at'])

    def delete_with_files(self):
        if os.path.isdir(self.folder_path):
            shutil.rmtree(self.folder_path)
        self.delete()

    def everyone_signed(self):
        waiting = self.signers.filter(signed_at__isnull=True)
        return not waiting.exists()


class Page(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='pages')
    number = models.PositiveIntegerField()
    image = models.FileField(max_length=500)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()

    class Meta:
        ordering = ['number']


class Signer(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='signers')
    name = models.CharField(max_length=100)
    email = models.EmailField()
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    signed_at = models.DateTimeField(null=True, blank=True)


class Placeholder(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='placeholders')
    signer = models.ForeignKey(Signer, on_delete=models.CASCADE, related_name='placeholders')
    page_number = models.PositiveIntegerField()
    left = models.FloatField()
    top = models.FloatField()
    width = models.FloatField()
    height = models.FloatField()
    font_size = models.FloatField()
    font_key = models.CharField(max_length=50)
    color = models.CharField(max_length=7)


def delete_if_possible(document):
    try:
        document.delete_with_files()
    except OSError:
        pass


def purge_abandoned_documents():
    now = timezone.now()
    still_editing = Document.objects.filter(sent_at__isnull=True)

    left_too_long_ago = still_editing.filter(left_at__lt=now - LEFT_GRACE_PERIOD)
    for document in left_too_long_ago:
        delete_if_possible(document)

    silent_too_long = still_editing.filter(last_seen__lt=now - ABANDONED_AFTER)
    for document in silent_too_long:
        delete_if_possible(document)

    never_finished = Document.objects.filter(sent_at__lt=now - SIGNING_EXPIRES_AFTER)
    for document in never_finished:
        delete_if_possible(document)
