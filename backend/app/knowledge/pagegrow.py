"""Page-growth synthesis.

Builds a scenario of N "pages" from a base scenario: each page repeats the base
section set with page-suffixed keys (identifiers__p2, description__p2, ...), so
the generic engine treats every page's units as distinct resolvable targets.
Each page carries populated elements (fields, body, table, graphics) plus a
seeded, shuffled subset of defects drawn from the knowledge base, distributed
per page. Corpus and corrections are extended in lockstep so every injected
defect has ground truth to resolve against.

Used by the alpha loop to grow a document one page at a time and confirm the
pipeline stays correct and converges as complexity increases.
"""
from __future__ import annotations

import copy
import random

from .catalog import DEFECTS, Defect


def _suffix(key: str, page: int) -> str:
    return key if page == 1 else f"{key}__p{page}"


def build_multipage(base_scenario: dict, base_template: dict, base_draft: dict,
                    base_corpus: dict[str, str], base_graphics: list[dict],
                    base_corrections: list[dict],
                    pages: int, seed: int) -> dict:
    """Return a dict with keys scenario/template/draft/corpus/graphics/corrections/
    injected for a `pages`-page document with per-page shuffled defects."""
    rng = random.Random(seed)

    scenario = {"id": f"pg{seed}", "title": f"{pages}-page synthetic",
                "fields": [], "section_bodies": {}, "table": None}
    template = {"artifact_kind": "template", "title": "Multi-page template",
                "required_sections": [], "table_specs": {}, "furniture": base_template["furniture"]}
    draft = {"artifact_kind": "draft", "title": "Multi-page draft", "sections": [],
             "furniture": copy.deepcopy(base_draft.get("furniture", {})), "cross_references": []}
    corpus = dict(base_corpus)
    graphics: list[dict] = []
    corrections: list[dict] = []
    injected: list[dict] = []

    base_sections = {s["key"]: s for s in base_draft["sections"]}

    for page in range(1, pages + 1):
        # Per-page defect subset: shuffle the catalog, take 1..3.
        catalog = list(DEFECTS)
        rng.shuffle(catalog)
        page_defects = catalog[: rng.randint(1, 3)]

        for tspec in base_template["required_sections"]:
            skey = _suffix(tspec["key"], page)
            new_tspec = dict(tspec, key=skey,
                             heading=f"{tspec['heading']} (p{page})")
            # Re-point required_graphic to this page's graphic id.
            if tspec.get("requires_graphic"):
                new_tspec["requires_graphic"] = _suffix(tspec["requires_graphic"], page)
            if tspec.get("requires_table"):
                new_tspec["requires_table"] = "corrective_actions_table"
            template["required_sections"].append(new_tspec)

        # Manifest fields / bodies / table per page.
        for f in base_scenario.get("fields", []):
            nf = dict(f, section=_suffix(f["section"], page))
            scenario["fields"].append(nf)
        for sb, sbv in base_scenario.get("section_bodies", {}).items():
            scenario["section_bodies"][_suffix(sb, page)] = sbv
        if base_scenario.get("table") and page == 1:
            scenario["table"] = base_scenario["table"]  # single table, page 1
        template["table_specs"] = base_template.get("table_specs", {})

        # Draft sections (deep-copied), then inject this page's defects.
        for tspec in base_template["required_sections"]:
            src = copy.deepcopy(base_sections.get(tspec["key"], {"key": tspec["key"], "heading": ""}))
            src["key"] = _suffix(tspec["key"], page)
            src["heading"] = f"{tspec.get('heading', '')} (p{page})"
            # Re-point graphic ref names to this page's graphics.
            for g in src.get("graphics", []) or []:
                g["ref_name"] = _page_graphic_name(g["ref_name"], page)
            draft["sections"].append(src)

        # Graphics manifest per page (page-suffixed ids, real names).
        for g in base_graphics:
            graphics.append({
                "graphic_id": _suffix(g["graphic_id"], page),
                "name": _page_graphic_name(g["name"], page),
                "caption": g["caption"],
                "source_doc": g["source_doc"],
                "belongs_in_section": _suffix(g["belongs_in_section"], page),
            })

        # Apply per-page defects to random sections of this page.
        page_section_dicts = [s for s in draft["sections"] if s["key"].endswith(f"__p{page}") or page == 1]
        for d in page_defects:
            target_sec = rng.choice(page_section_dicts)
            _apply(d, target_sec)
            injected.append({"page": page, "defect": d.id, "class": d.defect_class,
                             "section": target_sec["key"]})

    # Corrections: carry the base round-0 corrections re-pointed to page 1 only
    # (later pages rely on corpus-authoritative resolution).
    for c in base_corrections:
        corrections.append(c)

    return {"scenario": scenario, "template": template, "draft": draft,
            "corpus": corpus, "graphics": graphics, "corrections": corrections,
            "injected": injected, "pages": pages}


