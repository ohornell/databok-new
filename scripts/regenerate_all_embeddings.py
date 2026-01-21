#!/usr/bin/env python3
"""
Regenererar ALLA embeddings med voyage-4.
Uppdaterar både sections och knowledge-tabellerna.

Kör: python3 scripts/regenerate_all_embeddings.py
"""

import os
import time
import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# Voyage API
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY")
if not VOYAGE_API_KEY:
    raise ValueError("VOYAGE_API_KEY måste vara satt i miljövariabler")
VOYAGE_MODEL = "voyage-4"

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL och SUPABASE_KEY måste vara satta i miljövariabler")


def get_voyage_embedding(text: str, max_retries: int = 5) -> list[float]:
    """Hämta embedding för en text från Voyage AI API."""
    for attempt in range(max_retries):
        try:
            response = requests.post(
                "https://api.voyageai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {VOYAGE_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": VOYAGE_MODEL,
                    "input": [text],
                    "input_type": "document"
                },
                timeout=30
            )

            if response.status_code == 429:
                wait_time = 2 ** attempt * 5
                print(f"    Rate limited, väntar {wait_time}s...")
                time.sleep(wait_time)
                continue

            response.raise_for_status()
            return response.json()["data"][0]["embedding"]

        except Exception as e:
            if attempt < max_retries - 1:
                print(f"    Fel: {e}, försöker igen...")
                time.sleep(2)
            else:
                raise

    raise Exception("Max retries exceeded")


def regenerate_sections(supabase):
    """Regenerera embeddings för alla sections."""
    print("\n=== SECTIONS ===")

    result = supabase.table("sections").select("id, title, content").execute()
    sections = result.data

    if not sections:
        print("Inga sections hittades")
        return 0

    print(f"Hittade {len(sections)} sections att regenerera")

    for i, section in enumerate(sections):
        text = f"{section['title']}\n\n{section['content']}"

        try:
            embedding = get_voyage_embedding(text)

            supabase.table("sections").update({
                "embedding": embedding
            }).eq("id", section["id"]).execute()

            print(f"  [{i+1}/{len(sections)}] {section['title'][:50]}...")

            # Paus för att undvika rate limit
            time.sleep(0.5)

        except Exception as e:
            print(f"  [FEL] {section['title']}: {e}")

    return len(sections)


def regenerate_knowledge(supabase):
    """Regenerera embeddings för alla knowledge-poster."""
    print("\n=== KNOWLEDGE ===")

    result = supabase.table("knowledge").select("id, title, content").execute()
    knowledge = result.data

    if not knowledge:
        print("Inga knowledge-poster hittades")
        return 0

    print(f"Hittade {len(knowledge)} knowledge-poster att regenerera")

    for i, post in enumerate(knowledge):
        text = f"{post['title']}\n\n{post['content']}"

        try:
            embedding = get_voyage_embedding(text)

            supabase.table("knowledge").update({
                "embedding": embedding
            }).eq("id", post["id"]).execute()

            print(f"  [{i+1}/{len(knowledge)}] {post['title'][:50]}...")

            # Paus för att undvika rate limit
            time.sleep(0.5)

        except Exception as e:
            print(f"  [FEL] {post['title']}: {e}")

    return len(knowledge)


def main():
    print(f"Regenererar alla embeddings med {VOYAGE_MODEL}")
    print("=" * 50)

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    sections_count = regenerate_sections(supabase)
    knowledge_count = regenerate_knowledge(supabase)

    print("\n" + "=" * 50)
    print(f"KLART!")
    print(f"  Sections: {sections_count}")
    print(f"  Knowledge: {knowledge_count}")
    print(f"  Totalt: {sections_count + knowledge_count} embeddings regenererade med {VOYAGE_MODEL}")


if __name__ == "__main__":
    main()
