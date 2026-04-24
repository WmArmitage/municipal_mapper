from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.batch_manifest import load_manifest_rows, load_seed_platform_map

REQUIRED_POSTPROCESS_OBJECTS = (
    ("view", "vw_contacts_clean"),
    ("view", "vw_role_candidates_scored"),
    ("view", "vw_unresolved_roles"),
    ("view", "vw_best_role_per_town"),
)

REQUIRED_CONTACT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("entity_type", "TEXT"),
    ("role_normalized", "TEXT"),
    ("role_family", "TEXT"),
    ("department_normalized", "TEXT"),
    ("has_name", "INTEGER DEFAULT 0"),
    ("has_email", "INTEGER DEFAULT 0"),
    ("has_phone", "INTEGER DEFAULT 0"),
    ("has_department", "INTEGER DEFAULT 0"),
    ("is_role_only", "INTEGER DEFAULT 0"),
    ("page_type", "TEXT"),
    ("is_likely_noise", "INTEGER DEFAULT 0"),
    ("dedupe_key", "TEXT"),
    ("record_rank", "INTEGER"),
    ("semantic_confidence", "REAL"),
    ("display_confidence", "REAL"),
    ("suspicious_reason", "TEXT"),
    ("source_context", "TEXT"),
)

REQUIRED_SERVICE_LINK_COLUMNS: tuple[tuple[str, str], ...] = (
    ("service_type", "TEXT"),
    ("service_type_normalized", "TEXT"),
    ("provider_normalized", "TEXT"),
    ("is_external", "INTEGER DEFAULT 0"),
    ("display_confidence", "REAL"),
)


FALLBACK_VW_CONTACTS_CLEAN_SQL = """
CREATE VIEW vw_contacts_clean AS
SELECT
    c.contact_id,
    c.municipality_id,
    c.entity_type,
    c.name,
    c.title,
    c.role_normalized,
    c.role_family,
    c.department,
    c.department_normalized,
    c.email,
    c.email_type,
    c.phone,
    c.phone_ext,
    c.address,
    c.hours,
    c.page_type,
    c.source_url,
    c.display_confidence
FROM contacts c
WHERE COALESCE(c.record_rank, 1) = 1
  AND (
      COALESCE(c.is_likely_noise, 0) = 0
      OR NOT EXISTS (
          SELECT 1
          FROM contacts c2
          WHERE COALESCE(c2.dedupe_key, '') = COALESCE(c.dedupe_key, '')
            AND COALESCE(c2.is_likely_noise, 0) = 0
      )
  )
""".strip()


