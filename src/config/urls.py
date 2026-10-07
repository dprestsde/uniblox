from django.urls import path

from store.api.health import live, ready

urlpatterns = [
    path("health/live", live),
    path("health/ready", ready),
]
