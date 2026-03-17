#!/usr/bin/env python3
import argparse
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

TOOLCHAIN = "nightly"
DOCS_RS_BASE = "https://docs.rs"

def run_stream(cmd, *, cwd=None, env=None):
    """
    Executes a long-running command and streams its I/O directly to the console.
    Prevents the process from appearing frozen during heavy compilation tasks.
    Does not capture output, as it is routed directly to the host terminal.
    """
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if result.returncode != 0:
        print(f"ERROR: command {' '.join(cmd)} failed with exit {result.returncode}", file=sys.stderr)
        sys.exit(1)

def generate_workspace_json(bevy_dir):
    """
    Compiles the entire Cargo workspace into rustdoc JSON format.
    Utilizes RUSTDOCFLAGS environment variable passthrough to bypass cargo's
    strict CLI parameter validation which normally rejects --output-format 
    when used alongside --workspace.
    """
    if not bevy_dir.is_dir():
        print(f"ERROR: Bevy source directory not found at {bevy_dir}", file=sys.stderr)
        sys.exit(1)

    print("Compiling rustdoc JSON for Bevy workspace (This will take several minutes)...")
    
    env = os.environ.copy()
    env["RUSTDOCFLAGS"] = "-Z unstable-options --output-format json"
    
    run_stream(
        ["cargo", f"+{TOOLCHAIN}", "doc", "--workspace", "--no-deps"],
        cwd=str(bevy_dir),
        env=env
    )
    
    doc_target_dir = bevy_dir / "target" / "doc"
    json_files = list(doc_target_dir.glob("*.json"))
    if not json_files:
        print("ERROR: No generated JSON files found.", file=sys.stderr)
        sys.exit(1)
    return json_files

