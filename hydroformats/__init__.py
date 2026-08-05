"""hydroformats — pure-Python parsers for hydrographic survey log formats.

Supported dialects:

- HYPACK® RAW (single-beam survey logging): :func:`parse_raw`
- HYSWEEP® HSX (multibeam text logging): :func:`parse_hsx`

Most callers want :func:`open_session`, which sniffs the dialect, resolves
the device registry from the header, and streams typed records::

    from hydroformats import open_session

    session = open_session("0000_1346.RAW")
    for record in session.records():
        ...

Every record class documents the public source anchoring its field layout;
see ``docs/FORMAT-SOURCES.md``. Unanchored record types parse as
:class:`~hydroformats.records.UnknownRecord` — nothing is guessed.
"""
from . import records
from .hsx import parse_hsx
from .raw import parse_raw
from .session import Header, Session, open_session, sniff_dialect
from .synthetic import SyntheticSurvey, write_hsx, write_raw

__version__ = "0.1.0"

__all__ = [
    "Header",
    "Session",
    "SyntheticSurvey",
    "open_session",
    "parse_hsx",
    "parse_raw",
    "records",
    "sniff_dialect",
    "write_hsx",
    "write_raw",
    "__version__",
]
