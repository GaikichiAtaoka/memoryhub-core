"""Source-first retrieval and explicit memory management for local agent projects."""

from .memory_hub import HUB_TOOL, execute_memory_hub, render_memory_hub_context
from .memory_sidecar_index import (
    rebuild_memory_index, index_memory_sources, search_candidates,
)
from .memory_hub_temporal_bridge import (
    search_temporal_memory, render_temporal_memory_context,
    clear_embedding_cache, embedding_cache_stats,
)
from .memory_lifecycle import (
    MEMORY_LIFECYCLE_TOOL, execute_memory_management, memory_lifecycle_rows,
)

__version__ = "0.2.0a1"
__all__ = [
    "HUB_TOOL", "execute_memory_hub", "render_memory_hub_context",
    "rebuild_memory_index", "index_memory_sources", "search_candidates",
    "search_temporal_memory", "render_temporal_memory_context",
    "clear_embedding_cache", "embedding_cache_stats",
    "MEMORY_LIFECYCLE_TOOL", "execute_memory_management", "memory_lifecycle_rows",
]
