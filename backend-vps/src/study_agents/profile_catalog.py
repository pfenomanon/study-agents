from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from .profile_namespace import normalize_profile_id
from .supabase_client import create_supabase_client

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProfileSummary:
    profile_id: str
    name: str
    summary: str
    status: str
    doc_count: int
    node_count: int
    edge_count: int
    episode_count: int
    artifact_count: int
    last_activity: Optional[str]
    prompt_profile_name: Optional[str]
    tags: list[str]


class ProfileCatalogService:
    """Supabase-backed profile catalog for list/show/create flows."""

    def __init__(self, client: Any | None = None):
        self.client = client or create_supabase_client()
        self._schema_checked = False
        self._legacy_rollup_cache: dict[str, dict[str, Any]] = {}

    def _require_schema(self) -> None:
        if self._schema_checked:
            return
        try:
            self.client.table("profiles").select("profile_id").limit(1).execute()
            self.client.table("profile_catalog").select("profile_id").limit(1).execute()
        except Exception as exc:
            raise RuntimeError(
                "Profile catalog schema is missing. Apply "
                "supabase_schema.sql or supabase/migrations/202603150001_profile_catalog.sql."
            ) from exc
        self._schema_checked = True

    @staticmethod
    def _resp_data(resp: Any) -> Any:
        if resp is None:
            return None
        return getattr(resp, "data", None)

    def resolve_alias(self, profile_id: str) -> str:
        self._require_schema()
        normalized = normalize_profile_id(profile_id)
        try:
            alias_resp = (
                self.client.table("profile_aliases")
                .select("canonical_profile_id")
                .eq("alias_profile_id", normalized)
                .limit(1)
                .execute()
            )
            alias_data = self._resp_data(alias_resp)
            if isinstance(alias_data, list):
                alias_data = alias_data[0] if alias_data else {}
            alias_data = alias_data or {}
            if isinstance(alias_data, dict) and alias_data.get("canonical_profile_id"):
                return normalize_profile_id(alias_data["canonical_profile_id"])
        except Exception:
            # Alias table may not exist yet in older deployments.
            return normalized
        return normalized

    def ensure_profile(
        self,
        profile_id: str,
        *,
        name: str | None = None,
        summary: str | None = None,
        prompt_profile_name: str | None = None,
        tags: list[str] | None = None,
        status: str = "active",
    ) -> dict[str, Any]:
        self._require_schema()
        normalized = normalize_profile_id(profile_id)
        merged_tags = self._merge_tags_for_profile(normalized, tags) if tags is not None else None
        payload: dict[str, Any] = {
            "profile_id": normalized,
            "name": (name or normalized).strip() or normalized,
            "status": status,
        }
        if summary is not None:
            payload["summary_manual"] = summary
        if prompt_profile_name is not None:
            payload["prompt_profile_name"] = prompt_profile_name
        if merged_tags is not None:
            payload["tags"] = merged_tags

        self.client.table("profiles").upsert(payload).execute()
        return self.get_profile(normalized, include_recent=False)

    def list_profiles(
        self,
        *,
        limit: int = 100,
        include_inactive: bool = False,
        include_inferred: bool = False,
        active_profile_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self._require_schema()
        query = self.client.table("profile_catalog").select("*")
        if not include_inactive:
            query = query.eq("status", "active")
        response = query.order("last_activity", desc=True).limit(max(1, min(limit, 500))).execute()
        rows = self._resp_data(response) or []
        shaped = [self._shape_catalog_row(row) for row in rows]
        self._repair_legacy_rollups(shaped)
        if include_inferred:
            return shaped

        filtered = [row for row in shaped if self._is_user_created_profile(row)]
        active = normalize_profile_id(active_profile_id) if active_profile_id else None
        if active and all(row.get("profile_id") != active for row in filtered):
            try:
                filtered.insert(0, self.get_profile(active, include_recent=False))
            except Exception:
                pass
        return filtered

    def get_profile(self, profile_id: str, *, include_recent: bool = True) -> dict[str, Any]:
        self._require_schema()
        canonical = self.resolve_alias(profile_id)
        row_resp = (
            self.client.table("profile_catalog")
            .select("*")
            .eq("profile_id", canonical)
            .limit(1)
            .execute()
        )
        row_data = self._resp_data(row_resp)
        row = row_data[0] if isinstance(row_data, list) and row_data else row_data

        if not row:
            profile_resp = (
                self.client.table("profiles")
                .select("*")
                .eq("profile_id", canonical)
                .limit(1)
                .execute()
            )
            profile_data = self._resp_data(profile_resp)
            profile = (
                profile_data[0]
                if isinstance(profile_data, list) and profile_data
                else profile_data
            )
            if not profile:
                raise KeyError(f"Unknown profile: {canonical}")
            shaped = {
                "profile_id": canonical,
                "name": profile.get("name") or canonical,
                "summary": profile.get("summary_manual") or profile.get("summary_auto") or "",
                "status": profile.get("status") or "active",
                "doc_count": 0,
                "node_count": 0,
                "edge_count": 0,
                "episode_count": 0,
                "artifact_count": 0,
                "last_activity": None,
                "prompt_profile_name": profile.get("prompt_profile_name"),
                "tags": profile.get("tags") or [],
            }
        else:
            shaped = self._shape_catalog_row(row)
            self._repair_legacy_rollups([shaped])

        shaped["canonical_profile_id"] = canonical
        shaped["requested_profile_id"] = normalize_profile_id(profile_id)
        shaped["alias_resolved"] = shaped["requested_profile_id"] != canonical

        if include_recent:
            recent_resp = (
                self.client.table("artifacts")
                .select("artifact_id,agent,artifact_type,path,created_at,metadata")
                .eq("profile_id", canonical)
                .order("created_at", desc=True)
                .limit(20)
                .execute()
            )
            recent = self._resp_data(recent_resp) or []
            shaped["recent_artifacts"] = recent

        return shaped

    def record_artifact(
        self,
        *,
        profile_id: str,
        agent: str,
        artifact_type: str,
        path: str,
        run_id: str | None = None,
        source_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            self._require_schema()
            canonical = self.resolve_alias(profile_id)
        except Exception as exc:
            logger.warning("Profile schema unavailable; skipping artifact record: %s", exc)
            return
        artifact_id = f"ART:{canonical}:{agent}:{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
        payload = {
            "artifact_id": artifact_id,
            "profile_id": canonical,
            "agent": agent,
            "artifact_type": artifact_type,
            "path": path,
            "run_id": run_id,
            "source_ids": source_ids or [],
            "metadata": metadata or {},
        }
        try:
            self.client.table("artifacts").insert(payload).execute()
        except Exception as exc:
            logger.warning("Failed to record artifact for profile %s: %s", canonical, exc)

    def list_domain_wizard_history(
        self,
        profile_id: str,
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        """
        Return historical domain-wizard runs from artifacts for a profile.
        """
        self._require_schema()
        requested = normalize_profile_id(profile_id)
        canonical = self.resolve_alias(requested)
        profile_resp = (
            self.client.table("profiles")
            .select("profile_id,prompt_profile_name")
            .eq("profile_id", canonical)
            .limit(1)
            .execute()
        )
        profile_rows = self._resp_data(profile_resp) or []
        profile = profile_rows[0] if profile_rows else None
        if not profile:
            raise KeyError(f"Unknown profile: {canonical}")

        rows_resp = (
            self.client.table("artifacts")
            .select(
                "artifact_id,profile_id,agent,artifact_type,path,run_id,source_ids,metadata,created_at"
            )
            .eq("profile_id", canonical)
            .eq("agent", "domain_profile_agent")
            .eq("artifact_type", "prompt_profile_bundle")
            .order("created_at", desc=True)
            .limit(max(1, min(limit, 500)))
            .execute()
        )
        rows = self._resp_data(rows_resp) or []

        history: list[dict[str, Any]] = []
        for row in rows:
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            history.append(
                {
                    "artifact_id": row.get("artifact_id"),
                    "created_at": row.get("created_at"),
                    "path": row.get("path"),
                    "run_id": row.get("run_id"),
                    "source_ids": row.get("source_ids") or [],
                    "exit_code": metadata.get("exit_code"),
                    "generated_targets": metadata.get("generated_targets") or {},
                    "prompt_files": metadata.get("prompt_files") or [],
                    "rolled_back": bool(metadata.get("rolled_back", False)),
                    "profile_path": metadata.get("profile_path"),
                    "metadata": metadata,
                }
            )

        return {
            "profile_id": canonical,
            "requested_profile_id": requested,
            "alias_resolved": requested != canonical,
            "prompt_profile_name": profile.get("prompt_profile_name"),
            "history": history,
        }

    def purge_profile_data(
        self,
        profile_id: str,
        *,
        dry_run: bool = True,
        include_artifacts: bool = False,
    ) -> dict[str, Any]:
        """
        Purge profile-scoped knowledge rows from Supabase tables.

        Deletions are scoped to:
        - explicit profile_id match
        - legacy group_id prefix match (`profile:<id>:%`) for older rows
        """
        self._require_schema()
        requested = normalize_profile_id(profile_id)
        if not requested:
            raise ValueError("profile_id is required")
        canonical = self.resolve_alias(requested)
        group_prefix = f"profile:{canonical}:%"

        profile_exists = True
        try:
            self.get_profile(canonical, include_recent=False)
        except KeyError:
            profile_exists = False

        if not dry_run and not profile_exists:
            raise KeyError(f"Unknown profile: {canonical}")

        table_plan: list[tuple[str, bool]] = [
            ("documents", True),
            ("kg_nodes", True),
            ("kg_edges", True),
            ("kg_episodes", True),
        ]
        if include_artifacts:
            table_plan.append(("artifacts", False))

        tables: dict[str, dict[str, Any]] = {}
        for table_name, with_group_scope in table_plan:
            profile_scope_count = self._safe_count_by_profile(table_name, canonical)
            group_scope_count = (
                self._safe_count_by_group_prefix(table_name, group_prefix)
                if with_group_scope
                else 0
            )
            entry: dict[str, Any] = {
                "profile_scope_count": profile_scope_count,
                "group_scope_count": group_scope_count,
                "deleted_profile_scope": 0,
                "deleted_group_scope": 0,
                "remaining_profile_scope": profile_scope_count,
                "remaining_group_scope": group_scope_count,
                "group_scope_enabled": with_group_scope,
            }
            if not dry_run:
                entry["deleted_profile_scope"] = self._safe_delete_by_profile(
                    table_name, canonical
                )
                if with_group_scope:
                    entry["deleted_group_scope"] = self._safe_delete_by_group_prefix(
                        table_name, group_prefix
                    )
                entry["remaining_profile_scope"] = self._safe_count_by_profile(
                    table_name, canonical
                )
                entry["remaining_group_scope"] = (
                    self._safe_count_by_group_prefix(table_name, group_prefix)
                    if with_group_scope
                    else 0
                )
            tables[table_name] = entry

        summary = {
            "candidate_rows_by_profile_scope": sum(
                int(row.get("profile_scope_count") or 0) for row in tables.values()
            ),
            "candidate_rows_by_group_scope": sum(
                int(row.get("group_scope_count") or 0) for row in tables.values()
            ),
            "deleted_rows_by_profile_scope": sum(
                int(row.get("deleted_profile_scope") or 0) for row in tables.values()
            ),
            "deleted_rows_by_group_scope": sum(
                int(row.get("deleted_group_scope") or 0) for row in tables.values()
            ),
            "remaining_rows_by_profile_scope": sum(
                int(row.get("remaining_profile_scope") or 0) for row in tables.values()
            ),
            "remaining_rows_by_group_scope": sum(
                int(row.get("remaining_group_scope") or 0) for row in tables.values()
            ),
        }
        if not dry_run:
            self._legacy_rollup_cache.pop(canonical, None)

        return {
            "requested_profile_id": requested,
            "profile_id": canonical,
            "alias_resolved": requested != canonical,
            "profile_exists": profile_exists,
            "dry_run": dry_run,
            "include_artifacts": include_artifacts,
            "group_prefix": group_prefix,
            "tables": tables,
            "summary": summary,
        }

    def delete_profile_everything(
        self,
        profile_id: str,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """
        Delete all Supabase-side data tied to a profile, then remove the profile row.

        This includes:
        - documents / kg_* rows (via purge_profile_data with include_artifacts=True)
        - profile_aliases rows referencing this profile
        - profile_merges rows referencing this profile
        - profiles row itself
        """
        self._require_schema()
        requested = normalize_profile_id(profile_id)
        canonical = self.resolve_alias(requested)

        profile_exists = True
        prompt_profile_name: str | None = None
        try:
            profile = self.get_profile(canonical, include_recent=False)
            prompt_profile_name = str(profile.get("prompt_profile_name") or "").strip() or None
        except KeyError:
            profile_exists = False

        if not dry_run and not profile_exists:
            raise KeyError(f"Unknown profile: {canonical}")

        purge_report = self.purge_profile_data(
            canonical,
            dry_run=dry_run,
            include_artifacts=True,
        )

        db_counts = {
            "profiles": {
                "candidate": self._safe_count_eq("profiles", "profile_id", canonical),
                "deleted": 0,
                "remaining": self._safe_count_eq("profiles", "profile_id", canonical),
            },
            "profile_aliases_alias_profile": {
                "candidate": self._safe_count_eq(
                    "profile_aliases", "alias_profile_id", canonical
                ),
                "deleted": 0,
                "remaining": self._safe_count_eq(
                    "profile_aliases", "alias_profile_id", canonical
                ),
            },
            "profile_aliases_canonical_profile": {
                "candidate": self._safe_count_eq(
                    "profile_aliases", "canonical_profile_id", canonical
                ),
                "deleted": 0,
                "remaining": self._safe_count_eq(
                    "profile_aliases", "canonical_profile_id", canonical
                ),
            },
            "profile_merges_source_profile": {
                "candidate": self._safe_count_eq(
                    "profile_merges", "source_profile_id", canonical
                ),
                "deleted": 0,
                "remaining": self._safe_count_eq(
                    "profile_merges", "source_profile_id", canonical
                ),
            },
            "profile_merges_target_profile": {
                "candidate": self._safe_count_eq(
                    "profile_merges", "target_profile_id", canonical
                ),
                "deleted": 0,
                "remaining": self._safe_count_eq(
                    "profile_merges", "target_profile_id", canonical
                ),
            },
        }

        if not dry_run:
            db_counts["profile_aliases_alias_profile"]["deleted"] = self._safe_delete_eq(
                "profile_aliases", "alias_profile_id", canonical
            )
            db_counts["profile_aliases_canonical_profile"]["deleted"] = self._safe_delete_eq(
                "profile_aliases", "canonical_profile_id", canonical
            )
            db_counts["profile_merges_source_profile"]["deleted"] = self._safe_delete_eq(
                "profile_merges", "source_profile_id", canonical
            )
            db_counts["profile_merges_target_profile"]["deleted"] = self._safe_delete_eq(
                "profile_merges", "target_profile_id", canonical
            )
            db_counts["profiles"]["deleted"] = self._safe_delete_eq(
                "profiles", "profile_id", canonical
            )

            for key, row in db_counts.items():
                col = {
                    "profiles": "profile_id",
                    "profile_aliases_alias_profile": "alias_profile_id",
                    "profile_aliases_canonical_profile": "canonical_profile_id",
                    "profile_merges_source_profile": "source_profile_id",
                    "profile_merges_target_profile": "target_profile_id",
                }[key]
                table = "profiles"
                if key.startswith("profile_aliases_"):
                    table = "profile_aliases"
                elif key.startswith("profile_merges_"):
                    table = "profile_merges"
                row["remaining"] = self._safe_count_eq(table, col, canonical)

            self._legacy_rollup_cache.pop(canonical, None)

        db_summary = {
            "candidate_rows": sum(int(row.get("candidate") or 0) for row in db_counts.values()),
            "deleted_rows": sum(int(row.get("deleted") or 0) for row in db_counts.values()),
            "remaining_rows": sum(int(row.get("remaining") or 0) for row in db_counts.values()),
        }

        return {
            "requested_profile_id": requested,
            "profile_id": canonical,
            "alias_resolved": requested != canonical,
            "profile_exists": profile_exists,
            "prompt_profile_name": prompt_profile_name,
            "dry_run": dry_run,
            "purge_report": purge_report,
            "db_counts": db_counts,
            "db_summary": db_summary,
        }

    @staticmethod
    def _shape_catalog_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "profile_id": row.get("profile_id"),
            "name": row.get("name") or row.get("profile_id"),
            "summary": row.get("summary") or "",
            "status": row.get("status") or "active",
            "doc_count": int(row.get("doc_count") or 0),
            "node_count": int(row.get("node_count") or 0),
            "edge_count": int(row.get("edge_count") or 0),
            "episode_count": int(row.get("episode_count") or 0),
            "artifact_count": int(row.get("artifact_count") or 0),
            "last_activity": row.get("last_activity"),
            "prompt_profile_name": row.get("prompt_profile_name"),
            "tags": row.get("tags") or [],
        }

    def _merge_tags_for_profile(
        self, profile_id: str, incoming: list[str] | None
    ) -> list[str]:
        merged: set[str] = set()
        current_resp = (
            self.client.table("profiles")
            .select("tags")
            .eq("profile_id", profile_id)
            .limit(1)
            .execute()
        )
        current_data = self._resp_data(current_resp)
        current = (
            current_data[0]
            if isinstance(current_data, list) and current_data
            else current_data
        ) or {}
        current_tags = current.get("tags") if isinstance(current, dict) else []
        for tag in (current_tags or []):
            if tag is None:
                continue
            value = str(tag).strip().lower()
            if value:
                merged.add(value)
        for tag in (incoming or []):
            if tag is None:
                continue
            value = str(tag).strip().lower()
            if value:
                merged.add(value)
        return sorted(merged)

    @staticmethod
    def _is_user_created_profile(profile: dict[str, Any]) -> bool:
        tags = {str(tag).strip().lower() for tag in (profile.get("tags") or []) if str(tag).strip()}
        if "user_created" in tags:
            return True
        if str(profile.get("prompt_profile_name") or "").strip():
            return True
        return False

    def _repair_legacy_rollups(self, rows: list[dict[str, Any]]) -> None:
        """
        Backfill catalog counts when legacy rows were ingested without profile_id.
        """
        for row in rows:
            profile_id = normalize_profile_id(str(row.get("profile_id") or ""))
            if not profile_id:
                continue
            if (
                int(row.get("node_count") or 0) > 0
                and int(row.get("edge_count") or 0) > 0
                and int(row.get("episode_count") or 0) > 0
            ):
                continue

            legacy = self._legacy_rollups_for_profile(profile_id)
            for key in ("doc_count", "node_count", "edge_count", "episode_count"):
                current_val = int(row.get(key) or 0)
                legacy_val = int(legacy.get(key) or 0)
                if legacy_val > current_val:
                    row[key] = legacy_val

            row_last = row.get("last_activity")
            legacy_last = legacy.get("last_activity")
            if legacy_last and (not row_last or str(legacy_last) > str(row_last)):
                row["last_activity"] = legacy_last

    def _legacy_rollups_for_profile(self, profile_id: str) -> dict[str, Any]:
        cached = self._legacy_rollup_cache.get(profile_id)
        if cached is not None:
            return cached

        prefix = f"profile:{profile_id}:%"
        result = {
            "doc_count": 0,
            "node_count": 0,
            "edge_count": 0,
            "episode_count": 0,
            "last_activity": None,
        }
        mappings = [
            ("documents", "doc_count"),
            ("kg_nodes", "node_count"),
            ("kg_edges", "edge_count"),
            ("kg_episodes", "episode_count"),
        ]
        for table_name, out_key in mappings:
            count, last = self._safe_count_with_group_fallback(table_name, profile_id, prefix)
            result[out_key] = count
            if last and (
                result["last_activity"] is None
                or str(last) > str(result["last_activity"])
            ):
                result["last_activity"] = last

        self._legacy_rollup_cache[profile_id] = result
        return result

    def _safe_count_with_group_fallback(
        self, table_name: str, profile_id: str, group_prefix: str
    ) -> tuple[int, Optional[str]]:
        count = 0
        last: Optional[str] = None
        try:
            resp = (
                self.client.table(table_name)
                .select("created_at", count="exact")
                .eq("profile_id", profile_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            count = int(getattr(resp, "count", 0) or 0)
            data = self._resp_data(resp) or []
            if isinstance(data, list) and data:
                last = data[0].get("created_at")
        except Exception:
            return 0, None

        if count > 0:
            return count, last

        try:
            resp = (
                self.client.table(table_name)
                .select("created_at", count="exact")
                .like("group_id", group_prefix)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            count = int(getattr(resp, "count", 0) or 0)
            data = self._resp_data(resp) or []
            if isinstance(data, list) and data:
                last = data[0].get("created_at")
        except Exception:
            # Some deployments still miss documents.group_id; keep primary count.
            pass
        return count, last

    def _safe_count_by_profile(self, table_name: str, profile_id: str) -> int:
        try:
            resp = (
                self.client.table(table_name)
                .select("created_at", count="exact")
                .eq("profile_id", profile_id)
                .limit(1)
                .execute()
            )
            return int(getattr(resp, "count", 0) or 0)
        except Exception:
            return 0

    def _safe_count_eq(self, table_name: str, column: str, value: str) -> int:
        try:
            resp = (
                self.client.table(table_name)
                .select("created_at", count="exact")
                .eq(column, value)
                .limit(1)
                .execute()
            )
            return int(getattr(resp, "count", 0) or 0)
        except Exception:
            return 0

    def _safe_count_by_group_prefix(self, table_name: str, group_prefix: str) -> int:
        try:
            resp = (
                self.client.table(table_name)
                .select("created_at", count="exact")
                .like("group_id", group_prefix)
                .limit(1)
                .execute()
            )
            return int(getattr(resp, "count", 0) or 0)
        except Exception:
            return 0

    def _safe_delete_by_profile(self, table_name: str, profile_id: str) -> int:
        return self._safe_delete(table_name, profile_id=profile_id)

    def _safe_delete_by_group_prefix(self, table_name: str, group_prefix: str) -> int:
        return self._safe_delete(table_name, group_prefix=group_prefix)

    def _safe_delete(
        self,
        table_name: str,
        *,
        profile_id: str | None = None,
        group_prefix: str | None = None,
    ) -> int:
        if not profile_id and not group_prefix:
            return 0

        def _run_delete(with_count: bool) -> Any:
            query = (
                self.client.table(table_name).delete(count="exact")
                if with_count
                else self.client.table(table_name).delete()
            )
            if profile_id:
                query = query.eq("profile_id", profile_id)
            if group_prefix:
                query = query.like("group_id", group_prefix)
            return query.execute()

        try:
            resp = _run_delete(with_count=True)
            return int(getattr(resp, "count", 0) or 0)
        except TypeError:
            try:
                resp = _run_delete(with_count=False)
                data = self._resp_data(resp)
                return len(data) if isinstance(data, list) else 0
            except Exception:
                return 0
        except Exception:
            return 0

    def _safe_delete_eq(self, table_name: str, column: str, value: str) -> int:
        def _run_delete(with_count: bool) -> Any:
            query = (
                self.client.table(table_name).delete(count="exact")
                if with_count
                else self.client.table(table_name).delete()
            )
            query = query.eq(column, value)
            return query.execute()

        try:
            resp = _run_delete(with_count=True)
            return int(getattr(resp, "count", 0) or 0)
        except TypeError:
            try:
                resp = _run_delete(with_count=False)
                data = self._resp_data(resp)
                return len(data) if isinstance(data, list) else 0
            except Exception:
                return 0
        except Exception:
            return 0
