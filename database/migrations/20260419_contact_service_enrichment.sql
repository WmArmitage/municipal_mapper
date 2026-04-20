-- SQLite migration: production-oriented contact/service enrichment
-- IMPORTANT:
-- 1) SQLite does not support ALTER TABLE ... ADD COLUMN IF NOT EXISTS.
-- 2) Run each ALTER TABLE ADD COLUMN statement only when the column is absent
--    (typically via Python PRAGMA table_info guards).

BEGIN;

-- ==================================================
-- PART 1 - ALTER TABLE CHANGES (run with column-exists guards)
-- ==================================================

-- contacts
ALTER TABLE contacts ADD COLUMN entity_type TEXT;
ALTER TABLE contacts ADD COLUMN role_normalized TEXT;
ALTER TABLE contacts ADD COLUMN role_family TEXT;
ALTER TABLE contacts ADD COLUMN department_normalized TEXT;
ALTER TABLE contacts ADD COLUMN page_type TEXT;
ALTER TABLE contacts ADD COLUMN has_name INTEGER DEFAULT 0;
ALTER TABLE contacts ADD COLUMN has_email INTEGER DEFAULT 0;
ALTER TABLE contacts ADD COLUMN has_phone INTEGER DEFAULT 0;
ALTER TABLE contacts ADD COLUMN has_department INTEGER DEFAULT 0;
ALTER TABLE contacts ADD COLUMN is_role_only INTEGER DEFAULT 0;
ALTER TABLE contacts ADD COLUMN is_likely_noise INTEGER DEFAULT 0;
ALTER TABLE contacts ADD COLUMN dedupe_key TEXT;
ALTER TABLE contacts ADD COLUMN record_rank INTEGER;
ALTER TABLE contacts ADD COLUMN semantic_confidence REAL;
ALTER TABLE contacts ADD COLUMN display_confidence REAL;

-- service_links
ALTER TABLE service_links ADD COLUMN service_type TEXT;
ALTER TABLE service_links ADD COLUMN service_type_normalized TEXT;
ALTER TABLE service_links ADD COLUMN provider_normalized TEXT;
ALTER TABLE service_links ADD COLUMN is_external INTEGER DEFAULT 0;
ALTER TABLE service_links ADD COLUMN display_confidence REAL;

-- New evidence table
CREATE TABLE IF NOT EXISTS contact_evidence (
    evidence_id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL,
    municipality_id TEXT,
    source_url TEXT,
    source_context TEXT,
    extraction_method TEXT,
    field_claims_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (contact_id) REFERENCES contacts(contact_id),
    FOREIGN KEY (municipality_id) REFERENCES municipalities(municipality_id)
);

-- ==================================================
-- PART 3 - CONTACTS BACKFILL / NORMALIZATION
-- ==================================================

-- A) Basic presence flags
UPDATE contacts
SET
    has_name = CASE WHEN NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL THEN 1 ELSE 0 END,
    has_email = CASE WHEN NULLIF(TRIM(COALESCE(email, '')), '') IS NOT NULL THEN 1 ELSE 0 END,
    has_phone = CASE WHEN NULLIF(TRIM(COALESCE(phone, '')), '') IS NOT NULL THEN 1 ELSE 0 END,
    has_department = CASE WHEN NULLIF(TRIM(COALESCE(department, '')), '') IS NOT NULL THEN 1 ELSE 0 END;

-- B) entity_type
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
END;

-- C) is_role_only
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
END;

-- D) is_likely_noise
UPDATE contacts
SET is_likely_noise = CASE
    WHEN
        -- obvious action/noise text
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
        OR (
            NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
            AND TRIM(
                REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                    LOWER(COALESCE(name, '')),
                    '0',''),'1',''),'2',''),'3',''),'4',''),'5',''),'6',''),'7',''),'8',''),'9',''),
                    '(',''),
                    ')','')
            ) = ''
        )
        OR (
            NULLIF(TRIM(COALESCE(title, '')), '') IS NOT NULL
            AND TRIM(
                REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                    LOWER(COALESCE(title, '')),
                    '0',''),'1',''),'2',''),'3',''),'4',''),'5',''),'6',''),'7',''),'8',''),'9',''),
                    '(',''),
                    ')','')
            ) = ''
        )
    THEN 1
    ELSE 0
END;

-- E) page_type
UPDATE contacts
SET page_type = CASE
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
END;

-- F) role_normalized and role_family (title first, department fallback)
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
        'director of finance and administration'
    )
         OR LOWER(TRIM(COALESCE(title, ''))) LIKE 'assistant director of finance -%'
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
        THEN 'finance'
        ELSE NULL
    END;

