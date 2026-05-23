CREATE TABLE IF NOT EXISTS roles (
  id bigserial PRIMARY KEY,
  name text NOT NULL UNIQUE,
  description text NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS users (
  id bigserial PRIMARY KEY,
  username text NOT NULL UNIQUE,
  email text NOT NULL UNIQUE,
  firstname text NOT NULL,
  lastname text NOT NULL,
  password_salt text NOT NULL,
  password_hash text NOT NULL,
  role_id bigint NOT NULL REFERENCES roles(id) ON UPDATE CASCADE,
  is_blocked boolean NOT NULL DEFAULT false,
  must_change_password boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS llm_models (
  id bigserial PRIMARY KEY,
  name text NOT NULL UNIQUE,
  provider text NOT NULL,
  api_key text NOT NULL,
  endpoint text,
  temperature numeric(4,2) NOT NULL DEFAULT 0.70,
  max_tokens integer NOT NULL DEFAULT 4000,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS legal_documents (
  id bigserial PRIMARY KEY,
  source_file text NOT NULL UNIQUE,
  document_name text NOT NULL,
  code_name text NOT NULL DEFAULT '',
  language text NOT NULL DEFAULT '',
  source_format text NOT NULL DEFAULT 'json',
  source_path text NOT NULL DEFAULT '',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS legal_articles (
  id bigserial PRIMARY KEY,
  document_id bigint NOT NULL REFERENCES legal_documents(id) ON DELETE CASCADE ON UPDATE CASCADE,
  external_article_id text NOT NULL DEFAULT '',
  article_number text NOT NULL DEFAULT '',
  title text NOT NULL DEFAULT '',
  livre text NOT NULL DEFAULT '',
  titre text NOT NULL DEFAULT '',
  chapitre text NOT NULL DEFAULT '',
  section text NOT NULL DEFAULT '',
  sous_section text NOT NULL DEFAULT '',
  pages text NOT NULL DEFAULT '',
  content text NOT NULL DEFAULT '',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (document_id, article_number)
);

CREATE TABLE IF NOT EXISTS question_batches (
  id bigserial PRIMARY KEY,
  source_file text NOT NULL UNIQUE,
  document_id bigint REFERENCES legal_documents(id) ON DELETE SET NULL ON UPDATE CASCADE,
  model_name text NOT NULL DEFAULT '',
  questions_per_article integer NOT NULL DEFAULT 0,
  chunk_size integer NOT NULL DEFAULT 0,
  total_articles integer NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT '',
  stopped_reason text NOT NULL DEFAULT '',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS questions (
  id bigserial PRIMARY KEY,
  batch_id bigint REFERENCES question_batches(id) ON DELETE SET NULL ON UPDATE CASCADE,
  document_id bigint REFERENCES legal_documents(id) ON DELETE SET NULL ON UPDATE CASCADE,
  user_id bigint REFERENCES users(id) ON DELETE SET NULL ON UPDATE CASCADE,
  user_identifier text NOT NULL DEFAULT '',
  source_file text NOT NULL DEFAULT '',
  article_external_id text NOT NULL DEFAULT '',
  question_order integer NOT NULL DEFAULT 0,
  question_text text NOT NULL,
  language text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT '',
  origin text NOT NULL DEFAULT 'generated',
  filters jsonb NOT NULL DEFAULT '{}'::jsonb,
  sent_to_expert_at timestamptz,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reports (
  id bigserial PRIMARY KEY,
  report_uid text NOT NULL UNIQUE,
  title text NOT NULL,
  prompt text NOT NULL DEFAULT '',
  content_html text NOT NULL DEFAULT '',
  model_name text NOT NULL DEFAULT '',
  articles_count integer NOT NULL DEFAULT 0,
  vectorized_at timestamptz,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS report_articles (
  id bigserial PRIMARY KEY,
  report_id bigint NOT NULL REFERENCES reports(id) ON DELETE CASCADE ON UPDATE CASCADE,
  article_number text NOT NULL DEFAULT '',
  document_name text NOT NULL DEFAULT '',
  content text NOT NULL DEFAULT '',
  relevance integer NOT NULL DEFAULT 0,
  pages text NOT NULL DEFAULT '',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_questions_created_at ON questions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_questions_status ON questions (status);
CREATE INDEX IF NOT EXISTS idx_questions_user_id ON questions (user_id);
CREATE INDEX IF NOT EXISTS idx_questions_batch_id ON questions (batch_id);
CREATE INDEX IF NOT EXISTS idx_questions_document_id ON questions (document_id);
CREATE INDEX IF NOT EXISTS idx_question_batches_document_id ON question_batches (document_id);
CREATE INDEX IF NOT EXISTS idx_legal_articles_document_id ON legal_articles (document_id);
CREATE INDEX IF NOT EXISTS idx_report_articles_report_id ON report_articles (report_id);

CREATE OR REPLACE VIEW question_history_items AS
SELECT
  q.id,
  q.created_at,
  q.updated_at,
  q.question_text AS texte,
  q.language AS langue,
  q.status,
  q.origin,
  q.filters,
  q.sent_to_expert_at,
  q.source_file,
  q.article_external_id,
  q.question_order,
  q.user_identifier AS user,
  q.user_id,
  u.username,
  u.firstname,
  u.lastname,
  b.source_file AS batch_source_file,
  b.model_name AS batch_model_name,
  b.status AS batch_status
FROM questions q
LEFT JOIN users u ON u.id = q.user_id
LEFT JOIN question_batches b ON b.id = q.batch_id;

CREATE OR REPLACE VIEW questions_sent_to_expert AS
SELECT *
FROM question_history_items
WHERE lower(status) IN ('sent-to-expert', 'sent_to_expert', 'sent to expert');

CREATE OR REPLACE VIEW dashboard_counts AS
SELECT
  (SELECT count(*) FROM users) AS users_total,
  (SELECT count(*) FROM legal_documents) AS documents_total,
  (SELECT count(*) FROM legal_articles) AS articles_total,
  (SELECT count(*) FROM question_batches) AS question_batches_total,
  (SELECT count(*) FROM questions) AS questions_total,
  (SELECT count(*) FROM questions WHERE lower(status) IN ('sent-to-expert', 'sent_to_expert', 'sent to expert')) AS questions_sent_to_expert_total,
  (SELECT count(*) FROM reports) AS reports_total;