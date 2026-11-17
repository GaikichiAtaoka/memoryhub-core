"""Source-first read-only MemoryHub for local agent projects."""

from .memory_hub import HUB_TOOL, execute_memory_hub, render_memory_hub_context
from .memory_sidecar_index import (
    rebuild_memory_index, index_memory_sources, search_candidates,
)
from .memory_hub_temporal_bridge import (
    search_temporal_memory, render_temporal_memory_context,
    clear_embedding_cache, embedding_cache_stats,
)

__version__ = "0.1.0"
__all__ = [
    "HUB_TOOL", "execute_memory_hub", "render_memory_hub_context",
    "rebuild_memory_index", "index_memory_sources", "search_candidates",
    "search_temporal_memory", "render_temporal_memory_context",
    "clear_embedding_cache", "embedding_cache_stats",
]