def extract_safety_section(docs):
    """
    Extracts the '# Safety' section from the raw markdown documentation string.
    """
    if not docs:
        return ""
    pattern = re.compile(
        r"^#+\s+Safety\b.*?$\n(.*?)(?=^#+\s|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(docs)
    if not match:
        return ""
    text = match.group(1).strip()
    return re.sub(r"\s*\n\s*", " ", text)

def docs_rs_url(crate, path_segments, kind, *, path_kind="", parent_kind=""):
    """
    Constructs a URL pointing to the official docs.rs page for the specific item.
    """
    if len(path_segments) < 2:
        return ""
    crate_name = crate.replace("_", "-") 

    if path_kind == "method" and len(path_segments) >= 3:
        parent_segments = path_segments[:-1]
        parent_name = parent_segments[-1]
        method_name = path_segments[-1]
        module_parts = parent_segments[1:-1]
        page_prefix = {"struct": "struct", "enum": "enum", "trait": "trait"}.get(parent_kind, "")
        if not page_prefix:
            return ""
        parts = [DOCS_RS_BASE, crate_name, "latest", crate] + module_parts + [f"{page_prefix}.{parent_name}.html#method.{method_name}"]
        return "/".join(parts)

    module_parts = path_segments[1:-1]
    item_name = path_segments[-1]
    prefix = {"function": "fn", "trait": "trait"}.get(kind, "")
    if not prefix:
        return ""
    parts = [DOCS_RS_BASE, crate_name, "latest", crate] + list(module_parts) + [f"{prefix}.{item_name}.html"]
    return "/".join(parts)

def _find_resolved_path(node):
    """
    Recursively searches the JSON AST to find a resolved path ID and name.
    Warning: This is a fragile heuristic approach due to Python's weak typing.
    """
    if isinstance(node, dict):
        resolved = node.get("resolved_path")
        if isinstance(resolved, dict):
            type_id = resolved.get("id")
            type_name = resolved.get("name") or ""
            if type_id:
                return type_id, type_name
        for value in node.values():
            result = _find_resolved_path(value)
            if result is not None:
                return result
    elif isinstance(node, list):
        for value in node:
            result = _find_resolved_path(value)
            if result is not None:
                return result
    return None

def _method_parent_map(crate, index, paths):
    """
    Maps method item IDs back to their parent type's path and kind.
    Necessary because rustdoc JSON stores methods under 'impl' blocks 
    rather than directly under the struct/enum.
    """
    parent_by_item_id = {}
    for impl_item in index.values():
        impl_data = (impl_item.get("inner") or {}).get("impl")
        if not impl_data:
            continue
        impl_items = impl_data.get("items") or []
        if not impl_items:
            continue
        
        impl_for = impl_data.get("for") or {}
        resolved = _find_resolved_path(impl_for)
        if resolved is not None:
            parent_type_id, _ = resolved
            parent_path_entry = paths.get(str(parent_type_id)) or {}
            parent_path_segments = parent_path_entry.get("path") or []
            parent_kind = parent_path_entry.get("kind") or ""
            if parent_path_segments:
                for method_item_id in impl_items:
                    parent_by_item_id[str(method_item_id)] = (parent_path_segments, parent_kind)
    return parent_by_item_id

def collect_unsafe_items(json_path):
    """
    Parses the rustdoc JSON output and extracts public unsafe functions and traits.
    Filters out dependencies not prefixed with 'bevy'.
    """
    try:
        with open(json_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []

    if "index" not in data or "paths" not in data:
        return []

    index = data["index"]
    paths = data["paths"]
    crate = json_path.stem

    if not crate.startswith("bevy"):
        return []

    method_parents = _method_parent_map(crate, index, paths)
    path_kind_by_segments = {}
    for _item_id, path_info in paths.items():
        segs = path_info.get("path") or []
        if segs:
            path_kind_by_segments[tuple(segs)] = path_info.get("kind") or ""

    items = []
    for item_id, item in index.items():
        # Strictly enforce public visibility
        if item.get("visibility") != "public":
            continue

        inner = item.get("inner", {})
        kind = None

        if "function" in inner:
            if inner["function"].get("header", {}).get("is_unsafe"):
                kind = "function"
        elif "trait" in inner:
            if inner["trait"].get("is_unsafe"):
                kind = "trait"

        if kind is None:
            continue

        path_entry = paths.get(item_id)
        path_kind = ""
        parent_kind = ""

        if item_id in method_parents and item.get("name"):
            parent_segments, parent_kind = method_parents[item_id]
            full_path_segments = list(parent_segments) + [item.get("name")]
            path_kind = "method"
        elif path_entry is None:
            name = item.get("name") or ""
            full_path_segments = [crate, name] if name else [crate]
        else:
            full_path_segments = path_entry.get("path") or []
            path_kind = path_entry.get("kind") or ""

        if not full_path_segments:
            continue

        full_path = "::".join(full_path_segments)
        module_path = "::".join(full_path_segments[:-1]) if len(full_path_segments) > 1 else crate

        docs = item.get("docs") or ""
        safety_doc = extract_safety_section(docs)
        if path_kind == "method" and len(full_path_segments) >= 3:
            parent_kind = parent_kind or path_kind_by_segments.get(
                tuple(full_path_segments[:-1]), ""
            )

        url = docs_rs_url(crate, full_path_segments, kind, path_kind=path_kind, parent_kind=parent_kind)
        items.append((module_path, full_path, kind, url, safety_doc))

    return items

def write_html(all_items, output_path):
    """
    Generates a static HTML payload with inline CSS and JavaScript.
    Handles column resizing and local storage state management for the review checkboxes.
    """
    seen: dict[tuple[str, str, str], tuple[str, list[str]]] = {}
    for module_path, full_path, kind, url, safety_doc in all_items:
        key = (module_path, full_path, kind)
        if key not in seen:
            seen[key] = (url, [safety_doc] if safety_doc else [])
        else:
            existing_url, docs = seen[key]
            merged_url = existing_url or url
            if safety_doc and safety_doc not in docs:
                docs.append(safety_doc)
            seen[key] = (merged_url, docs)

    def _sort_key(entry):
        (module_path, full_path, kind), _val = entry
        api_name = full_path.split("::")[-1]
        return (module_path, api_name)

    sorted_items = sorted(seen.items(), key=_sort_key)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Public Unsafe APIs </title>",
        "<style>",
        "* { box-sizing: border-box; }",
        "body { margin: 0; font-family: system-ui, sans-serif; }",
        ".page-wrap { width: 100%; padding: 16px 24px; }",
        ".unsafe-table-wrap { width: 100%; overflow-x: auto; }",
        ".unsafe-table-wrap table { width: 100%; table-layout: fixed;"
        " border-collapse: collapse; min-width: 600px; }",
        ".unsafe-table-wrap th, .unsafe-table-wrap td"
        " { padding: 4px 8px; word-break: break-word; vertical-align: top;"
        " border: 1px solid #ddd; }",
        ".unsafe-table-wrap th { position: relative; white-space: nowrap;"
        " user-select: none; -webkit-user-select: none; }",
        ".col-resize-handle { position: absolute; right: 0; top: 0; bottom: 0;"
        " width: 5px; cursor: col-resize; }",
        ".col-resize-handle:hover { background: rgba(0,0,0,.15); }",
        ".confirm-cell { text-align: center; }",
        ".confirm-cb { cursor: pointer; width: 16px; height: 16px; }",
        ".row-confirmed td { background-color: #f0fff4; }",
        "</style>",
        "</head>",
        "<body>",
        '<div class="page-wrap">',
        f"<h1>Bevy Public Unsafe APIs </h1>",
        f"<p>Generated from workspace: <code>bevy</code>.</p>",
        "",
        "<script>",
        "(function () {",
        "  var STORAGE_CHECKED_KEY = 'unsafe-doc-checked:' + location.pathname;",
        "  document.addEventListener('DOMContentLoaded', function () {",
        "    var table = document.querySelector('.unsafe-table-wrap table');",
        "    if (!table) return;",
        "    var tbody = table.querySelector('tbody');",
        "    var cols = table.querySelectorAll('col');",
        "    var ths  = table.querySelectorAll('thead th');",
        "",
        "    // Column resize logic",
        "    ths.forEach(function (th, i) {",
        "      var handle = document.createElement('div');",
        "      handle.className = 'col-resize-handle';",
        "      th.appendChild(handle);",
        "      var startX = 0, startW = 0;",
        "      handle.addEventListener('mousedown', function (e) {",
        "        startX = e.clientX;",
        "        startW = th.getBoundingClientRect().width;",
        "        document.addEventListener('mousemove', onMove);",
        "        document.addEventListener('mouseup', onUp);",
        "        e.preventDefault();",
        "      });",
        "      function onMove(e) {",
        "        var w = startW + (e.clientX - startX);",
        "        if (w > 40) { cols[i].style.width = w + 'px'; }",
        "      }",
        "      function onUp() {",
        "        document.removeEventListener('mousemove', onMove);",
        "        document.removeEventListener('mouseup', onUp);",
        "      }",
        "    });",
        "",
        "    // Checkbox state management",
        "    function getRows() { return Array.from(tbody.querySelectorAll('tr')); }",
        "    function saveChecked() {",
        "      var state = {};",
        "      getRows().forEach(function (r) {",
        "        var cb = r.querySelector('.confirm-cb');",
        "        if (cb) state[r.dataset.id] = cb.checked;",
        "      });",
        "      try { localStorage.setItem(STORAGE_CHECKED_KEY, JSON.stringify(state)); } catch (e) {}",
        "    }",
        "    function loadChecked() {",
        "      try {",
        "        var saved = localStorage.getItem(STORAGE_CHECKED_KEY);",
        "        if (!saved) return;",
        "        var state = JSON.parse(saved);",
        "        getRows().forEach(function (r) {",
        "          var cb = r.querySelector('.confirm-cb');",
        "          if (cb && r.dataset.id in state) {",
        "            cb.checked = state[r.dataset.id];",
        "            r.classList.toggle('row-confirmed', cb.checked);",
        "          }",
        "        });",
        "      } catch (e) {}",
        "    }",
        "",
        "    getRows().forEach(function (row) {",
        "      var cb = row.querySelector('.confirm-cb');",
        "      if (cb) {",
        "        cb.addEventListener('change', function () {",
        "          row.classList.toggle('row-confirmed', cb.checked);",
        "          saveChecked();",
        "        });",
        "      }",
        "    });",
        "",
        "    loadChecked();",
        "  });",
        "}());",
        "</script>",
        "",
        '<div class="unsafe-table-wrap">',
        '<table>',
        '<colgroup>',
        '<col style="width:4%">',
        '<col style="width:15%">',
        '<col style="width:18%">',
        '<col style="width:7%">',
        '<col style="width:49%">',
        '<col style="width:7%">',
        '</colgroup>',
        '<thead>',
        '<tr><th>Index</th><th>Module Path</th><th>API Name</th>'
        '<th>Kind</th><th>Safety Doc</th><th> Mark </th></tr>',
        '</thead>',
        '<tbody>',
    ]

    for idx, ((module_path, full_path, kind), (url, docs)) in enumerate(sorted_items, 1):
        api_name = full_path.split("::")[-1]
        module_cell = f"<code>{html.escape(module_path)}</code>"
        if url:
            api_cell = f'<a href="{html.escape(url)}"><code>{html.escape(api_name)}</code></a>'
        else:
            api_cell = f"<code>{html.escape(api_name)}</code>"
        kind_cell = html.escape(kind)
        safety_cell = "<br/>".join(html.escape(d) for d in docs)
        lines.append(
            f'<tr data-id="{html.escape(full_path, quote=True)}">'
            f'<td>{idx}</td>'
            f'<td>{module_cell}</td>'
            f'<td>{api_cell}</td>'
            f'<td>{kind_cell}</td>'
            f'<td>{safety_cell}</td>'
            f'<td class="confirm-cell">'
            f'<input type="checkbox" class="confirm-cb" aria-label="Confirmed">'
            f'</td>'
            f'</tr>'
        )

    lines += ["</tbody>", "</table>", "</div>", "</div>", "</body>", "</html>", ""]
    output_path.write_text("\n".join(lines), encoding="utf-8")

def main():
    parser = argparse.ArgumentParser(description="Extract public unsafe APIs from Bevy.")
    parser.add_argument("--bevy-dir", required=True, help="Path to the cloned Bevy source directory")
    parser.add_argument("--output", required=True, help="Path to write the generated HTML file")
    args = parser.parse_args()

    bevy_dir = Path(args.bevy_dir).resolve()
    output_path = Path(args.output).resolve()

    json_files = generate_workspace_json(bevy_dir)
    
    all_items = []
    for json_path in json_files:
        items = collect_unsafe_items(json_path)
        all_items.extend(items)
        print(f"[{json_path.stem}] Parsed {len(items)} unsafe items")
    
    write_html(all_items, output_path)
    print(f"\nSuccessfully generated HTML at: {output_path}")

if __name__ == "__main__":
    main()