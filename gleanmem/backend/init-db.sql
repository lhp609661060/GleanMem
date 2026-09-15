-- Extensions and zhparser text search configuration only.
-- Tables, trigger functions and triggers are managed by Alembic migrations
-- (see alembic/versions/edac4c069c8c_initial_all_5_tables.py).

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS zhparser;

DO $$ BEGIN
    CREATE TEXT SEARCH CONFIGURATION zhparser (PARSER = zhparser);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Map common Chinese part-of-speech tokens to the simple dictionary
ALTER TEXT SEARCH CONFIGURATION zhparser ADD MAPPING FOR n,v,a,i,e,l,j WITH simple;
