SELECT
  municipality_id,
  role_normalized,
  name,
  email,
  phone,
  department,
  source_url
FROM vw_best_role_per_town
WHERE
  email IS NULL OR TRIM(email) = ''
  OR (role_normalized = 'Assessor' AND LOWER(source_url) LIKE '%tax%')
  OR (role_normalized = 'Tax Collector' AND LOWER(source_url) LIKE '%clerk%')
  OR (role_normalized = 'Town Clerk' AND LOWER(source_url) LIKE '%planning%')
  OR LOWER(COALESCE(name, '')) LIKE '%assistant%'
ORDER BY municipality_id, role_normalized;