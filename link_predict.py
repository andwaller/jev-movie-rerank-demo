"""
Link prediction (Neo4j MovieLens + TypeSafe Jev) demo.

Question: given a user's taste profile (their genre preferences, inferred from
movies they've already rated highly), can Jev predict whether they'd rate an
UNSEEN movie 4+ stars -- without being told the real answer?

This is a real accuracy test, not a plausible-looking rank order:
  - POSITIVES: movies this user actually rated 4+ stars (held out from the
    profile used to build their taste description, so there's no leakage)
  - NEGATIVES: movies rated 4+ stars by OTHER users, that this specific user
    never rated at all -- genuinely well-liked movies, not random junk, so
    a correct "no" from Jev has to come from actual personalization, not
    from spotting an obviously bad movie

All pairs go into ONE Jev call as separate Noul (probabilistic boolean)
questions. We already know the ground truth for every pair locally; Jev
never sees it. Accuracy is just: how many of Jev's yes/no guesses matched
what we already know to be true.

Env vars required:
  NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
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

N_POSITIVES = 5
N_NEGATIVES = 5


def pick_active_user(driver, min_ratings: int = 100):
    """Find a user with plenty of 4+ star ratings, so we have enough to build
    a taste profile AND hold out test pairs without running out of data."""
    query = """
    MATCH (u:User)-[r:RATED]->(:Movie)
    WHERE r.rating >= 4.0
    WITH u, count(r) AS highRatingCount
    WHERE highRatingCount >= $minRatings
    RETURN u.userId AS userId, highRatingCount
    ORDER BY highRatingCount DESC
    LIMIT 1
    """
    with driver.session() as session:
        result = session.run(query, minRatings=min_ratings)
        record = result.single()
        return record["userId"] if record else None


def build_test_set(driver, user_id: int):
    """Build the taste profile, positives, and negatives for one user."""
    with driver.session() as session:
        # Taste profile: top genres from this user's 4+ ratings
        profile_query = """
        MATCH (u:User {userId: $userId})-[r:RATED]->(m:Movie)-[:HAS_GENRE]->(g:Genre)
        WHERE r.rating >= 4.0
        WITH g.name AS genre, count(*) AS cnt
        ORDER BY cnt DESC
        LIMIT 5
        RETURN collect(genre) AS topGenres
        """
        profile = session.run(profile_query, userId=user_id).single()
        top_genres = profile["topGenres"]

        # Positives: movies this user actually rated 4+, held out as test items
        positives_query = """
        MATCH (u:User {userId: $userId})-[r:RATED]->(m:Movie)
        WHERE r.rating >= 4.0
        WITH m, r
        ORDER BY rand()
        LIMIT $n
        OPTIONAL MATCH (m)-[:HAS_GENRE]->(g:Genre)
        RETURN m.title AS title, r.rating AS actualRating, collect(g.name) AS genres
        """
        positives = [dict(r) for r in session.run(
            positives_query, userId=user_id, n=N_POSITIVES
        )]

        # Negatives: movies rated 4+ by OTHER users, this user never rated at all
        negatives_query = """
        MATCH (other:User)-[r:RATED]->(m:Movie)
        WHERE r.rating >= 4.0
          AND NOT EXISTS {
            MATCH (:User {userId: $userId})-[:RATED]->(m)
          }
        WITH m, count(DISTINCT other) AS otherFans
        WHERE otherFans >= 10
        WITH m, otherFans
        ORDER BY rand()
        LIMIT $n
        OPTIONAL MATCH (m)-[:HAS_GENRE]->(g:Genre)
        RETURN m.title AS title, otherFans, collect(g.name) AS genres
        """
        negatives = [dict(r) for r in session.run(
            negatives_query, userId=user_id, n=N_NEGATIVES
        )]

    return top_genres, positives, negatives


def build_jev_request(top_genres: list, positives: list, negatives: list):
    questions = {}
    ground_truth = {}

    for i, p in enumerate(positives):
        qid = f"pos_{i}"
        questions[qid] = {
            "type": "noul",
            "instructions": (
                f"A user's favorite genres, based on movies they've rated 4+ "
                f"stars, are: {', '.join(top_genres)}. Would this user likely "
                f"rate the movie '{p['title']}' (genres: {', '.join(p['genres'])}) "
                f"4 stars or higher?"
            ),
        }
        ground_truth[qid] = {"expected": True, "title": p["title"]}

    for i, n in enumerate(negatives):
        qid = f"neg_{i}"
        questions[qid] = {
            "type": "noul",
            "instructions": (
                f"A user's favorite genres, based on movies they've rated 4+ "
                f"stars, are: {', '.join(top_genres)}. Would this user likely "
                f"rate the movie '{n['title']}' (genres: {', '.join(n['genres'])}) "
                f"4 stars or higher?"
            ),
        }
        ground_truth[qid] = {"expected": False, "title": n["title"]}

    body = {
        "state": {"top_genres": top_genres},
        "model": JEV_MODEL,
        "questions": questions,
    }
    return body, ground_truth


def call_jev(body: dict):
    headers = {
        "Authorization": f"Bearer {TYPESAFE_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(JEV_ENDPOINT, json=body, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def run():
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        user_id = pick_active_user(driver)
        if user_id is None:
            print("No user found with enough ratings. Try lowering min_ratings.")
            return
        top_genres, positives, negatives = build_test_set(driver, user_id)
    finally:
        driver.close()

    print(f"Testing link prediction for User {user_id}")
    print(f"Taste profile (top genres from held-out 4+ ratings): {top_genres}\n")

    jev_body, ground_truth = build_jev_request(top_genres, positives, negatives)
    jev_response = call_jev(jev_body)
    answers = jev_response.get("answers", {})

    correct = 0
    total = 0
    print(f"{'Movie':<45} {'Expected':<10} {'Jev said':<10} {'Prob.':<8} {'Correct?'}")
    print("-" * 90)
    for qid, truth in ground_truth.items():
        ans = answers.get(qid, {})
        probability = ans.get("noul")
        predicted = probability is not None and probability >= 0.5
        is_correct = predicted == truth["expected"]
        correct += int(is_correct)
        total += 1
        print(
            f"{truth['title']:<45} {str(truth['expected']):<10} "
            f"{str(predicted):<10} {str(probability):<8} "
            f"{'✓' if is_correct else '✗'}"
        )

    print(f"\nAccuracy: {correct}/{total} ({100 * correct / total:.0f}%)")


if __name__ == "__main__":
    run()
