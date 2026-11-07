from django.urls import path
from . import views

app_name = 'astrology'

urlpatterns = [
    path('', views.index, name='index'),
    path('chart/', views.chart, name='chart'),
    path('saved/<str:token>/', views.saved_chart, name='saved_chart'),
    path('saved/<str:token>/delete/', views.delete_saved_chart, name='delete_saved_chart'),
    path('chat/', views.chat, name='chat'),
    path('placement-summary/', views.placement_summary, name='placement_summary'),
]