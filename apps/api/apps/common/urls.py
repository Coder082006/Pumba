from django.urls import path

from apps.common.views import ConfigView, HealthView

app_name = "common"

urlpatterns = [
    path("health", HealthView.as_view(), name="health"),
    path("config", ConfigView.as_view(), name="config"),
]