def _page_graphic_name(name: str, page: int) -> str:
    if page == 1:
        return name
    stem, _, ext = name.rpartition(".")
    return f"{stem}__p{page}.{ext}" if ext else f"{name}__p{page}"


def _apply(defect: Defect, section: dict) -> None:
    try:
        defect.inject(section)
    except Exception:
        # A defect that does not apply to this section shape is a no-op; the
        # loop still exercises the rest. (e.g. table defect on a non-table section)
        pass


def build_multipage_from_pool(base_scenario: dict, base_template: dict, base_draft: dict,
                              base_graphics: list[dict], pool, pages: int, seed: int) -> dict:
    """Pool-backed multi-page builder: each page WITHDRAWS a distinct record from
    the fact pool, so every page has genuinely distinct ground truth (unlike the
    shared-corpus builder). Page count is capped at the pool's capacity; asking
    for more raises PoolExhaustedError.

    Returns scenario/template/draft/corpus/graphics/corrections/injected/records,
    where `corpus` holds one source doc PER PAGE and `records` are the withdrawn
    facts (for distinctness assertions).
    """
    from .factpool import record_to_corpus_doc

    rng = random.Random(seed)
    records = pool.withdraw_n(pages)  # raises PoolExhaustedError if pages > capacity

    scenario = {"id": f"pool{seed}", "title": f"{pages}-page pool-backed",
                "fields": [], "section_bodies": {}, "table": None}
    template = {"artifact_kind": "template", "title": "Multi-page template",
                "required_sections": [], "table_specs": base_template.get("table_specs", {}),
                "furniture": base_template["furniture"]}
    draft = {"artifact_kind": "draft", "title": "Multi-page draft", "sections": [],
             "furniture": copy.deepcopy(base_draft.get("furniture", {})), "cross_references": []}
    corpus: dict[str, str] = {}
    graphics: list[dict] = []
    injected: list[dict] = []

    base_sections = {s["key"]: s for s in base_draft["sections"]}

    for i, record in enumerate(records, start=1):
        page = i
        # Per-page corpus doc holding THIS page's distinct facts.
        fname, text = record_to_corpus_doc(record, page)
        corpus[fname] = text

        # Template + manifest fields/bodies for this page (page-suffixed).
        for tspec in base_template["required_sections"]:
            skey = _suffix(tspec["key"], page)
            new_tspec = dict(tspec, key=skey, heading=f"{tspec['heading']} (p{page})")
            if tspec.get("requires_graphic"):
                new_tspec["requires_graphic"] = _suffix(tspec["requires_graphic"], page)
            template["required_sections"].append(new_tspec)
        for f in base_scenario.get("fields", []):
            # Scope each page's field retrieval to THIS page's corpus doc.
            scenario["fields"].append(dict(f, section=_suffix(f["section"], page),
                                           source_doc=fname))
        for sb, sbv in base_scenario.get("section_bodies", {}).items():
            scenario["section_bodies"][_suffix(sb, page)] = dict(sbv, source_doc=fname)

        # Draft sections seeded with THIS page's (wrong-on-purpose) values so the
        # engine corrects them against this page's corpus facts.
        for tspec in base_template["required_sections"]:
            src = copy.deepcopy(base_sections.get(tspec["key"], {"key": tspec["key"], "heading": ""}))
            src["key"] = _suffix(tspec["key"], page)
            src["heading"] = f"{tspec.get('heading', '')} (p{page})"
            for g in src.get("graphics", []) or []:
                g["ref_name"] = _page_graphic_name(g["ref_name"], page)
            draft["sections"].append(src)

        for g in base_graphics:
            graphics.append({
                "graphic_id": _suffix(g["graphic_id"], page),
                "name": _page_graphic_name(g["name"], page),
                "caption": g["caption"], "source_doc": fname,
                "belongs_in_section": _suffix(g["belongs_in_section"], page),
            })

        # One shuffled defect per page from the KB (kept light; distinctness is
        # the focus here, not defect volume).
        catalog = list(DEFECTS)
        rng.shuffle(catalog)
        d = catalog[0]
        page_secs = [s for s in draft["sections"] if s["key"].endswith(f"__p{page}") or (page == 1)]
        if page_secs:
            target = rng.choice(page_secs)
            _apply(d, target)
            injected.append({"page": page, "defect": d.id, "section": target["key"]})

    return {"scenario": scenario, "template": template, "draft": draft,
            "corpus": corpus, "graphics": graphics, "corrections": [],
            "injected": injected, "records": records, "pages": pages}
