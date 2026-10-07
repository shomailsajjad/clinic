from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('patients/', views.patient_list, name='patient_list'),
    path('patients/new/', views.patient_edit, name='patient_create'),
    path('patients/<int:pk>/', views.patient_detail, name='patient_detail'),
    path('patients/<int:pk>/edit/', views.patient_edit, name='patient_edit'),
    path('services/', views.service_list, name='service_list'),
    path('services/new/', views.service_edit, name='service_create'),
    path('services/<int:pk>/edit/', views.service_edit, name='service_edit'),
    path('users/', views.staff_list, name='staff_list'),
    path('users/new/', views.staff_edit, name='staff_create'),
    path('users/<int:pk>/edit/', views.staff_edit, name='staff_edit'),
    path('audit/', views.audit_list, name='audit_list'),
]
