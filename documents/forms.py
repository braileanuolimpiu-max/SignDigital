from django import forms
from .models import Pdffile, Image
class PdffileForm(forms.ModelForm):
    pagenumforcover = forms.IntegerField(min_value=1)
    class Meta:
        model = Pdffile
        fields = (
            'pdf',
            'filename',
            'pagenumforcover',
        )
class ImageForm(forms.ModelForm):
    class Meta:
        model = Image
        fields = (
            'file',
            'name',
        )
class HomeForm(forms.Form):
    pass