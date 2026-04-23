from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from src.normalize import make_id, normalize_whitespace

STAGE_COUNT_FIELDS = (
    "municipality_id",
    "revize_rows_extracted",
    "revize_rows_normalized_seen",
    "revize_rows_normalized_kept",
    "revize_rows_normalized_rejected",
    "revize_rows_insert_attempted",
    "revize_rows_inserted_or_updated",
    "revize_rows_dropped_pre_clean_contacts",
    "revize_rows_in_clean_contacts",
    "revize_rows_considered_for_role_winners",
    "revize_rows_selected_as_role_winners",
)


class RevizeTraceCollector:
    def __init__(
        self,
        output_dir: str | Path,
        sample_size: int = 8,
        follow_match: str | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.sample_size = max(1, int(sample_size))
        self.follow_match = (follow_match or "").strip().lower()
        self.revize_municipalities: set[str] = set()
        self.stage_counts: dict[str, dict[str, int | str]] = {}
        self.drop_reasons: Counter[tuple[str, str, str]] = Counter()
        self.row_traces: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    def register_municipality(self, municipality_id: str, platform: str | None) -> None:
        platform_lower = (platform or "").strip().lower()
        if platform_lower != "revize":
            return
        self.revize_municipalities.add(municipality_id)
        if municipality_id not in self.stage_counts:
            self.stage_counts[municipality_id] = self._default_stage_counts(municipality_id)

    def record_revize_result(self, municipality_id: str, revize_result: dict[str, Any]) -> None:
        if municipality_id not in self.revize_municipalities:
            return
        stage = self.stage_counts.setdefault(municipality_id, self._default_stage_counts(municipality_id))
        stage["revize_rows_extracted"] = int(revize_result.get("rows_extracted_total") or 0)
        stage["revize_rows_normalized_seen"] = int(revize_result.get("rows_normalized_seen") or 0)
        stage["revize_rows_normalized_kept"] = int(revize_result.get("rows_normalized_kept") or 0)
        stage["revize_rows_normalized_rejected"] = int(revize_result.get("rows_normalized_rejected") or 0)

        for row in list(revize_result.get("extracted_rows_sample") or []):
            self._record_row_stage(municipality_id, row, "extracted_raw", drop_stage="", drop_reason="")
        for row in list(revize_result.get("normalized_rows_sample") or []):
            self._record_row_stage(municipality_id, row, "normalized", drop_stage="", drop_reason="")
        for rejected in list(revize_result.get("rejected_rows_sample") or []):
            row = dict(rejected.get("row") or {})
            drop_reason = str(rejected.get("drop_reason") or "unknown_pipeline_drop")
            self._record_row_stage(
                municipality_id,
                row,
                "normalized_rejected",
                drop_stage="normalization",
                drop_reason=drop_reason,
            )

        for reason, count in dict(revize_result.get("suspicious_reduction_counts") or {}).items():
            self._add_drop_reason(
                municipality_id=municipality_id,
                drop_stage="normalization",
                drop_reason=str(reason or "unknown_pipeline_drop"),
                count=int(count or 0),
            )

    def record_insert_debug(
        self,
        municipality_id: str,
        insert_attempted: int,
        inserted_or_updated: int,
        debug_rows: list[dict[str, Any]],
    ) -> None:
        if municipality_id not in self.revize_municipalities:
            return
        stage = self.stage_counts.setdefault(municipality_id, self._default_stage_counts(municipality_id))
        stage["revize_rows_insert_attempted"] = int(insert_attempted)
        stage["revize_rows_inserted_or_updated"] = int(inserted_or_updated)

        for event in debug_rows:
            row = dict(event.get("row") or {})
            stage_name = str(event.get("stage") or "insert_stage")
            drop_reason = str(event.get("drop_reason") or "")
            drop_stage = str(event.get("drop_stage") or "")
            self._record_row_stage(municipality_id, row, stage_name, drop_stage=drop_stage, drop_reason=drop_reason)
            if drop_stage and drop_reason:
                self._add_drop_reason(municipality_id, drop_stage, drop_reason, count=1)

    def finalize_from_db(self, conn) -> None:
        clean_view_exists = _object_exists(conn, "vw_contacts_clean", "view")
        winners_view_exists = _object_exists(conn, "vw_best_role_per_town", "view")
        for municipality_id in sorted(self.revize_municipalities):
            stage = self.stage_counts.setdefault(municipality_id, self._default_stage_counts(municipality_id))
            clean_count = 0
            clean_rows: list[dict[str, Any]] = []
            if clean_view_exists:
                clean_count, clean_rows = self._count_and_fetch_clean_rows(conn, municipality_id)
                stage["revize_rows_in_clean_contacts"] = clean_count
                stage["revize_rows_dropped_pre_clean_contacts"] = max(
                    0,
                    int(stage.get("revize_rows_inserted_or_updated") or 0) - clean_count,
                )
            else:
                stage["revize_rows_in_clean_contacts"] = 0
                stage["revize_rows_dropped_pre_clean_contacts"] = 0
                self._add_drop_reason(
                    municipality_id=municipality_id,
                    drop_stage="pre_clean_contacts",
                    drop_reason="clean_contacts_view_missing",
                    count=1,
                )

            considered_count, considered_rows = self._count_and_fetch_role_candidates(conn, municipality_id)
            stage["revize_rows_considered_for_role_winners"] = considered_count
            if clean_view_exists and clean_count > 0 and considered_count == 0:
                self._add_drop_reason(
                    municipality_id=municipality_id,
                    drop_stage="role_candidate_filter",
                    drop_reason="failed_role_mapping",
                    count=clean_count,
                )

            winner_count, winner_rows = (0, [])
            if winners_view_exists:
                winner_count, winner_rows = self._count_and_fetch_role_winners(conn, municipality_id)
            elif considered_count > 0:
                self._add_drop_reason(
                    municipality_id=municipality_id,
                    drop_stage="role_winner_selection",
                    drop_reason="role_winner_view_missing",
                    count=considered_count,
                )
            stage["revize_rows_selected_as_role_winners"] = winner_count

            for row in clean_rows:
                self._record_row_stage(municipality_id, row, "clean_contacts", drop_stage="", drop_reason="")
            for row in considered_rows:
                self._record_row_stage(municipality_id, row, "role_candidate", drop_stage="", drop_reason="")
            for row in winner_rows:
                self._record_row_stage(municipality_id, row, "role_winner", drop_stage="", drop_reason="")

            dropped_pre_clean = int(stage.get("revize_rows_dropped_pre_clean_contacts") or 0)
            if dropped_pre_clean > 0:
                reason_counts = self._count_pre_clean_drop_reasons(conn, municipality_id)
                if reason_counts:
                    for reason, count in reason_counts.items():
                        self._add_drop_reason(
                            municipality_id=municipality_id,
                            drop_stage="pre_clean_contacts",
                            drop_reason=reason,
                            count=count,
                        )
                else:
                    self._add_drop_reason(
                        municipality_id=municipality_id,
                        drop_stage="pre_clean_contacts",
                        drop_reason="failed_clean_contact_filter",
                        count=dropped_pre_clean,
                    )
            if winners_view_exists:
                candidate_minus_winner = max(0, considered_count - winner_count)
                if candidate_minus_winner > 0:
                    self._add_drop_reason(
                        municipality_id=municipality_id,
                        drop_stage="role_winner_selection",
                        drop_reason="failed_role_mapping_or_scoring",
                        count=candidate_minus_winner,
                    )

    def write_outputs(self) -> dict[str, Path]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stage_counts_path = self.output_dir / "revize_stage_counts.csv"
        row_trace_path = self.output_dir / "revize_row_trace.jsonl"
        drop_reasons_path = self.output_dir / "revize_drop_reasons.csv"

        self._write_stage_counts(stage_counts_path)
        self._write_row_trace(row_trace_path)
        self._write_drop_reasons(drop_reasons_path)
        return {
            "revize_stage_counts.csv": stage_counts_path,
            "revize_row_trace.jsonl": row_trace_path,
            "revize_drop_reasons.csv": drop_reasons_path,
        }

    def _default_stage_counts(self, municipality_id: str) -> dict[str, int | str]:
        return {
            "municipality_id": municipality_id,
            "revize_rows_extracted": 0,
            "revize_rows_normalized_seen": 0,
            "revize_rows_normalized_kept": 0,
            "revize_rows_normalized_rejected": 0,
            "revize_rows_insert_attempted": 0,
            "revize_rows_inserted_or_updated": 0,
            "revize_rows_dropped_pre_clean_contacts": 0,
            "revize_rows_in_clean_contacts": 0,
            "revize_rows_considered_for_role_winners": 0,
            "revize_rows_selected_as_role_winners": 0,
        }

    def _row_fingerprint(self, municipality_id: str, row: dict[str, Any]) -> str:
        name = normalize_whitespace(str(row.get("name") or "")) or ""
        title = normalize_whitespace(str(row.get("title") or "")) or ""
        department = normalize_whitespace(str(row.get("department") or "")) or ""
        email = str(row.get("email") or "").strip().lower()
        phone = str(row.get("phone") or "").strip()
        source_url = normalize_whitespace(str(row.get("source_url") or "")) or ""
        return make_id("rtrace", municipality_id, name, title, department, email, phone, source_url, length=18)

    def _record_row_stage(
        self,
        municipality_id: str,
        row: dict[str, Any],
        stage_name: str,
        drop_stage: str,
        drop_reason: str,
    ) -> None:
        row_payload = self._trace_payload(row)
        fingerprint = self._row_fingerprint(municipality_id, row_payload)
        include_due_to_match = self._matches_follow(fingerprint, row_payload)
        traces = self.row_traces[municipality_id]
        if fingerprint not in traces and not include_due_to_match and len(traces) >= self.sample_size:
            return
        entry = traces.setdefault(
            fingerprint,
            {
                "municipality_id": municipality_id,
                "fingerprint": fingerprint,
                "source_url": row_payload.get("source_url"),
                "name": row_payload.get("name"),
                "title": row_payload.get("title"),
                "department": row_payload.get("department"),
                "stages": {},
                "drop_stage": "",
                "drop_reason": "",
            },
        )
        entry["stages"][stage_name] = row_payload
        if drop_stage and drop_reason:
            entry["drop_stage"] = drop_stage
            entry["drop_reason"] = drop_reason

    def _matches_follow(self, fingerprint: str, row: dict[str, Any]) -> bool:
        if not self.follow_match:
            return False
        blob = " ".join(
            [
                fingerprint.lower(),
                str(row.get("name") or "").lower(),
                str(row.get("source_url") or "").lower(),
                str(row.get("email") or "").lower(),
            ]
        )
        return self.follow_match in blob

    def _trace_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_url": normalize_whitespace(str(row.get("source_url") or "")) or "",
            "name": normalize_whitespace(str(row.get("name") or "")) or "",
            "title": normalize_whitespace(str(row.get("title") or "")) or "",
            "department": normalize_whitespace(str(row.get("department") or "")) or "",
            "email": str(row.get("email") or "").strip().lower(),
            "phone": str(row.get("phone") or "").strip(),
            "phone_ext": str(row.get("phone_ext") or "").strip(),
            "page_type": normalize_whitespace(str(row.get("page_type") or "")) or "",
            "role_normalized": normalize_whitespace(str(row.get("role_normalized") or "")) or "",
            "source_context": normalize_whitespace(str(row.get("source_context") or "")) or "",
            "confidence": _coerce_float(row.get("confidence")),
        }

    def _add_drop_reason(
        self,
        municipality_id: str,
        drop_stage: str,
        drop_reason: str,
        count: int,
    ) -> None:
        if count <= 0:
            return
        key = (
            municipality_id,
            drop_stage or "unknown_stage",
            drop_reason or "unknown_pipeline_drop",
        )
        self.drop_reasons[key] += int(count)

    def _count_and_fetch_clean_rows(self, conn, municipality_id: str) -> tuple[int, list[dict[str, Any]]]:
        if not _object_exists(conn, "vw_contacts_clean", "view"):
            return 0, []
        clean_columns = _object_columns(conn, "vw_contacts_clean")
        count = conn.execute(
            "SELECT COUNT(*) FROM vw_contacts_clean WHERE municipality_id = ?",
            (municipality_id,),
        ).fetchone()[0]
        selected_columns = [
            "municipality_id",
            "source_url",
            "name",
            "title",
            "department",
            "email",
            "phone",
        ]
        for candidate_column in ("page_type", "role_normalized", "source_context"):
            if candidate_column in clean_columns:
                selected_columns.append(candidate_column)
            else:
                selected_columns.append(f"'' AS {candidate_column}")
        confidence_column = "display_confidence" if "display_confidence" in clean_columns else (
            "confidence" if "confidence" in clean_columns else "0.0"
        )
        rows = conn.execute(
            f"""
            SELECT {", ".join(selected_columns)}, COALESCE({confidence_column}, 0.0) AS confidence
            FROM vw_contacts_clean
            WHERE municipality_id = ?
            ORDER BY COALESCE({confidence_column}, 0.0) DESC, COALESCE(source_url, '')
            LIMIT ?
            """,
            (municipality_id, max(12, self.sample_size * 2)),
        ).fetchall()
        return int(count), [dict(row) for row in rows]

    def _count_and_fetch_role_candidates(self, conn, municipality_id: str) -> tuple[int, list[dict[str, Any]]]:
        if _object_exists(conn, "vw_contacts_clean", "view"):
            clean_columns = _object_columns(conn, "vw_contacts_clean")
            if "role_normalized" not in clean_columns:
                return 0, []
            count = conn.execute(
                """
                SELECT COUNT(*)
                FROM vw_contacts_clean
                WHERE municipality_id = ?
                  AND NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NOT NULL
                """,
                (municipality_id,),
            ).fetchone()[0]
            selected_columns = [
                "municipality_id",
                "source_url",
                "name",
                "title",
                "department",
                "email",
                "phone",
            ]
            for candidate_column in ("page_type", "role_normalized", "source_context"):
                if candidate_column in clean_columns:
                    selected_columns.append(candidate_column)
                else:
                    selected_columns.append(f"'' AS {candidate_column}")
            confidence_column = "display_confidence" if "display_confidence" in clean_columns else (
                "confidence" if "confidence" in clean_columns else "0.0"
            )
            rows = conn.execute(
                f"""
                SELECT {", ".join(selected_columns)}, COALESCE({confidence_column}, 0.0) AS confidence
                FROM vw_contacts_clean
                WHERE municipality_id = ?
                  AND NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NOT NULL
                ORDER BY COALESCE({confidence_column}, 0.0) DESC, COALESCE(source_url, '')
                LIMIT ?
                """,
                (municipality_id, max(12, self.sample_size * 2)),
            ).fetchall()
            return int(count), [dict(row) for row in rows]

        if not _object_exists(conn, "contacts", "table"):
            return 0, []
        contact_columns = _table_columns(conn, "contacts")
        if "role_normalized" not in contact_columns:
            return 0, []
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM contacts
            WHERE municipality_id = ?
              AND NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NOT NULL
            """,
            (municipality_id,),
        ).fetchone()[0]
        selected_columns = [
            "municipality_id",
            "source_url",
            "name",
            "title",
            "department",
            "email",
            "phone",
        ]
        for candidate_column in ("page_type", "role_normalized", "source_context"):
            if candidate_column in contact_columns:
                selected_columns.append(candidate_column)
            else:
                selected_columns.append(f"'' AS {candidate_column}")
        confidence_column = "confidence"
        if "display_confidence" in contact_columns:
            confidence_column = "display_confidence"
        rows = conn.execute(
            f"""
            SELECT {", ".join(selected_columns)}, COALESCE({confidence_column}, 0.0) AS confidence
            FROM contacts
            WHERE municipality_id = ?
              AND NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NOT NULL
            ORDER BY COALESCE({confidence_column}, 0.0) DESC, COALESCE(source_url, '')
            LIMIT ?
            """,
            (municipality_id, max(12, self.sample_size * 2)),
        ).fetchall()
        return int(count), [dict(row) for row in rows]

    def _count_and_fetch_role_winners(self, conn, municipality_id: str) -> tuple[int, list[dict[str, Any]]]:
        if not _object_exists(conn, "vw_best_role_per_town", "view"):
            return 0, []
        winner_columns = _object_columns(conn, "vw_best_role_per_town")
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM vw_best_role_per_town
            WHERE municipality_id = ?
              AND NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NOT NULL
            """,
            (municipality_id,),
        ).fetchone()[0]
        selected_columns = [
            "municipality_id",
            "source_url",
            "name",
            "title",
            "department",
            "email",
            "phone",
        ]
        for candidate_column in ("page_type", "role_normalized"):
            if candidate_column in winner_columns:
                selected_columns.append(candidate_column)
            else:
                selected_columns.append(f"'' AS {candidate_column}")
        confidence_column = "display_confidence" if "display_confidence" in winner_columns else (
            "confidence" if "confidence" in winner_columns else "0.0"
        )
        rows = conn.execute(
            f"""
            SELECT {", ".join(selected_columns)}, '' AS source_context, COALESCE({confidence_column}, 0.0) AS confidence
            FROM vw_best_role_per_town
            WHERE municipality_id = ?
              AND NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NOT NULL
            ORDER BY COALESCE({confidence_column}, 0.0) DESC, COALESCE(source_url, '')
            LIMIT ?
            """,
            (municipality_id, max(12, self.sample_size * 2)),
        ).fetchall()
        return int(count), [dict(row) for row in rows]

    def _count_pre_clean_drop_reasons(self, conn, municipality_id: str) -> Counter[str]:
        if not _object_exists(conn, "contacts", "table"):
            return Counter()
        if not _object_exists(conn, "vw_contacts_clean", "view"):
            return Counter()

        contact_columns = _table_columns(conn, "contacts")
        if "contact_id" not in contact_columns:
            return Counter()

        selected_columns = ["c.contact_id"]
        has_noise = "is_likely_noise" in contact_columns
        has_rank = "record_rank" in contact_columns
        if has_noise:
            selected_columns.append("COALESCE(c.is_likely_noise, 0) AS is_likely_noise")
        else:
            selected_columns.append("0 AS is_likely_noise")
        if has_rank:
            selected_columns.append("COALESCE(c.record_rank, 1) AS record_rank")
        else:
            selected_columns.append("1 AS record_rank")

        rows = conn.execute(
            f"""
            SELECT {", ".join(selected_columns)}
            FROM contacts c
            LEFT JOIN vw_contacts_clean v
              ON v.contact_id = c.contact_id
            WHERE c.municipality_id = ?
              AND v.contact_id IS NULL
            """,
            (municipality_id,),
        ).fetchall()

        reason_counts: Counter[str] = Counter()
        for row in rows:
            is_likely_noise = int(row["is_likely_noise"] or 0)
            record_rank = int(row["record_rank"] or 1)
            if is_likely_noise > 0:
                reason_counts["failed_clean_contact_filter"] += 1
            elif record_rank > 1:
                reason_counts["deduped_as_duplicate"] += 1
            else:
                reason_counts["unknown_pipeline_drop"] += 1
        return reason_counts

    def _write_stage_counts(self, path: Path) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(STAGE_COUNT_FIELDS))
            writer.writeheader()
            for municipality_id in sorted(self.stage_counts):
                row = self.stage_counts[municipality_id]
                writer.writerow({field: row.get(field, 0) for field in STAGE_COUNT_FIELDS})

    def _write_row_trace(self, path: Path) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for municipality_id in sorted(self.row_traces):
                traces = self.row_traces[municipality_id]
                for fingerprint in sorted(traces):
                    handle.write(json.dumps(traces[fingerprint], sort_keys=True) + "\n")

    def _write_drop_reasons(self, path: Path) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["municipality_id", "drop_stage", "drop_reason", "count"],
            )
            writer.writeheader()
            for (municipality_id, drop_stage, drop_reason), count in sorted(self.drop_reasons.items()):
                writer.writerow(
                    {
                        "municipality_id": municipality_id,
                        "drop_stage": drop_stage,
                        "drop_reason": drop_reason,
                        "count": int(count),
                    }
                )


def _object_exists(conn, name: str, object_type: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ? LIMIT 1",
        (object_type, name),
    ).fetchone()
    return row is not None


def _table_columns(conn, table_name: str) -> set[str]:
    if not _object_exists(conn, table_name, "table"):
        return set()
    return _object_columns(conn, table_name)


def _object_columns(conn, object_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({object_name})").fetchall()
    return {str(row["name"]).strip().lower() for row in rows}


def _coerce_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0
