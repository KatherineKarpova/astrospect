import json
import re
from django.conf import settings
from openai import OpenAI
import logging

logger = logging.getLogger(__name__)

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

HOUSE_TOPICS = {
    1: "self, body, and approach",
    2: "resources, family, and speech",
    3: "effort, skills, and siblings",
    4: "home, roots, and foundations",
    5: "learning, creativity, and children",
    6: "work, service, and difficulties",
    7: "partnerships and agreements",
    8: "shared resources and major change",
    9: "teachers, meaning, and long journeys",
    10: "work, responsibility, and public role",
    11: "friends, networks, and gains",
    12: "rest, retreat, and letting go",
}

HOUSE_NAMES = (
    "First_House", "Second_House", "Third_House", "Fourth_House",
    "Fifth_House", "Sixth_House", "Seventh_House", "Eighth_House",
    "Ninth_House", "Tenth_House", "Eleventh_House", "Twelfth_House",
)
HOUSE_NUMBERS = {
    name: number for number, name in enumerate(HOUSE_NAMES, start=1)
}

PLACEMENT_SUMMARY_PROMPT = """Write a concise, personalized explanation of the supplied chart placement.
Use only the supplied chart facts and the approved ancient Jyotisha text(s).
Use the supplied zodiac and house system; never substitute or assume another.
When the zodiac is tropical, explain that applying Jyotisha concepts to it is a
modern adaptation, not a claim that the source prescribed tropical astrology.
When the zodiac is sidereal, name the supplied ayanamsa. Do not introduce
nakshatras, dashas, or unprovided chart facts. Describe astrology as historical
symbolism, not scientific prediction. Use plain, non-fatalistic language.
If the placement has "possible_sign_change" facts, explain that its sign is
uncertain because it may have changed during the UTC birth date. Use the
listed possible signs and do not present the noon-UTC sign as certain.

First identify the exact supplied placement by its name and sign; do not
interpret every placement as the Ascendant. Follow these rules for the named
placement:
- Ascendant: call it the rising sign (Lagna). Describe how its sign can color
  outward approach and first impression, as symbolism not a fixed personality
  fact. Explain its role using the supplied house system. Do not assign it
  planetary dignity.
- Descendant: explain the supplied sign as a traditional lens on partnership
  and one-to-one relating, and that it marks the 7th-house axis opposite Lagna.
- Medium_Coeli: explain the supplied sign as a lens on public contribution or
  vocation, and state its calculated house; do not assume it is in house 10.
- Imum_Coeli: explain the supplied sign as a lens on home, roots, and private
  foundations, and state its calculated house; do not assume it is in house 4.
- Planet: state what the named planet signifies in simple terms, then connect
  its supplied sign expression with its calculated house topic.
  Explain how those parts modify one another, then finish by stating its
  supplied sign condition only if the facts say own sign, exalted, or
  debilitated. Sign condition comes from the sign, not the house. If none
  applies, omit sign condition entirely. Never say "no special sign condition"
  or "other sign." Mention angularity only when supplied in the chart facts.
For every placement with a known house, use its exact supplied house number,
house sign, and house topic. Do not attribute Placidus-specific calculations
to the source text. Without a birthplace, state that houses and angles are
unavailable and that the time is a UTC approximation, or noon UTC if no time
was given. Mention possible sign changes supplied in the chart facts and do
not assert one sign as certain when a planet changed signs that UTC date.
Without a birth time but with a known birthplace, state that house is unknown.
Return only 1-3 concise, plain-language sentences as plain text. Do not return
JSON, quotation marks around the response, markdown fences, citations, or
source IDs."""


def _placement_summary_fallback(placement, chart_context):
    name = placement["name"]
    sign = placement["sign"]
    house = placement.get("house")
    topic = placement.get("house_topic")
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
    if house and topic:
        house_system = chart_context.get("house_system", "whole_sign")
        house_label = "whole-sign" if house_system == "whole_sign" else "Placidus"
        sentences.append(
            f"In {house_label} house {house}, the area of {topic}, those themes "
            "are especially connected with that part of life."
        )
    if dignities:
        sentences.append(
            f"Its sign condition is {', '.join(dignities)}."
        )
    return " ".join(sentences)