-- F2) Conservative finance fallback from title only when family already resolved to finance
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
WHERE NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NULL
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
          'comptroller',
          'chief financial officer',
          'cfo',
          'treasurer',
          'town treasurer',
          'city treasurer',
          'borough treasurer'
      )
      OR LOWER(TRIM(COALESCE(title, ''))) LIKE 'assistant director of finance -%'
  );

-- G) department_normalized (department first, title fallback)
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
END;

-- H) semantic_confidence
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
        - CASE WHEN is_likely_noise = 1 THEN 0.25 ELSE 0.0 END
        - CASE WHEN entity_type = 'unknown' THEN 0.10 ELSE 0.0 END
    )
);

-- I) dedupe_key
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
);

-- J) record_rank using window function
WITH ranked AS (
    SELECT
        contact_id,
        ROW_NUMBER() OVER (
            PARTITION BY COALESCE(dedupe_key, '')
            ORDER BY
                COALESCE(has_name, 0) DESC,
                COALESCE(has_email, 0) DESC,
                COALESCE(has_phone, 0) DESC,
                COALESCE(is_likely_noise, 0) ASC,
                COALESCE(semantic_confidence, 0.0) DESC,
                COALESCE(source_url, '') ASC
        ) AS rn
    FROM contacts
)
UPDATE contacts
SET record_rank = (
    SELECT ranked.rn
    FROM ranked
    WHERE ranked.contact_id = contacts.contact_id
);

-- K) display_confidence
UPDATE contacts
SET display_confidence = CASE
    WHEN COALESCE(record_rank, 1) = 1 THEN COALESCE(semantic_confidence, 0.0)
    ELSE MAX(0.0, MIN(1.0, COALESCE(semantic_confidence, 0.0) - 0.20))
END;

-- ==================================================
-- PART 4 - SERVICE_LINKS BACKFILL / NORMALIZATION
-- ==================================================

-- A) service_type
UPDATE service_links
SET service_type = NULLIF(TRIM(COALESCE(category, '')), '');

-- B) service_type_normalized
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
END;

-- C) provider_normalized
UPDATE service_links
SET provider_normalized = CASE
    WHEN LOWER(COALESCE(vendor, '')) LIKE '%civicplus%' THEN 'CivicPlus'
    WHEN LOWER(COALESCE(vendor, '')) LIKE '%governmentjobs%' THEN 'GovernmentJobs'
    WHEN LOWER(COALESCE(vendor, '')) LIKE '%vision%' OR LOWER(COALESCE(vendor, '')) LIKE '%vision government solutions%' THEN 'Vision'
    ELSE NULLIF(TRIM(COALESCE(vendor, '')), '')
END;

-- D) is_external
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
END;

-- E) display_confidence
UPDATE service_links
SET display_confidence = MAX(
    0.0,
    MIN(
        1.0,
        COALESCE(confidence, 0.0)
        + CASE WHEN NULLIF(TRIM(COALESCE(provider_normalized, '')), '') IS NOT NULL THEN 0.05 ELSE 0.0 END
        + CASE WHEN NULLIF(TRIM(COALESCE(service_type_normalized, '')), '') IS NOT NULL THEN 0.05 ELSE 0.0 END
    )
);

-- ==================================================
-- PART 5 - INDEXES
-- ==================================================

-- contacts
CREATE INDEX IF NOT EXISTS idx_contacts_municipality_id ON contacts (municipality_id);
CREATE INDEX IF NOT EXISTS idx_contacts_entity_type ON contacts (entity_type);
CREATE INDEX IF NOT EXISTS idx_contacts_role_normalized ON contacts (role_normalized);
CREATE INDEX IF NOT EXISTS idx_contacts_role_family ON contacts (role_family);
CREATE INDEX IF NOT EXISTS idx_contacts_department_normalized ON contacts (department_normalized);
CREATE INDEX IF NOT EXISTS idx_contacts_dedupe_key ON contacts (dedupe_key);
CREATE INDEX IF NOT EXISTS idx_contacts_display_confidence ON contacts (display_confidence);

-- service_links
CREATE INDEX IF NOT EXISTS idx_service_links_municipality_id ON service_links (municipality_id);
CREATE INDEX IF NOT EXISTS idx_service_links_service_type_normalized ON service_links (service_type_normalized);
CREATE INDEX IF NOT EXISTS idx_service_links_provider_normalized ON service_links (provider_normalized);

-- contact_evidence
CREATE INDEX IF NOT EXISTS idx_contact_evidence_contact_id ON contact_evidence (contact_id);
CREATE INDEX IF NOT EXISTS idx_contact_evidence_municipality_id ON contact_evidence (municipality_id);

-- ==================================================
-- PART 6 - STREAMLIT / APP-FACING VIEWS
-- ==================================================

