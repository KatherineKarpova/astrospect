import json
import math
import re
from datetime import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from kerykeion import AstrologicalSubject, KerykeionChartSVG

HOUSE_SYSTEM_IDENTIFIERS = {
    "whole_sign": "W",
    "placidus": "P",
}
ZODIAC_TYPE_NAMES = {
    "tropical": "Tropic",
    "sidereal": "Sidereal",
}
SIDEREAL_MODE_NAMES = {
    "lahiri": "LAHIRI",
}
HOUSE_NUMBERS = {
    "First_House": 1,
    "Second_House": 2,
    "Third_House": 3,
    "Fourth_House": 4,
    "Fifth_House": 5,
    "Sixth_House": 6,
    "Seventh_House": 7,
    "Eighth_House": 8,
    "Ninth_House": 9,
    "Tenth_House": 10,
    "Eleventh_House": 11,
    "Twelfth_House": 12,
}

# make global array so the 4 angels I want in the chart are included on top of the 7 planets and true nodes in active_points
TRADITIONAL_CHART_POINTS = [ 
    'Sun',
    'Moon',
    'Mercury',
    'Venus',
    'Mars',
    'Jupiter',
    'Saturn',
    'True_Node',
    'Ascendant',
    'Descendant',
    'Medium_Coeli',
    'Imum_Coeli',
]


class BirthplaceLookupError(ValueError):
    """Raised when a typed birthplace cannot be resolved to chart coordinates."""


def resolve_birthplace(query):
    if not settings.GEOAPIFY_API_KEY:
        raise BirthplaceLookupError(
            "Birthplace lookup is not configured. Choose a suggested location "
            "or leave the birthplace blank."
        )

    parameters = urlencode({
        "text": query,
        "type": "city",
        "format": "json",
        "limit": 1,
        "apiKey": settings.GEOAPIFY_API_KEY,
    })
    url = f"https://api.geoapify.com/v1/geocode/search?{parameters}"
    try:
        with urlopen(url, timeout=8) as response:
            payload = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise BirthplaceLookupError(
            "The birthplace lookup service could not be reached. Please try "
            "again or choose a suggested location."
        ) from exc

    if not isinstance(payload, dict):
        raise BirthplaceLookupError(
            "The birthplace lookup returned an unexpected response. Please "
            "choose a suggested location or try again."
        )
    results = payload.get("results", [])
    place = results[0] if results else None
    if not isinstance(place, dict):
        raise BirthplaceLookupError(
            "We could not find that birthplace. Choose a suggested location "
            "or check the spelling."
        )

    timezone_details = place.get("timezone")
    timezone_name = (
        timezone_details.get("name")
        if isinstance(timezone_details, dict)
        else None
    )
    latitude = place.get("lat")
    longitude = place.get("lon")
    place_id = place.get("place_id")
    if not isinstance(timezone_name, str) or not timezone_name or not place_id:
        raise BirthplaceLookupError(
            "That location did not include the required time-zone details. "
            "Choose a more specific birthplace suggestion."
        )
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError) as exc:
        raise BirthplaceLookupError(
            "That location did not include valid coordinates. Choose a "
            "suggested birthplace."
        ) from exc
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        raise BirthplaceLookupError(
            "That location returned coordinates outside the valid range. "
            "Choose a suggested birthplace."
        )
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise BirthplaceLookupError(
            "The birthplace lookup returned an invalid time zone. Choose a "
            "suggested location or try a more specific place."
        ) from exc
    return {
        "birthplace": place.get("formatted") or query,
        "location_id": place_id,
        "latitude": latitude,
        "longitude": longitude,
        "birth_timezone": timezone_name,
        "has_birth_location": True,
    }


def calculate_birth_chart(data, timezone_name):
    # create a Kerykeion astrological subject from validated birth data

    birth_date = data["birth_date"]
    birth_time = data["birth_time"]
    has_birth_location = data.get("has_birth_location", True)

    if not has_birth_location:
        birth_time = data.get("birth_time")
        hour = birth_time.hour if birth_time else 12
        minute = birth_time.minute if birth_time else 0
        latitude = 0
        longitude = 0
        timezone_name = "UTC"
    elif birth_time is None:
        hour = 12
        minute = 0
        latitude = data["latitude"]
        longitude = data["longitude"]
    else:
        hour = birth_time.hour
        minute = birth_time.minute
        latitude = data["latitude"]
        longitude = data["longitude"]

    # this function creates the chart Kerykeion object from the validated form data 
    zodiac_system = data.get("zodiac_system", "tropical")
    ayanamsa = data.get("ayanamsa") or "lahiri"
    return AstrologicalSubject(
        name=data.get("name") or "Your Chart",
        year=birth_date.year,
        month=birth_date.month,
        day=birth_date.day,
        hour=hour,
        minute=minute,
        lat=latitude,
        lng=longitude,
        tz_str=timezone_name,
        online=False,
        zodiac_type=ZODIAC_TYPE_NAMES[zodiac_system],
        sidereal_mode=(
            SIDEREAL_MODE_NAMES[ayanamsa]
            if zodiac_system == "sidereal"
            else None
        ),
        houses_system_identifier=HOUSE_SYSTEM_IDENTIFIERS[
            data.get("house_system", "whole_sign")
        ],
    )


