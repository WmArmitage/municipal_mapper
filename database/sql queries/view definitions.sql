SELECT name, sql
FROM sqlite_master
WHERE type IN ('table', 'view')
  AND name IN ('contacts', 'vw_contacts_clean', 'vw_best_role_per_town');