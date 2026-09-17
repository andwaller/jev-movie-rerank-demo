// Recall step (MovieLens version): find movies favored by the same enthusiastic
// raters as a seed movie ("people who loved this movie also loved..."), along
// with each candidate's genres for context in the rerank step.
// Run this in the Aura Query tab after loading MovieLens via import_movielens.cypher

MATCH (seed:Movie {title: "Toy Story (1995)"})
OPTIONAL MATCH (seed)-[:HAS_GENRE]->(sg:Genre)
WITH seed, collect(DISTINCT sg.name) AS seedGenres
MATCH (seed)<-[r1:RATED]-(u:User)
WHERE r1.rating >= 4.0
MATCH (u)-[r2:RATED]->(candidate:Movie)
WHERE candidate <> seed AND r2.rating >= 4.0
WITH seedGenres, candidate, count(DISTINCT u) AS sharedRaters, avg(r2.rating) AS avgRatingAmongSharedRaters
OPTIONAL MATCH (candidate)-[:HAS_GENRE]->(cg:Genre)
WITH seedGenres, candidate, sharedRaters, avgRatingAmongSharedRaters, collect(DISTINCT cg.name) AS candidateGenres
RETURN candidate.title AS title, sharedRaters, avgRatingAmongSharedRaters, candidateGenres, seedGenres
ORDER BY sharedRaters DESC
LIMIT 20;
