SELECT
  municipality_id,
  role_normalized,
  name,
  email,
  phone,
  department,
  source_url
FROM vw_best_role_per_town
WHERE email IS NULL OR TRIM(email) = '';