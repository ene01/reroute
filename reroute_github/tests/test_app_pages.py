"""Every page must have its own web address, or Streamlit refuses to start the whole site."""

import ast
from pathlib import Path


def test_page_keys_are_unique():
    tree = ast.parse((Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8"))
    keys = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and node.targets and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("HOME", "STAFF", "STUDENT", "LEARN"):
            keys += [elt.elts[0].value for elt in node.value.elts]
    assert len(keys) > 20
    assert len(keys) == len(set(keys)), sorted(k for k in keys if keys.count(k) > 1)
