SELECT
  municipality_id,
  role_normalized,
  name,
  email,
  phone,
  department,
  source_url
FROM vw_best_role_per_town
ORDER BY role_normalized, municipality_id;