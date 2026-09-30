"""Build discipline: the authoritative spec for how every document element must
be placed and formatted, plus the inspection that checks a real document's
observed evidence against it.

Document inspection against this discipline is the app's core purpose: catching
misplaced images, missing/wrong captions, disorganized tables, missing
headers/footers/page numbers, and per-element formatting violations.
"""
from .fonts import fonts_equivalent, normalize_font
from .learn import learn_discipline
from .spec import BuildDiscipline, load_discipline, load_profile

__all__ = ["BuildDiscipline", "load_discipline", "load_profile", "learn_discipline",
           "normalize_font", "fonts_equivalent"]
