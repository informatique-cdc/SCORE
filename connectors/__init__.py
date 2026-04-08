# Import connector modules so they register via @register_connector
from . import generic  # noqa: F401

try:
    from . import elasticsearch  # noqa: F401
except ImportError:
    pass  # elasticsearch package not installed
