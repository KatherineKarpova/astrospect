import re
import hashlib
import json
from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
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


def ai_configured():
    return bool(settings.AI_API_KEY and settings.AI_BASE_URL and settings.AI_MODEL)


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

PLACEMENT_SUMMARY_VERSION = 7


def chart_display_context(chart_context, chart_svg, has_birth_time):
    has_birth_location = chart_context.get("birth_location_known", False)
    has_house_data = chart_context.get(
        "house_data_available",
        has_birth_time and has_birth_location,
    )
    return {
        'chart_svg': chart_svg,
        'has_birth_time': has_birth_time,
        'has_birth_location': has_birth_location,
        'has_house_data': has_house_data,
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
            "A planet marked below changed tropical signs during this UTC "
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
                'house_brief': (
                    f"House {planet['house']}"
                    if planet['house']
                    else ""
                ),
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
                'house_brief': (
                    f"Whole-sign house {angle['house']} · "
                    f"{angle['house_topic'].capitalize()} "
                    f"({angle['house_sign']})"
                ),
            }
            for angle in chart_context['angles']
        ],
    }


def active_chart(request):
    saved_chart_id = request.session.get("saved_chart_id")
    if saved_chart_id:
        try:
            saved_chart = SavedChart.objects.get(pk=saved_chart_id)
        except SavedChart.DoesNotExist:
            return None, None
        return saved_chart, saved_chart.chart_context
    return None, request.session.get("chart_context")


# display the birth chart form and calculate after valid submission
def index(request):
    
    return render(request, 'astrospect/index.html',{
        'geoapify_api_key': settings.GEOAPIFY_API_KEY,
        })

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

            chart_context = build_chart_context(
                chart,
                has_birth_time,
                has_birth_location,
                daily_sign_changes,
            )
            if data["birth_time"]:
                chart_context["birth_time_display"] = data["birth_time"].strftime(
                    "%H:%M"
                )
            request.session["chart_context"] = chart_context
            chart_svg = generate_chart_svg(
                chart,
                show_houses=has_birth_time and has_birth_location,
            )
            request.session.pop("saved_chart_id", None)
            request.session["chart_svg"] = chart_svg
            request.session["browser_cache_enabled"] = (
                request.POST.get("save_to_browser") == "on"
            )
            request.session.pop("chat_history", None)
            request.session.pop("placement_summaries", None)
            request.session.pop("placement_summary_version", None)
            response = render(
                request,
                "astrospect/chart.html",
                {
                    **chart_display_context(
                        chart_context,
                        chart_svg,
                        has_birth_time,
                    ),
                    "browser_cache_enabled": request.session[
                        "browser_cache_enabled"
                    ],
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
                "form": form,
                "geoapify_api_key": settings.GEOAPIFY_API_KEY,
            },
        )
    else: 
        form = BirthChartForm()
        # display the form for a get request, or redisplay it with
        # validation errors after an unsuccessful submission
        return render(request, 'astrospect/index.html',{
            'form': form,
            'geoapify_api_key': settings.GEOAPIFY_API_KEY,
            })


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
    
