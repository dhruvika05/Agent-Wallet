import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(Path(__file__).parent / ".env")

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY"),
    http_options=types.HttpOptions(timeout=30000),  # 30 seconds, so it fails instead of hanging
)

print("Model in .env:", os.getenv("GEMINI_MODEL"))

print("\nModels your key can use:")
for m in client.models.list():
    if "generateContent" in (m.supported_actions or []):
        print(" ", m.name)

print("\nTest call:")
r = client.models.generate_content(model=os.getenv("GEMINI_MODEL"), contents="Say hello in 3 words.")
print(r.text)