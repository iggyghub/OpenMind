"""
UNO edit script -- run by LibreOffice's bundled python.exe, NOT Cerebral's Python.

Usage: python.exe documents_uno_edit.py <doc_path_abs> <edits_json>

edits_json: JSON array of edit ops:
  {"op": "find_replace", "find": "...", "replace": "...", "match_case": false}
  {"op": "replace_paragraph", "match": "...", "new_text": "..."}

Exit 0 on success; non-zero with error message on stderr on failure.
"""
import json
import os
import sys


def _apply_edits(doc, edits):
    for edit in edits:
        op = edit.get("op")
        if op == "find_replace":
            desc = doc.createReplaceDescriptor()
            desc.SearchRegularExpression = False
            desc.SearchWords = False
            desc.SearchCaseSensitive = bool(edit.get("match_case", False))
            desc.SearchString = edit["find"]
            desc.ReplaceString = edit["replace"]
            doc.replaceAll(desc)
        elif op == "replace_paragraph":
            match_text = edit["match"]
            new_text = edit["new_text"]
            text = doc.getText()
            enum = text.createEnumeration()
            while enum.hasMoreElements():
                para = enum.nextElement()
                if para.supportsService("com.sun.star.text.Paragraph"):
                    if match_text in para.getString():
                        para.setString(new_text)
                        break
        else:
            print(f"[documents_uno_edit] unknown op ignored: {op!r}", file=sys.stderr)


def main():
    if len(sys.argv) < 3:
        print(
            "usage: python.exe documents_uno_edit.py <doc_path> <edits_json>",
            file=sys.stderr,
        )
        sys.exit(1)

    doc_path = os.path.abspath(sys.argv[1])
    edits = json.loads(sys.argv[2])

    import uno
    from com.sun.star.beans import PropertyValue

    ctx = uno.getComponentContext()
    smgr = ctx.ServiceManager
    desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)

    url = uno.systemPathToFileUrl(doc_path)

    hidden = PropertyValue()
    hidden.Name = "Hidden"
    hidden.Value = True

    # ponytail: MacroExecutionMode 4 = ALWAYS_EXECUTE_NO_WARN; suppresses macro prompts in headless
    macro_mode = PropertyValue()
    macro_mode.Name = "MacroExecutionMode"
    macro_mode.Value = 4

    doc = desktop.loadComponentFromURL(url, "_blank", 0, (hidden, macro_mode))
    try:
        _apply_edits(doc, edits)
        ext = os.path.splitext(doc_path)[1].lower()
        if ext == ".docx":
            filter_prop = PropertyValue()
            filter_prop.Name = "FilterName"
            filter_prop.Value = "MS Word 2007 XML"
            doc.storeToURL(url, (filter_prop,))
        else:
            doc.store()
    finally:
        doc.close(True)


if __name__ == "__main__":
    main()
