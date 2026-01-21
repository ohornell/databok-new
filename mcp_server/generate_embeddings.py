#!/usr/bin/env python3
"""
Genererar embeddings för alla sections med Voyage AI.
Kör: python3 generate_embeddings.py
"""

import os
import time
import requests
from supabase import create_client

# Voyage API
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY")
if not VOYAGE_API_KEY:
    raise ValueError("VOYAGE_API_KEY måste vara satt i miljövariabler")
VOYAGE_MODEL = "voyage-4"  # 1024 dimensioner, bra balans kvalitet/kostnad

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL och SUPABASE_KEY måste vara satta i miljövariabler")


def get_voyage_embeddings(texts: list[str], max_retries: int = 5) -> list[list[float]]:
    """Hämta embeddings från Voyage AI API med retry-logik."""
    for attempt in range(max_retries):
        response = requests.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {VOYAGE_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": VOYAGE_MODEL,
                "input": texts,
                "input_type": "document"
            }
        )

        if response.status_code == 429:
            wait_time = 2 ** attempt * 5  # 5, 10, 20, 40, 80 sekunder
            print(f"    Rate limited, väntar {wait_time}s...")
            time.sleep(wait_time)
            continue

        response.raise_for_status()
        data = response.json()
        return [item["embedding"] for item in data["data"]]

    raise Exception("Max retries exceeded")


def main():
    print("Ansluter till Supabase...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Hämta alla sections utan embedding
    print("Hämtar sections utan embeddings...")
    result = supabase.table("sections").select("id, title, content").is_("embedding", "null").execute()
    sections = result.data

    if not sections:
        print("Alla sections har redan embeddings!")
        return

    print(f"Hittade {len(sections)} sections att processa")

    # Processa i batchar om 10 (Voyage limit är 128)
    batch_size = 10
    total_processed = 0

    for i in range(0, len(sections), batch_size):
        batch = sections[i:i + batch_size]

        # Kombinera title + content för bättre embedding
        texts = [f"{s['title']}\n\n{s['content']}" for s in batch]

        print(f"Genererar embeddings för batch {i // batch_size + 1}...")

        try:
            embeddings = get_voyage_embeddings(texts)

            # Uppdatera varje section med sin embedding
            for section, embedding in zip(batch, embeddings):
                supabase.table("sections").update({
                    "embedding": embedding
                }).eq("id", section["id"]).execute()
                total_processed += 1

            print(f"  [OK] {len(batch)} sections uppdaterade")

            # Paus mellan batchar för att undvika rate limit
            if i + batch_size < len(sections):
                time.sleep(1)

        except Exception as e:
            print(f"  [FEL] Fel vid batch {i // batch_size + 1}: {e}")
            raise

    print(f"\nKlart! {total_processed} sections har fått embeddings.")

    # Visa token-användning
    total_chars = sum(len(f"{s['title']}\n\n{s['content']}") for s in sections)
    estimated_tokens = total_chars // 4
    print(f"Uppskattad token-användning: ~{estimated_tokens:,} tokens")


if __name__ == "__main__":
    main()