DROP VIEW IF EXISTS vw_contacts_clean;
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
  );

DROP VIEW IF EXISTS vw_role_directory;
CREATE VIEW vw_role_directory AS
SELECT
    municipality_id,
    role_normalized,
    role_family,
    name,
    email,
    phone,
    department_normalized,
    source_url,
    display_confidence
FROM vw_contacts_clean
WHERE entity_type IN ('person', 'role', 'department_contact')
  AND NULLIF(TRIM(COALESCE(role_normalized, '')), '') IS NOT NULL;

DROP VIEW IF EXISTS vw_service_directory;
CREATE VIEW vw_service_directory AS
SELECT
    service_id,
    municipality_id,
    service_type_normalized,
    label,
    url,
    provider_normalized,
    domain,
    is_external,
    display_confidence,
    source_url
FROM service_links;

DROP VIEW IF EXISTS vw_town_profile;
CREATE VIEW vw_town_profile AS
SELECT
    m.municipality_id,

    (
        SELECT c.name
        FROM vw_contacts_clean c
        WHERE c.municipality_id = m.municipality_id
          AND c.role_family = 'chief_executive'
        ORDER BY COALESCE(c.display_confidence, 0.0) DESC, COALESCE(c.source_url, '') ASC
        LIMIT 1
    ) AS chief_executive_name,
    (
        SELECT c.email
        FROM vw_contacts_clean c
        WHERE c.municipality_id = m.municipality_id
          AND c.role_family = 'chief_executive'
        ORDER BY COALESCE(c.display_confidence, 0.0) DESC, COALESCE(c.source_url, '') ASC
        LIMIT 1
    ) AS chief_executive_email,
    (
        SELECT c.phone
        FROM vw_contacts_clean c
        WHERE c.municipality_id = m.municipality_id
          AND c.role_family = 'chief_executive'
        ORDER BY COALESCE(c.display_confidence, 0.0) DESC, COALESCE(c.source_url, '') ASC
        LIMIT 1
    ) AS chief_executive_phone,

    (
        SELECT c.name
        FROM vw_contacts_clean c
        WHERE c.municipality_id = m.municipality_id
          AND c.role_family = 'assessor'
        ORDER BY COALESCE(c.display_confidence, 0.0) DESC, COALESCE(c.source_url, '') ASC
        LIMIT 1
    ) AS assessor_name,
    (
        SELECT c.email
        FROM vw_contacts_clean c
        WHERE c.municipality_id = m.municipality_id
          AND c.role_family = 'assessor'
        ORDER BY COALESCE(c.display_confidence, 0.0) DESC, COALESCE(c.source_url, '') ASC
        LIMIT 1
    ) AS assessor_email,
    (
        SELECT c.phone
        FROM vw_contacts_clean c
        WHERE c.municipality_id = m.municipality_id
          AND c.role_family = 'assessor'
        ORDER BY COALESCE(c.display_confidence, 0.0) DESC, COALESCE(c.source_url, '') ASC
        LIMIT 1
    ) AS assessor_phone,

    (
        SELECT c.name
        FROM vw_contacts_clean c
        WHERE c.municipality_id = m.municipality_id
          AND c.role_family = 'tax_collector'
        ORDER BY COALESCE(c.display_confidence, 0.0) DESC, COALESCE(c.source_url, '') ASC
        LIMIT 1
    ) AS tax_collector_name,
    (
        SELECT c.email
        FROM vw_contacts_clean c
        WHERE c.municipality_id = m.municipality_id
          AND c.role_family = 'tax_collector'
        ORDER BY COALESCE(c.display_confidence, 0.0) DESC, COALESCE(c.source_url, '') ASC
        LIMIT 1
    ) AS tax_collector_email,
    (
        SELECT c.phone
        FROM vw_contacts_clean c
        WHERE c.municipality_id = m.municipality_id
          AND c.role_family = 'tax_collector'
        ORDER BY COALESCE(c.display_confidence, 0.0) DESC, COALESCE(c.source_url, '') ASC
        LIMIT 1
    ) AS tax_collector_phone,

    (
        SELECT c.name
        FROM vw_contacts_clean c
        WHERE c.municipality_id = m.municipality_id
          AND c.role_family = 'town_clerk'
        ORDER BY COALESCE(c.display_confidence, 0.0) DESC, COALESCE(c.source_url, '') ASC
        LIMIT 1
    ) AS town_clerk_name,
    (
        SELECT c.email
        FROM vw_contacts_clean c
        WHERE c.municipality_id = m.municipality_id
          AND c.role_family = 'town_clerk'
        ORDER BY COALESCE(c.display_confidence, 0.0) DESC, COALESCE(c.source_url, '') ASC
        LIMIT 1
    ) AS town_clerk_email,
    (
        SELECT c.phone
        FROM vw_contacts_clean c
        WHERE c.municipality_id = m.municipality_id
          AND c.role_family = 'town_clerk'
        ORDER BY COALESCE(c.display_confidence, 0.0) DESC, COALESCE(c.source_url, '') ASC
        LIMIT 1
    ) AS town_clerk_phone,

    (
        SELECT s.url
        FROM service_links s
        WHERE s.municipality_id = m.municipality_id
          AND s.service_type_normalized = 'gis'
        ORDER BY COALESCE(s.display_confidence, s.confidence, 0.0) DESC, COALESCE(s.source_url, '') ASC
        LIMIT 1
    ) AS gis_url,
    (
        SELECT s.url
        FROM service_links s
        WHERE s.municipality_id = m.municipality_id
          AND s.service_type_normalized = 'property_cards'
        ORDER BY COALESCE(s.display_confidence, s.confidence, 0.0) DESC, COALESCE(s.source_url, '') ASC
        LIMIT 1
    ) AS property_cards_url,
    (
        SELECT s.url
        FROM service_links s
        WHERE s.municipality_id = m.municipality_id
          AND s.service_type_normalized = 'tax_payment'
        ORDER BY COALESCE(s.display_confidence, s.confidence, 0.0) DESC, COALESCE(s.source_url, '') ASC
        LIMIT 1
    ) AS tax_payment_url,
    (
        SELECT s.url
        FROM service_links s
        WHERE s.municipality_id = m.municipality_id
          AND s.service_type_normalized = 'jobs'
        ORDER BY COALESCE(s.display_confidence, s.confidence, 0.0) DESC, COALESCE(s.source_url, '') ASC
        LIMIT 1
    ) AS jobs_url,
    (
        SELECT s.url
        FROM service_links s
        WHERE s.municipality_id = m.municipality_id
          AND s.service_type_normalized = 'permits'
        ORDER BY COALESCE(s.display_confidence, s.confidence, 0.0) DESC, COALESCE(s.source_url, '') ASC
        LIMIT 1
    ) AS permits_url,
    (
        SELECT s.url
        FROM service_links s
        WHERE s.municipality_id = m.municipality_id
          AND s.service_type_normalized = 'agendas_minutes'
        ORDER BY COALESCE(s.display_confidence, s.confidence, 0.0) DESC, COALESCE(s.source_url, '') ASC
        LIMIT 1
    ) AS agendas_minutes_url,

    (
        SELECT l.address
        FROM locations l
        WHERE l.municipality_id = m.municipality_id
          AND NULLIF(TRIM(COALESCE(l.address, '')), '') IS NOT NULL
        ORDER BY COALESCE(l.source_url, '') ASC
        LIMIT 1
    ) AS location_address,
    (
        SELECT l.hours
        FROM locations l
        WHERE l.municipality_id = m.municipality_id
          AND NULLIF(TRIM(COALESCE(l.hours, '')), '') IS NOT NULL
        ORDER BY COALESCE(l.source_url, '') ASC
        LIMIT 1
    ) AS location_hours
