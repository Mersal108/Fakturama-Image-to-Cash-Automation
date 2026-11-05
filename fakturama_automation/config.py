"""Run settings and Fakturama discovery."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

DEFAULT_ARTIFACTS = "artifacts"

# Where an install4j/MSI install of Fakturama 2.x normally lands.
_CANDIDATE_PATHS = (
    r"C:\Program Files\Fakturama2\Fakturama.exe",
    r"C:\Program Files\Fakturama\Fakturama.exe",
    r"C:\Program Files (x86)\Fakturama2\Fakturama.exe",
    r"C:\Program Files (x86)\Fakturama\Fakturama.exe",
)


def _user_candidates() -> List[str]:
    paths: List[str] = []
    for root_var in ("LOCALAPPDATA", "PROGRAMFILES", "USERPROFILE"):
        root = os.environ.get(root_var)
        if not root:
            continue
        for folder in ("Fakturama2", "Fakturama", r"Programs\Fakturama2",
                       r"Programs\Fakturama", "FakturamaApp"):
            paths.append(os.path.join(root, folder, "Fakturama.exe"))
    return paths


def discover_fakturama_exe() -> Optional[str]:
    """Locate Fakturama.exe, preferring an explicit environment override."""
    override = os.environ.get("FAKTURAMA_EXE")
    if override and os.path.isfile(override):
        return override
    for path in list(_CANDIDATE_PATHS) + _user_candidates():
        if os.path.isfile(path):
            return path
    return None


@dataclass
class Settings:
    """Everything one run needs to know."""

    image_path: str
    fakturama_exe: Optional[str] = None
    artifacts_dir: str = DEFAULT_ARTIFACTS

    # Extraction
    ocr_engine: str = "windows"
    refine_cells: bool = True

    # UI automation
    launch_timeout: float = 120.0
    timeout: float = 30.0
    short_timeout: float = 8.0
    poll_interval: float = 0.25
    stable_for: float = 0.6

    # Behaviour
    extract_only: bool = False
    keep_open: bool = True
    screenshots: bool = True

    notes: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.fakturama_exe is None:
            self.fakturama_exe = discover_fakturama_exe()
        self.artifacts_dir = os.path.abspath(self.artifacts_dir)
        os.makedirs(self.artifacts_dir, exist_ok=True)
