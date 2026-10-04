"""Defect knowledge base.

An explicit, enumerable catalog of the document defects the pipeline is expected
to detect and resolve, formalizing the taxonomy proven across the projects and
adversarial review. Each entry knows how to INJECT itself into a first_attempt
and what RESOLUTION status to expect after reconciliation.

Single source for the page-growth alpha loop and the randomized-draft test, so
coverage is measured against a named catalog rather than ad-hoc mutations.
"""
from .catalog import DEFECTS, Defect, defects_by_class, inject

__all__ = ["DEFECTS", "Defect", "defects_by_class", "inject"]