FROM municipalities m;

-- ==================================================
-- PART 7 - CONTACT EVIDENCE SEED
-- ==================================================

INSERT INTO contact_evidence (
    evidence_id,
    contact_id,
    municipality_id,
    source_url,
    source_context,
    extraction_method,
    field_claims_json
)
SELECT
    'cev_' || LOWER(HEX(RANDOMBLOB(10))) AS evidence_id,
    c.contact_id,
    c.municipality_id,
    c.source_url,
    c.source_context,
    'legacy_backfill' AS extraction_method,
    '{'
    || '"name":"' || REPLACE(COALESCE(c.name, ''), '"', '\"') || '",'
    || '"title":"' || REPLACE(COALESCE(c.title, ''), '"', '\"') || '",'
    || '"department":"' || REPLACE(COALESCE(c.department, ''), '"', '\"') || '",'
    || '"email":"' || REPLACE(COALESCE(c.email, ''), '"', '\"') || '",'
    || '"phone":"' || REPLACE(COALESCE(c.phone, ''), '"', '\"') || '",'
    || '"phone_ext":"' || REPLACE(COALESCE(c.phone_ext, ''), '"', '\"') || '",'
    || '"address":"' || REPLACE(COALESCE(c.address, ''), '"', '\"') || '",'
    || '"hours":"' || REPLACE(COALESCE(c.hours, ''), '"', '\"') || '"'
    || '}' AS field_claims_json
FROM contacts c
WHERE NOT EXISTS (
    SELECT 1
    FROM contact_evidence ce
    WHERE ce.contact_id = c.contact_id
      AND ce.extraction_method = 'legacy_backfill'
);

COMMIT;
