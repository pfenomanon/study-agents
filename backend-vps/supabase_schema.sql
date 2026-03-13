-- Supabase schema for Study Agents knowledge graph (vectors + KG)
-- Run this SQL once when provisioning a new project

create extension if not exists vector;

create table if not exists documents (
  id        text primary key,
  content   text not null,
  embedding vector(1536),
  meta      jsonb,
  created_at timestamptz default now()
);

create index if not exists documents_embedding_idx
  on documents using ivfflat (embedding vector_cosine_ops) with (lists = 100);

create or replace function match_documents(
  query_embedding vector(1536),
  match_threshold double precision default 0.2,
  match_count int default 8
) returns table (id text, content text, similarity double precision)
language sql stable as $$
  select d.id, d.content,
         1 - (d.embedding <=> query_embedding) as similarity
  from documents d
  where d.embedding is not null
    and 1 - (d.embedding <=> query_embedding) >= match_threshold
  order by d.embedding <=> query_embedding
  limit match_count
$$;

create table if not exists kg_nodes (
  id    text primary key,
  type  text,
  title text,
  group_id text,
  attrs jsonb default '{}',
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  embedding vector(1536)
);

create table if not exists kg_edges (
  id    bigserial primary key,
  src   text not null references kg_nodes(id) on delete cascade,
  rel   text not null,
  dst   text not null references kg_nodes(id) on delete cascade,
  group_id text,
  attrs jsonb,
  valid_at timestamptz,
  invalid_at timestamptz,
  created_at timestamptz default now(),
  expired_at timestamptz,
  episode_id text
);

create index if not exists kg_nodes_type_idx on kg_nodes(type);
create index if not exists kg_nodes_group_idx on kg_nodes(group_id);
create index if not exists kg_edges_src_idx  on kg_edges(src);
create index if not exists kg_edges_dst_idx  on kg_edges(dst);
create index if not exists kg_edges_rel_idx  on kg_edges(rel);
create index if not exists kg_edges_valid_idx on kg_edges(valid_at);

create table if not exists kg_episodes (
  episode_id text primary key,
  source text,
  source_type text,
  group_id text,
  reference_time timestamptz,
  tags text[],
  metadata jsonb,
  raw_content text,
  created_at timestamptz default now()
);