def summarize_placement(chart_context, placement):
    """todo:
    send only the selected placement and its calculation methods to the hosted
    model, then validate the output and use the local fallback if it is unusable.
    """
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
    if is_ascendant:
        target_chart_facts["houses"] = chart_context.get("houses", [])

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
                    "placement": placement,
                    "sources": SOURCES,
                }),
            },
        ],
        max_tokens=1500,
        extra_body={"reasoning": {"enabled": False}},
    )
    choice = completion.choices[0]
    raw_content = choice.message.content
    if not isinstance(raw_content, str) or not raw_content.strip():
        logger.warning(
            "Empty placement summary: finish_reason=%s usage=%s has_reasoning=%s",
            choice.finish_reason,
            completion.usage,
            bool(getattr(choice.message, "reasoning", None)),
        )
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
        summary = _placement_summary_fallback(placement, chart_context)

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
    """todo:
    keep recent history bounded to limit sensitive data sent to the provider,
    and validate every model field because hosted model output is untrusted.
    """

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
        "sources": SOURCES,
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

        # the policy requests JSON and the code validates it; skipping
        # provider-specific JSON mode keeps more hosted models compatible.
        # bound response length and the cost of an individual answer.
        max_tokens=2500,
        extra_body={"reasoning": {"enabled": False}},
    )

    choice = completion.choices[0]
    raw_content = choice.message.content

    if not raw_content:
        logger.warning(
            "Empty chat reply: finish_reason=%s usage=%s",
            choice.finish_reason,
            completion.usage,
        )
        raise ValueError("The AI did not return an answer. Please try again.")

    reply = parse_model_json(raw_content)
    if reply is None or not isinstance(reply.get("answer"), str):
        logger.warning("Unparseable chat reply: %r", raw_content[:200])
        reply = {"answer": raw_content.strip(), "reflection": "", "source_ids": []}

    reply.setdefault("reflection", "")
    if not isinstance(reply["reflection"], str):
        reply["reflection"] = ""
    source_ids = reply.get("source_ids")
    if not isinstance(source_ids, list):
        source_ids = []

    # keep only ids from the app's own collection and drop anything invented
    approved_sources = {source["id"]: source for source in SOURCES}
    source_ids = [
        source_id for source_id in source_ids
        if isinstance(source_id, str) and source_id in approved_sources
    ]
    reply["source_ids"] = source_ids
    reply["sources"] = [approved_sources[s] for s in dict.fromkeys(source_ids)]

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
    # Traditional Jyotisha sign conditions: own sign, exaltation, and fall.
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
        "ayanamsa": ayanamsa,
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
        "houses": [],
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

        context["chart_ruler"] = sign_rulers[rising_sign_index]
        if house_system == "whole_sign":
            houses = [
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
        else:
            # use kerykeion's cusps and assignments for placidus houses.
            houses = [
                {
                    "house": house_number,
                    "sign": sign_names[
                        getattr(chart, house_name.lower()).sign_num
                    ],
                    "sign_ruler": sign_rulers[
                        getattr(chart, house_name.lower()).sign_num
                    ],
                }
                for house_number, house_name in enumerate(HOUSE_NAMES, start=1)
            ]
        context["houses"] = houses
        if house_system == "whole_sign":
            context["whole_sign_houses"] = houses
        houses_by_number = {house["house"]: house for house in houses}
        for name in ("Ascendant", "Descendant", "Medium_Coeli", "Imum_Coeli"):
            angle = getattr(chart, name.lower())
            house_number = (
                (angle.sign_num - rising_sign_index) % 12 + 1
                if house_system == "whole_sign"
                else HOUSE_NUMBERS.get(angle.house)
            )
            house = houses_by_number.get(house_number)
            context["angles"].append({
                "name": name,
                "sign": sign_names[angle.sign_num],
                "degree": round(angle.position, 2),
                "house": house_number,
                "house_sign": house["sign"] if house else None,
                "house_topic": HOUSE_TOPICS[house_number] if house_number else None,
            })

    for name in planet_names:
        # getattr(chart, "sun") is equivalent to chart.sun
        # using getattr lets the same code handle all seven planets
        planet = getattr(chart, name.lower())

        house = None

        if has_birth_time and has_birth_location:
            if house_system == "whole_sign":
                house_number = (
                    planet.sign_num - rising_sign_index
                ) % 12 + 1
            else:
                house_number = HOUSE_NUMBERS.get(planet.house)
            house = houses_by_number.get(house_number)

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
        context["planets"].append({
            "name": name,
            "sign": sign,
            "degree": round(planet.position, 2),
            "house": house["house"] if house else None,
            "house_sign": house["sign"] if house else None,
            "house_topic": HOUSE_TOPICS[house["house"]] if house else None,
            "sign_ruler": sign_rulers[planet.sign_num],
            "essential_dignity": dignities,
            "house_strength": (
                "angular" if house and house["house"] in {1, 4, 7, 10}
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

def parse_model_json(raw):
    """Return the first JSON object found in the reply, or None."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            result, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict):
            return result
    return None