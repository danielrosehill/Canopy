# Canopy

Release tier: medium

Public repo. It takes generic stack code only. Never commit credentials, private hostnames or addresses,
personal upstream lists, private skills, or anything about a particular deployment: who runs it, its people,
its machines, its schedule or its settings. Examples use made-up names. Private context for this checkout,
if any, is in `CLAUDE.local.md`, which is not committed.

Code ported from private repos is rewritten or scanned first, and never brought over with its git history.

Releases are cut by CI: each push to `main` that passes CI is tagged with the next `v1.NN` (see
`.github/workflows/ci.yml`), and `release.yml` names its images.
