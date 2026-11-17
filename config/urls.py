from django.urls import include, path

urlpatterns = [
    path('', include('documents.urls')),
    path('', include('accounts.urls')),
]
