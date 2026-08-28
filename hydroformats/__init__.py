"""hydroformats — pure-Python parsers for hydrographic survey log formats.

Supported dialects:

- HYPACK® RAW (single-beam survey logging): :func:`parse_raw`
- HYSWEEP® HSX (multibeam text logging): :func:`parse_hsx`
- HYSWEEP® HS2X (multibeam binary edit format): :func:`parse_hs2x`
- Cerulean SVLog/SVLZ (Surveyor 240-16 packet logging): :func:`read_svlog`
- Generic Sensor Format (swath bathymetry interchange): :func:`read_gsf`

Most callers want :func:`open_session`, which sniffs the dialect, resolves
the device registry from the header, and streams typed records::

    from hydroformats import open_session

    session = open_session("0000_1346.RAW")
    for record in session.records():
        ...

Every record class documents the source anchoring its field layout; see
``docs/FORMAT-SOURCES.md``. Unanchored record types parse as
:class:`~hydroformats.records.UnknownRecord` (text) or
:class:`~hydroformats.records.Hs2xOpaque` (binary) — nothing is guessed.
"""
from . import records
from .gsf import load_swath, read_gsf
from .hs2x import parse_hs2x
from .hsx import parse_hsx
from .raw import parse_raw
from .session import Header, Session, open_session, sniff_dialect
from .svlog import atof_to_yz, load_survey, read_svlog
from .synthetic import SyntheticSurvey, write_hs2x, write_hsx, write_raw

__version__ = "0.2.0"

__all__ = [
    "Header",
    "Session",
    "SyntheticSurvey",
    "atof_to_yz",
    "load_survey",
    "load_swath",
    "open_session",
    "parse_hs2x",
    "parse_hsx",
    "parse_raw",
    "read_gsf",
    "read_svlog",
    "records",
    "sniff_dialect",
    "write_hs2x",
    "write_hsx",
    "write_raw",
    "__version__",
]
