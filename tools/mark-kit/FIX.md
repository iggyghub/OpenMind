# Fixing marks

This folder was written by mark-kit. Each line of `marks.jsonl` is one UI change
request someone made by pointing at the running app (format:
`mark.schema.json` in mark-kit). `shots/<id>.png` is what they saw, inputs
blacked out.

For each mark with `status` `open` or `reopened`:

1. Find the element. Search the source for `locator.text` and the classes in
   `locator.sel` (tag.class); `selector` is the full CSS path if that fails.
   `app` says where in the app it was (Felix: `route`, `subtab`).
2. Read `type` (fix, remove, move, add, link, bug, idea) and `note`.
   - `scope: "all"` means every element matching `match` (`matched` found).
   - `add` has `kind` (page, tab, section, control, field) and maybe boxes
     drawn on the screenshot (`sketch`).
   - `link` points at a second element (`link`) with a `relation`.
   - `reopened` marks carry the reason in the last `history` entry's `note`.
3. Make the change. Group marks that touch the same code into one commit.
4. Set the status: `fixed` with the PR or commit, or `declined` with a reason.
   Never delete a mark. In Felix use the `marks_update` tool; otherwise edit
   the line in `marks.jsonl` and append to its `history`.

The person who marked it reviews `fixed` marks in the app and moves them to
`verified` or back to `reopened`.

Marks in `fixing` are being worked on by another run: leave them alone. Marks in
`verified` or `declined` are done. If a request is impossible or would break
something, decline it with the reason rather than half-doing it.
