import json
import re
from django.conf import settings
from openai import OpenAI


# load these server-controlled files.
# the user can ask questions, but cannot replace the application's
# instructions or its approved source collection.
SYSTEM_PROMPT = (
    settings.BASE_DIR / "shared" / "chat-policy.txt"
).read_text(encoding="utf-8")

SOURCES = json.loads(
    (
        settings.BASE_DIR / "shared" / "sources.json"
    ).read_text(encoding="utf-8")
)


def sources_for_chart_context(chart_context):
    # method references are added only when their calculation convention is
    # active, so the model cannot cite an unselected methodology as applied.
    selected_ids = {"BPHS"}
    if (
        chart_context.get("zodiac") == "sidereal"
        or chart_context.get("house_system") == "placidus"
    ):
        selected_ids.add("SWEPH")
    return [
        source for source in SOURCES
        if source["id"] in selected_ids
    ]


HOUSE_TOPICS = {
    # concise noun phrases describe life areas without repeating house signs.
    1: "self, body, approach",
    2: "resources, family, speech",
    3: "effort, skills, siblings",
    4: "home, roots, foundations",
    5: "learning, creativity, children",
    6: "work, service, health",
    7: "partnerships, agreements",
    8: "shared resources, transformation",
    9: "teachers, beliefs, long journeys",
    10: "work, responsibility, public role",
    11: "friends, networks, gains",
    12: "rest, retreat, release",
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

PLACEMENT_SUMMARY_PROMPT = """Write a concise, personalized explanation of the supplied chart placement.
Use only the supplied chart facts and approved method-relevant sources.
Do not blend or substitute an unselected zodiac or house system. The selected
zodiac and house system are included in the supplied chart method.
When tropical is selected, be transparent that applying Jyotisha concepts is a
modern adaptation and do not claim traditional Jyotisha texts prescribed
tropical placements. When sidereal Lahiri is selected, use the supplied Lahiri
sidereal placements and do not reinterpret them as tropical.
Whole-sign and Placidus houses are distinct calculations. Use only the supplied
house assignments; do not recalculate them using another house system.
Never introduce nakshatras, dashas, or unprovided chart facts. Describe
astrology as historical symbolism, not scientific prediction.
Use plain, non-fatalistic language.
If the placement has "possible_sign_change" facts, explain that its sign is
uncertain because it may have changed during the UTC birth date. Use the
listed possible signs and do not present the noon-UTC sign as certain.

First identify the exact supplied placement by its name and sign; do not
interpret every placement as the Ascendant. Follow these rules for the named
placement:
- Ascendant: call it the rising sign (Lagna). Describe how its sign can color
  outward approach and first impression, as symbolism not a fixed personality
  fact. For whole-sign charts, explain that it starts house 1 and sets the
  whole-sign houses. For Placidus charts, explain only the supplied Placidus
  house facts. Do not assign it planetary dignity.
- Descendant: explain the supplied sign as a traditional lens on partnership
  and one-to-one relating, and that it marks the 7th-house axis opposite Lagna.
- Medium_Coeli: explain the supplied sign as a lens on public contribution or
  vocation, and state its supplied house only when one is available; do not
  assume it is in house 10.
- Imum_Coeli: explain the supplied sign as a lens on home, roots, and private
  foundations, and state its supplied house only when one is available; do not
  assume it is in house 4.
- Planet: state what the named planet signifies in simple terms, then connect
  its supplied sign expression with its calculated house context.
  Explain how those parts modify one another, then finish by stating its
  supplied sign condition only if the facts say own sign, exalted, or
  debilitated. Sign condition comes from the sign, not the house. If none
  applies, omit sign condition entirely. Never say "no special sign condition"
  or "other sign." Houses 1, 4, 7, and 10 are traditionally angular and more
  prominent; mention that only when supplied as angular.
House numbers and life-area descriptors are displayed directly below each
placement. Do not repeat them in summaries. Do not invent house numbers when
the chart context marks houses unavailable. Do not repeat the placement sign
in parentheses. Without a birthplace, state that house and angle are
unavailable. Without a birthplace, the chart uses the entered time as a UTC
approximation for planetary positions, or noon UTC if no time was given.
Explain this limitation once only when relevant.
Mention possible sign changes supplied in the chart facts and do not assert
one sign as certain for a planet that crossed signs on that UTC date.
Without a birth time but with a known birthplace, state that house is unknown.
Return only 1-3 concise, plain-language sentences as plain text. Do not return
JSON, quotation marks around the response, markdown fences, citations, or
source IDs."""


def _placement_summary_fallback(placement):
    name = placement["name"]
    sign = placement["sign"]
    dignities = [
        condition for condition in placement.get("essential_dignity", [])
        if condition in {"own sign", "exalted", "debilitated"}
    ]
    if name == "Ascendant":
        sentences = [
            f"A {sign} Ascendant can color first impressions and outward approach "
            "with the sign's traditional qualities."
        ]
    elif name == "Descendant":
        sentences = [
            f"{sign} on the Descendant offers a traditional lens on one-to-one "
            "relationships and partnership."
        ]
    elif name == "Medium_Coeli":
        sentences = [
            f"The Midheaven in {sign} can describe a traditional lens on public "
            "contribution and vocation."
        ]
    elif name == "Imum_Coeli":
        sentences = [
            f"The Imum Coeli in {sign} can describe a traditional lens on home, "
            "roots, and private foundations."
        ]
    else:
        planet_themes = {
            "Sun": "vitality and purpose",
            "Moon": "feeling and perception",
            "Mercury": "learning and communication",
            "Venus": "connection and enjoyment",
            "Mars": "effort and initiative",
            "Jupiter": "growth and counsel",
            "Saturn": "responsibility and perseverance",
        }
        themes = planet_themes.get(name, "its traditional themes")
        sentences = [
            f"{name} in {sign} brings themes of {themes} into the chart, "
            "expressed through the sign's traditional qualities."
        ]
    if dignities:
        sentences.append(
            f"Its sign condition is {', '.join(dignities)}."
        )
    return " ".join(sentences)


def summarize_placement(chart_context, placement):
    """Generate one source-grounded, plain-language placement summary."""
    if not isinstance(placement, dict):
        raise ValueError("Unknown chart placement.")

    client = OpenAI(
        api_key=settings.AI_API_KEY,
        base_url=settings.AI_BASE_URL,
        timeout=settings.AI_TIMEOUT_SECONDS,
        max_retries=0,
    )
    placement_name = placement.get("name")
    placement_sign = placement.get("sign")
    if not isinstance(placement_name, str) or not isinstance(placement_sign, str):
        raise ValueError("The chart placement is missing its name or sign.")

    is_ascendant = placement_name == "Ascendant"
    target_instructions = (
        f"The ONLY placement being explained is {placement_name} in {placement_sign}. "
        + (
            "This is the Ascendant; explain rising sign and chart structure."
            if is_ascendant
            else (
                "This is NOT the Ascendant. Do not mention Ascendant, rising sign, "
                "Lagna, or chart ruler. Explain only this named placement."
            )
        )
    )
    target_chart_facts = {
        "zodiac": chart_context.get("zodiac"),
        "house_system": chart_context.get("house_system"),
        "ayanamsa": chart_context.get("ayanamsa"),
    }
    placement_facts = {
        key: value
        for key, value in placement.items()
        if key not in {"house_sign", "house_topic"}
    }
    if is_ascendant and chart_context.get("house_system") == "whole_sign":
        target_chart_facts["whole_sign_houses"] = chart_context.get(
            "whole_sign_houses",
            [],
        )

    completion = client.chat.completions.create(
        model=settings.AI_MODEL,
        messages=[
            {
                "role": "system",
                "content": f"{PLACEMENT_SUMMARY_PROMPT}\n\n{target_instructions}",
            },
            {
                "role": "user",
                "content": json.dumps({
                    "chart_method": target_chart_facts,
                    "placement": placement_facts,
                    "sources": sources_for_chart_context(chart_context),
                }),
            },
        ],
        max_tokens=300,
    )
    raw_content = completion.choices[0].message.content
    if not isinstance(raw_content, str) or not raw_content.strip():
        raise ValueError("The model did not return a placement summary.")

    summary = raw_content.strip()
    if summary.startswith("```") and summary.endswith("```"):
        summary = summary[3:-3].strip()
        if summary.startswith("text"):
            summary = summary[4:].lstrip()
    if not summary:
        raise ValueError("The model returned an empty placement summary.")
    if not is_ascendant:
        summary_sentences = re.split(r"(?<=[.!?])\s+", summary)
        summary = " ".join(
            sentence for sentence in summary_sentences
            if not re.search(
                r"\b(ascendant|rising sign|lagna|chart ruler)\b",
                sentence,
                flags=re.IGNORECASE,
            )
        )
    if (
        not summary
        or not re.search(rf"\b{re.escape(placement_name)}\b", summary, re.IGNORECASE)
        or not re.search(rf"\b{re.escape(placement_sign)}\b", summary, re.IGNORECASE)
    ):
        summary = _placement_summary_fallback(placement)

    return {
        "summary": summary,
    }


def ask_chart_guide(
    chart_context,
    question,
    history,
    level="auto",
    tone="auto",
):
    """generate an explanation using calculated facts and recent chat."""

    # explicit preferences override the model's estimate.
    # automatic mode leaves the level and tone to the instructions.
    if level not in {"auto", "beginner", "technical"}:
        raise ValueError("Invalid explanation level.")

    if tone not in {"auto", "gentle", "direct"}:
        raise ValueError("Invalid conversation tone.")

    client = OpenAI(
        api_key=settings.AI_API_KEY,
        base_url=settings.AI_BASE_URL,
        timeout=settings.AI_TIMEOUT_SECONDS,
        max_retries=0,
    )

    # the chart and sources are supplied separately from the question.
    # this makes the distinction between application context and
    # the user's conversational input clearer.
    supplied_context = {
        "chart": chart_context,
        "sources": sources_for_chart_context(chart_context),
        "preferences": {
            "level": level,
            "tone": tone,
        },
    }

    # retain a bounded amount of recent conversation.
    # without history, a follow-up such as "explain that more simply"
    # would arrive without the explanation it refers to.
    recent_history = history[-8:]

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "system",
            "content": json.dumps(supplied_context),
        },
        *recent_history,
        {
            "role": "user",
            "content": question,
        },
    ]

    completion = client.chat.completions.create(
        model=settings.AI_MODEL,
        messages=messages,

        # request a machine-readable response so django can separate
        # the answer, reflection question, and source references.
        response_format={"type": "json_object"},

        # bound response length and the cost of an individual answer.
        max_tokens=850,

        # request that this completion not be stored by this feature.
        # this does not mean the provider never processes request data.
    )

    raw_content = completion.choices[0].message.content

    if not raw_content:
        raise ValueError("The model did not return an answer.")

    reply = json.loads(raw_content)

    # json mode guarantees neither your expected fields nor their types.
    # validate the response before passing it to your interface.
    if not isinstance(reply, dict):
        raise ValueError("Unexpected response format.")

    if not isinstance(reply.get("answer"), str):
        raise ValueError("The response is missing an answer.")

    if not isinstance(reply.get("reflection"), str):
        raise ValueError("The reflection has an invalid format.")

    source_ids = reply.get("source_ids")

    if not isinstance(source_ids, list):
        raise ValueError("The source references have an invalid format.")

    approved_sources = {
        source["id"]: source
        for source in SOURCES
    }

    # accept only ids from your own collection.
    # the browser will receive verified urls rather than model-generated urls.
    if any(
        not isinstance(source_id, str)
        or source_id not in approved_sources
        for source_id in source_ids
    ):
        raise ValueError("The response cited an unknown source.")

    reply["sources"] = [
        approved_sources[source_id]
        for source_id in dict.fromkeys(source_ids)
    ]

    return reply

