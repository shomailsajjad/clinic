from django.contrib.auth import views as auth_views
from django.urls import include, path
from clinic.auth_views import ClinicLoginView

urlpatterns = [
    path('login/', ClinicLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', include('clinic.urls')),
]
