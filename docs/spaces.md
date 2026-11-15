# Spaces: who sees what

Decided 2026-11-15. **Status: design.** Nothing here is built yet. Today a catalogue is scoped by
`CATALOGUE_MARKETPLACES` and `CATALOGUE_SERVERS` (`src/canopy/retrieval.py`), which is one profile set by
hand, and the gateway uses MCPJungle groups. Spaces replace both, as part of the hub
([design](design.md#replacing-mcpjungle-with-the-hub)).

Canopy treats an MCP server and a plugin marketplace as the same kind of thing: a **source**, with tools or
skills nested inside it. Sharing works the same way for both, so one model covers them.

## The household this was designed for

Two people at home, each with a small business. The business names here are made up.

| | Who | Kind |
| --- | --- | --- |
| <img src="spaces/daniel.svg" width="48"> | **Daniel** | Person |
| <img src="spaces/ridgeline.svg" width="48"> | **Ridgeline**, Daniel's business | Organisation |
| <img src="spaces/hannah.svg" width="48"> | **Hannah**, Daniel's wife | Person |
| <img src="spaces/willow.svg" width="48"> | **Willow Studio**, Hannah's business | Organisation |
| <img src="spaces/household.svg" width="48"> | **Household**, what both use | Shared |

Most things are private to one of them. A few are useful to both, like Home Assistant. This setup isn't an
enterprise: two people, two small businesses, one house.

## Four rules

1. **A space is the only container.** Each row above is a space, and so is `common` (below). Spaces don't nest. A business
   "attached" to a person is just a space whose only member is that person.
2. **Every source belongs to exactly one space.** An MCP server, a marketplace or a context volume is never in
   two. If two people need it, it goes in a shared space. It isn't copied.
3. **A profile is a person plus some of their spaces.** A token is issued to a profile. A person can have
   several profiles, for example personal and work, and can only include spaces they're a member of.
4. **What a profile sees is the union of its spaces.** There are no overrides, deny lists or inheritance.
   Something outside the profile looks the same as something that doesn't exist.

Credentials go with the source, so the Home Assistant token sits in Household. Neither person's client holds it,
and a profile without Household can't reach it.

## The household as spaces and profiles

```mermaid
flowchart LR
    subgraph profiles["Profiles (one token each)"]
        dp["Daniel, personal (everything he has)"]
        dw["Daniel, work"]
        hp["Hannah"]
    end
    subgraph spaces["Spaces (each source in exactly one)"]
        d["Daniel<br/>his habits, notes, personal Google"]
        r["Ridgeline<br/>business email, accounts, client repos"]
        h["Hannah<br/>her habits, notes, personal Google"]
        w["Willow Studio<br/>booking, studio site"]
        home["Household<br/>Home Assistant, NAS, shared calendar"]
    end
    dp --> d
    dp --> r
    dp --> home
    dw --> r
    hp --> h
    hp --> w
    hp --> home
```

| Space | Members | Example sources |
| --- | --- | --- |
| `daniel` | Daniel | His context volume (habits, SOPs), personal Google, his own marketplaces |
| `ridgeline` | Daniel | Business Google, accounting, the business's marketplace |
| `hannah` | Hannah | Her context volume, her personal Google |
| `willow` | Hannah | Booking system, studio website |
| `household` | Daniel, Hannah | Home Assistant, NAS, shared calendar, household skills |
| `common` | Everyone | Keyless, harmless utilities: time, currency rates, docs lookup |

| Profile | Person | Spaces | New notes and memories go to |
| --- | --- | --- | --- |
| `daniel` | Daniel | `daniel`, `ridgeline`, `household`, `common` | `daniel` |
| `daniel-work` | Daniel | `ridgeline`, `common` | `ridgeline` |
| `hannah` | Hannah | `hannah`, `willow`, `household`, `common` | `hannah` |

Which spaces go in which profile is the user's choice. Daniel's personal profile sees everything he belongs to;
his work profile is a strict subset for a business-only client, so home and personal tools stay out of business
sessions. A profile writes to its first space unless told otherwise. Writing to Household is a deliberate act,
never the default.

`common` is how a harmless utility reaches every profile without breaking rule 2. Leave out anything with a
key that costs money or reaches private data: that belongs to whoever owns the key.

## Planned config

The `space` key will be added to the existing source configs (`config/servers.json`,
`config/marketplaces.json`), and one new file will name the spaces and profiles. A source with no `space`
key belongs to the operator's own space. With no `spaces.json`, everything is one space, so a single-user
install doesn't change.

```json
{
  "operator": "daniel",
  "spaces": {
    "daniel":    {"members": ["daniel"]},
    "ridgeline": {"members": ["daniel"]},
    "hannah":    {"members": ["hannah"]},
    "willow":    {"members": ["hannah"]},
    "household": {"members": ["daniel", "hannah"]},
    "common":    {"members": ["daniel", "hannah"]}
  },
  "profiles": {
    "daniel":      {"person": "daniel", "spaces": ["daniel", "ridgeline", "household", "common"]},
    "daniel-work": {"person": "daniel", "spaces": ["ridgeline", "common"]},
    "hannah":      {"person": "hannah", "spaces": ["hannah", "willow", "household", "common"]}
  }
}
```

```json
{"name": "home-assistant", "space": "household", "transport": "streamable_http", "url": "...", "bearer_token": "${HA_TOKEN}"}
```

Validation will refuse:
- a profile that names a space its person isn't a member of;
- a source in an unknown space;
- two sources with the same name.

## How it maps onto the parts

- **Hub:** a profile's servers are its `scope` (`Hub.tools(scope)`, `Hub.call(..., scope=)`), which is
  already built.
- **Catalogue:** a profile's marketplaces and servers fill `retrieval.scope()`, which already scopes both
  kinds. This replaces the env vars with a per-request value.
- **Front door:** one MCP endpoint. The bearer token picks the profile, and the profile sets the scope for
  search, read, `skills/list` and `call_tool`.
- **Context and memory:** a context volume is a source, so it has a space like anything else. Memory
  namespaces get a space the same way.
