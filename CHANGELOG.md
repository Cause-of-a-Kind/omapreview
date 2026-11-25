# Changelog

## 0.1.1 — 2026-11-25

Installation supply-chain hardening for marketplace review; PDF editing behavior is unchanged.

- Bind the documented runtime setup to an exact full source commit rather than the default branch.
- Lock direct and transitive Python dependencies and build tools with SHA-256 artifact hashes.
- Reject a mismatched or modified source checkout before setup and refuse to overwrite an existing runtime.
- Install only hash-verified dependency wheels, build the application without network dependency resolution, and install it non-editably.
- Launch the installed application in isolated Python mode instead of importing source through `PYTHONPATH`.
- Keep development/test tools separate from the normal desktop runtime.

## 0.1.0 — 2026-11-24

Initial public release.

- Fill native PDF text fields, checkboxes and radio groups.
- Add wrapping text annotations with contextual point-size controls.
- Draw or import visual signatures and initials; keep a primary mark and additional named or automatically named marks.
- Sign once without adding the ink to the reusable signature library.
- Select, move, resize and delete placed marks; keep new marks editable after save and reopen.
- Scroll continuous pages, jump through thumbnails, and render previews for the current display density.
- Save a copy without overwriting the source PDF; undo and redo edits.
- Integrate with the Omarchy bar, application launcher and PDF Open With menu.

The marketplace plugin requires a separate one-time desktop runtime setup; see README. Processing is local. Saved signatures are private local files, not encrypted storage. Visual signatures are not certificate-based digital signatures.
