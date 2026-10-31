# Decisions

Decision records on `<domain>/decisions` move through their life cycle only with `set_decision_status`: proposed to accepted, rejected or superseded; accepted to superseded or void (with a reason). Records are never deleted, and accepted records accept append only. `brief` lists decisions by status.

## Sub-features

- `dec-accept` a proposal becomes accepted.
- `dec-supersede` an accepted record is superseded by another accepted one; an unknown target is refused.
- `dec-void` void needs a reason; the record stays with Status void and a Void reason line.
- `dec-transition` a transition the life cycle does not allow is `bad_transition`.
- `dec-append-only` replacing an accepted record's text is `decision_overwritten`; changing its Status line by hand is `status_line_changed`; appending is allowed.
- `dec-owner` a worker cannot change a status.

## How to get to it (user POV)

- Call the MCP tool `set_decision_status` with `domain`, `id`, `status`, `superseded_by` or `reason`.
- Read the record with `read` on `<domain>/decisions`; see the summary in `brief`.

## Driving it with scripts/verify

Preconditions:

- Seed decisions on `atlas/decisions`: D01 and D02 accepted, P01 proposed.

- **Scripted journey.** Run `uv run --extra postgres scripts/verify drive decisions`. It prints `PASS decisions [<backend>]`.
- **Accept.** Run `scripts/verify call set_decision_status '{"domain": "atlas", "id": "P01", "status": "accepted"}'`. `ok` is true.
- **Supersede.** With `"superseded_by": "P99"`: `superseded_by_required`. With `{"id": "D02", "status": "superseded", "superseded_by": "D01"}`: `ok`.
- **Void.** `{"id": "D01", "status": "void"}` without reason: `reason_required`. With `"reason": "accepted by mistake in a demo"`: `ok`. Then `"status": "accepted"` on D01: `bad_transition`. `read` of `D01 — Cache tiles at the edge (2026-10-01)` holds `- **Status:** void` and `- **Void reason:** accepted by mistake in a demo`.
- **Brief.** `brief atlas` holds `accepted: P01`, `superseded: D02→D01` and `void: D01`.
- **Append-only.** Replace P01 with `"\n- **Status:** accepted\n"`: `decision_overwritten`. Replace it with its content but `proposed`: `status_line_changed`. Append `- **Note:** shipped in v2.`: `ok`.
- **Worker.** The same status call with `--role worker`: `owner_only`.
- **Side effects.** The decisions table holds D01 void, D02 superseded by D01, P01 accepted.

Run on both backends.

## Gotchas

- Use the full dated heading to read a record; the candidates list helps when the date is missing.
- `superseded_by` must name an accepted decision other than the record itself.
