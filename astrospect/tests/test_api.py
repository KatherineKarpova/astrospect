import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    base_url=os.getenv("AI_BASE_URL"),
    api_key=os.getenv("AI_API_KEY"),
)

response = client.chat.completions.create(
    model=os.getenv("AI_MODEL"),
    messages=[{"role": "user", "content": "Say hello"}],
)
print(response.choices[0].message.content)