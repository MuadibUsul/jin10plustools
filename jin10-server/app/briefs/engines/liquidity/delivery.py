"""Re-export the canonical delivery contract from app.briefs.delivery."""
from __future__ import annotations

from pathlib import Path
import sys

try:
    from app.briefs.delivery import (
        ALIASES,
        FIXED_NOTES,
        IMAGE_NOTE_CATEGORIES,
        OIL_CLOSING,
        complete_body,
        default_notes,
        split_markdown,
        write_delivery,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    from app.briefs.delivery import (
        ALIASES,
        FIXED_NOTES,
        IMAGE_NOTE_CATEGORIES,
        OIL_CLOSING,
        complete_body,
        default_notes,
        split_markdown,
        write_delivery,
    )

__all__ = [
    'ALIASES',
    'FIXED_NOTES',
    'IMAGE_NOTE_CATEGORIES',
    'OIL_CLOSING',
    'complete_body',
    'default_notes',
    'split_markdown',
    'write_delivery',
]
