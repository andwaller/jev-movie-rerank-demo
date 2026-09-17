"""
Movie recall (Neo4j) + rerank (TypeSafe Jev) demo.

Pipeline:
  1. Cypher finds candidate movies that share actors/directors with a seed movie.
     This is the "recall" step -- fast, structural, no model involved.
  2. All candidates are packed into ONE Jev API call as separate Score questions.
     Jev evaluates them in parallel and returns a 0-4 fit score + confidence for
     each, with no generated prose -- this is the "rerank" step.

Env vars required:
  NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD   (Aura or local Movie sample graph)
  TYPESAFE_API_KEY

pip install neo4j requests
"""

import os
import requests
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI = os.environ["NEO4J_URI"]
NEO4J_USER = os.environ["NEO4J_USER"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]
TYPESAFE_API_KEY = os.environ["TYPESAFE_API_KEY"]

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"

SCORE_LEGEND = {
    "0": "Poor fit",
    "1": "Weak fit",
    "2": "Decent fit",
    "3": "Strong fit",
    "4": "Excellent fit",
}


def get_candidates(driver, seed_title: str, limit: int = 20):
    """Recall step: movies sharing cast/crew with the seed movie."""
    query = """
    MATCH (seed:Movie {title: $seedTitle})<-[:ACTED_IN|DIRECTED]-(p:Person)
          -[:ACTED_IN|DIRECTED]->(candidate:Movie)
    WHERE candidate <> seed
    WITH candidate, collect(DISTINCT p.name) AS sharedPeople,
         count(DISTINCT p) AS sharedCount
    RETURN candidate.title AS title,
           candidate.released AS released,
           candidate.tagline AS tagline,
           sharedPeople,
           sharedCount
    ORDER BY sharedCount DESC
    LIMIT $limit
    """
    with driver.session() as session:
        result = session.run(query, seedTitle=seed_title, limit=limit)
        return [dict(r) for r in result]


def build_jev_request(seed_title: str, candidates: list):
    """Rerank step: one call, one Score question per candidate."""
    questions = {}
    for i, c in enumerate(candidates):
        qid = f"candidate_{i}"
        shared = ", ".join(c["sharedPeople"][:5])
        questions[qid] = {
            "type": "score",
            "instructions": (
                f"A viewer loved the movie '{seed_title}'. Rate how well they "
                f"would likely enjoy '{c['title']}' ({c.get('released', 'n/a')}), "
                f"which shares these cast/crew members with the seed movie: {shared}. "
                f"Tagline: {c.get('tagline') or 'none'}."
            ),
            "criteria": list(SCORE_LEGEND.values()),
        }

    body = {
        "state": {
            "seed_movie": seed_title,
            "candidates": [c["title"] for c in candidates],
        },
        "model": JEV_MODEL,
        "questions": questions,
    }
    return body


def call_jev(body: dict):
    headers = {
        "Authorization": f"Bearer {TYPESAFE_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(JEV_ENDPOINT, json=body, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def rerank(seed_title: str, limit: int = 20):
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        candidates = get_candidates(driver, seed_title, limit=limit)
    finally:
        driver.close()

    if not candidates:
        print(f"No candidates found for '{seed_title}'. Check the title spelling.")
        return

    jev_body = build_jev_request(seed_title, candidates)
    jev_response = call_jev(jev_body)
    answers = jev_response.get("answers", {})

    ranked = []
    for i, c in enumerate(candidates):
        ans = answers.get(f"candidate_{i}", {})
        ranked.append({
            "title": c["title"],
            "shared_count": c["sharedCount"],
            "score": ans.get("score"),
            "confidence": ans.get("confidence"),
        })

    ranked.sort(key=lambda r: (r["score"] is not None, r["score"]), reverse=True)

    print(f"\nReranked recommendations for fans of '{seed_title}':\n")
    for r in ranked:
        print(
            f"  {r['title']:<40} score={r['score']} "
            f"confidence={r['confidence']} shared_people={r['shared_count']}"
        )


if __name__ == "__main__":
    rerank("The Matrix", limit=20)
