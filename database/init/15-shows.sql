-- First-start show registry, before episodes. Existing schemas use the documented migrations.
\connect radiofrance
SET ROLE radiofrance;
BEGIN;
CREATE TABLE IF NOT EXISTS shows (
 id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 slug text UNIQUE NOT NULL CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
 url text NOT NULL UNIQUE,
 enabled boolean NOT NULL DEFAULT true
);
INSERT INTO shows (slug, url) VALUES
 ('affaires-sensibles', 'https://www.radiofrance.fr/franceinter/podcasts/affaires-sensibles'),
 ('lsd-la-serie-documentaire', 'https://www.radiofrance.fr/franceculture/podcasts/lsd-la-serie-documentaire'),
 ('les-nuits-de-france-culture', 'https://www.radiofrance.fr/franceculture/podcasts/les-nuits-de-france-culture'),
 ('les-pieds-sur-terre', 'https://www.radiofrance.fr/franceculture/podcasts/les-pieds-sur-terre'),
 ('le-cours-de-l-histoire', 'https://www.radiofrance.fr/franceculture/podcasts/le-cours-de-l-histoire'),
 ('mecaniques-du-journalisme', 'https://www.radiofrance.fr/franceculture/podcasts/mecaniques-du-journalisme')
ON CONFLICT (slug) DO NOTHING;
COMMIT;
RESET ROLE;