def calculate_daily_sign_changes(data):
    """Return selected-zodiac sign changes across the UTC birth date."""
    birth_date = data["birth_date"]
    sign_names = (
        "Aries", "Taurus", "Gemini", "Cancer",
        "Leo", "Virgo", "Libra", "Scorpio",
        "Sagittarius", "Capricorn", "Aquarius", "Pisces",
    )
    zodiac_system = data.get("zodiac_system", "tropical")
    zodiac_type = ZODIAC_TYPE_NAMES[zodiac_system]
    sidereal_mode = (
        SIDEREAL_MODE_NAMES[data.get("ayanamsa") or "lahiri"]
        if zodiac_system == "sidereal"
        else None
    )
    day_start = AstrologicalSubject(
        name="Daily Sign Check",
        year=birth_date.year,
        month=birth_date.month,
        day=birth_date.day,
        hour=0,
        minute=0,
        lat=0,
        lng=0,
        tz_str="UTC",
        online=False,
        zodiac_type=zodiac_type,
        sidereal_mode=sidereal_mode,
        houses_system_identifier="W",
    )
    day_end = AstrologicalSubject(
        name="Daily Sign Check",
        year=birth_date.year,
        month=birth_date.month,
        day=birth_date.day,
        hour=23,
        minute=59,
        lat=0,
        lng=0,
        tz_str="UTC",
        online=False,
        zodiac_type=zodiac_type,
        sidereal_mode=sidereal_mode,
        houses_system_identifier="W",
    )
    planet_names = ("Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn")
    changed_signs = {}
    for name in planet_names:
        start_sign = sign_names[getattr(day_start, name.lower()).sign_num]
        end_sign = sign_names[getattr(day_end, name.lower()).sign_num]
        if start_sign != end_sign:
            changed_signs[name] = [start_sign, end_sign]
    return changed_signs

# this function manipulates the svg string created form 
def generate_chart_svg(chart, show_houses=True):
    # traditional active points excludes uranus, neptune, pluto, chiron, and lilith
    # takes the astrological subject object and creates a chart data object specifically to render
    drawer = KerykeionChartSVG(chart, active_points=TRADITIONAL_CHART_POINTS)
    svg = drawer.makeWheelOnlyTemplate()
    svg = re.sub(
        r"<title>.*?</title>",
        "<title>Birth Chart</title>",
        svg,
        count=1,
        flags=re.DOTALL,
    )
    element_colors = {
        "fire": ("#e7b493", "#a35428"),
        "earth": ("#c9c6aa", "#706b43"),
        "air": ("#bfd2d8", "#426f82"),
        "water": ("#a9cec7", "#28766f"),
    }
    sign_elements = (
        "fire", "earth", "air", "water",
        "fire", "earth", "air", "water",
        "fire", "earth", "air", "water",
    )
    color_overrides = {
        "house-number": "#292824",
        "houses-radix-line": "#57544d",
        "houses-transit-line": "#57544d",
        "first-house": "#57544d",
        "tenth-house": "#57544d",
        "seventh-house": "#57544d",
        "fourth-house": "#57544d",
        "square": "#57544d",
    }
    color_overrides.update({
        f"zodiac-radix-ring-{index}": "#57544d"
        for index in range(4)
    })
    color_overrides.update({
        f"zodiac-transit-ring-{index}": "#57544d"
        for index in range(4)
    })
    for sign_index, element in enumerate(sign_elements):
        background, foreground = element_colors[element]
        color_overrides[f"zodiac-bg-{sign_index}"] = background
        color_overrides[f"zodiac-icon-{sign_index}"] = foreground
    for variable, color in color_overrides.items():
        svg = re.sub(
            rf"(--kerykeion-chart-color-{re.escape(variable)}):\s*#[0-9a-f]{{3,8}}",
            rf"\1: {color}",
            svg,
            flags=re.IGNORECASE,
        )
    planet_variables = (
        "sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn",
        "true-node", "mean-node",
    )
    chart_point = re.compile(
        r"(<g kr:node='ChartPoint'[^>]*kr:sign='([A-Za-z]+)'[^>]*)(>)"
    )

    def color_chart_point(match):
        sign_index = {
            "Ari": 0, "Tau": 1, "Gem": 2, "Can": 3,
            "Leo": 4, "Vir": 5, "Lib": 6, "Sco": 7,
            "Sag": 8, "Cap": 9, "Aqu": 10, "Pis": 11,
        }.get(match.group(2))
        if sign_index is None:
            return match.group(0)
        foreground = element_colors[sign_elements[sign_index]][1]
        style = ";".join(
            f"--kerykeion-chart-color-{variable}: {foreground}"
            for variable in planet_variables
        )
        return f"{match.group(1)} style='{style}'{match.group(3)}"

    svg = chart_point.sub(color_chart_point, svg)
    if show_houses:
        house_number_pattern = re.compile(
            r"<g kr:node='HouseNumber'><text[^>]*><tspan "
            r"x='([^']+)' y='([^']+)'>(\d{1,2})</tspan></text></g>"
        )
        svg = house_number_pattern.sub(
            lambda match: (
                f"<g kr:node='HouseNumber'><text "
                f"style='fill: #292824; font-size: 14px; "
                f"font-family: sans-serif'><tspan x='{match.group(1)}' "
                f"y='{match.group(2)}'>{match.group(3)}</tspan></text></g>"
            ),
            svg,
        )
    # remove houses from made svg if show houses is False, as determined by no birth time given
    if not show_houses:
        svg = remove_houses_from_svg(svg)

    return svg


# this function removes the houses visibility from the wheel for when birth time is unknown
def remove_houses_from_svg(svg):
    houses_start = svg.find('<!-- Houses -->')
    planets_start = svg.find('<!-- Planets -->')

    if houses_start != -1 and planets_start != -1:
        svg = svg[:houses_start] + svg[planets_start:]

    return svg