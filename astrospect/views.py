import re
import hashlib
import json
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import render
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from openai import APIConnectionError, APIStatusError, APITimeoutError
from .models import SavedChart
from .forms import BirthChartForm
from .services import (
    BirthplaceLookupError,
    calculate_birth_chart,
    calculate_daily_sign_changes,
    generate_chart_svg,
    resolve_birthplace,
)
from .chatbot import (
    SOURCES,
    build_chart_context,
    ask_chart_guide,
    summarize_placement,
)
import logging

logger = logging.getLogger(__name__)

AI_UNAVAILABLE = "The AI assistant isn't available right now. Please try again later."


# check whether all required ai service settings are present.
def ai_configured():
    return bool(settings.AI_API_KEY and settings.AI_BASE_URL and settings.AI_MODEL)


# translate ai provider failures into safe user-facing messages.
def ai_error_response(exc):
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        logger.warning("AI connection problem: %r", exc)
        message = "The AI took too long to respond. Please try again in a moment."
    elif isinstance(exc, APIStatusError):
        logger.warning("AI status error: %s", exc.status_code)
        message = "The AI is busy right now. Please try again in a moment."
    else:
        logger.exception("Unexpected AI error")
        message = AI_UNAVAILABLE
    return JsonResponse({"error": message}, status=503)

PLANET_SYMBOLS = {
    "Sun": "☉",
    "Moon": "☽",
    "Mercury": "☿",
    "Venus": "♀",
    "Mars": "♂",
    "Jupiter": "♃",
    "Saturn": "♄",
}

ANGLE_LABELS = {
    "Ascendant": "ASC · Ascendant",
    "Descendant": "DSC · Descendant",
    "Medium_Coeli": "MC · Midheaven",
    "Imum_Coeli": "IC · Imum Coeli",
}

PLACEMENT_SUMMARY_VERSION = 8


# prepare the labels and placement details shown on the chart page.
def chart_display_context(chart_context, chart_svg, has_birth_time):
    has_birth_location = chart_context.get("birth_location_known", False)
    zodiac_system = chart_context.get("zodiac", "tropical")
    house_system = chart_context.get("house_system", "whole_sign")
    has_house_data = chart_context.get(
        "house_data_available",
        has_birth_time and has_birth_location,
    )

    # format a house number with its topic and sign.
    def format_house_brief(placement):
        house = placement.get("house")
        topic = placement.get("house_topic")
        house_sign = placement.get("house_sign")
        if not house or not topic or not house_sign:
            return ""
        return f"House {house} · {topic.capitalize()} ({house_sign})"

    return {
        'chart_svg': chart_svg,
        'has_birth_time': has_birth_time,
        'has_birth_location': has_birth_location,
        'has_house_data': has_house_data,
        'chart_zodiac': zodiac_system,
        'chart_zodiac_label': (
            "Sidereal (Lahiri)"
            if zodiac_system == "sidereal"
            else "Tropical"
        ),
        'chart_house_system': house_system,
        'chart_house_system_label': (
            "Whole sign" if house_system == "whole_sign" else "Placidus"
        ),
        'chart_ayanamsa': chart_context.get("ayanamsa"),
        'birth_time_note': (
            (
                f"Birth time received: {chart_context['birth_time_display']} "
                + (
                    "local time for the selected birthplace."
                    if has_birth_location
                    else "used as a UTC approximation because no birthplace was selected."
                )
            )
            if has_birth_time and chart_context.get("birth_time_display")
            else (
                "A birth time was provided; this saved chart does not retain the entered time."
                if has_birth_time
                else (
                    "No birth time was entered; planetary positions use noon UTC."
                    if not has_birth_location
                    else "No birth time was entered, so houses and angles are unavailable."
                )
            )
        ),
        'browser_cache_enabled': False,
        'location_note': (
            "No birthplace was selected, so houses and angles are unavailable."
            if not has_birth_location
            else ""
        ),
        'daily_sign_change_note': (
            f"A planet marked below changed {zodiac_system} signs during this UTC "
            "calendar date. Without a birthplace and time zone, its exact "
            "birth sign cannot be confirmed."
            if chart_context.get("daily_sign_changes")
            else ""
        ),
        'chat_enabled': bool(
            settings.AI_API_KEY and settings.AI_BASE_URL and settings.AI_MODEL
        ),
        'planet_placements': [
            {
                **planet,
                'key': planet['name'].lower(),
                'symbol': PLANET_SYMBOLS[planet['name']],
                'house_brief': format_house_brief(planet),
                'possible_sign_change': planet.get("possible_sign_change", []),
                'dignity_label': (
                    'Exalted' if 'exalted' in planet['essential_dignity']
                    else 'Debilitated' if 'debilitated' in planet['essential_dignity']
                    else 'Own sign' if 'own sign' in planet['essential_dignity']
                    else ''
                ),
            }
            for planet in chart_context['planets']
        ],
        'angle_placements': [
            {
                **angle,
                'key': angle['name'].lower(),
                'label': ANGLE_LABELS[angle['name']],
                'house_brief': format_house_brief(angle),
            }
            for angle in chart_context['angles']
        ],
    }


