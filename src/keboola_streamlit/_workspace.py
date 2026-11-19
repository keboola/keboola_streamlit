from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Optional

NEEDS_SIGNED_IN_USER = "This app's data needs a signed-in user."


@dataclass(frozen=True)
class Workspace:
    """
    The Query Service workspace of the app.

    Attributes:
        id (str): The numeric workspace ID.
        governed (Optional[bool]): Whether row-level or column-level security governs the workspace. None when it is
            unknown: the workspace comes from WORKSPACE_ID, or the platform does not write the field yet. Query Service
            refuses a query it may not run either way.
    """

    id: str
    governed: Optional[bool]


def get_workspace() -> Workspace:
    """
    Returns the app's workspace from the manifest at KBC_WORKSPACE_MANIFEST_PATH, or from WORKSPACE_ID without one.

    The platform can update the manifest while the app runs, so it is read on every call.

    Raises:
        RuntimeError: There is no workspace, or its ID is not numeric.
        OSError, ValueError: The manifest cannot be read.
    """
    path = os.environ.get("KBC_WORKSPACE_MANIFEST_PATH")
    if path:
        with open(path, encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
        workspace_id = manifest.get("workspaceId") if isinstance(manifest, dict) else None
        if not workspace_id:
            raise RuntimeError(f"Workspace manifest {path} does not contain workspaceId.")
        if not _is_ascii_number(str(workspace_id)):
            raise RuntimeError(f"Workspace manifest {path} has a non-numeric workspaceId.")
        governed = manifest.get("governed")
        return Workspace(id=str(workspace_id), governed=governed if isinstance(governed, bool) else None)
    workspace_id = os.environ.get("WORKSPACE_ID")
    if not workspace_id:
        raise RuntimeError(
            "Neither KBC_WORKSPACE_MANIFEST_PATH nor WORKSPACE_ID is set. "
            "Turn on Storage Access in the data app configuration."
        )
    if not _is_ascii_number(workspace_id):
        raise RuntimeError(f"WORKSPACE_ID must be a numeric workspace ID, got '{workspace_id}'.")
    return Workspace(id=workspace_id, governed=None)


def _is_ascii_number(value: str) -> bool:
    return re.fullmatch(r"[0-9]+", value) is not None
