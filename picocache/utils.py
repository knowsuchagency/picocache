from __future__ import annotations
from typing import Any, Callable, Dict, Tuple

import functools
import hashlib
import pickle


def _make_key(
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
    typed: bool,
    module_name: str,
    qualname: str,
) -> str:
    """Create a stable hashable key from call args/kwargs (mimics internal
    ``functools._make_key`` but returns hex digest for external storage).

    Includes the module name and function qualname to prevent collisions
    between functions in different modules *or* different functions within
    the same module called with the same arguments.

    .. note::
        Adding ``qualname`` changes the SHA-256 digest for every key.
        Existing persisted caches built with an older version of picocache
        will be **invalidated** (one-time cold-start after upgrade).
    """
    key_parts: Tuple[Any, ...] = (module_name, qualname) + args
    if kwargs:
        key_parts += (object(),)  # separator to avoid collisions
        for item in sorted(kwargs.items()):
            key_parts += item
    if typed:
        key_parts += tuple(type(v) for v in args)
        if kwargs:
            key_parts += tuple(type(v) for _, v in sorted(kwargs.items()))
    pickled = pickle.dumps(key_parts, protocol=pickle.HIGHEST_PROTOCOL)
    return hashlib.sha256(pickled).hexdigest()


def _copy_metadata(src_func: Callable[..., Any]):
    """Return a ``functools.wraps`` decorator pre‑configured for *src_func*."""
    return functools.wraps(
        src_func,
        assigned=functools.WRAPPER_ASSIGNMENTS + ("__annotations__",),
        updated=(),
    )
