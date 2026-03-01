-- Migration: Add pgvector semantic search support
-- Run this in Supabase SQL Editor: https://supabase.com/dashboard/project/zezuhfgbfzgdylvvxhpl/sql/new

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS "vector" WITH SCHEMA extensions;

-- Content embeddings table
CREATE TABLE IF NOT EXISTS public.content_embeddings (
    id UUID PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    content_type TEXT NOT NULL,
    content_id UUID NOT NULL,
    question_id UUID NOT NULL REFERENCES public.questions(id) ON DELETE CASCADE,
    embedding extensions.vector(1536) NOT NULL,
    content_text TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    UNIQUE(content_type, content_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_content_embeddings_question_id ON public.content_embeddings(question_id);
CREATE INDEX IF NOT EXISTS idx_content_embeddings_type ON public.content_embeddings(content_type);
CREATE INDEX IF NOT EXISTS idx_content_embeddings_vector ON public.content_embeddings
    USING hnsw (embedding extensions.vector_cosine_ops);

-- RLS
ALTER TABLE public.content_embeddings ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY "Anyone can view embeddings" ON public.content_embeddings FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
    CREATE POLICY "Service can insert embeddings" ON public.content_embeddings FOR INSERT WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
    CREATE POLICY "Service can update embeddings" ON public.content_embeddings FOR UPDATE USING (true);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
    CREATE POLICY "Service can delete embeddings" ON public.content_embeddings FOR DELETE USING (true);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- RPC function for semantic search
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
