-- DRAFT compiled serving model. Not an editable source of truth.
-- The compiler must enable foreign_keys and validate JSON schemas/references.
PRAGMA foreign_keys = ON;

CREATE TABLE metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL CHECK(json_valid(value_json)));
CREATE TABLE books (
  id TEXT PRIMARY KEY, revision TEXT NOT NULL, title TEXT NOT NULL,
  content_json TEXT NOT NULL CHECK(json_valid(content_json))
);
CREATE TABLE book_aliases (
  normalized_alias TEXT NOT NULL, edition_id TEXT NOT NULL REFERENCES books(id),
  PRIMARY KEY(normalized_alias, edition_id)
);
CREATE TABLE recipes (
  id TEXT PRIMARY KEY, revision TEXT NOT NULL,
  edition_id TEXT NOT NULL REFERENCES books(id), title TEXT NOT NULL,
  normalized_title TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('dish','component','beverage','unknown')),
  dish_type TEXT NOT NULL, classification_status TEXT NOT NULL,
  content_json TEXT NOT NULL CHECK(json_valid(content_json))
);
CREATE INDEX recipes_title ON recipes(normalized_title, id);
CREATE INDEX recipes_filters ON recipes(edition_id, kind, dish_type, id);
CREATE TABLE recipe_aliases (
  normalized_alias TEXT NOT NULL, recipe_id TEXT NOT NULL REFERENCES recipes(id),
  PRIMARY KEY(normalized_alias, recipe_id)
);
CREATE TABLE knowledge_items (
  id TEXT PRIMARY KEY, revision TEXT NOT NULL,
  edition_id TEXT NOT NULL REFERENCES books(id), kind TEXT NOT NULL, title TEXT NOT NULL,
  content_json TEXT NOT NULL CHECK(json_valid(content_json))
);
CREATE INDEX knowledge_kind ON knowledge_items(edition_id, kind, id);
CREATE TABLE knowledge_topics (
  item_id TEXT NOT NULL REFERENCES knowledge_items(id), topic TEXT NOT NULL,
  PRIMARY KEY(topic, item_id)
);
CREATE TABLE knowledge_recipes (
  item_id TEXT NOT NULL REFERENCES knowledge_items(id), recipe_id TEXT NOT NULL REFERENCES recipes(id),
  PRIMARY KEY(recipe_id, item_id)
);
CREATE TABLE ingredient_sources (
  rowid INTEGER PRIMARY KEY,
  recipe_id TEXT NOT NULL REFERENCES recipes(id), block_id TEXT NOT NULL,
  text TEXT NOT NULL, UNIQUE(recipe_id,block_id)
);
CREATE TABLE ingredient_occurrences (
  recipe_id TEXT NOT NULL REFERENCES recipes(id), block_id TEXT NOT NULL,
  group_id TEXT, identity TEXT, role TEXT NOT NULL, optional INTEGER,
  variant_scope TEXT, source_json TEXT NOT NULL CHECK(json_valid(source_json)),
  source_id INTEGER NOT NULL REFERENCES ingredient_sources(rowid),
  PRIMARY KEY(recipe_id, block_id), CHECK(optional IS NULL OR optional IN (0,1))
);
CREATE INDEX ingredient_lookup ON ingredient_occurrences(identity, recipe_id);
CREATE TABLE relationships (
  id TEXT PRIMARY KEY, source_entity TEXT NOT NULL, source_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL, target_entity TEXT, target_id TEXT,
  kind TEXT NOT NULL, disposition TEXT NOT NULL, choice_group TEXT,
  review_status TEXT NOT NULL,
  content_json TEXT NOT NULL CHECK(json_valid(content_json)),
  UNIQUE(source_entity, source_id, ordinal)
);
-- Polymorphic endpoints are checked by the compiler, not misleading SQL FKs.
CREATE INDEX relationship_source ON relationships(source_entity, source_id, ordinal);
-- Compiler-only consistency registry; canonical citations remain archived.
-- Edition references are explicitly checked because TEMP cannot FK main.books.
CREATE TEMP TABLE citations (
  id TEXT PRIMARY KEY, edition_id TEXT NOT NULL,
  content_json TEXT NOT NULL CHECK(json_valid(content_json))
) WITHOUT ROWID;
CREATE TABLE id_mappings (
  old_namespace TEXT NOT NULL, old_id TEXT NOT NULL, old_revision TEXT NOT NULL,
  status TEXT NOT NULL, targets_json TEXT NOT NULL CHECK(json_valid(targets_json)),
  PRIMARY KEY(old_namespace, old_id, old_revision)
);
CREATE TABLE search_chunks (
  rowid INTEGER PRIMARY KEY, chunk_id TEXT UNIQUE NOT NULL,
  entity TEXT NOT NULL, item_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
  title TEXT NOT NULL, text TEXT NOT NULL, text_sha256 TEXT NOT NULL,
  UNIQUE(entity, item_id, ordinal)
);
CREATE INDEX chunk_parent ON search_chunks(entity, item_id, ordinal);
-- Normal FTS tables intentionally duplicate only disposable search text.
-- Populate offline; no startup rebuilding or runtime writes.
CREATE VIRTUAL TABLE recipe_fts USING fts5(recipe_id UNINDEXED, title, aliases, ingredients, headnotes);
CREATE VIRTUAL TABLE knowledge_fts USING fts5(chunk_id UNINDEXED, title, text);

-- Candidate-only normalized occurrence index; final food identity uses the
-- accepted matcher. Contentless rows expose only stable SQLite row IDs.
CREATE VIRTUAL TABLE ingredient_fts USING fts5(text, content='', tokenize='unicode61 remove_diacritics 2');
