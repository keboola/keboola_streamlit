from __future__ import annotations

import json
import os
from typing import Optional

NEEDS_SIGNED_IN_USER = "This app's data needs a signed-in user."


def get_workspace_governed() -> Optional[bool]:
    """
    Returns whether the app's workspace is governed by row-level or column-level security.

    The value comes from the workspace manifest at KBC_WORKSPACE_MANIFEST_PATH, which the platform can update while the
    app runs, so the file is read on every call.

    Returns:
        Optional[bool]: True or False, or None when it is unknown (no manifest, an older platform without the field, or
        a file that cannot be read). Query Service refuses a query it may not run either way.
    """
    path = os.environ.get("KBC_WORKSPACE_MANIFEST_PATH")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
    except (OSError, ValueError):
        return None
    governed = manifest.get("governed") if isinstance(manifest, dict) else None
    return governed if isinstance(governed, bool) else None
