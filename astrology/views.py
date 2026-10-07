import re
import hashlib
import json

from django.conf import settings
from django.db import transaction
from django.http import Http404
from django.shortcuts import render
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from openai import APIConnectionError, APITimeoutError
from .models import (
    BirthChart,
    BirthChartSummary,
    ChatMessage,
    SavedChart,
)
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
        'chat_enabled': bool(settings.AI_BASE_URL and settings.AI_MODEL),
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
                    f"{angle['house_topic']}"
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


def active_birth_chart(request):
    # scoping the lookup to the current anonymous visitor prevents a stale or
    # forged session value from linking another visitor's chart data.
    birth_chart_id = request.session.get("birth_chart_id")
    if not birth_chart_id:
        return None
    return BirthChart.objects.filter(
        pk=birth_chart_id,
        user=request.visitor,
    ).first()


def serialize_form_data(data):
    # storing ISO-formatted dates and times makes every value JSON-compatible
    # while keeping the individual submitted components easy to query in JSONB.
    field_names = (
        "name",
        "birth_month",
        "birth_day",
        "birth_year",
        "birth_date",
        "birth_time",
        "birthplace",
        "location_id",
        "latitude",
        "longitude",
        "birth_timezone",
    )
    serialized = {}
    for field_name in field_names:
        value = data.get(field_name)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        if value == "":
            value = None
        serialized[field_name] = value
    return serialized


# display the birth chart form and calculate after valid submission
def index(request):
    
    return render(request, 'astrology/index.html',{
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
                        "astrology/index.html",
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
            # persist the validated inputs beside their calculated chart so a
            # later query can compare source data and derived placements.
            birth_chart = BirthChart.objects.create(
                user=request.visitor,
                form_data=serialize_form_data(data),
                chart_context=chart_context,
                placements=chart_context.get("planets") or None,
                birthplace=data.get("birthplace") or None,
                location_id=data.get("location_id") or None,
                latitude=data.get("latitude"),
                longitude=data.get("longitude"),
                birth_timezone=data.get("birth_timezone") or None,
                has_birth_time=has_birth_time,
            )
            request.session["birth_chart_id"] = birth_chart.pk
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
                "astrology/chart.html",
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
            "astrology/index.html",
            {
                "form": form,
                "geoapify_api_key": settings.GEOAPIFY_API_KEY,
            },
        )
    else: 
        form = BirthChartForm()
        # display the form for a get request, or redisplay it with
        # validation errors after an unsuccessful submission
        return render(request, 'astrology/index.html',{
            'form': form,
            'geoapify_api_key': settings.GEOAPIFY_API_KEY,
            })


def saved_chart(request, token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise Http404
    token_digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    chart = get_object_or_404(SavedChart, token_digest=token_digest)
    request.session.pop("birth_chart_id", None)
    request.session["saved_chart_id"] = chart.pk
    response = render(
            request,
            "astrology/chart.html",
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
        request.session.pop("birth_chart_id", None)
        request.session.pop("chart_context", None)
        request.session.pop("placement_summaries", None)
        request.session.pop("placement_summary_version", None)
    chart.delete()
    return redirect("astrology:index")


def chat(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required."}, status=405)

    _, chart_context = active_chart(request)
    if not chart_context:
        return JsonResponse({"error": "Generate a chart before starting a chat."}, status=400)

    question = request.POST.get("question", "").strip()
    if not question or len(question) > 2000:
        return JsonResponse({"error": "Enter a question of 1–2000 characters."}, status=400)

    birth_chart = active_birth_chart(request)
    # fetch only the two prompt fields and cap the database work to the recent
    # context window; reversing restores chronological order for the model.
    stored_history = list(
        ChatMessage.objects.filter(
            user=request.visitor,
            birth_chart=birth_chart,
        ).order_by("-created_at", "-pk").values_list("role", "content")[:16]
    )
    history = (
        [
            {"role": role, "content": content}
            for role, content in reversed(stored_history)
        ]
        if stored_history
        else request.session.get("chat_history", [])
    )
    try:
        reply = ask_chart_guide(
            chart_context,
            question,
            history,
            request.POST.get("level", "auto"),
            request.POST.get("tone", "auto"),
        )
    except (ValueError, OSError) as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    except (APIConnectionError, APITimeoutError):
        return JsonResponse(
            {
                "error": (
                    "The free local AI service is unavailable. "
                    "Start Ollama and download the configured model, "
                    "then try again."
                )
            },
            status=503,
        )

    # saving both sides atomically avoids a conversation that appears to have
    # a user question with no corresponding assistant response.
    with transaction.atomic():
        ChatMessage.objects.create(
            user=request.visitor,
            birth_chart=birth_chart,
            role=ChatMessage.Role.USER,
            content=question,
        )
        ChatMessage.objects.create(
            user=request.visitor,
            birth_chart=birth_chart,
            role=ChatMessage.Role.ASSISTANT,
            content=reply["answer"],
        )
    return JsonResponse(reply)


def placement_summary(request):
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

    birth_chart = active_birth_chart(request)
    if birth_chart:
        # the chart/placement/version lookup uses the chart's unique placement
        # index, avoiding another model call for previously generated output.
        stored_summary = BirthChartSummary.objects.filter(
            user=request.visitor,
            birth_chart=birth_chart,
            placement_key=placement_key,
            version=PLACEMENT_SUMMARY_VERSION,
        ).first()
        if stored_summary:
            return JsonResponse(stored_summary.summary_data)

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

    try:
        result = summarize_placement(chart_context, placement)
    except (ValueError, OSError) as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    except (APIConnectionError, APITimeoutError):
        return JsonResponse(
            {
                "error": (
                    "The local AI service is unavailable. Start Ollama and "
                    "download the configured model, then try again."
                )
            },
            status=503,
        )

    summary_cache[placement_key] = result
    summary_values = {
        "user": request.visitor,
        "birth_chart": birth_chart,
        "placement_key": placement_key,
        "summary_data": result,
        "version": PLACEMENT_SUMMARY_VERSION,
    }
    if birth_chart:
        BirthChartSummary.objects.update_or_create(
            user=request.visitor,
            birth_chart=birth_chart,
            placement_key=placement_key,
            defaults={
                "summary_data": result,
                "version": PLACEMENT_SUMMARY_VERSION,
            },
        )
    else:
        BirthChartSummary.objects.create(**summary_values)
    if saved_chart:
        saved_chart.placement_summaries = summary_cache
        saved_chart.save(update_fields=["placement_summaries"])
    elif not birth_chart:
        # only legacy session-only charts need the old cache; new chart records
        # already have a durable, indexed summary row in PostgreSQL.
        request.session["placement_summaries"] = summary_cache
    return JsonResponse(result)
    
