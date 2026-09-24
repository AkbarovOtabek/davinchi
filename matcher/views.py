"""REST API: анкеты, сообщения, настройки, ручная генерация."""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from . import ai, services
from .models import ActionLog, BotConfig, GeneratedMessage, Profile
from .parser import parse_profile_caption
from .serializers import (
    ActionLogSerializer,
    BotConfigSerializer,
    GenerateRequestSerializer,
    GeneratedMessageSerializer,
    ParseRequestSerializer,
    PauseRequestSerializer,
    ProfileSerializer,
)


class ProfileViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/profiles/ — список анкет. Фильтры: ?status=&city=&has_description=&q="""

    serializer_class = ProfileSerializer

    def get_queryset(self):
        qs = Profile.objects.prefetch_related("messages").all()
        params = self.request.query_params
        if value := params.get("status"):
            qs = qs.filter(status=value)
        if value := params.get("city"):
            qs = qs.filter(city__icontains=value)
        if value := params.get("q"):
            qs = qs.filter(raw_caption__icontains=value)
        has_description = params.get("has_description")
        if has_description is not None:
            if has_description.lower() in {"1", "true", "yes"}:
                qs = qs.exclude(about="")
            elif has_description.lower() in {"0", "false", "no"}:
                qs = qs.filter(about="")
        return qs

    @action(detail=True, methods=["post"])
    def regenerate(self, request, pk=None):
        """POST /api/profiles/{id}/regenerate/ — сгенерировать новый вариант текста."""
        profile = self.get_object()
        try:
            message = services.generate_for_profile(profile)
        except ai.AIError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(GeneratedMessageSerializer(message).data, status=status.HTTP_201_CREATED)


class GeneratedMessageViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = GeneratedMessageSerializer

    def get_queryset(self):
        qs = GeneratedMessage.objects.select_related("profile").all()
        if value := self.request.query_params.get("status"):
            qs = qs.filter(status=value)
        return qs


class ActionLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ActionLogSerializer

    def get_queryset(self):
        qs = ActionLog.objects.all()
        if value := self.request.query_params.get("level"):
            qs = qs.filter(level=value)
        if value := self.request.query_params.get("event"):
            qs = qs.filter(event=value)
        return qs


class BotConfigView(RetrieveUpdateAPIView):
    """GET/PATCH /api/config/ — настройки бота на лету."""

    serializer_class = BotConfigSerializer

    def get_object(self):
        return BotConfig.get_solo()


class ParseView(APIView):
    """POST /api/parse/ {caption} — проверить разбор подписи анкеты."""

    def post(self, request):
        serializer = ParseRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        parsed = parse_profile_caption(serializer.validated_data["caption"])
        if not parsed:
            return Response(
                {"detail": "Не похоже на анкету Дайвинчика."}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(parsed)


class GenerateView(APIView):
    """POST /api/generate/ — сгенерировать текст по анкете (без отправки в Telegram)."""

    def post(self, request):
        serializer = GenerateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        config = BotConfig.get_solo()

        fields = {
            "name": data.get("name", ""),
            "age": data.get("age"),
            "city": data.get("city", ""),
            "about": data.get("about", ""),
            "raw_caption": data.get("raw_caption", ""),
        }
        if data.get("raw_caption"):
            parsed = parse_profile_caption(data["raw_caption"])
            if parsed:
                fields.update(parsed)

        if data.get("save"):
            profile = services.save_profile(fields, chat_id=0)
            try:
                message = services.generate_for_profile(profile, config)
            except ai.AIError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
            return Response(GeneratedMessageSerializer(message).data, status=status.HTTP_201_CREATED)

        try:
            result = ai.generate_reply(
                name=fields["name"],
                age=fields["age"],
                city=fields["city"],
                about=fields["about"],
                tone=data.get("tone", config.tone),
                max_chars=data.get("max_chars", config.max_chars),
                extra_instructions=config.extra_instructions,
                use_vision=data.get("use_vision", False),
            )
        except ai.AIError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({**result, "profile": fields})


class StatsView(APIView):
    """GET /api/stats/ — сводка по работе бота."""

    def get(self, request):
        return Response(services.stats())


class PauseView(APIView):
    """POST /api/pause/ {minutes} — поставить бота на паузу (0 = снять)."""

    def post(self, request):
        serializer = PauseRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        minutes = serializer.validated_data["minutes"]
        config = BotConfig.get_solo()
        config.paused_until = timezone.now() + timedelta(minutes=minutes) if minutes else None
        config.save(update_fields=["paused_until"])
        services.log_event("pause", f"пауза на {minutes} мин")
        return Response(BotConfigSerializer(config).data)


class HealthView(APIView):
    def get(self, request):
        ok, reason = services.can_run()
        return Response({"status": "ok", "can_run": ok, "reason": reason})
