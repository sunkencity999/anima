"""ANIMA — the agent is the artifact, the harness is its nervous system."""

__version__ = "0.1.0"


def __getattr__(name):
    # Lazy so `import anima` stays feather-light and subpackages stay
    # independently importable.
    if name == "EntityRoot":
        from .entity import EntityRoot
        return EntityRoot
    raise AttributeError(f"module 'anima' has no attribute {name!r}")