# load the active chart from a saved record or the current session.
def active_chart(request):
    saved_chart_id = request.session.get("saved_chart_id")
    if saved_chart_id:
        try:
            saved_chart = SavedChart.objects.get(pk=saved_chart_id)
        except SavedChart.DoesNotExist:
            return None, None
        chart_context = {
            key: value
            for key, value in saved_chart.chart_context.items()
            if key != "chart_name"
        }
        return saved_chart, chart_context
    return None, request.session.get("chart_context")


# show the form used to enter birth chart details.
def index(request):
    
    return render(request, 'astrospect/index.html',{
        'geoapify_api_key': settings.GEOAPIFY_API_KEY,
        })

# calculate a posted chart once, then render the session result on get.
def chart(request):
    if request.method == 'POST':
        form = BirthChartForm(request.POST)

        if form.is_valid():
            data = form.cleaned_data
            if data["needs_location_lookup"]:
                try:
                    data.update(resolve_birthplace(data["birthplace"]))
                except BirthplaceLookupError as exc:
                    form.add_error(None, str(exc))
                    return render(
                        request,
                        "astrospect/index.html",
                        {
                            "form": form,
                            "geoapify_api_key": settings.GEOAPIFY_API_KEY,
                        },
                    )
            has_birth_location = data["has_birth_location"]
            has_birth_time = data["birth_time"] is not None
            daily_sign_changes = (
                {}
                if has_birth_location
                else calculate_daily_sign_changes(data)
            )
            chart = calculate_birth_chart(
                data,
                data.get('birth_timezone') or "UTC",
            )

            zodiac_system = data["zodiac_system"]
            ayanamsa = "lahiri" if zodiac_system == "sidereal" else None
            chart_context = build_chart_context(
                chart,
                has_birth_time,
                has_birth_location,
                daily_sign_changes,
                zodiac_system=zodiac_system,
                house_system=data["house_system"],
                ayanamsa=ayanamsa,
            )
            if data["birth_time"]:
                timezone_name = data.get("birth_timezone") or "UTC"
                chart_context["birth_time_display"] = datetime.combine(
                    data["birth_date"],
                    data["birth_time"],
                ).replace(
                    tzinfo=ZoneInfo(timezone_name)
                ).strftime("%H:%M %Z")
            request.session["chart_context"] = chart_context
            request.session["has_birth_time"] = has_birth_time
            chart_svg = generate_chart_svg(
                chart,
                show_houses=has_birth_time and has_birth_location,
            )
            request.session["chart_name"] = data.get("name") or "Your Birth Chart"
            request.session.pop("saved_chart_id", None)
            request.session["chart_svg"] = chart_svg
            request.session["browser_cache_enabled"] = data["save_to_browser"]
            request.session.pop("chat_history", None)
            request.session.pop("placement_summaries", None)
            request.session.pop("placement_summary_version", None)
            return redirect("astrology:chart")
        return render(
            request,
            "astrospect/index.html",
            {
                "form": form,
                "geoapify_api_key": settings.GEOAPIFY_API_KEY,
            },
        )

    if request.method in ("GET", "HEAD"):
        chart_context = request.session.get("chart_context")
        chart_svg = request.session.get("chart_svg")
        if chart_context and chart_svg:
            has_birth_time = request.session.get(
                "has_birth_time",
                chart_context.get("birth_time_known", False),
            )
            response = render(
                request,
                "astrospect/chart.html",
                {
                    **chart_display_context(
                        chart_context,
                        chart_svg,
                        has_birth_time,
                    ),
                    "chart_name": request.session.get(
                        "chart_name",
                        "Your Birth Chart",
                    ),
                    "browser_cache_enabled": request.session.get(
                        "browser_cache_enabled",
                        False,
                    ),
                    "chart_cache_key": hashlib.sha256(
                        json.dumps(
                            chart_context,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                    "source": SOURCES[0],
                },
            )
            response["Cache-Control"] = "private, no-store"
            response["Referrer-Policy"] = "no-referrer"
            response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
            return response

        return render(
            request,
            "astrospect/index.html",
            {
                "form": BirthChartForm(),
                "geoapify_api_key": settings.GEOAPIFY_API_KEY,
                "auto_restore_chart": True,
            },
        )

    return HttpResponseNotAllowed(["GET", "HEAD", "POST"])


# save the active session chart and return its private link.
def save_chart(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required."}, status=405)

    if request.session.get("saved_chart_id"):
        return JsonResponse({"error": "This chart already has a private link."}, status=400)

    chart_context = request.session.get("chart_context")
    chart_svg = request.session.get("chart_svg")
    if not chart_context or not chart_svg:
        return JsonResponse({"error": "Generate a chart before saving it."}, status=400)

    token = secrets.token_urlsafe(32)
    # keep only a digest in postgres; the raw token is returned once in the link.
    token_digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    stored_context = dict(chart_context)
    chart_name = request.POST.get("chart_name", "").strip()[:100]
    if chart_name:
        stored_context["chart_name"] = chart_name

    placement_summaries = request.session.get("placement_summaries", {})
    if request.session.get("placement_summary_version") != PLACEMENT_SUMMARY_VERSION:
        placement_summaries = {}
    if not isinstance(placement_summaries, dict):
        return JsonResponse({"error": "Chart summaries could not be saved."}, status=500)

    saved_chart = SavedChart.objects.create(
        token_digest=token_digest,
        chart_context=stored_context,
        chart_svg=chart_svg,
        has_birth_time=chart_context.get("birth_time_known", False),
        placement_summaries=placement_summaries,
        placement_summary_version=PLACEMENT_SUMMARY_VERSION,
    )
    request.session["saved_chart_id"] = saved_chart.pk
    private_url = request.build_absolute_uri(
        reverse("astrology:saved_chart", kwargs={"token": token})
    )
    delete_url = request.build_absolute_uri(
        reverse("astrology:delete_saved_chart", kwargs={"token": token})
    )
    response = JsonResponse({
        "url": private_url,
        "delete_url": delete_url,
    })
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


# load a private chart using its unguessable access token.
def saved_chart(request, token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise Http404
    token_digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    chart = get_object_or_404(SavedChart, token_digest=token_digest)
    request.session["saved_chart_id"] = chart.pk
    response = render(
            request,
            "astrospect/chart.html",
            {
                **chart_display_context(
                    chart.chart_context,
                    chart.chart_svg,
                    chart.has_birth_time,
                ),
                "chart_name": chart.chart_context.get(
                    "chart_name",
                    "Your Birth Chart",
                ),
                "private_chart_url": request.build_absolute_uri(
                    reverse("astrology:saved_chart", kwargs={"token": token})
                ),
                "private_chart_delete_url": request.build_absolute_uri(
                    reverse("astrology:delete_saved_chart", kwargs={"token": token})
                ),
                "chart_cache_key": hashlib.sha256(
                    json.dumps(
                        chart.chart_context,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "source": SOURCES[0],
            },
    )
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


# delete a saved chart and clear its active session references.
def delete_saved_chart(request, token):
    if request.method != "POST":
        return JsonResponse({"error": "POST required."}, status=405)
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise Http404
    token_digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    chart = get_object_or_404(SavedChart, token_digest=token_digest)
    if request.session.get("saved_chart_id") == chart.pk:
        request.session.pop("saved_chart_id", None)
        request.session.pop("chart_context", None)
        request.session.pop("placement_summaries", None)
        request.session.pop("placement_summary_version", None)
    chart.delete()
    return redirect("astrology:index")


# answer a chart question and retain a short conversation history.
def chat(request):
    """todo:
    keep the conversation history sent to OpenRouter bounded, and translate
    upstream failures into useful messages without exposing provider details.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required."}, status=405)

    if not ai_configured():
        return JsonResponse({"error": AI_UNAVAILABLE}, status=503)

    _, chart_context = active_chart(request)
    if not chart_context:
        return JsonResponse({"error": "Generate a chart before starting a chat."}, status=400)

    question = request.POST.get("question", "").strip()
    if not question or len(question) > 2000:
        return JsonResponse({"error": "Enter a question of 1–2000 characters."}, status=400)

    history = request.session.get("chat_history", [])
    try:
        reply = ask_chart_guide(
            chart_context,
            question,
            history,
            request.POST.get("level", "auto"),
            request.POST.get("tone", "auto"),
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    except (APITimeoutError, APIConnectionError, APIStatusError, OSError) as exc:
        return ai_error_response(exc)

    history.extend([
        {"role": "user", "content": question},
        {"role": "assistant", "content": json.dumps({
            "answer": reply["answer"],
            "reflection": reply.get("reflection", ""),
            "source_ids": reply.get("source_ids", []),
        })},
    ])
    request.session["chat_history"] = history[-16:]
    return JsonResponse(reply)


# return a cached or newly generated summary for a chart placement.
def placement_summary(request):
    """todo:
    validate that the requested placement belongs to the active chart and
    return cached summaries before making an inference request; provider errors
    must never expose credentials or raw upstream response data.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required."}, status=405)

    saved_chart, chart_context = active_chart(request)
    if not chart_context:
        return JsonResponse({"error": "Generate a chart before opening a placement."}, status=400)

    placement_key = request.POST.get("placement", "").strip().lower()
    placements = {
        placement["name"].lower(): placement
        for placement in chart_context.get("planets", [])
    }
    placements.update({
        placement["name"].lower(): placement
        for placement in chart_context.get("angles", [])
    })
    placement = placements.get(placement_key)
    if placement is None:
        return JsonResponse({"error": "That chart placement is not available."}, status=400)

    if saved_chart:
        if saved_chart.placement_summary_version != PLACEMENT_SUMMARY_VERSION:
            saved_chart.placement_summaries = {}
            saved_chart.placement_summary_version = PLACEMENT_SUMMARY_VERSION
            saved_chart.save(update_fields=[
                "placement_summaries",
                "placement_summary_version",
            ])
        summary_cache = saved_chart.placement_summaries
    else:
        summary_cache_version = PLACEMENT_SUMMARY_VERSION
        if request.session.get("placement_summary_version") != summary_cache_version:
            request.session["placement_summaries"] = {}
            request.session["placement_summary_version"] = summary_cache_version
        summary_cache = request.session.get("placement_summaries", {})
    if placement_key in summary_cache:
        return JsonResponse(summary_cache[placement_key])

    if not ai_configured():
        return JsonResponse({"error": AI_UNAVAILABLE}, status=503)
    try:
        result = summarize_placement(chart_context, placement)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    except (APITimeoutError, APIConnectionError, APIStatusError, OSError) as exc:
        return ai_error_response(exc)

    summary_cache[placement_key] = result
    if saved_chart:
        saved_chart.placement_summaries = summary_cache
        saved_chart.save(update_fields=["placement_summaries"])
    else:
        request.session["placement_summaries"] = summary_cache
    return JsonResponse(result)
    
