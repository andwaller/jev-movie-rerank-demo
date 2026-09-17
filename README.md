# Movie recall + rerank demo

Recall candidate movies with Cypher (shared actors/directors on the Neo4j Movie sample graph), then rerank the whole shortlist in one call to [Jev](https://typesafe.ai), TypeSafe's typed decision model. No generated prose, no per-candidate API calls.

Full walkthrough: [blog post link, once published]

## Requirements

- A free [Neo4j Aura](https://console.neo4j.io/?attribution=graphacademy) instance with the Movie sample dataset loaded (`:play movies` in the Aura Query tab)
- A [TypeSafe](https://typesafe.ai) API key

## Setup

```bash
git clone <this-repo-url>
cd jev-movie-rerank-demo
pip install -r requirements.txt
cp .env.example .env   # fill in your Aura and TypeSafe credentials
python rerank.py
```

## Files

- `rerank.py` — the full pipeline: recall via Cypher, rerank via one Jev call
- `recall.cypher` — the recall query on its own, to run directly in the Aura Query tab
- `.env.example` — connection details template

## How it works

1. `get_candidates()` runs a Cypher query that finds movies sharing cast or crew with a seed movie.
2. `build_jev_request()` turns every candidate into its own `Score` question in a single Jev request.
3. `call_jev()` sends one request; Jev evaluates every question in parallel and returns a score and confidence per candidate.
4. Candidates get sorted by score and printed.

Swap `"The Matrix"` at the bottom of `rerank.py` for any movie in your graph.