FALLBACK_VW_ROLE_CANDIDATES_SCORED_SQL = """
CREATE VIEW vw_role_candidates_scored AS
WITH base AS (
  SELECT
    c.contact_id,
    c.municipality_id,
    c.entity_type,
    c.name,
    c.title,
    c.role_normalized,
    c.role_family,
    c.department,
    c.department_normalized,
    c.email,
    c.email_type,
    c.phone,
    c.phone_ext,
    c.address,
    c.hours,
    c.page_type,
    c.source_url,
    c.display_confidence,
    c.source_context,
    COALESCE(c.is_likely_noise, 0) AS is_likely_noise,
    c.suspicious_reason,
    CASE
      WHEN LOWER(COALESCE(c.source_context, '')) LIKE 'revize:%' THEN 1
      ELSE 0
    END AS is_revize,
    LOWER(TRIM(COALESCE(c.name, ''))) AS name_norm,
    LOWER(TRIM(COALESCE(c.title, ''))) AS title_norm,
    LOWER(TRIM(COALESCE(c.department, ''))) AS department_norm,
    LOWER(TRIM(COALESCE(c.role_normalized, ''))) AS role_norm,
    LOWER(COALESCE(c.source_url, '')) AS source_url_norm,
    LOWER(COALESCE(c.page_type, '')) AS page_type_norm,
    LOWER(COALESCE(c.source_context, '')) AS source_context_norm
  FROM contacts c
  WHERE NULLIF(TRIM(COALESCE(c.role_normalized, '')), '') IS NOT NULL
),
component_scores AS (
  SELECT
    b.*,
    (
      CASE
        WHEN COALESCE(b.entity_type, '') = 'person' THEN 4
        ELSE 0
      END
      + CASE
          WHEN NULLIF(TRIM(COALESCE(b.name, '')), '') IS NOT NULL
               AND (
                 LENGTH(TRIM(COALESCE(b.name, '')))
                 - LENGTH(REPLACE(TRIM(COALESCE(b.name, '')), ' ', ''))
                 + 1
               ) >= 2
          THEN 4
          ELSE 0
        END
      + CASE
          WHEN TRIM(COALESCE(b.name, '')) GLOB '[A-Z]* [A-Z]*' THEN 2
          ELSE 0
        END
      + CASE
          WHEN NULLIF(TRIM(COALESCE(b.name, '')), '') IS NOT NULL
               AND LENGTH(TRIM(COALESCE(b.name, ''))) BETWEEN 5 AND 60
          THEN 1
          ELSE 0
        END
      + CASE
          WHEN LOWER(TRIM(COALESCE(b.name, ''))) = LOWER(TRIM(COALESCE(b.role_normalized, '')))
               OR LOWER(TRIM(COALESCE(b.name, ''))) = LOWER(TRIM(COALESCE(b.title, '')))
               OR LOWER(TRIM(COALESCE(b.name, ''))) = LOWER(TRIM(COALESCE(b.department, '')))
          THEN -8
          ELSE 0
        END
    ) AS person_name_score,
    CASE
      WHEN b.page_type_norm = 'staff_directory'
           OR b.source_context_norm LIKE '%page_class=staff_directory%'
      THEN 5
      WHEN b.source_context_norm LIKE 'revize:reconstructed_contact_block%' THEN 4
      WHEN b.page_type_norm = 'department_page'
           OR b.source_context_norm LIKE '%page_class=department_page%'
      THEN 2
      WHEN b.page_type_norm = 'contact_hub'
           OR b.source_context_norm LIKE '%page_class=contact_hub%'
      THEN -3
      WHEN b.page_type_norm IN ('generic', 'homepage', 'other')
           OR b.source_context_norm LIKE '%page_class=generic%'
      THEN -5
      ELSE 0
    END AS source_score,
    CASE
      WHEN b.source_context_norm LIKE 'revize:reconstructed_contact_block%' THEN 4
      ELSE 0
    END AS reconstruction_score,
    (
      CASE WHEN NULLIF(TRIM(COALESCE(b.email, '')), '') IS NOT NULL THEN 2 ELSE 0 END
      + CASE WHEN NULLIF(TRIM(COALESCE(b.phone, '')), '') IS NOT NULL THEN 2 ELSE 0 END
      + CASE WHEN NULLIF(TRIM(COALESCE(b.title, '')), '') IS NOT NULL THEN 1 ELSE 0 END
      + CASE WHEN NULLIF(TRIM(COALESCE(b.department, '')), '') IS NOT NULL THEN 1 ELSE 0 END
      + CASE
          WHEN NULLIF(TRIM(COALESCE(b.email, '')), '') IS NOT NULL
               AND NULLIF(TRIM(COALESCE(b.phone, '')), '') IS NOT NULL
          THEN 1
          ELSE 0
        END
    ) AS contact_score,
    CASE
      WHEN COALESCE(b.suspicious_reason, '') = 'role_department_mismatch' THEN -6
      WHEN b.role_normalized = 'Assessor'
           AND (b.department_norm LIKE '%assessor%' OR b.department_norm LIKE '%assessment%')
      THEN 3
      WHEN b.role_normalized = 'Tax Collector'
           AND (b.department_norm LIKE '%tax%' OR b.department_norm LIKE '%revenue%')
      THEN 3
      WHEN b.role_normalized = 'Town Clerk'
           AND b.department_norm LIKE '%clerk%'
      THEN 3
      WHEN b.role_normalized = 'Building Official'
           AND (b.department_norm LIKE '%building%' OR b.department_norm LIKE '%inspection%' OR b.department_norm LIKE '%zoning%')
      THEN 3
      WHEN b.role_normalized IN ('Land Use', 'Planner', 'Zoning Enforcement Officer', 'ZEO')
           AND (b.department_norm LIKE '%land use%' OR b.department_norm LIKE '%planning%' OR b.department_norm LIKE '%zoning%')
      THEN 3
      WHEN b.role_normalized IN ('Finance Director', 'Treasurer')
           AND (b.department_norm LIKE '%finance%' OR b.department_norm LIKE '%treasurer%' OR b.department_norm LIKE '%accounting%')
      THEN 3
      WHEN b.role_normalized = 'First Selectman'
           AND (b.department_norm LIKE '%selectman%' OR b.department_norm LIKE '%board of selectmen%')
      THEN 3
      WHEN b.role_normalized = 'Mayor'
           AND b.department_norm LIKE '%mayor%'
      THEN 3
      WHEN b.role_normalized IN ('Town Manager', 'Town Administrator')
           AND (b.department_norm LIKE '%town manager%' OR b.department_norm LIKE '%town administrator%')
      THEN 3
      ELSE 0
    END AS department_score,
    CASE
      WHEN b.role_normalized = 'Assessor'
           AND (
             b.title_norm LIKE '%assessor%'
             OR b.department_norm LIKE '%assessor%'
             OR b.department_norm LIKE '%assessment%'
             OR b.source_url_norm LIKE '%assessor%'
           )
      THEN 6
      WHEN b.role_normalized = 'Tax Collector'
           AND (
             b.title_norm LIKE '%tax%'
             OR b.title_norm LIKE '%revenue%'
             OR b.department_norm LIKE '%tax%'
             OR b.department_norm LIKE '%revenue%'
             OR b.source_url_norm LIKE '%tax%'
           )
      THEN 6
      WHEN b.role_normalized = 'Town Clerk'
           AND (
             b.title_norm LIKE '%clerk%'
             OR b.department_norm LIKE '%clerk%'
             OR b.source_url_norm LIKE '%clerk%'
           )
      THEN 6
      WHEN b.role_normalized = 'Building Official'
           AND (
             b.title_norm LIKE '%building%'
             OR b.title_norm LIKE '%inspection%'
             OR b.department_norm LIKE '%building%'
             OR b.department_norm LIKE '%inspection%'
             OR b.department_norm LIKE '%zoning%'
             OR b.source_url_norm LIKE '%building%'
             OR b.source_url_norm LIKE '%inspection%'
           )
      THEN 6
      WHEN b.role_normalized IN ('Land Use', 'Planner', 'Zoning Enforcement Officer', 'ZEO')
           AND (
             b.title_norm LIKE '%land use%'
             OR b.title_norm LIKE '%planner%'
             OR b.title_norm LIKE '%planning%'
             OR b.title_norm LIKE '%zoning%'
             OR b.department_norm LIKE '%land use%'
             OR b.department_norm LIKE '%planning%'
             OR b.department_norm LIKE '%zoning%'
             OR b.source_url_norm LIKE '%land-use%'
             OR b.source_url_norm LIKE '%planning%'
             OR b.source_url_norm LIKE '%zoning%'
           )
      THEN 6
      WHEN b.role_normalized = 'Finance Director'
           AND (
             b.title_norm LIKE '%finance%'
             OR b.title_norm LIKE '%treasurer%'
             OR b.title_norm LIKE '%accounting%'
             OR b.department_norm LIKE '%finance%'
             OR b.department_norm LIKE '%treasurer%'
             OR b.department_norm LIKE '%accounting%'
             OR b.source_url_norm LIKE '%finance%'
             OR b.source_url_norm LIKE '%treasurer%'
           )
      THEN 6
      WHEN b.role_normalized = 'Treasurer'
           AND (
             b.title_norm LIKE '%treasurer%'
             OR b.department_norm LIKE '%treasurer%'
             OR b.department_norm LIKE '%finance%'
             OR b.source_url_norm LIKE '%treasurer%'
           )
      THEN 6
      WHEN b.role_normalized = 'First Selectman'
           AND (
             b.title_norm LIKE '%first selectman%'
             OR b.title_norm LIKE '%selectman%'
             OR b.department_norm LIKE '%selectman%'
             OR b.source_url_norm LIKE '%selectman%'
           )
      THEN 6
      WHEN b.role_normalized = 'Mayor'
           AND (
             b.title_norm LIKE '%mayor%'
             OR b.department_norm LIKE '%mayor%'
             OR b.source_url_norm LIKE '%mayor%'
           )
      THEN 6
      WHEN b.role_normalized = 'Town Manager'
           AND (
             b.title_norm LIKE '%town manager%'
             OR b.department_norm LIKE '%town manager%'
             OR b.source_url_norm LIKE '%town_manager%'
             OR b.source_url_norm LIKE '%town-manager%'
           )
      THEN 6
      WHEN b.role_normalized = 'Town Administrator'
           AND (
             b.title_norm LIKE '%town administrator%'
             OR b.department_norm LIKE '%town administrator%'
             OR b.source_url_norm LIKE '%town_administrator%'
             OR b.source_url_norm LIKE '%town-administrator%'
           )
      THEN 6
      WHEN b.title_norm LIKE '%' || b.role_norm || '%'
           OR b.department_norm LIKE '%' || b.role_norm || '%'
      THEN 5
      ELSE 0
    END AS role_match_score,
    CASE
      WHEN COALESCE(b.suspicious_reason, '') = 'role_department_mismatch' THEN -5
      WHEN COALESCE(b.suspicious_reason, '') IN ('invalid_person_name', 'role_only_name') THEN -8
      WHEN COALESCE(b.suspicious_reason, '') IN ('contact_hub_candidate', 'assistant_role_contamination') THEN -4
      WHEN COALESCE(b.suspicious_reason, '') = 'low_context' THEN -3
      WHEN COALESCE(b.suspicious_reason, '') = 'non_person_role_candidate' THEN -2
      ELSE 0
    END AS suspicious_score,
    CASE
      WHEN b.is_revize = 1
           AND NULLIF(TRIM(COALESCE(b.name, '')), '') IS NULL
      THEN 1
      ELSE 0
    END AS blank_name_flag,
    CASE
      WHEN b.is_revize = 1
           AND (
             b.name_norm LIKE '%your link name%'
             OR b.name_norm = 'link name'
             OR b.name_norm LIKE '%click here%'
             OR b.name_norm LIKE '%email me%'
             OR b.name_norm LIKE '%affidavit%'
             OR b.name_norm LIKE '%vacanc%'
             OR b.name_norm IN ('building', 'land use', 'tax collector', 'assessor', 'department', 'office')
             OR (' ' || b.name_norm || ' ') LIKE '% department %'
             OR (' ' || b.name_norm || ' ') LIKE '% office %'
           )
      THEN 1
      ELSE 0
    END AS artifact_name_flag,
    CASE
      WHEN b.is_revize = 1
           AND (
             NULLIF(TRIM(COALESCE(b.role_normalized, '')), '') IS NOT NULL
             OR NULLIF(TRIM(COALESCE(b.role_family, '')), '') IS NOT NULL
           )
           AND (
             NULLIF(TRIM(COALESCE(b.email, '')), '') IS NOT NULL
             OR NULLIF(TRIM(COALESCE(b.phone, '')), '') IS NOT NULL
             OR b.source_context_norm LIKE 'revize:%'
           )
           AND COALESCE(b.is_likely_noise, 0) = 0
           AND b.page_type_norm <> 'contact_hub'
           AND b.name_norm NOT LIKE '%your link name%'
           AND b.name_norm <> 'link name'
           AND b.name_norm NOT LIKE '%click here%'
           AND b.name_norm NOT LIKE '%email me%'
           AND b.name_norm NOT LIKE '%affidavit%'
           AND b.name_norm NOT LIKE '%vacanc%'
           AND b.title_norm NOT LIKE '%vacanc%'
           AND b.title_norm NOT LIKE '%share this page%'
           AND b.title_norm NOT LIKE '%click here%'
           AND b.source_context_norm NOT LIKE '%share this page%'
           AND b.source_context_norm NOT LIKE '%copy and paste this code%'
      THEN 1
      ELSE 0
    END AS artifact_structural_allow_flag
  FROM base b
),
scored AS (
  SELECT
    s.*,
    CASE
      WHEN s.person_name_score >= 7 THEN 1
      ELSE 0
    END AS strong_name_quality,
    CASE
      WHEN s.role_match_score >= 5 THEN 1
      ELSE 0
    END AS strong_role_match,
    CASE
      WHEN (s.source_score + s.reconstruction_score) >= 3 THEN 1
      ELSE 0
    END AS strong_source_match,
    (
      s.person_name_score
      + s.role_match_score
      + s.source_score
      + s.reconstruction_score
      + s.contact_score
      + s.department_score
      + s.suspicious_score
    ) AS candidate_score,
    CASE
      WHEN s.is_revize = 1
           AND s.artifact_name_flag = 1
           AND COALESCE(s.artifact_structural_allow_flag, 0) = 0
      THEN 'artifact_name'
      WHEN s.is_revize = 1 AND s.blank_name_flag = 1 THEN 'blank_name'
      WHEN s.is_revize = 1
           AND COALESCE(s.suspicious_reason, '') IN ('invalid_person_name', 'role_only_name')
           AND NOT (s.artifact_name_flag = 1 AND COALESCE(s.artifact_structural_allow_flag, 0) = 1)
      THEN TRIM(COALESCE(s.suspicious_reason, ''))
      WHEN s.is_revize = 1 AND COALESCE(s.entity_type, '') <> 'person' THEN 'non_person_contact'
      WHEN s.is_revize = 1
           AND COALESCE(s.suspicious_reason, '') IN (
             'contact_hub_candidate',
             'role_department_mismatch',
             'assistant_role_contamination',
             'low_context'
           )
      THEN TRIM(COALESCE(s.suspicious_reason, ''))
      WHEN s.is_revize = 1
           AND s.page_type_norm = 'contact_hub'
           AND s.source_context_norm NOT LIKE 'revize:reconstructed_contact_block%'
      THEN 'contact_hub_candidate'
      WHEN s.is_revize = 1
           AND NULLIF(TRIM(COALESCE(s.email, '')), '') IS NULL
           AND NULLIF(TRIM(COALESCE(s.phone, '')), '') IS NULL
      THEN 'missing_email_and_phone'
      WHEN s.is_revize = 1
           AND s.person_name_score < 7
           AND NOT (s.artifact_name_flag = 1 AND COALESCE(s.artifact_structural_allow_flag, 0) = 1)
      THEN 'weak_name_quality'
      WHEN s.is_revize = 1 AND s.role_match_score < 5 THEN 'weak_role_match'
      WHEN s.is_revize = 1 AND (s.source_score + s.reconstruction_score) < 3 THEN 'weak_source_match'
      ELSE ''
    END AS winner_disqualifier_reason
  FROM component_scores s
),
ranked AS (
  SELECT
    s.*,
    CASE
      WHEN s.is_revize = 1 THEN
        CASE
          WHEN s.artifact_name_flag = 0
               AND s.blank_name_flag = 0
               AND COALESCE(s.entity_type, '') = 'person'
               AND NULLIF(TRIM(COALESCE(s.suspicious_reason, '')), '') IS NULL
               AND s.strong_name_quality = 1
               AND s.strong_role_match = 1
               AND s.strong_source_match = 1
               AND s.contact_score >= 3
               AND s.candidate_score >= 12
          THEN 1
          ELSE 0
        END
      ELSE 1
    END AS high_confidence_eligible,
    CASE
      WHEN s.is_revize = 1
           AND (
             s.artifact_name_flag = 0
             OR COALESCE(s.artifact_structural_allow_flag, 0) = 1
           )
           AND (
             COALESCE(s.suspicious_reason, '') NOT IN ('invalid_person_name', 'role_only_name')
             OR (s.artifact_name_flag = 1 AND COALESCE(s.artifact_structural_allow_flag, 0) = 1)
           )
           AND (
             NULLIF(TRIM(COALESCE(s.name, '')), '') IS NOT NULL
             OR NULLIF(TRIM(COALESCE(s.title, '')), '') IS NOT NULL
             OR NULLIF(TRIM(COALESCE(s.department, '')), '') IS NOT NULL
           )
           AND (
             NULLIF(TRIM(COALESCE(s.email, '')), '') IS NOT NULL
             OR NULLIF(TRIM(COALESCE(s.phone, '')), '') IS NOT NULL
             OR NULLIF(TRIM(COALESCE(s.title, '')), '') IS NOT NULL
             OR NULLIF(TRIM(COALESCE(s.department, '')), '') IS NOT NULL
           )
      THEN 1
      ELSE 0
    END AS review_candidate_eligible,
    ROW_NUMBER() OVER (
      PARTITION BY s.municipality_id, s.role_normalized
      ORDER BY
        s.candidate_score DESC,
        COALESCE(s.display_confidence, 0.0) DESC,
        COALESCE(s.contact_id, '') ASC
    ) AS candidate_rank,
    ROW_NUMBER() OVER (
      PARTITION BY s.municipality_id, s.role_normalized, CASE
        WHEN s.is_revize = 1 THEN
          CASE
            WHEN s.artifact_name_flag = 0
                 AND s.blank_name_flag = 0
                 AND COALESCE(s.entity_type, '') = 'person'
                 AND NULLIF(TRIM(COALESCE(s.suspicious_reason, '')), '') IS NULL
                 AND s.strong_name_quality = 1
                 AND s.strong_role_match = 1
                 AND s.strong_source_match = 1
                 AND s.contact_score >= 3
                 AND s.candidate_score >= 12
            THEN 1
            ELSE 0
          END
        ELSE 1
      END
      ORDER BY
        s.candidate_score DESC,
        COALESCE(s.display_confidence, 0.0) DESC,
        COALESCE(s.contact_id, '') ASC
    ) AS high_confidence_rank,
    SUM(
      CASE
        WHEN s.is_revize = 1 THEN
          CASE
            WHEN s.artifact_name_flag = 0
                 AND s.blank_name_flag = 0
                 AND COALESCE(s.entity_type, '') = 'person'
                 AND NULLIF(TRIM(COALESCE(s.suspicious_reason, '')), '') IS NULL
                 AND s.strong_name_quality = 1
                 AND s.strong_role_match = 1
                 AND s.strong_source_match = 1
                 AND s.contact_score >= 3
                 AND s.candidate_score >= 12
            THEN 1
            ELSE 0
          END
        ELSE 1
      END
    ) OVER (
      PARTITION BY s.municipality_id, s.role_normalized
    ) AS eligible_candidate_count
  FROM scored s
),
labeled AS (
  SELECT
    r.*,
    CASE
      WHEN r.high_confidence_eligible = 1 AND r.high_confidence_rank = 1 THEN 'high_confidence_winner'
      WHEN r.is_revize = 1 AND r.review_candidate_eligible = 1 THEN 'candidate_for_review'
      ELSE 'disqualified'
    END AS candidate_state,
    CASE
      WHEN r.is_revize = 1
           AND (
             (
               r.artifact_name_flag = 1
               AND COALESCE(r.artifact_structural_allow_flag, 0) = 0
             )
             OR (
               COALESCE(r.suspicious_reason, '') IN ('invalid_person_name', 'role_only_name')
               AND NOT (r.artifact_name_flag = 1 AND COALESCE(r.artifact_structural_allow_flag, 0) = 1)
             )
           )
      THEN 1
      ELSE 0
    END AS invalid_candidate_disqualified
  FROM ranked r
)
SELECT *
FROM labeled
""".strip()


