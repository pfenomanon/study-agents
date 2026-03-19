-- Supabase schema for Study Agents knowledge graph (vectors + KG + profiles)
-- Run this SQL once when provisioning a new project

create extension if not exists vector;

create table if not exists profiles (
  profile_id text primary key,
  name text not null,
  summary_manual text,
  summary_auto text,
  prompt_profile_name text,
  tags text[] default '{}',
  status text not null default 'active' check (status in ('active', 'archived', 'merged')),
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists profile_aliases (
  alias_profile_id text primary key,
  canonical_profile_id text not null references profiles(profile_id) on delete cascade,
  reason text,
  created_at timestamptz default now()
);

create table if not exists profile_merges (
  merge_id bigserial primary key,
  source_profile_id text not null,
  target_profile_id text not null,
  dry_run boolean not null default false,
  details jsonb,
  merged_by text,
  created_at timestamptz default now()
);

create table if not exists documents (
  id         text primary key,
  content    text not null,
  embedding  vector(1536),
  group_id   text,
  profile_id text,
  meta       jsonb,
  created_at timestamptz default now()
);

-- Backfill legacy deployments where documents existed before group/profile columns.
alter table if exists documents add column if not exists group_id text;
alter table if exists documents add column if not exists profile_id text;
alter table if exists kg_nodes add column if not exists group_id text;
alter table if exists kg_nodes add column if not exists profile_id text;
alter table if exists kg_edges add column if not exists group_id text;
alter table if exists kg_edges add column if not exists profile_id text;
alter table if exists kg_episodes add column if not exists group_id text;
alter table if exists kg_episodes add column if not exists profile_id text;

create index if not exists documents_embedding_idx
  on documents using ivfflat (embedding vector_cosine_ops) with (lists = 100);

create index if not exists documents_group_idx on documents(group_id);
create index if not exists documents_profile_idx on documents(profile_id);

create or replace function infer_profile_id(input_group_id text)
returns text
language plpgsql
immutable
as $$
declare
  raw text;
begin
  raw := coalesce(trim(input_group_id), '');
  if raw = '' then
    return null;
  end if;

  if raw like 'profile:%' then
    return nullif(split_part(raw, ':', 2), '');
  end if;

  if position(':' in raw) > 0 then
    return nullif(split_part(raw, ':', 1), '');
  end if;

  return raw;
end;
$$;

create or replace function match_documents(
  query_embedding vector(1536),
  match_threshold double precision default 0.2,
  match_count int default 8,
  group_prefix text default null,
  profile_filter text default null
) returns table (id text, content text, similarity double precision, meta jsonb)
language sql stable as $$
  select d.id,
         d.content,
         1 - (d.embedding <=> query_embedding) as similarity,
         d.meta
  from documents d
  where d.embedding is not null
    and (group_prefix is null OR (d.group_id is not null AND d.group_id LIKE group_prefix || '%'))
    and (profile_filter is null OR d.profile_id = profile_filter)
    and 1 - (d.embedding <=> query_embedding) >= match_threshold
  order by d.embedding <=> query_embedding
  limit match_count
$$;

create table if not exists kg_nodes (
  id         text primary key,
  type       text,
  title      text,
  group_id   text,
  profile_id text,
  attrs      jsonb default '{}',
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  embedding  vector(1536)
);

create table if not exists kg_edges (
  id         bigserial primary key,
  src        text not null references kg_nodes(id) on delete cascade,
  rel        text not null,
  dst        text not null references kg_nodes(id) on delete cascade,
  group_id   text,
  profile_id text,
  attrs      jsonb,
  valid_at   timestamptz,
  invalid_at timestamptz,
  created_at timestamptz default now(),
  expired_at timestamptz,
  episode_id text
);

create index if not exists kg_nodes_type_idx on kg_nodes(type);
create index if not exists kg_nodes_group_idx on kg_nodes(group_id);
create index if not exists kg_nodes_profile_idx on kg_nodes(profile_id);
create index if not exists kg_edges_src_idx  on kg_edges(src);
create index if not exists kg_edges_dst_idx  on kg_edges(dst);
create index if not exists kg_edges_rel_idx  on kg_edges(rel);
create index if not exists kg_edges_valid_idx on kg_edges(valid_at);
create index if not exists kg_edges_profile_idx on kg_edges(profile_id);

create table if not exists kg_episodes (
  episode_id   text primary key,
  source       text,
  source_type  text,
  group_id     text,
  profile_id   text,
  reference_time timestamptz,
  tags         text[],
  metadata     jsonb,
  raw_content  text,
  created_at   timestamptz default now()
);

create index if not exists kg_episodes_profile_idx on kg_episodes(profile_id);

create table if not exists artifacts (
  artifact_id text primary key,
  profile_id text not null references profiles(profile_id) on delete restrict,
  agent text not null,
  artifact_type text not null,
  path text not null,
  source_ids text[] default '{}',
  run_id text,
  metadata jsonb default '{}',
  created_at timestamptz default now()
);

create index if not exists artifacts_profile_idx on artifacts(profile_id);
create index if not exists artifacts_created_idx on artifacts(created_at desc);

insert into profiles (profile_id, name)
select distinct p.profile_id, p.profile_id
from (
  select profile_id from documents where profile_id is not null
  union
  select infer_profile_id(group_id) as profile_id from documents where group_id is not null
  union
  select profile_id from kg_nodes where profile_id is not null
  union
  select infer_profile_id(group_id) as profile_id from kg_nodes where group_id is not null
  union
  select profile_id from kg_edges where profile_id is not null
  union
  select infer_profile_id(group_id) as profile_id from kg_edges where group_id is not null
  union
  select profile_id from kg_episodes where profile_id is not null
  union
  select infer_profile_id(group_id) as profile_id from kg_episodes where group_id is not null
) p
where p.profile_id is not null
on conflict (profile_id) do nothing;

create or replace view profile_rollups as
with
  d as (
    select
      coalesce(profile_id, infer_profile_id(group_id)) as profile_id,
      count(*)::bigint as doc_count,
      max(created_at) as docs_last_at
    from documents
    where coalesce(profile_id, infer_profile_id(group_id)) is not null
    group by 1
  ),
  n as (
    select
      coalesce(profile_id, infer_profile_id(group_id)) as profile_id,
      count(*)::bigint as node_count,
      max(created_at) as nodes_last_at
    from kg_nodes
    where coalesce(profile_id, infer_profile_id(group_id)) is not null
    group by 1
  ),
  e as (
    select
      coalesce(profile_id, infer_profile_id(group_id)) as profile_id,
      count(*)::bigint as edge_count,
      max(created_at) as edges_last_at
    from kg_edges
    where coalesce(profile_id, infer_profile_id(group_id)) is not null
    group by 1
  ),
  ep as (
    select
      coalesce(profile_id, infer_profile_id(group_id)) as profile_id,
      count(*)::bigint as episode_count,
      max(created_at) as episodes_last_at
    from kg_episodes
    where coalesce(profile_id, infer_profile_id(group_id)) is not null
    group by 1
  ),
  a as (
    select profile_id, count(*)::bigint as artifact_count, max(created_at) as artifacts_last_at
    from artifacts
    where profile_id is not null
    group by profile_id
  )
select
  p.profile_id,
  coalesce(d.doc_count, 0) as doc_count,
  coalesce(n.node_count, 0) as node_count,
  coalesce(e.edge_count, 0) as edge_count,
  coalesce(ep.episode_count, 0) as episode_count,
  coalesce(a.artifact_count, 0) as artifact_count,
  greatest(
    coalesce(d.docs_last_at, to_timestamp(0)),
    coalesce(n.nodes_last_at, to_timestamp(0)),
    coalesce(e.edges_last_at, to_timestamp(0)),
    coalesce(ep.episodes_last_at, to_timestamp(0)),
    coalesce(a.artifacts_last_at, to_timestamp(0)),
    coalesce(p.updated_at, to_timestamp(0)),
    coalesce(p.created_at, to_timestamp(0))
  ) as last_activity
from profiles p
left join d on d.profile_id = p.profile_id
left join n on n.profile_id = p.profile_id
left join e on e.profile_id = p.profile_id
left join ep on ep.profile_id = p.profile_id
left join a on a.profile_id = p.profile_id;

create or replace view profile_catalog as
select
  p.profile_id,
  p.name,
  coalesce(nullif(p.summary_manual, ''), nullif(p.summary_auto, ''), 'No summary available yet.') as summary,
  p.status,
  p.prompt_profile_name,
  p.tags,
  r.doc_count,
  r.node_count,
  r.edge_count,
  r.episode_count,
  r.artifact_count,
  r.last_activity,
  p.created_at,
  p.updated_at
from profiles p
left join profile_rollups r on r.profile_id = p.profile_id;
