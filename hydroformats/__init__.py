"""hydroformats — pure-Python parsers for hydrographic survey log formats.

Supported dialects:

- HYPACK® RAW (single-beam survey logging): :func:`parse_raw`
- HYSWEEP® HSX (multibeam text logging): :func:`parse_hsx`
- HYSWEEP® HS2X (multibeam binary edit format): :func:`parse_hs2x`
- Cerulean SVLog/SVLZ (Surveyor 240-16 packet logging): :func:`read_svlog`
- Generic Sensor Format (swath bathymetry interchange): :func:`read_gsf`
- Sound Metrics ARIS/DIDSON DDF (imaging sonar): :func:`read_aris`
- EdgeTech JSF (side scan and bathymetric side scan): :func:`read_jsf`
- Triton XTF (sidescan interchange): :func:`read_xtf`; the survey bundle
  is :func:`hydroformats.xtf.load_survey`, exported here as
  :func:`load_sidescan` because the SVLog loader claimed the
  ``load_survey`` name first

Most callers want :func:`open_session`, which sniffs the dialect, resolves
the device registry from the header, and streams typed records::

    from hydroformats import open_session

    session = open_session("0000_1346.RAW")
    for record in session.records():
        ...

Each binary dialect also has a bundle loader that materializes a whole
file into working series: :func:`load_survey` (SVLog), :func:`load_swath`
(GSF), :func:`load_imaging` (DDF), and :func:`load_jsf` (JSF; inside
:mod:`hydroformats.jsf` it is named ``load_survey``, aliased here because
SVLog claimed the package-level name first).

Every record class documents the source anchoring its field layout; see
``docs/FORMAT-SOURCES.md``. Unanchored record types parse as
:class:`~hydroformats.records.UnknownRecord` (text) or
:class:`~hydroformats.records.Hs2xOpaque` (binary) — nothing is guessed.
"""
from . import records
from .aris import beam_count_for_ping_mode, load_imaging, read_aris
from .gsf import load_swath, read_gsf
from .hs2x import parse_hs2x
from .hsx import parse_hsx
from .jsf import load_survey as load_jsf
from .jsf import read_jsf
from .raw import parse_raw
from .session import Header, Session, open_session, sniff_dialect
from .svlog import atof_to_yz, load_survey, read_svlog
from .synthetic import SyntheticSurvey, write_hs2x, write_hsx, write_raw
from .xtf import load_survey as load_sidescan
from .xtf import read_xtf

__version__ = "0.2.0"

__all__ = [
    "Header",
    "Session",
    "SyntheticSurvey",
    "atof_to_yz",
    "beam_count_for_ping_mode",
    "load_imaging",
    "load_jsf",
    "load_sidescan",
    "load_survey",
    "load_swath",
    "open_session",
    "parse_hs2x",
    "parse_hsx",
    "parse_raw",
    "read_aris",
    "read_gsf",
    "read_jsf",
    "read_svlog",
    "read_xtf",
    "records",
    "sniff_dialect",
    "write_hs2x",
    "write_hsx",
    "write_raw",
    "__version__",
]
