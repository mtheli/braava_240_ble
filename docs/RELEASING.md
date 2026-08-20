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

**One section per feature.** The bullets underneath carry the details. If it is
not obvious who a feature applies to, say so in one line under the heading.

**One sentence per bullet,** opening with two to five bold words that run into
the sentence. No labels, no whole sentence in bold. Write what the user sees,
with the previous behaviour as a short trailing clause where one is needed.

**Plain language.** No literary voice, no marketing tone, no idiom where a verb
will do. This holds for commit messages too. Reasoning belongs in the commit
message, not in the notes.

**No hard line breaks.** GitHub renders a single newline as a line break and
tears prose apart mid-sentence. One paragraph, one line.

```markdown
## Last session

Two new sensors, Last Session and Last Session Duration. On brushes with the storage service and on the Sonicare for Kids, not on the 7100 (HX742X).

- **The state is the start time** of the session, the attributes hold duration, routine, mode and intensity.
- **Sessions brushed without a connection** are read on the next one.
- **Sessions the brush cannot date** are skipped.

---

📟 **No ESP bridge firmware change** — `MIN_BRIDGE_VERSION` stays 1.4.0.
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
