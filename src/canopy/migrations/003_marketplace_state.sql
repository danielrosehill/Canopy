-- What the last pass saw of each marketplace: the commit its clone is at. A pass fetches a marketplace
-- again only when that moves. The list itself stays in agent-data/config/marketplaces.json.
CREATE TABLE catalogue.marketplaces (
    name TEXT PRIMARY KEY, repo TEXT NOT NULL, ref TEXT, head TEXT, fetched_at DOUBLE PRECISION);