FALLBACK_VW_UNRESOLVED_ROLES_SQL = """
CREATE VIEW vw_unresolved_roles AS
WITH grouped_candidates AS (
  SELECT
    v.*,
    COALESCE(NULLIF(TRIM(COALESCE(v.role_family, '')), ''), NULLIF(TRIM(COALESCE(v.role_normalized, '')), '')) AS role_group,
    ROW_NUMBER() OVER (
      PARTITION BY v.municipality_id,
                   COALESCE(NULLIF(TRIM(COALESCE(v.role_family, '')), ''), NULLIF(TRIM(COALESCE(v.role_normalized, '')), ''))
      ORDER BY
        v.candidate_score DESC,
        COALESCE(v.display_confidence, 0.0) DESC,
        COALESCE(v.contact_id, '') ASC
    ) AS role_group_rank
  FROM vw_role_candidates_scored v
  WHERE v.is_revize = 1
),
role_counts AS (
  SELECT
    municipality_id,
    role_group,
    COUNT(*) AS candidate_count,
    SUM(CASE WHEN candidate_state = 'candidate_for_review' THEN 1 ELSE 0 END) AS review_candidate_count,
    SUM(CASE WHEN invalid_candidate_disqualified = 1 THEN 1 ELSE 0 END) AS invalid_candidate_disqualified_count
  FROM grouped_candidates
  GROUP BY municipality_id, role_group
),
selected_winners AS (
  SELECT DISTINCT municipality_id, role_group
  FROM vw_best_role_per_town
  WHERE COALESCE(is_revize, 0) = 1
),
top_candidates AS (
  SELECT *
  FROM grouped_candidates
  WHERE role_group_rank = 1
)
SELECT
  rc.municipality_id,
  COALESCE(tc.role_normalized, rc.role_group) AS role_normalized,
  rc.candidate_count,
  rc.review_candidate_count,
  rc.invalid_candidate_disqualified_count,
  CASE
    WHEN rc.candidate_count > 0 AND sw.role_group IS NULL THEN 1
    ELSE 0
  END AS forced_fallback_blocked,
  tc.contact_id AS top_candidate_contact_id,
  tc.name AS top_candidate_name,
  tc.title AS top_candidate_title,
  tc.department AS top_candidate_department,
  tc.email AS top_candidate_email,
  tc.phone AS top_candidate_phone,
  tc.page_type AS top_candidate_page_type,
  tc.source_url AS top_candidate_source_url,
  tc.candidate_score AS top_candidate_score,
  tc.candidate_state AS top_candidate_state,
  tc.winner_disqualifier_reason AS top_candidate_winner_block_reason
FROM role_counts rc
LEFT JOIN selected_winners sw
  ON sw.municipality_id = rc.municipality_id
 AND sw.role_group = rc.role_group
LEFT JOIN top_candidates tc
  ON tc.municipality_id = rc.municipality_id
 AND tc.role_group = rc.role_group
WHERE sw.role_group IS NULL
""".strip()


