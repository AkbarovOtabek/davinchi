from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ActionLogViewSet,
    BotConfigView,
    GenerateView,
    GeneratedMessageViewSet,
    HealthView,
    ParseView,
    PauseView,
    ProfileViewSet,
    StatsView,
)

router = DefaultRouter()
router.register("profiles", ProfileViewSet, basename="profile")
router.register("messages", GeneratedMessageViewSet, basename="message")
router.register("logs", ActionLogViewSet, basename="log")

urlpatterns = [
    path("", include(router.urls)),
    path("config/", BotConfigView.as_view(), name="config"),
    path("generate/", GenerateView.as_view(), name="generate"),
    path("parse/", ParseView.as_view(), name="parse"),
    path("stats/", StatsView.as_view(), name="stats"),
    path("pause/", PauseView.as_view(), name="pause"),
    path("health/", HealthView.as_view(), name="health"),
]
