from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RunSummary:
    artist: str
    playlist_name: str
    playlist_id: Optional[str]
    albums_included: int
    tracks_added: int
    total_ms: int
    dry_run: bool
    cover_applied: bool = False
    cover_error: Optional[str] = None
