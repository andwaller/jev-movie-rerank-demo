"""
Movie recall (Neo4j, MovieLens ml-latest-small) + rerank (TypeSafe Jev) demo.

Pipeline:
  1. Cypher finds candidate movies favored by the same enthusiastic raters as a
     seed movie ("people who rated this 4+ stars also rated these 4+ stars"),
     collaborative-filtering style. This surfaces genuinely popular movies among
     that audience -- but popularity and "actually similar to the seed" are not
     the same thing, which is exactly the gap rerank exists to close.
  2. All candidates are packed into ONE Jev API call as separate Score questions,
     including each candidate's genres (and the seed's genres) so Jev has a fair
     shot at distinguishing "broadly beloved, wrong genre" from "actually similar."

Env vars required:
  NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD   (Aura instance with MovieLens loaded)
  TYPESAFE_API_KEY

pip install neo4j requests python-dotenv
"""

import os
import requests
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv(override=True)

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
    """Recall step: collaborative filtering -- movies favored by the same
    enthusiastic (4+ star) raters as the seed movie, with genres attached."""
    query = """
    MATCH (seed:Movie {title: $seedTitle})
    OPTIONAL MATCH (seed)-[:HAS_GENRE]->(sg:Genre)
    WITH seed, collect(DISTINCT sg.name) AS seedGenres
    MATCH (seed)<-[r1:RATED]-(u:User)
    WHERE r1.rating >= 4.0
    MATCH (u)-[r2:RATED]->(candidate:Movie)
    WHERE candidate <> seed AND r2.rating >= 4.0
    WITH seedGenres, candidate, count(DISTINCT u) AS sharedRaters,
         avg(r2.rating) AS avgRatingAmongSharedRaters
    OPTIONAL MATCH (candidate)-[:HAS_GENRE]->(cg:Genre)
    WITH seedGenres, candidate, sharedRaters, avgRatingAmongSharedRaters,
         collect(DISTINCT cg.name) AS candidateGenres
    RETURN candidate.title AS title, sharedRaters, avgRatingAmongSharedRaters,
           candidateGenres, seedGenres
    ORDER BY sharedRaters DESC
    LIMIT $limit
    """
    with driver.session() as session:
        result = session.run(query, seedTitle=seed_title, limit=limit)
        return [dict(r) for r in result]


def build_jev_request(seed_title: str, candidates: list):
    """Rerank step: one call, one Score question per candidate, genres included
    so Jev can judge actual similarity, not just shared-audience popularity."""
    seed_genres = candidates[0]["seedGenres"] if candidates else []
    questions = {}
    for i, c in enumerate(candidates):
        qid = f"candidate_{i}"
        candidate_genres = ", ".join(c["candidateGenres"]) or "unknown"
        questions[qid] = {
            "type": "score",
            "instructions": (
                f"A viewer loved the movie '{seed_title}' "
                f"(genres: {', '.join(seed_genres) or 'unknown'}). "
                f"{c['sharedRaters']} other users who also rated '{seed_title}' "
                f"4+ stars rated '{c['title']}' (genres: {candidate_genres}) "
                f"4+ stars too, averaging {c['avgRatingAmongSharedRaters']:.2f}. "
                f"Rate how well '{c['title']}' actually fits what someone who "
                f"specifically liked '{seed_title}' is looking for -- not just "
                f"whether it's a broadly well-liked movie."
            ),
            "criteria": list(SCORE_LEGEND.values()),
        }

    body = {
        "state": {
            "seed_movie": seed_title,
            "seed_genres": seed_genres,
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
        print(f"No candidates found for '{seed_title}'. Check the title spelling "
              f"and year, e.g. 'Toy Story (1995)'.")
        return

    jev_body = build_jev_request(seed_title, candidates)
    jev_response = call_jev(jev_body)
    answers = jev_response.get("answers", {})

    ranked = []
    for i, c in enumerate(candidates):
        ans = answers.get(f"candidate_{i}", {})
        ranked.append({
            "title": c["title"],
            "shared_raters": c["sharedRaters"],
            "genres": ", ".join(c["candidateGenres"]),
            "score": ans.get("score"),
            "confidence": ans.get("confidence"),
        })

    ranked.sort(key=lambda r: (r["score"] is not None, r["score"]), reverse=True)

    print(f"\nReranked recommendations for fans of '{seed_title}':\n")
    for r in ranked:
        print(
            f"  {r['title']:<55} score={r['score']} "
            f"confidence={r['confidence']} shared_raters={r['shared_raters']:<4} "
            f"genres={r['genres']}"
        )


if __name__ == "__main__":
    rerank("Toy Story (1995)", limit=20)
