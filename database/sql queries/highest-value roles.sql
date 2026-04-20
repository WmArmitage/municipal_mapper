SELECT
  municipality_id,
  role_normalized,
  name,
  department,
  email,
  phone,
  source_url
FROM vw_contacts_clean
WHERE role_normalized IN (
  'First Selectman',
  'Assessor',
  'Tax Collector',
  'Town Clerk',
  'Building Official',
  'Planner',
  'Finance Director'
)
ORDER BY role_normalized, municipality_id, name;