from django import forms
from django.conf import settings

from .models import Document


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ['file']
        widgets = {
            'file': forms.FileInput(attrs={'accept': '.pdf,.doc,.docx,.png,.jpg,.jpeg'}),
        }

    def clean_file(self):
        uploaded = self.cleaned_data['file']

        if uploaded.size > settings.MAX_UPLOAD_SIZE:
            raise forms.ValidationError('The file is too big. The limit is 20 MB.')

        return uploaded
