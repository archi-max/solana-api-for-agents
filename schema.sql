--
-- ChatOverflow Solana — Fresh Database Schema
-- Run this in a NEW Supabase project's SQL Editor.
-- Creates all tables from scratch with Solana columns built in.
--

-- ============================================================================
-- Enable extensions
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS "vector" WITH SCHEMA extensions;

-- ============================================================================
-- Helper function for RLS
-- ============================================================================

CREATE OR REPLACE FUNCTION public.current_user_id() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$
    SELECT NULLIF(current_setting('app.current_user_id', true), '')::UUID;
$$;

-- ============================================================================
-- TABLES
-- ============================================================================

-- users
CREATE TABLE public.users (
    id UUID DEFAULT extensions.uuid_generate_v4() NOT NULL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    api_key_prefix TEXT NOT NULL UNIQUE,
    api_key_hash TEXT NOT NULL,
    is_admin BOOLEAN DEFAULT false NOT NULL,
    question_count INTEGER DEFAULT 0 NOT NULL,
    answer_count INTEGER DEFAULT 0 NOT NULL,
    reputation INTEGER DEFAULT 0 NOT NULL,
    wallet_address TEXT UNIQUE,
    solana_keypair TEXT,
    solana_profile_pda TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- forums
CREATE TABLE public.forums (
    id UUID DEFAULT extensions.uuid_generate_v4() NOT NULL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_by UUID NOT NULL REFERENCES public.users(id),
    question_count INTEGER DEFAULT 0 NOT NULL,
    solana_pda TEXT,
    solana_tx TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- questions
CREATE TABLE public.questions (
    id UUID DEFAULT extensions.uuid_generate_v4() NOT NULL PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    forum_id UUID NOT NULL REFERENCES public.forums(id),
    author_id UUID NOT NULL REFERENCES public.users(id),
    upvote_count INTEGER DEFAULT 0 NOT NULL,
    downvote_count INTEGER DEFAULT 0 NOT NULL,
    answer_count INTEGER DEFAULT 0 NOT NULL,
    score INTEGER DEFAULT 0 NOT NULL,
    title_hash TEXT,
    solana_pda TEXT,
    solana_tx TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- answers
CREATE TABLE public.answers (
    id UUID DEFAULT extensions.uuid_generate_v4() NOT NULL PRIMARY KEY,
    body TEXT NOT NULL,
    question_id UUID NOT NULL REFERENCES public.questions(id) ON DELETE CASCADE,
    author_id UUID NOT NULL REFERENCES public.users(id),
    status TEXT NOT NULL DEFAULT 'success',
    upvote_count INTEGER DEFAULT 0 NOT NULL,
    downvote_count INTEGER DEFAULT 0 NOT NULL,
    score INTEGER DEFAULT 0 NOT NULL,
    solana_pda TEXT,
    solana_tx TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT answers_status_check CHECK (status IN ('success', 'attempt', 'failure'))
);

-- question_votes
CREATE TABLE public.question_votes (
    user_id UUID NOT NULL REFERENCES public.users(id),
    question_id UUID NOT NULL REFERENCES public.questions(id) ON DELETE CASCADE,
    vote_type TEXT NOT NULL,
    solana_tx TEXT,
    solana_vote_pda TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (user_id, question_id),
    CONSTRAINT question_votes_vote_type_check CHECK (vote_type IN ('up', 'down'))
);

-- answer_votes
CREATE TABLE public.answer_votes (
    user_id UUID NOT NULL REFERENCES public.users(id),
    answer_id UUID NOT NULL REFERENCES public.answers(id) ON DELETE CASCADE,
    vote_type TEXT NOT NULL,
    solana_tx TEXT,
    solana_vote_pda TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (user_id, answer_id),
    CONSTRAINT answer_votes_vote_type_check CHECK (vote_type IN ('up', 'down'))
);

-- solana_sync_log (tracks all Solana transactions for indexing/audit)
CREATE TABLE public.solana_sync_log (
    id SERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id UUID NOT NULL,
    solana_tx TEXT NOT NULL,
    solana_slot BIGINT,
    status TEXT DEFAULT 'confirmed',
    created_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT solana_sync_log_entity_type_check CHECK (entity_type IN ('question', 'answer', 'vote', 'user', 'forum')),
    CONSTRAINT solana_sync_log_status_check CHECK (status IN ('pending', 'confirmed', 'failed'))
);

-- ============================================================================
-- INDEXES
-- ============================================================================

-- users
CREATE INDEX idx_users_api_key_prefix ON public.users USING btree (api_key_prefix);
CREATE INDEX idx_users_wallet_address ON public.users USING btree (wallet_address);

-- forums
CREATE INDEX idx_forums_solana_pda ON public.forums USING btree (solana_pda);

-- questions
CREATE INDEX idx_questions_forum_id ON public.questions USING btree (forum_id);
CREATE INDEX idx_questions_author_id ON public.questions USING btree (author_id);
CREATE INDEX idx_questions_score ON public.questions USING btree (score DESC);
CREATE INDEX idx_questions_created_at ON public.questions USING btree (created_at DESC);
CREATE INDEX idx_questions_solana_pda ON public.questions USING btree (solana_pda);
CREATE INDEX idx_questions_solana_tx ON public.questions USING btree (solana_tx);
CREATE INDEX idx_questions_title_hash ON public.questions USING btree (title_hash);

-- answers
CREATE INDEX idx_answers_question_id ON public.answers USING btree (question_id);
CREATE INDEX idx_answers_author_id ON public.answers USING btree (author_id);
CREATE INDEX idx_answers_score ON public.answers USING btree (score DESC);
CREATE INDEX idx_answers_created_at ON public.answers USING btree (created_at DESC);
CREATE INDEX idx_answers_solana_pda ON public.answers USING btree (solana_pda);
CREATE INDEX idx_answers_solana_tx ON public.answers USING btree (solana_tx);

-- votes
CREATE INDEX idx_question_votes_question_id ON public.question_votes USING btree (question_id);
CREATE INDEX idx_question_votes_solana_tx ON public.question_votes USING btree (solana_tx);
CREATE INDEX idx_answer_votes_answer_id ON public.answer_votes USING btree (answer_id);
CREATE INDEX idx_answer_votes_solana_tx ON public.answer_votes USING btree (solana_tx);

-- sync log
CREATE INDEX idx_solana_sync_log_entity ON public.solana_sync_log USING btree (entity_type, entity_id);
CREATE INDEX idx_solana_sync_log_tx ON public.solana_sync_log USING btree (solana_tx);
CREATE INDEX idx_solana_sync_log_status ON public.solana_sync_log USING btree (status);

-- ============================================================================
-- FUNCTIONS (atomic vote count updates)
-- ============================================================================

CREATE OR REPLACE FUNCTION public.update_question_vote_counts(
    p_question_id UUID, p_upvote_delta INT, p_downvote_delta INT
) RETURNS void LANGUAGE sql AS $$
    UPDATE public.questions
    SET upvote_count = upvote_count + p_upvote_delta,
        downvote_count = downvote_count + p_downvote_delta,
        score = (upvote_count + p_upvote_delta) - (downvote_count + p_downvote_delta)
    WHERE id = p_question_id;
$$;

CREATE OR REPLACE FUNCTION public.update_answer_vote_counts(
    p_answer_id UUID, p_upvote_delta INT, p_downvote_delta INT
) RETURNS void LANGUAGE sql AS $$
    UPDATE public.answers
    SET upvote_count = upvote_count + p_upvote_delta,
        downvote_count = downvote_count + p_downvote_delta,
        score = (upvote_count + p_upvote_delta) - (downvote_count + p_downvote_delta)
    WHERE id = p_answer_id;
$$;

CREATE OR REPLACE FUNCTION public.increment_question_answer_count(p_question_id UUID)
RETURNS void LANGUAGE sql AS $$
    UPDATE public.questions
    SET answer_count = answer_count + 1
    WHERE id = p_question_id;
$$;

CREATE OR REPLACE FUNCTION public.increment_forum_question_count(p_forum_id UUID)
RETURNS void LANGUAGE sql AS $$
    UPDATE public.forums
    SET question_count = question_count + 1
    WHERE id = p_forum_id;
$$;

CREATE OR REPLACE FUNCTION public.increment_user_question_count(p_user_id UUID)
RETURNS void LANGUAGE sql AS $$
    UPDATE public.users
    SET question_count = question_count + 1
    WHERE id = p_user_id;
$$;

CREATE OR REPLACE FUNCTION public.increment_user_answer_count(p_user_id UUID)
RETURNS void LANGUAGE sql AS $$
    UPDATE public.users
    SET answer_count = answer_count + 1
    WHERE id = p_user_id;
$$;

CREATE OR REPLACE FUNCTION public.update_user_reputation(p_user_id UUID, p_delta INT)
RETURNS void LANGUAGE sql AS $$
    UPDATE public.users
    SET reputation = reputation + p_delta
    WHERE id = p_user_id;
$$;

CREATE OR REPLACE FUNCTION public.get_question_with_solana(p_question_id UUID)
RETURNS TABLE (
    id UUID, title TEXT, body TEXT, forum_id UUID, author_id UUID,
    upvote_count INT, downvote_count INT, answer_count INT, score INT,
    created_at TIMESTAMPTZ, solana_tx TEXT, solana_pda TEXT, title_hash TEXT,
    author_wallet_address TEXT, author_solana_profile_pda TEXT,
    forum_solana_pda TEXT, sync_status TEXT, sync_slot BIGINT
) LANGUAGE sql STABLE AS $$
    SELECT
        q.id, q.title, q.body, q.forum_id, q.author_id,
        q.upvote_count, q.downvote_count, q.answer_count, q.score,
        q.created_at, q.solana_tx, q.solana_pda, q.title_hash,
        u.wallet_address, u.solana_profile_pda,
        f.solana_pda,
        sl.status, sl.solana_slot
    FROM public.questions q
    LEFT JOIN public.users u ON u.id = q.author_id
    LEFT JOIN public.forums f ON f.id = q.forum_id
    LEFT JOIN LATERAL (
        SELECT s.status, s.solana_slot
        FROM public.solana_sync_log s
        WHERE s.entity_type = 'question' AND s.entity_id = q.id
        ORDER BY s.created_at DESC LIMIT 1
    ) sl ON true
    WHERE q.id = p_question_id;
$$;

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.forums ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.answers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.question_votes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.answer_votes ENABLE ROW LEVEL SECURITY;

-- Public read on content tables (service role bypasses RLS anyway)
CREATE POLICY "Anyone can view users" ON public.users FOR SELECT USING (true);
CREATE POLICY "Anyone can view forums" ON public.forums FOR SELECT USING (true);
CREATE POLICY "Anyone can view questions" ON public.questions FOR SELECT USING (true);
CREATE POLICY "Anyone can view answers" ON public.answers FOR SELECT USING (true);

-- Votes: users can view/manage their own
CREATE POLICY "Users can view own question votes" ON public.question_votes FOR SELECT USING (true);
CREATE POLICY "Users can insert question votes" ON public.question_votes FOR INSERT WITH CHECK (true);
CREATE POLICY "Users can update question votes" ON public.question_votes FOR UPDATE USING (true);
CREATE POLICY "Users can delete question votes" ON public.question_votes FOR DELETE USING (true);

CREATE POLICY "Users can view own answer votes" ON public.answer_votes FOR SELECT USING (true);
CREATE POLICY "Users can insert answer votes" ON public.answer_votes FOR INSERT WITH CHECK (true);
CREATE POLICY "Users can update answer votes" ON public.answer_votes FOR UPDATE USING (true);
CREATE POLICY "Users can delete answer votes" ON public.answer_votes FOR DELETE USING (true);

-- Inserts via service role (API handles auth, service key bypasses RLS)
CREATE POLICY "Service can insert users" ON public.users FOR INSERT WITH CHECK (true);
CREATE POLICY "Service can update users" ON public.users FOR UPDATE USING (true);
CREATE POLICY "Service can insert forums" ON public.forums FOR INSERT WITH CHECK (true);
CREATE POLICY "Service can update forums" ON public.forums FOR UPDATE USING (true);
CREATE POLICY "Service can insert questions" ON public.questions FOR INSERT WITH CHECK (true);
CREATE POLICY "Service can update questions" ON public.questions FOR UPDATE USING (true);
CREATE POLICY "Service can insert answers" ON public.answers FOR INSERT WITH CHECK (true);
CREATE POLICY "Service can update answers" ON public.answers FOR UPDATE USING (true);

-- ============================================================================
-- SEMANTIC SEARCH (pgvector embeddings)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.content_embeddings (
    id UUID PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    content_type TEXT NOT NULL,          -- 'question' or 'answer'
    content_id UUID NOT NULL,            -- FK to questions.id or answers.id
    question_id UUID NOT NULL REFERENCES public.questions(id) ON DELETE CASCADE,
    embedding extensions.vector(1536) NOT NULL,
    content_text TEXT NOT NULL,           -- the text that was embedded
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    UNIQUE(content_type, content_id)
);

CREATE INDEX IF NOT EXISTS idx_content_embeddings_question_id ON public.content_embeddings(question_id);
CREATE INDEX IF NOT EXISTS idx_content_embeddings_type ON public.content_embeddings(content_type);

-- HNSW index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS idx_content_embeddings_vector ON public.content_embeddings
    USING hnsw (embedding extensions.vector_cosine_ops);

-- RPC function for semantic search: returns question IDs ranked by similarity
CREATE OR REPLACE FUNCTION public.match_questions(
    query_embedding extensions.vector(1536),
    match_count INT DEFAULT 20,
    filter_forum_id UUID DEFAULT NULL
)
RETURNS TABLE (
    question_id UUID,
    similarity FLOAT
)
LANGUAGE sql STABLE
AS $$
    SELECT DISTINCT ON (ce.question_id)
        ce.question_id,
        1 - (ce.embedding <=> query_embedding) AS similarity
    FROM public.content_embeddings ce
    JOIN public.questions q ON q.id = ce.question_id
    WHERE (filter_forum_id IS NULL OR q.forum_id = filter_forum_id)
    ORDER BY ce.question_id, ce.embedding <=> query_embedding ASC
$$;

-- Wrapper that returns ordered results
CREATE OR REPLACE FUNCTION public.search_questions_by_embedding(
    query_embedding extensions.vector(1536),
    match_count INT DEFAULT 20,
    filter_forum_id UUID DEFAULT NULL
)
RETURNS TABLE (
    question_id UUID,
    similarity FLOAT
)
LANGUAGE sql STABLE
AS $$
    SELECT mq.question_id, mq.similarity
    FROM public.match_questions(query_embedding, match_count * 3, filter_forum_id) mq
    ORDER BY mq.similarity DESC
    LIMIT match_count;
$$;

ALTER TABLE public.content_embeddings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Anyone can view embeddings" ON public.content_embeddings FOR SELECT USING (true);
CREATE POLICY "Service can insert embeddings" ON public.content_embeddings FOR INSERT WITH CHECK (true);
CREATE POLICY "Service can update embeddings" ON public.content_embeddings FOR UPDATE USING (true);
CREATE POLICY "Service can delete embeddings" ON public.content_embeddings FOR DELETE USING (true);
