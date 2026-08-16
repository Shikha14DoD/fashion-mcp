CREATE TABLE garments (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  gender        TEXT,
  category      TEXT,
  article_type  TEXT,
  colour        TEXT,
  season        TEXT,
  usage_type    TEXT,
  price_cents   INTEGER NOT NULL,
  fabric        TEXT
);

CREATE TABLE inventory (
  garment_id INTEGER REFERENCES garments(id),
  size       TEXT,
  qty        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (garment_id, size)
);

CREATE TABLE users (
  id       TEXT PRIMARY KEY,
  api_key  TEXT UNIQUE NOT NULL
);

CREATE TABLE wishlist (
  user_id    TEXT REFERENCES users(id),
  garment_id INTEGER REFERENCES garments(id),
  added_at   TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (user_id, garment_id)
);

CREATE TABLE audit_log (
  id         BIGSERIAL PRIMARY KEY,
  user_id    TEXT,
  tool       TEXT NOT NULL,
  args       JSONB,
  result     TEXT,
  latency_ms INTEGER,
  at         TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_garments_search ON garments (article_type, colour, price_cents);
CREATE INDEX idx_audit_user ON audit_log (user_id, at DESC);