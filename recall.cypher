// Recall step: find movies that share actors or directors with a seed movie.
// Run this in the Aura Query tab (or Neo4j Browser) after loading the Movie sample dataset with :play movies

MATCH (seed:Movie {title: "The Matrix"})<-[:ACTED_IN|DIRECTED]-(p:Person)
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
LIMIT 20;
