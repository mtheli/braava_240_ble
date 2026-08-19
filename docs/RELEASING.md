# Releasing

How a release is cut and, more importantly, how its notes are written. The
format is shared with the sibling integrations (philips_sonicare_ble,
philips_shaver, isdt_air_ble, gardena-smart-schedule) — this file exists so
it stops drifting.

## Release notes

Written for someone who runs the integration, not for someone who reads the
diff. What changed for them, and what they have to do about it.

**Written in English.** The integration is localized, the notes are not — one text
every reader can open beats a partial set of translated ones. German belongs
in the German-language forum threads, where a release gets announced in the
reader's own language; the notes themselves stay English.

**Structure:** `##` sections by theme, each holding bullets that open with a
bold phrase and then explain themselves in one or two sentences.

```markdown
## Cleaning state while docked

Optional lead-in paragraph — only when the bullets need context to make
sense, e.g. an external cause the reader could not know about.

- **Docked and charging are told apart** — the vacuum entity stays `docked`
  while the battery fills, instead of dropping to `idle` for a moment.
- **A lost connection no longer clears the last known state** — values hold
  until the robot reports again.
```

**Title:** `vX.Y.Z — what it is about`, e.g.
*v0.3.1 — Braava Jet 240 naming and brand assets*.

**Credit belongs in the notes.** Name whoever reported the problem, tested
the fix or supplied the logs, with `@handle` and the issue number, in the
bullet their work belongs to. The `@` is not decoration: it notifies them
and links their profile, and it is how the release and the issue thread
explain each other. This one matters more here than elsewhere: the robot is
long out of production, so almost everything we learn about it comes from
someone else's capture.

When an external change caused the release, link it — a Home Assistant
version, a `bleak` release, an upstream pull request. A reader who upgraded
something and then saw something break deserves to know the two are
connected.

**What does not belong:** commit lists, file names, internal symbol names,
test tallies, and documentation-only changes.

Protocol findings belong in `PROTOCOL.md`, not in the notes. The notes say
what the integration now does; the protocol document says what the robot
sends.

## Cutting the release

1. Content commits first, pushed and green (`scripts/test_parser.py`).
2. `custom_components/braava_240_ble/manifest.json` — new integration
   version, as its own commit: `release: vX.Y.Z`.
3. Tag `vX.Y.Z`, push, then `gh release create` with the notes above.

**The manifest version and the tag must be the same string.** HACS offers
the release by its tag but Home Assistant reports the manifest version, so a
tag that ran ahead of the manifest leaves every user's diagnostics naming a
version that was never released — and no update prompt when the two are
reconciled later.
