# mark-kit

Point at a live UI, say what should change, hand the batch to a fixer.
Ctrl+Shift+M toggles mark mode; click an element, type a note, Enter. "Copy all"
puts every mark on the clipboard. The bar is draggable and remembers where you put it.

Host-agnostic core: one file, no dependencies, no imports
(`tests/boundary.test.js` enforces it). Felix is the first host
(`tray/windows/main.html`). Design and build plan: `MARK-MODE-IDEAS.md` at the repo root.

## Use

```html
<script src="mark-kit.js"></script>
<script>
  MarkKit.init(document, {
    // optional: extra app state stored on each mark as `app`
    context: function (el) { return { route: location.hash }; },
  });
</script>
```

Hooks (screenshots, a store, review, a fixer) and per-UI-technology recipes:
`INTEGRATE.md` (also a valid SKILL.md). The mark format: `mark.schema.json`.
What a fixer does with `.marks/`: `FIX.md`.

## Tests

```
node --test "tools/mark-kit/tests/*.test.js"
```
