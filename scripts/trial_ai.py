import time
from django.conf import settings
from openai import OpenAI

client = OpenAI(
    api_key=settings.AI_API_KEY,
    base_url=settings.AI_BASE_URL,
    timeout=60,
    max_retries=0,
)

PROMPT = "In three sentences, explain what a Sun sign is in astrology."


def trial(label, max_tokens=1500, **extra):
    start = time.time()
    try:
        r = client.chat.completions.create(
            model=settings.AI_MODEL,
            messages=[{"role": "user", "content": PROMPT}],
            max_tokens=max_tokens,
            **extra,
        )
    except Exception as error:
        print(f"{label}: ERROR {type(error).__name__}: {str(error)[:200]}")
        return
    choice = r.choices[0]
    details = r.usage.completion_tokens_details if r.usage else None
    reasoning = getattr(details, "reasoning_tokens", None)
    text = (choice.message.content or "")[:80]
    print(
        f"{label}: finish={choice.finish_reason} reasoning_tokens={reasoning} "
        f"seconds={time.time() - start:.1f} text={text!r}"
    )


trial("1 default, 1500 tokens")
trial("2 effort low", extra_body={"reasoning": {"effort": "low"}})
trial("3 reasoning off", extra_body={"reasoning": {"enabled": False}})
trial("4 default, 4000 tokens", max_tokens=4000)
