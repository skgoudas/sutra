## Summary

<!-- What does this change and why? Link the issue: Fixes #123 -->

## How it was tested

- [ ] `.venv/bin/python -m pytest -q` passes
- [ ] New or changed behaviour has tests (including denial/error paths for security-relevant code)

## Checklist

- [ ] No changes to `sutra.contracts`, or the change is backward compatible / called out below
- [ ] Choke points respected (ModelClient, ToolExecutor, ContextBuilder, PolicyEngine)
- [ ] No new telemetry attributes outside `sutra.*` / `gen_ai.*`; no content, args, subjects or secrets emitted
- [ ] Optional dependencies are imported lazily
- [ ] Docs and `CHANGELOG.md` (Unreleased) updated for user-visible changes

## Notes for reviewers