# provide json dicts for llm to have context for responses
def build_chart_context(
    chart,
    has_birth_time,
    has_birth_location=True,
    daily_sign_changes=None,
    zodiac_system="tropical",
    house_system="whole_sign",
    ayanamsa=None,
):
    """convert a kerykeion subject into json-compatible chart facts."""

    sign_names = [
        "Aries", "Taurus", "Gemini", "Cancer",
        "Leo", "Virgo", "Libra", "Scorpio",
        "Sagittarius", "Capricorn", "Aquarius", "Pisces",
    ]

    # the list follows zodiac order, matching kerykeion's sign_num
    # these are traditional rulers: mars rules scorpio,
    # jupiter rules pisces, and saturn rules aquarius
    sign_rulers = [
        "Mars", "Venus", "Mercury", "Moon",
        "Sun", "Mercury", "Venus", "Mars",
        "Jupiter", "Saturn", "Saturn", "Jupiter",
    ]

    own_signs = {
        "Sun": {"Leo"},
        "Moon": {"Cancer"},
        "Mercury": {"Gemini", "Virgo"},
        "Venus": {"Taurus", "Libra"},
        "Mars": {"Aries", "Scorpio"},
        "Jupiter": {"Sagittarius", "Pisces"},
        "Saturn": {"Capricorn", "Aquarius"},
    }
    # traditional jyotisha sign conditions: own sign, exaltation, and fall.
    exaltations = {
        "Sun": "Aries",
        "Moon": "Taurus",
        "Mercury": "Virgo",
        "Venus": "Pisces",
        "Mars": "Capricorn",
        "Jupiter": "Cancer",
        "Saturn": "Libra",
    }

    planet_names = [
        "Sun", "Moon", "Mercury", "Venus",
        "Mars", "Jupiter", "Saturn",
    ]

    context = {
        "zodiac": zodiac_system,
        "house_system": house_system,
        "ayanamsa": ayanamsa if zodiac_system == "sidereal" else None,
        "method_label": (
            f"sidereal ({ayanamsa or 'lahiri'} ayanamsa)"
            if zodiac_system == "sidereal"
            else "tropical"
        ),
        "birth_time_known": has_birth_time,
        "birth_location_known": has_birth_location,
        "house_data_available": has_birth_time and has_birth_location,
        "birth_time_basis": (
            "local time with selected birthplace"
            if has_birth_location and has_birth_time
            else "UTC approximation without birthplace"
            if has_birth_time
            else "no time provided"
        ),
        "ascendant": None,
        "chart_ruler": None,
        "planets": [],
        "angles": [],
        "whole_sign_houses": [],
        "daily_sign_changes": daily_sign_changes or {},
    }

    # current calculation uses noon when the time is unknown
    # that does not establish a real rising sign or house placement
    # explicitly recording the uncertainty helps the model avoid
    # interpreting those temporary calculations as birth facts
    if has_birth_time and has_birth_location:
        rising_sign_index = chart.ascendant.sign_num

        context["ascendant"] = {
            "sign": sign_names[rising_sign_index],
            "degree": round(chart.ascendant.position, 2),
            "house": 1,
            "house_sign": sign_names[rising_sign_index],
            "house_topic": HOUSE_TOPICS[1],
        }

        if house_system == "whole_sign":
            context["chart_ruler"] = sign_rulers[rising_sign_index]
            context["whole_sign_houses"] = [
                {
                    "house": house_number,
                    "sign": sign_names[
                        (rising_sign_index + house_number - 1) % 12
                    ],
                    "sign_ruler": sign_rulers[
                        (rising_sign_index + house_number - 1) % 12
                    ],
                }
                for house_number in range(1, 13)
            ]
        for name in ("Ascendant", "Descendant", "Medium_Coeli", "Imum_Coeli"):
            angle = getattr(chart, name.lower())
            house_number = (
                (angle.sign_num - rising_sign_index) % 12 + 1
                if house_system == "whole_sign"
                else HOUSE_NUMBERS.get(getattr(angle, "house", None))
            )
            context["angles"].append({
                "name": name,
                "sign": sign_names[angle.sign_num],
                "degree": round(angle.position, 2),
                "house": house_number,
                "house_sign": (
                    sign_names[angle.sign_num]
                    if house_system == "whole_sign"
                    else None
                ),
                "house_topic": (
                    HOUSE_TOPICS[house_number]
                    if house_number
                    else None
                ),
            })

    for name in planet_names:
        # getattr(chart, "sun") is equivalent to chart.sun
        # using getattr lets the same code handle all seven planets
        planet = getattr(chart, name.lower())

        house = None

        if has_birth_time and has_birth_location:
            if house_system == "whole_sign":
                # whole-sign houses count signs from the rising sign.
                house = (
                    planet.sign_num - rising_sign_index
                ) % 12 + 1
            else:
                # placidus house numbers come from the ephemeris engine's
                # calculated point assignment, not from sign distance.
                house = HOUSE_NUMBERS.get(getattr(planet, "house", None))

        sign = sign_names[planet.sign_num]
        exalted_sign = exaltations[name]
        fall_sign = sign_names[(sign_names.index(exalted_sign) + 6) % 12]
        dignities = []
        if sign in own_signs[name]:
            dignities.append("own sign")
        if sign == exalted_sign:
            dignities.append("exalted")
        if sign == fall_sign:
            dignities.append("debilitated")
        whole_sign_house = (
            context["whole_sign_houses"][house - 1]
            if house and house_system == "whole_sign"
            else None
        )
        context["planets"].append({
            "name": name,
            "sign": sign,
            "degree": round(planet.position, 2),
            "house": house,
            "house_sign": whole_sign_house["sign"] if whole_sign_house else None,
            "house_topic": HOUSE_TOPICS[house] if house else None,
            "sign_ruler": sign_rulers[planet.sign_num],
            "essential_dignity": dignities,
            "house_strength": (
                "angular" if house and house in {1, 4, 7, 10}
                else "non-angular"
            ) if house else None,
            "possible_sign_change": (
                daily_sign_changes or {}
            ).get(name, []),
            "retrograde": planet.retrograde,
        })

    # the model needs the calculated placements
    # it does not need the person's name or raw birth location
    return context