FALLBACK_VW_BEST_ROLE_PER_TOWN_SQL = """
CREATE VIEW vw_best_role_per_town AS
WITH eligible_candidates AS (
  SELECT
    v.*,
    CASE
      WHEN COALESCE(v.is_revize, 0) = 1
      THEN COALESCE(NULLIF(TRIM(COALESCE(v.role_family, '')), ''), NULLIF(TRIM(COALESCE(v.role_normalized, '')), ''))
      ELSE NULLIF(TRIM(COALESCE(v.role_normalized, '')), '')
    END AS role_group,
    ROW_NUMBER() OVER (
      PARTITION BY v.municipality_id,
                   CASE
                     WHEN COALESCE(v.is_revize, 0) = 1
                     THEN COALESCE(NULLIF(TRIM(COALESCE(v.role_family, '')), ''), NULLIF(TRIM(COALESCE(v.role_normalized, '')), ''))
                     ELSE NULLIF(TRIM(COALESCE(v.role_normalized, '')), '')
                   END
      ORDER BY
        CASE
          WHEN v.candidate_state = 'high_confidence_winner' THEN 1
          WHEN v.candidate_state = 'candidate_for_review'
               AND COALESCE(v.is_likely_noise, 0) = 0
               AND NULLIF(TRIM(COALESCE(v.winner_disqualifier_reason, '')), '') IS NULL
          THEN 2
          ELSE 3
        END,
        v.candidate_score DESC,
        COALESCE(v.display_confidence, 0.0) DESC,
        COALESCE(v.contact_id, '') ASC
    ) AS rn,
    CASE
      WHEN v.candidate_state = 'high_confidence_winner' THEN 0
      ELSE 1
    END AS forced_fallback
  FROM vw_role_candidates_scored v
  WHERE v.candidate_state = 'high_confidence_winner'
     OR (
       COALESCE(v.is_revize, 0) = 1
       AND v.candidate_state = 'candidate_for_review'
       AND COALESCE(v.is_likely_noise, 0) = 0
       AND NULLIF(TRIM(COALESCE(v.winner_disqualifier_reason, '')), '') IS NULL
     )
)
SELECT *
FROM eligible_candidates
WHERE rn = 1
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run post-processing enrichment for one manifest batch.")
    parser.add_argument("--batch-id", required=True, help="Batch ID, e.g. batch_1")
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "data" / "manifests" / "civicplus_manifest.csv"),
        help="Manifest CSV path.",
    )
    parser.add_argument(
        "--platform",
        default=None,
        help="Optional platform filter (e.g. CivicPlus) based on municipalities_seed.csv platform column.",
    )
    parser.add_argument(
        "--seed-csv",
        default=str(ROOT / "config" / "municipalities_seed.csv"),
        help="Seed CSV path containing municipality_id + platform columns.",
    )
    parser.add_argument("--db", default=str(ROOT / "database" / "master.sqlite"), help="SQLite DB path.")
    parser.add_argument(
        "--allow-missing-required-objects",
        action="store_true",
        help="Do not fail when required postprocess views are missing after refresh (debug only).",
    )
    return parser.parse_args()


def placeholders(size: int) -> str:
    return ",".join("?" for _ in range(size))


def select_batch_municipality_ids(
    manifest_path: str | Path,
    batch_id: str,
    platform: str | None,
    seed_csv_path: str | Path,
) -> list[str]:
    rows = load_manifest_rows(manifest_path)
    selected = [row for row in rows if row["batch_id"].strip().lower() == batch_id.strip().lower()]
    if platform:
        platform_map = load_seed_platform_map(seed_csv_path)
        wanted = platform.strip().lower()
        selected = [
            row
            for row in selected
            if (platform_map.get(row["municipality_id"]) or "").strip().lower() == wanted
        ]
    municipality_ids = [row["municipality_id"] for row in selected]
    if not municipality_ids:
        raise SystemExit("No municipalities selected for post-processing.")
    return municipality_ids


def view_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def object_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({name})").fetchall()
    except sqlite3.OperationalError:
        return set()
    return {str(row["name"]).strip().lower() for row in rows if row["name"]}


def get_view_sql(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'view' AND name = ?",
        (name,),
    ).fetchone()
    if not row:
        return None
    sql = row[0]
    return str(sql).strip() if sql else None


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    existing = {
        str(row["name"]).strip().lower()
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name.strip().lower() in existing:
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_postprocess_columns(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "contacts"):
        for column_name, column_type in REQUIRED_CONTACT_COLUMNS:
            _ensure_column(conn, "contacts", column_name, column_type)
    if _table_exists(conn, "service_links"):
        for column_name, column_type in REQUIRED_SERVICE_LINK_COLUMNS:
            _ensure_column(conn, "service_links", column_name, column_type)


def ensure_hygiene_columns(conn: sqlite3.Connection) -> None:
    ensure_postprocess_columns(conn)


def count_metrics(conn: sqlite3.Connection, municipality_ids: list[str]) -> dict[str, int]:
    params = tuple(municipality_ids)
    where_in = placeholders(len(municipality_ids))

    metrics = {
        "raw_contacts": conn.execute(
            f"SELECT COUNT(*) FROM contacts WHERE municipality_id IN ({where_in})",
            params,
        ).fetchone()[0],
        "contacts_with_entity_type": conn.execute(
            f"""
            SELECT COUNT(*)
            FROM contacts
            WHERE municipality_id IN ({where_in})
              AND NULLIF(TRIM(COALESCE(entity_type, '')), '') IS NOT NULL
            """,
            params,
        ).fetchone()[0],
        "contacts_with_role_normalized": conn.execute(
            f"""
            SELECT COUNT(*)
            FROM contacts
            WHERE municipality_id IN ({where_in})
              AND NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NOT NULL
            """,
            params,
        ).fetchone()[0],
        "rows_in_vw_contacts_clean": 0,
        "rows_in_vw_best_role_per_town": 0,
        "revize_winner_rows_from_staff_directory": 0,
        "revize_winner_rows_from_department_pages": 0,
        "revize_winner_rows_from_contact_hubs": 0,
        "revize_winner_penalty_non_person_name": conn.execute(
            f"""
            SELECT COUNT(*)
            FROM contacts
            WHERE municipality_id IN ({where_in})
              AND LOWER(COALESCE(source_context, '')) LIKE 'revize:%'
              AND COALESCE(suspicious_reason, '') = 'invalid_person_name'
            """,
            params,
        ).fetchone()[0],
        "revize_winner_penalty_role_department_mismatch": conn.execute(
            f"""
            SELECT COUNT(*)
            FROM contacts
            WHERE municipality_id IN ({where_in})
              AND LOWER(COALESCE(source_context, '')) LIKE 'revize:%'
              AND COALESCE(suspicious_reason, '') = 'role_department_mismatch'
            """,
            params,
        ).fetchone()[0],
        "revize_winner_penalty_office_row": conn.execute(
            f"""
            SELECT COUNT(*)
            FROM contacts
            WHERE municipality_id IN ({where_in})
              AND LOWER(COALESCE(source_context, '')) LIKE 'revize:%'
              AND COALESCE(suspicious_reason, '') = 'non_person_role_candidate'
            """,
            params,
        ).fetchone()[0],
        "revize_candidates_scored": conn.execute(
            f"""
            SELECT COUNT(*)
            FROM contacts
            WHERE municipality_id IN ({where_in})
              AND LOWER(COALESCE(source_context, '')) LIKE 'revize:%'
              AND NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NOT NULL
            """,
            params,
        ).fetchone()[0],
        "revize_winners_selected": 0,
        "revize_candidates_for_review": 0,
        "revize_roles_unresolved": 0,
        "revize_invalid_candidates_disqualified": 0,
        "revize_forced_fallback_blocked": 0,
        "revize_roles_with_no_candidates": conn.execute(
            f"""
            SELECT COUNT(*)
            FROM (
              SELECT DISTINCT municipality_id, role_normalized
              FROM contacts
              WHERE municipality_id IN ({where_in})
                AND LOWER(COALESCE(source_context, '')) LIKE 'revize:%'
                AND NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NOT NULL
            ) rr
            """,
            params,
        ).fetchone()[0],
        "revize_roles_with_forced_fallback": 0,
        "revize_reconstructed_rows_promoted_to_winner": 0,
        "revize_garbage_rows_demoted": conn.execute(
            f"""
            SELECT COUNT(*)
            FROM contacts
            WHERE municipality_id IN ({where_in})
              AND LOWER(COALESCE(source_context, '')) LIKE 'revize:%'
              AND (
                    COALESCE(suspicious_reason, '') IN ('invalid_person_name', 'role_only_name')
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%affidavit%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%vacanc%'
                    OR LOWER(TRIM(COALESCE(name, ''))) IN ('building', 'tax collector', 'department', 'office')
                  )
            """,
            params,
        ).fetchone()[0],
    }

    if view_exists(conn, "vw_contacts_clean"):
        metrics["rows_in_vw_contacts_clean"] = conn.execute(
            f"SELECT COUNT(*) FROM vw_contacts_clean WHERE municipality_id IN ({where_in})",
            params,
        ).fetchone()[0]
    if view_exists(conn, "vw_role_candidates_scored"):
        metrics["revize_candidates_scored"] = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM vw_role_candidates_scored
            WHERE municipality_id IN ({where_in})
              AND COALESCE(is_revize, 0) = 1
            """,
            params,
        ).fetchone()[0]
        metrics["revize_candidates_for_review"] = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM vw_role_candidates_scored
            WHERE municipality_id IN ({where_in})
              AND COALESCE(is_revize, 0) = 1
              AND candidate_state = 'candidate_for_review'
            """,
            params,
        ).fetchone()[0]
        metrics["revize_invalid_candidates_disqualified"] = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM vw_role_candidates_scored
            WHERE municipality_id IN ({where_in})
              AND COALESCE(is_revize, 0) = 1
              AND COALESCE(invalid_candidate_disqualified, 0) = 1
            """,
            params,
        ).fetchone()[0]
    if view_exists(conn, "vw_unresolved_roles"):
        metrics["revize_roles_unresolved"] = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM vw_unresolved_roles
            WHERE municipality_id IN ({where_in})
            """,
            params,
        ).fetchone()[0]
        metrics["revize_forced_fallback_blocked"] = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM vw_unresolved_roles
            WHERE municipality_id IN ({where_in})
              AND COALESCE(forced_fallback_blocked, 0) = 1
            """,
            params,
        ).fetchone()[0]
    if view_exists(conn, "vw_best_role_per_town"):
        winner_columns = object_columns(conn, "vw_best_role_per_town")
        metrics["rows_in_vw_best_role_per_town"] = conn.execute(
            f"SELECT COUNT(*) FROM vw_best_role_per_town WHERE municipality_id IN ({where_in})",
            params,
        ).fetchone()[0]
        if "source_context" in winner_columns:
            metrics["revize_winners_selected"] = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM vw_best_role_per_town v
                WHERE v.municipality_id IN ({where_in})
                  AND LOWER(COALESCE(v.source_context, '')) LIKE 'revize:%'
                """,
                params,
            ).fetchone()[0]
        else:
            metrics["revize_winners_selected"] = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM vw_best_role_per_town v
                JOIN contacts c ON c.contact_id = v.contact_id
                WHERE v.municipality_id IN ({where_in})
                  AND LOWER(COALESCE(c.source_context, '')) LIKE 'revize:%'
                """,
                params,
            ).fetchone()[0]
        metrics["revize_winner_rows_from_staff_directory"] = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM vw_best_role_per_town v
            JOIN contacts c ON c.contact_id = v.contact_id
            WHERE v.municipality_id IN ({where_in})
              AND LOWER(COALESCE(c.source_context, '')) LIKE 'revize:%'
              AND (
                LOWER(COALESCE(c.source_context, '')) LIKE '%page_class=staff_directory%'
                OR LOWER(COALESCE(c.page_type, '')) = 'staff_directory'
              )
            """,
            params,
        ).fetchone()[0]
        metrics["revize_winner_rows_from_department_pages"] = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM vw_best_role_per_town v
            JOIN contacts c ON c.contact_id = v.contact_id
            WHERE v.municipality_id IN ({where_in})
              AND LOWER(COALESCE(c.source_context, '')) LIKE 'revize:%'
              AND (
                LOWER(COALESCE(c.source_context, '')) LIKE '%page_class=department_page%'
                OR LOWER(COALESCE(c.page_type, '')) = 'department_page'
              )
            """,
            params,
        ).fetchone()[0]
        metrics["revize_winner_rows_from_contact_hubs"] = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM vw_best_role_per_town v
            JOIN contacts c ON c.contact_id = v.contact_id
            WHERE v.municipality_id IN ({where_in})
              AND LOWER(COALESCE(c.source_context, '')) LIKE 'revize:%'
              AND (
                LOWER(COALESCE(c.source_context, '')) LIKE '%page_class=contact_hub%'
                OR LOWER(COALESCE(c.page_type, '')) = 'contact_hub'
              )
            """,
            params,
        ).fetchone()[0]
        metrics["revize_reconstructed_rows_promoted_to_winner"] = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM vw_best_role_per_town v
            JOIN contacts c ON c.contact_id = v.contact_id
            WHERE v.municipality_id IN ({where_in})
              AND LOWER(COALESCE(c.source_context, '')) LIKE 'revize:reconstructed_contact_block%'
            """,
            params,
        ).fetchone()[0]
        if "forced_fallback" in winner_columns:
            if "source_context" in winner_columns:
                metrics["revize_roles_with_forced_fallback"] = conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM vw_best_role_per_town v
                    WHERE v.municipality_id IN ({where_in})
                      AND COALESCE(v.forced_fallback, 0) = 1
                      AND LOWER(COALESCE(v.source_context, '')) LIKE 'revize:%'
                    """,
                    params,
                ).fetchone()[0]
            else:
                metrics["revize_roles_with_forced_fallback"] = conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM vw_best_role_per_town v
                    JOIN contacts c ON c.contact_id = v.contact_id
                    WHERE v.municipality_id IN ({where_in})
                      AND COALESCE(v.forced_fallback, 0) = 1
                      AND LOWER(COALESCE(c.source_context, '')) LIKE 'revize:%'
                    """,
                    params,
                ).fetchone()[0]
        if view_exists(conn, "vw_role_candidates_scored"):
            metrics["revize_roles_with_no_candidates"] = conn.execute(
                f"""
                WITH revize_role_scope AS (
                  SELECT DISTINCT municipality_id, role_normalized
                  FROM vw_role_candidates_scored
                  WHERE municipality_id IN ({where_in})
                    AND COALESCE(is_revize, 0) = 1
                ),
                winners AS (
                  SELECT DISTINCT municipality_id, role_normalized
                  FROM vw_best_role_per_town
                  WHERE municipality_id IN ({where_in})
                    AND NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NOT NULL
                )
                SELECT COUNT(*)
                FROM revize_role_scope r
                LEFT JOIN winners w
                  ON w.municipality_id = r.municipality_id
                 AND w.role_normalized = r.role_normalized
                WHERE w.role_normalized IS NULL
                """,
                params + params,
            ).fetchone()[0]
    return {key: int(value) for key, value in metrics.items()}


def run_batch_enrichment(conn: sqlite3.Connection, municipality_ids: list[str]) -> None:
    params = tuple(municipality_ids)
    where_in = placeholders(len(municipality_ids))
    where_contacts = f"municipality_id IN ({where_in})"
    where_services = f"municipality_id IN ({where_in})"

    conn.execute(
        f"""
        UPDATE contacts
        SET
            has_name = CASE WHEN NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL THEN 1 ELSE 0 END,
            has_email = CASE WHEN NULLIF(TRIM(COALESCE(email, '')), '') IS NOT NULL THEN 1 ELSE 0 END,
            has_phone = CASE WHEN NULLIF(TRIM(COALESCE(phone, '')), '') IS NOT NULL THEN 1 ELSE 0 END,
            has_department = CASE WHEN NULLIF(TRIM(COALESCE(department, '')), '') IS NOT NULL THEN 1 ELSE 0 END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET entity_type = CASE
            WHEN
                NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%email%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%contact%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%office%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%department%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%town hall%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%click here%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%phone%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%fax%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%hours%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%board of%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%commission%'
                AND LOWER(TRIM(COALESCE(name, ''))) NOT LIKE '%committee%'
            THEN 'person'
            WHEN
                (
                    NULLIF(TRIM(COALESCE(name, '')), '') IS NULL
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%email%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%contact%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%office%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%department%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%town hall%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%click here%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%phone%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%fax%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%hours%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%board of%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%commission%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%committee%'
                )
                AND NULLIF(TRIM(COALESCE(title, '')), '') IS NOT NULL
            THEN 'role'
            WHEN
                (
                    NULLIF(TRIM(COALESCE(name, '')), '') IS NULL
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%email%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%contact%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%office%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%department%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%town hall%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%click here%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%phone%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%fax%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%hours%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%board of%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%commission%'
                    OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%committee%'
                )
                AND NULLIF(TRIM(COALESCE(title, '')), '') IS NULL
                AND NULLIF(TRIM(COALESCE(department, '')), '') IS NOT NULL
            THEN 'department_contact'
            ELSE 'unknown'
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET is_role_only = CASE
            WHEN entity_type = 'role' THEN 1
            WHEN has_name = 0
                 AND (
                     NULLIF(TRIM(COALESCE(title, '')), '') IS NOT NULL
                     OR NULLIF(TRIM(COALESCE(department, '')), '') IS NOT NULL
                 )
            THEN 1
            ELSE 0
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET is_likely_noise = CASE
            WHEN
                LOWER(COALESCE(name, '')) LIKE 'email %'
                OR LOWER(COALESCE(title, '')) LIKE 'email %'
                OR LOWER(COALESCE(department, '')) LIKE 'email %'
                OR LOWER(COALESCE(name, '')) LIKE '%click here%'
                OR LOWER(COALESCE(title, '')) LIKE '%click here%'
                OR LOWER(COALESCE(department, '')) LIKE '%click here%'
                OR LOWER(COALESCE(name, '')) LIKE '%title:%'
                OR LOWER(COALESCE(title, '')) LIKE '%title:%'
                OR LOWER(COALESCE(department, '')) LIKE '%title:%'
                OR (
                    (LOWER(COALESCE(name, '')) LIKE '%board%' OR LOWER(COALESCE(title, '')) LIKE '%board%')
                    AND (LOWER(COALESCE(name, '')) LIKE '%phone%' OR LOWER(COALESCE(title, '')) LIKE '%phone%')
                )
                OR (
                    (LOWER(COALESCE(name, '')) LIKE '%commission%' OR LOWER(COALESCE(title, '')) LIKE '%commission%')
                    AND (LOWER(COALESCE(name, '')) LIKE '%phone%' OR LOWER(COALESCE(title, '')) LIKE '%phone%')
                )
                OR (
                    (LOWER(COALESCE(name, '')) LIKE '%committee%' OR LOWER(COALESCE(title, '')) LIKE '%committee%')
                    AND (LOWER(COALESCE(name, '')) LIKE '%phone%' OR LOWER(COALESCE(title, '')) LIKE '%phone%')
                )
                OR LOWER(TRIM(COALESCE(name, ''))) LIKE 'the %'
                OR LOWER(COALESCE(name, '')) LIKE '%google maps%'
                OR LOWER(COALESCE(name, '')) LIKE '%requested%'
                OR LOWER(COALESCE(name, '')) LIKE '%hours%'
                OR LOWER(COALESCE(name, '')) LIKE '%office%'
                OR LOWER(COALESCE(name, '')) LIKE '%department%'
                OR LOWER(COALESCE(name, '')) LIKE '%click%'
                OR LOWER(COALESCE(name, '')) LIKE '%view%'
                OR LOWER(COALESCE(name, '')) LIKE '%faq%'
                OR LENGTH(TRIM(COALESCE(name, ''))) > 80
                OR (
                    NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                    AND LOWER(TRIM(COALESCE(name, ''))) NOT GLOB '*[a-z]*'
                )
                OR (
                    NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                    AND NULLIF(TRIM(COALESCE(title, '')), '') IS NOT NULL
                    AND LOWER(
                        TRIM(
                            REPLACE(
                                REPLACE(
                                    REPLACE(REPLACE(COALESCE(name, ''), '.', ''), ',', ''),
                                    '-',
                                    ' '
                                ),
                                '  ',
                                ' '
                            )
                        )
                    ) = LOWER(
                        TRIM(
                            REPLACE(
                                REPLACE(
                                    REPLACE(REPLACE(COALESCE(title, ''), '.', ''), ',', ''),
                                    '-',
                                    ' '
                                ),
                                '  ',
                                ' '
                            )
                        )
                    )
                )
            THEN 1
            ELSE 0
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET page_type = CASE
            WHEN LOWER(COALESCE(source_context, '')) LIKE '%page_class=staff_directory%'
            THEN 'staff_directory'
            WHEN LOWER(COALESCE(source_context, '')) LIKE '%page_class=department_page%'
            THEN 'department_page'
            WHEN LOWER(COALESCE(source_context, '')) LIKE '%page_class=contact_hub%'
            THEN 'contact_hub'
            WHEN LOWER(COALESCE(source_context, '')) LIKE '%page_class=generic%'
            THEN 'generic'
            WHEN LOWER(COALESCE(source_url, '')) LIKE '%staff%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%directory%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%departments%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%directory.aspx%'
                 OR LOWER(COALESCE(source_context, '')) LIKE '%staff%'
                 OR LOWER(COALESCE(source_context, '')) LIKE '%directory%'
                 OR LOWER(COALESCE(source_context, '')) LIKE '%departments%'
            THEN 'staff_directory'
            WHEN LOWER(COALESCE(source_url, '')) LIKE '%assessor%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%tax%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%clerk%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%building%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%planning%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%zoning%'
            THEN 'department_page'
            WHEN
                NULLIF(TRIM(COALESCE(source_url, '')), '') IS NOT NULL
                AND (
                    (LENGTH(TRIM(COALESCE(source_url, ''))) - LENGTH(REPLACE(TRIM(COALESCE(source_url, '')), '/', ''))) <= 3
                    OR LOWER(TRIM(COALESCE(source_url, ''))) LIKE '%/index.%'
                    OR LOWER(TRIM(COALESCE(source_url, ''))) LIKE '%/home%'
                )
            THEN 'homepage'
            WHEN LOWER(COALESCE(source_url, '')) LIKE '%board%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%commission%'
                 OR LOWER(COALESCE(source_url, '')) LIKE '%committee%'
                 OR LOWER(COALESCE(source_context, '')) LIKE '%board%'
                 OR LOWER(COALESCE(source_context, '')) LIKE '%commission%'
                 OR LOWER(COALESCE(source_context, '')) LIKE '%committee%'
            THEN 'board_page'
            ELSE 'other'
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET
            role_normalized = CASE
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%first selectman%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%selectman%'
                THEN 'First Selectman'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%mayor%'
                THEN 'Mayor'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%town manager%'
                THEN 'Town Manager'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%town administrator%'
                THEN 'Town Administrator'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%assessor%'
                THEN 'Assessor'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%tax collector%'
                THEN 'Tax Collector'
                WHEN LOWER(TRIM(COALESCE(title, ''))) IN (
                    'revenue collector',
                    'tax office clerk',
                    'delinquent & deferral tax clerk'
                )
                     AND (
                         LOWER(COALESCE(NULLIF(TRIM(department), ''), '')) LIKE '%tax%'
                         OR LOWER(COALESCE(NULLIF(TRIM(department), ''), '')) LIKE '%revenue%'
                     )
                THEN 'Tax Collector'
                WHEN LOWER(TRIM(COALESCE(title, ''))) IN (
                    'town and city clerk, registrar of vital statistics',
                    'town and city clerk',
                    'deputy town and city clerk',
                    'deputy town and city clerk, cctc',
                    'city clerk'
                )
                THEN 'Town Clerk'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%town clerk%'
                THEN 'Town Clerk'
                WHEN LOWER(TRIM(COALESCE(title, ''))) IN (
                    'chief building official',
                    'acting building official',
                    'assistant building official'
                )
                THEN 'Building Official'
                WHEN LOWER(TRIM(COALESCE(title, ''))) IN (
                    'building inspector',
                    'code official'
                )
                     AND LOWER(COALESCE(NULLIF(TRIM(department), ''), '')) LIKE '%building%'
                THEN 'Building Official'
                WHEN LOWER(TRIM(COALESCE(title, ''))) IN (
                    'code enforcement officer',
                    'zoning/code enforcement officer',
                    'zoning/ code enforcement officer'
                )
                     AND LOWER(COALESCE(NULLIF(TRIM(department), ''), '')) LIKE '%building%'
                THEN 'Building Official'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%building official%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%building department%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%building%'
                THEN 'Building Official'
                WHEN LOWER(TRIM(COALESCE(title, ''))) = 'land use administrator'
                     AND (
                         LOWER(COALESCE(NULLIF(TRIM(department), ''), '')) LIKE '%land use%'
                         OR LOWER(COALESCE(NULLIF(TRIM(department), ''), '')) LIKE '%planning%'
                         OR LOWER(COALESCE(NULLIF(TRIM(department), ''), '')) LIKE '%zoning%'
                     )
                THEN 'Planner'
                WHEN LOWER(TRIM(COALESCE(title, ''))) = 'zoning administrator'
                     AND (
                         LOWER(COALESCE(NULLIF(TRIM(department), ''), '')) LIKE '%planning%'
                         OR LOWER(COALESCE(NULLIF(TRIM(department), ''), '')) LIKE '%zoning%'
                     )
                THEN 'Planner'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%land use%'
                THEN 'Land Use'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%planner%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%planning%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%zoning%'
                THEN 'Planner'
                WHEN LOWER(TRIM(COALESCE(title, ''))) IN (
                    'director of finance',
                    'title: director of finance',
                    'assistant director of finance',
                    'assistant director of finance - budget & grants',
                    'assistant director of finance - operations',
                    'director of finance and revenue',
                    'director of finance and administration',
                    'administrative officer / director of finance'
                )
                     OR LOWER(TRIM(COALESCE(title, ''))) LIKE 'assistant director of finance -%'
                     OR (
                         LOWER(TRIM(COALESCE(title, ''))) IN (
                             'finance manager',
                             'finance administrator',
                             'accounting manager'
                         )
                         AND (
                             LOWER(COALESCE(NULLIF(TRIM(department), ''), '')) LIKE '%finance%'
                             OR LOWER(COALESCE(NULLIF(TRIM(department), ''), '')) LIKE '%treasurer%'
                         )
                     )
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%finance director%'
                THEN 'Finance Director'
                WHEN LOWER(TRIM(COALESCE(title, ''))) IN (
                    'treasurer',
                    'town treasurer',
                    'city treasurer',
                    'borough treasurer'
                )
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%treasurer%'
                THEN 'Treasurer'
                ELSE NULL
            END,
            role_family = CASE
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%first selectman%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%selectman%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%mayor%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%town manager%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%town administrator%'
                THEN 'chief_executive'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%assessor%'
                THEN 'assessor'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%tax collector%'
                     OR (
                         LOWER(TRIM(COALESCE(title, ''))) IN (
                             'revenue collector',
                             'tax office clerk',
                             'delinquent & deferral tax clerk'
                         )
                         AND (
                             LOWER(COALESCE(NULLIF(TRIM(department), ''), '')) LIKE '%tax%'
                             OR LOWER(COALESCE(NULLIF(TRIM(department), ''), '')) LIKE '%revenue%'
                         )
                     )
                THEN 'tax_collector'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%town clerk%'
                THEN 'town_clerk'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%building%'
                THEN 'building'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%planner%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%planning%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%zoning%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%land use%'
                THEN 'planning_zoning'
                WHEN LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%finance director%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%treasurer%'
                     OR LOWER(COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(department), ''), '')) LIKE '%finance%'
                     OR (
                         LOWER(TRIM(COALESCE(title, ''))) = 'accounting manager'
                         AND (
                             LOWER(COALESCE(NULLIF(TRIM(department), ''), '')) LIKE '%finance%'
                             OR LOWER(COALESCE(NULLIF(TRIM(department), ''), '')) LIKE '%treasurer%'
                         )
                     )
                THEN 'finance'
                ELSE NULL
            END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET role_normalized = CASE
            WHEN LOWER(TRIM(COALESCE(title, ''))) IN (
                'director of finance',
                'title: director of finance',
                'assistant director of finance',
                'assistant director of finance - budget & grants',
                'assistant director of finance - operations',
                'director of finance and revenue',
                'director of finance and administration',
                'administrative officer / director of finance',
                'comptroller',
                'chief financial officer',
                'cfo'
            ) THEN 'Finance Director'
            WHEN LOWER(TRIM(COALESCE(title, ''))) LIKE 'assistant director of finance -%'
            THEN 'Finance Director'
            WHEN LOWER(TRIM(COALESCE(title, ''))) IN (
                'treasurer',
                'town treasurer',
                'city treasurer',
                'borough treasurer'
            ) THEN 'Treasurer'
            ELSE role_normalized
        END
        WHERE {where_contacts}
          AND NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NULL
          AND role_family = 'finance'
          AND (
                LOWER(TRIM(COALESCE(title, ''))) IN (
                    'director of finance',
                    'title: director of finance',
                    'assistant director of finance',
                    'assistant director of finance - budget & grants',
                    'assistant director of finance - operations',
                    'director of finance and revenue',
                    'director of finance and administration',
                    'administrative officer / director of finance',
                    'comptroller',
                    'chief financial officer',
                    'cfo',
                    'treasurer',
                    'town treasurer',
                    'city treasurer',
                    'borough treasurer'
                )
                OR LOWER(TRIM(COALESCE(title, ''))) LIKE 'assistant director of finance -%'
          )
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET department_normalized = CASE
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%assessor%'
            THEN 'Assessor'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%tax collector%'
            THEN 'Tax Collector'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%town clerk%'
            THEN 'Town Clerk'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%building%'
            THEN 'Building'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%planning%'
                 OR LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%zoning%'
            THEN 'Planning & Zoning'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%finance%'
                 OR LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%treasurer%'
            THEN 'Finance'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%land use%'
            THEN 'Land Use'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%board of selectmen%'
                 OR LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%selectmen%'
            THEN 'Board of Selectmen'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%mayor%'
            THEN 'Mayor''s Office'
            WHEN LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%town manager%'
                 OR LOWER(COALESCE(NULLIF(TRIM(department), ''), NULLIF(TRIM(title), ''), '')) LIKE '%town administrator%'
            THEN 'Town Manager'
            WHEN NULLIF(TRIM(COALESCE(department, '')), '') IS NOT NULL
            THEN TRIM(department)
            ELSE NULL
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET suspicious_reason = CASE
            WHEN LOWER(TRIM(COALESCE(page_type, ''))) IN ('staff_directory', 'directory')
            THEN NULL
            WHEN role_normalized = 'Finance Director'
                 AND NULLIF(TRIM(COALESCE(department, '')), '') IS NULL
            THEN 'low_context'
            WHEN role_normalized = 'Assessor'
                 AND NULLIF(TRIM(COALESCE(department, '')), '') IS NOT NULL
                 AND LOWER(TRIM(COALESCE(department, ''))) NOT IN ('staff', 'directory', 'staff directory')
                 AND LOWER(COALESCE(department, '')) NOT LIKE '%assessor%'
                 AND LOWER(COALESCE(department, '')) NOT LIKE '%assessment%'
            THEN 'role_department_mismatch'
            WHEN role_normalized = 'Tax Collector'
                 AND NULLIF(TRIM(COALESCE(department, '')), '') IS NOT NULL
                 AND LOWER(TRIM(COALESCE(department, ''))) NOT IN ('staff', 'directory', 'staff directory')
                 AND LOWER(COALESCE(department, '')) NOT LIKE '%tax%'
                 AND LOWER(COALESCE(department, '')) NOT LIKE '%revenue%'
            THEN 'role_department_mismatch'
            WHEN role_normalized = 'Town Clerk'
                 AND NULLIF(TRIM(COALESCE(department, '')), '') IS NOT NULL
                 AND LOWER(TRIM(COALESCE(department, ''))) NOT IN ('staff', 'directory', 'staff directory')
                 AND LOWER(COALESCE(department, '')) NOT LIKE '%clerk%'
            THEN 'role_department_mismatch'
            WHEN role_normalized = 'Building Official'
                 AND NULLIF(TRIM(COALESCE(department, '')), '') IS NOT NULL
                 AND LOWER(TRIM(COALESCE(department, ''))) NOT IN ('staff', 'directory', 'staff directory')
                 AND LOWER(COALESCE(department, '')) NOT LIKE '%building%'
            THEN 'role_department_mismatch'
            WHEN role_normalized = 'Finance Director'
                 AND NULLIF(TRIM(COALESCE(department, '')), '') IS NOT NULL
                 AND LOWER(TRIM(COALESCE(department, ''))) NOT IN ('staff', 'directory', 'staff directory')
                 AND LOWER(COALESCE(department, '')) NOT LIKE '%finance%'
                 AND LOWER(COALESCE(department, '')) NOT LIKE '%treasurer%'
            THEN 'role_department_mismatch'
            ELSE NULL
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET suspicious_reason = CASE
            WHEN NULLIF(TRIM(COALESCE(suspicious_reason, '')), '') IS NOT NULL
            THEN suspicious_reason
            WHEN NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NULL
            THEN suspicious_reason
            WHEN LOWER(COALESCE(source_context, '')) NOT LIKE 'revize:%'
            THEN suspicious_reason
            WHEN (
                NULLIF(TRIM(COALESCE(name, '')), '') IS NULL
                OR LOWER(COALESCE(name, '')) LIKE '% street%'
                OR LOWER(COALESCE(name, '')) LIKE '% avenue%'
                OR LOWER(COALESCE(name, '')) LIKE '% road%'
                OR LOWER(COALESCE(name, '')) LIKE '% lane%'
                OR LOWER(COALESCE(name, '')) LIKE '% drive%'
                OR LOWER(COALESCE(name, '')) LIKE '% ct %'
                OR LOWER(COALESCE(name, '')) LIKE '% connecticut%'
                OR LOWER(COALESCE(name, '')) GLOB '*[0-9][0-9][0-9][0-9][0-9]*'
            )
            THEN 'invalid_person_name'
            WHEN COALESCE(entity_type, '') <> 'person'
            THEN 'non_person_role_candidate'
            WHEN LOWER(COALESCE(page_type, '')) = 'contact_hub'
                 AND (
                    COALESCE(entity_type, '') <> 'person'
                    OR NULLIF(TRIM(COALESCE(name, '')), '') IS NULL
                    OR NULLIF(TRIM(COALESCE(title, '')), '') IS NULL
                    OR NULLIF(TRIM(COALESCE(department_normalized, '')), '') IS NULL
                    OR (
                        NULLIF(TRIM(COALESCE(email, '')), '') IS NULL
                        AND NULLIF(TRIM(COALESCE(phone, '')), '') IS NULL
                    )
                 )
            THEN 'contact_hub_candidate'
            WHEN LOWER(COALESCE(source_url, '')) LIKE '%contact%'
                 AND (
                    LOWER(COALESCE(title, '')) LIKE '%contact%'
                    OR LOWER(COALESCE(title, '')) LIKE '%department contact%'
                    OR LOWER(COALESCE(name, '')) LIKE '%request%'
                    OR LOWER(COALESCE(name, '')) LIKE '%information%'
                 )
            THEN 'contact_hub_candidate'
            WHEN role_normalized IN ('First Selectman', 'Mayor', 'Town Manager', 'Town Administrator')
                 AND (
                    LOWER(COALESCE(name, '')) LIKE '%assistant%'
                    OR LOWER(COALESCE(title, '')) LIKE '%assistant%'
                 )
            THEN 'assistant_role_contamination'
            ELSE suspicious_reason
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET suspicious_reason = CASE
            WHEN LOWER(COALESCE(source_context, '')) NOT LIKE 'revize:%'
            THEN suspicious_reason
            WHEN (
                LOWER(TRIM(COALESCE(name, ''))) LIKE '%affidavit%'
                OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%vacanc%'
                OR LOWER(TRIM(COALESCE(name, ''))) = 'building'
                OR LOWER(TRIM(COALESCE(name, ''))) = 'tax collector'
                OR LOWER(TRIM(COALESCE(name, ''))) = 'department'
                OR LOWER(TRIM(COALESCE(name, ''))) = 'office'
                OR (' ' || LOWER(TRIM(COALESCE(name, ''))) || ' ') LIKE '% department %'
                OR (' ' || LOWER(TRIM(COALESCE(name, ''))) || ' ') LIKE '% office %'
            )
            THEN 'invalid_person_name'
            WHEN NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                 AND (
                    LOWER(TRIM(COALESCE(name, ''))) = LOWER(TRIM(COALESCE(title, '')))
                    OR LOWER(TRIM(COALESCE(name, ''))) = LOWER(TRIM(COALESCE(role_normalized, '')))
                 )
            THEN 'role_only_name'
            ELSE suspicious_reason
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET semantic_confidence = MAX(
            0.0,
            MIN(
                1.0,
                COALESCE(confidence, 0.0)
                + CASE WHEN has_name = 1 THEN 0.15 ELSE 0.0 END
                + CASE WHEN has_email = 1 THEN 0.10 ELSE 0.0 END
                + CASE WHEN has_phone = 1 THEN 0.05 ELSE 0.0 END
                + CASE WHEN NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NOT NULL THEN 0.10 ELSE 0.0 END
                + CASE WHEN NULLIF(TRIM(COALESCE(department_normalized, '')), '') IS NOT NULL THEN 0.05 ELSE 0.0 END
                + CASE
                    WHEN LOWER(COALESCE(page_type, '')) = 'staff_directory' THEN 0.12
                    WHEN LOWER(COALESCE(page_type, '')) = 'department_page' THEN 0.06
                    WHEN LOWER(COALESCE(page_type, '')) = 'contact_hub' THEN -0.10
                    WHEN LOWER(COALESCE(page_type, '')) IN ('homepage', 'generic', 'other') THEN -0.12
                    ELSE 0.0
                  END
                - CASE WHEN is_likely_noise = 1 THEN 0.25 ELSE 0.0 END
                - CASE WHEN entity_type = 'unknown' THEN 0.10 ELSE 0.0 END
            )
        )
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET dedupe_key = LOWER(
            COALESCE(TRIM(municipality_id), '')
            || '|'
            || COALESCE(NULLIF(TRIM(role_normalized), ''), NULLIF(TRIM(title), ''), '')
            || '|'
            || COALESCE(
                NULLIF(TRIM(email), ''),
                NULLIF(TRIM(phone), ''),
                NULLIF(TRIM(name), ''),
                ''
            )
        )
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        WITH ranked AS (
            SELECT
                contact_id,
                ROW_NUMBER() OVER (
                    PARTITION BY COALESCE(dedupe_key, '')
                    ORDER BY
                        COALESCE(is_likely_noise, 0) ASC,
                        COALESCE(has_name, 0) DESC,
                        COALESCE(has_email, 0) DESC,
                        COALESCE(has_phone, 0) DESC,
                        COALESCE(semantic_confidence, 0.0) DESC,
                        COALESCE(source_url, '') ASC
                ) AS rn
            FROM contacts
            WHERE {where_contacts}
        )
        UPDATE contacts
        SET record_rank = (
            SELECT ranked.rn
            FROM ranked
            WHERE ranked.contact_id = contacts.contact_id
        )
        WHERE {where_contacts}
        """,
        params + params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET display_confidence = CASE
            WHEN COALESCE(record_rank, 1) = 1 THEN COALESCE(semantic_confidence, 0.0)
            ELSE MAX(0.0, MIN(1.0, COALESCE(semantic_confidence, 0.0) - 0.20))
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE contacts
        SET title = CASE
            WHEN NULLIF(TRIM(COALESCE(title, '')), '') IS NULL THEN NULL
            WHEN LOWER(COALESCE(title, '')) LIKE '%is responsible for%' THEN NULL
            WHEN (
                LENGTH(TRIM(COALESCE(title, ''))) - LENGTH(REPLACE(TRIM(COALESCE(title, '')), ' ', '')) + 1
            ) > 12 THEN NULL
            WHEN (
                (
                    LOWER(COALESCE(title, '')) LIKE '%.%'
                    OR LOWER(COALESCE(title, '')) LIKE '%?%'
                    OR LOWER(COALESCE(title, '')) LIKE '%!%'
                )
                AND (
                    LENGTH(TRIM(COALESCE(title, ''))) - LENGTH(REPLACE(TRIM(COALESCE(title, '')), ' ', '')) + 1
                ) >= 6
            ) THEN NULL
            ELSE TRIM(title)
        END
        WHERE {where_contacts}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE service_links
        SET service_type = NULLIF(TRIM(COALESCE(category, '')), '')
        WHERE {where_services}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE service_links
        SET service_type_normalized = CASE
            WHEN LOWER(COALESCE(category, '')) LIKE '%gis%'
                 OR LOWER(COALESCE(label, '')) LIKE '%gis%'
                 OR LOWER(COALESCE(url, '')) LIKE '%gis%'
            THEN 'gis'
            WHEN LOWER(COALESCE(category, '')) LIKE '%property%'
                 OR LOWER(COALESCE(category, '')) LIKE '%field card%'
                 OR LOWER(COALESCE(label, '')) LIKE '%property card%'
                 OR LOWER(COALESCE(label, '')) LIKE '%field card%'
                 OR LOWER(COALESCE(url, '')) LIKE '%property%'
            THEN 'property_cards'
            WHEN LOWER(COALESCE(category, '')) LIKE '%tax%'
                 OR LOWER(COALESCE(label, '')) LIKE '%tax payment%'
                 OR LOWER(COALESCE(url, '')) LIKE '%tax%'
            THEN 'tax_payment'
            WHEN LOWER(COALESCE(category, '')) LIKE '%job%'
                 OR LOWER(COALESCE(category, '')) LIKE '%employment%'
                 OR LOWER(COALESCE(label, '')) LIKE '%job%'
                 OR LOWER(COALESCE(label, '')) LIKE '%employment%'
            THEN 'jobs'
            WHEN LOWER(COALESCE(category, '')) LIKE '%permit%'
                 OR LOWER(COALESCE(label, '')) LIKE '%permit%'
                 OR LOWER(COALESCE(url, '')) LIKE '%permit%'
            THEN 'permits'
            WHEN LOWER(COALESCE(category, '')) LIKE '%agenda%'
                 OR LOWER(COALESCE(category, '')) LIKE '%minute%'
                 OR LOWER(COALESCE(label, '')) LIKE '%agenda%'
                 OR LOWER(COALESCE(label, '')) LIKE '%minute%'
                 OR LOWER(COALESCE(url, '')) LIKE '%agenda%'
                 OR LOWER(COALESCE(url, '')) LIKE '%minute%'
            THEN 'agendas_minutes'
            ELSE NULLIF(TRIM(COALESCE(category, '')), '')
        END
        WHERE {where_services}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE service_links
        SET provider_normalized = CASE
            WHEN LOWER(COALESCE(vendor, '')) LIKE '%civicplus%' THEN 'CivicPlus'
            WHEN LOWER(COALESCE(vendor, '')) LIKE '%governmentjobs%' THEN 'GovernmentJobs'
            WHEN LOWER(COALESCE(vendor, '')) LIKE '%vision%' OR LOWER(COALESCE(vendor, '')) LIKE '%vision government solutions%' THEN 'Vision'
            ELSE NULLIF(TRIM(COALESCE(vendor, '')), '')
        END
        WHERE {where_services}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE service_links
        SET is_external = CASE
            WHEN LOWER(COALESCE(service_page_type, '')) LIKE '%external%'
                 OR LOWER(COALESCE(service_page_type, '')) LIKE '%vendor%'
                 OR LOWER(COALESCE(service_page_type, '')) LIKE '%third%'
            THEN 1
            WHEN EXISTS (
                SELECT 1
                FROM municipalities m
                WHERE m.municipality_id = service_links.municipality_id
                  AND NULLIF(TRIM(COALESCE(service_links.domain, '')), '') IS NOT NULL
                  AND NULLIF(TRIM(COALESCE(m.domain, '')), '') IS NOT NULL
                  AND LOWER(TRIM(service_links.domain)) NOT LIKE '%' || LOWER(TRIM(m.domain))
                  AND LOWER(TRIM(m.domain)) NOT LIKE '%' || LOWER(TRIM(service_links.domain))
            )
            THEN 1
            ELSE 0
        END
        WHERE {where_services}
        """,
        params,
    )

    conn.execute(
        f"""
        UPDATE service_links
        SET display_confidence = MAX(
            0.0,
            MIN(
                1.0,
                COALESCE(confidence, 0.0)
                + CASE WHEN NULLIF(TRIM(COALESCE(provider_normalized, '')), '') IS NOT NULL THEN 0.05 ELSE 0.0 END
                + CASE WHEN NULLIF(TRIM(COALESCE(service_type_normalized, '')), '') IS NOT NULL THEN 0.05 ELSE 0.0 END
            )
        )
        WHERE {where_services}
        """,
        params,
    )


def refresh_views(conn: sqlite3.Connection) -> None:
    current_vw_contacts_clean = get_view_sql(conn, "vw_contacts_clean")

    conn.execute("DROP VIEW IF EXISTS vw_best_role_per_town")
    conn.execute("DROP VIEW IF EXISTS vw_unresolved_roles")
    conn.execute("DROP VIEW IF EXISTS vw_role_candidates_scored")
    conn.execute("DROP VIEW IF EXISTS vw_contacts_clean")

    conn.execute(current_vw_contacts_clean or FALLBACK_VW_CONTACTS_CLEAN_SQL)
    conn.execute(FALLBACK_VW_ROLE_CANDIDATES_SCORED_SQL)
    conn.execute(FALLBACK_VW_BEST_ROLE_PER_TOWN_SQL)
    conn.execute(FALLBACK_VW_UNRESOLVED_ROLES_SQL)


def _count_rows_for_scope(conn: sqlite3.Connection, object_name: str, municipality_ids: list[str]) -> int:
    if not municipality_ids:
        return 0
    where_in = placeholders(len(municipality_ids))
    row = conn.execute(
        f"SELECT COUNT(*) FROM {object_name} WHERE municipality_id IN ({where_in})",
        tuple(municipality_ids),
    ).fetchone()
    return int(row[0] if row else 0)


def verify_required_postprocess_objects(
    conn: sqlite3.Connection,
    municipality_ids: list[str],
    strict: bool = True,
) -> dict[str, int]:
    missing: list[str] = []
    for object_type, name in REQUIRED_POSTPROCESS_OBJECTS:
        if not view_exists(conn, name):
            missing.append(f"{object_type}:{name}")
    if missing and strict:
        missing_str = ", ".join(missing)
        raise RuntimeError(
            "Required postprocess objects missing after refresh: "
            f"{missing_str}. Run postprocess against a DB with base tables and enrichment columns."
        )

    summary = {
        "vw_contacts_clean_exists": 1 if view_exists(conn, "vw_contacts_clean") else 0,
        "vw_role_candidates_scored_exists": 1 if view_exists(conn, "vw_role_candidates_scored") else 0,
        "vw_unresolved_roles_exists": 1 if view_exists(conn, "vw_unresolved_roles") else 0,
        "vw_best_role_per_town_exists": 1 if view_exists(conn, "vw_best_role_per_town") else 0,
        "rows_in_vw_contacts_clean_scope": 0,
        "rows_in_vw_role_candidates_scored_scope": 0,
        "rows_in_vw_unresolved_roles_scope": 0,
        "rows_in_vw_best_role_per_town_scope": 0,
    }
    if summary["vw_contacts_clean_exists"]:
        summary["rows_in_vw_contacts_clean_scope"] = _count_rows_for_scope(
            conn,
            "vw_contacts_clean",
            municipality_ids,
        )
    if summary["vw_role_candidates_scored_exists"]:
        summary["rows_in_vw_role_candidates_scored_scope"] = _count_rows_for_scope(
            conn,
            "vw_role_candidates_scored",
            municipality_ids,
        )
    if summary["vw_unresolved_roles_exists"]:
        summary["rows_in_vw_unresolved_roles_scope"] = _count_rows_for_scope(
            conn,
            "vw_unresolved_roles",
            municipality_ids,
        )
    if summary["vw_best_role_per_town_exists"]:
        summary["rows_in_vw_best_role_per_town_scope"] = _count_rows_for_scope(
            conn,
            "vw_best_role_per_town",
            municipality_ids,
        )
    return summary


def print_metrics(title: str, metrics: dict[str, int]) -> None:
    print(title)
    print(f"  raw contacts: {metrics['raw_contacts']}")
    print(f"  contacts with entity_type: {metrics['contacts_with_entity_type']}")
    print(f"  contacts with role_normalized: {metrics['contacts_with_role_normalized']}")
    print(f"  rows in vw_contacts_clean: {metrics['rows_in_vw_contacts_clean']}")
    print(f"  rows in vw_best_role_per_town: {metrics['rows_in_vw_best_role_per_town']}")
    print(f"  revize winners selected: {metrics['revize_winners_selected']}")
    print(f"  revize winner rows (staff_directory): {metrics['revize_winner_rows_from_staff_directory']}")
    print(f"  revize winner rows (department_page): {metrics['revize_winner_rows_from_department_pages']}")
    print(f"  revize winner rows (contact_hub): {metrics['revize_winner_rows_from_contact_hubs']}")
    print(f"  revize winner penalty (non-person name): {metrics['revize_winner_penalty_non_person_name']}")
    print(
        f"  revize winner penalty (role/department mismatch): {metrics['revize_winner_penalty_role_department_mismatch']}"
    )
    print(f"  revize winner penalty (office row): {metrics['revize_winner_penalty_office_row']}")
    print(
        "  revize reconstructed rows promoted to winner: "
        f"{metrics['revize_reconstructed_rows_promoted_to_winner']}"
    )
    print(f"  revize garbage rows demoted: {metrics['revize_garbage_rows_demoted']}")
    print(f"  revize candidates scored: {metrics['revize_candidates_scored']}")
    print(f"  revize candidates for review: {metrics['revize_candidates_for_review']}")
    print(f"  revize roles unresolved: {metrics['revize_roles_unresolved']}")
    print(f"  revize invalid candidates disqualified: {metrics['revize_invalid_candidates_disqualified']}")
    print(f"  revize roles with no candidates: {metrics['revize_roles_with_no_candidates']}")
    print(f"  revize roles with forced fallback: {metrics['revize_roles_with_forced_fallback']}")
    print(f"  revize forced fallback blocked: {metrics['revize_forced_fallback_blocked']}")


def main() -> None:
    args = parse_args()
    municipality_ids = select_batch_municipality_ids(
        manifest_path=args.manifest,
        batch_id=args.batch_id,
        platform=args.platform,
        seed_csv_path=args.seed_csv,
    )

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    verification: dict[str, int] = {}
    try:
        ensure_postprocess_columns(conn)
        before = count_metrics(conn, municipality_ids)
        run_batch_enrichment(conn, municipality_ids)
        refresh_views(conn)
        verification = verify_required_postprocess_objects(
            conn,
            municipality_ids,
            strict=not args.allow_missing_required_objects,
        )
        conn.commit()
        after = count_metrics(conn, municipality_ids)
    finally:
        conn.close()

    print(f"Batch post-processing complete for {args.batch_id}")
    print(f"Municipalities in scope: {len(municipality_ids)}")
    print("Postprocess verification:")
    print(f"  vw_contacts_clean exists: {verification['vw_contacts_clean_exists']}")
    print(f"  vw_role_candidates_scored exists: {verification['vw_role_candidates_scored_exists']}")
    print(f"  vw_unresolved_roles exists: {verification['vw_unresolved_roles_exists']}")
    print(f"  vw_best_role_per_town exists: {verification['vw_best_role_per_town_exists']}")
    print(f"  vw_contacts_clean rows (scope): {verification['rows_in_vw_contacts_clean_scope']}")
    print(f"  vw_role_candidates_scored rows (scope): {verification['rows_in_vw_role_candidates_scored_scope']}")
    print(f"  vw_unresolved_roles rows (scope): {verification['rows_in_vw_unresolved_roles_scope']}")
    print(f"  vw_best_role_per_town rows (scope): {verification['rows_in_vw_best_role_per_town_scope']}")
    print_metrics("Before:", before)
    print_metrics("After:", after)


if __name__ == "__main__":
    main()
