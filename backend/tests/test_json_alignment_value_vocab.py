"""Tests for the human-taught learned value vocabulary.

Locks the governed behavior: a human-accepted value is remembered and widens a
golden field's allowed set (recall), an un-accepted look-alike stays vetoed
(precision), CRUD edit/remove work, remove takes effect immediately (re-veto),
the merge is a pure function, and a value can only be taught for a real field.
"""
from __future__ import annotations

import pytest
from app.config import settings
from app.json_alignment.pipeline import run_alignment
from app.json_alignment.value_vocab import (
    ValueVocabulary,
    VocabEntry,
    merge_vocab_into_schema,
)
from app.main import app
from starlette.testclient import TestClient

_SCHEMA = {
    "title": "game",
    "properties": {
        "platform": {
            "type": "string",
            "description": "the console or hardware system",
            "x-pydi-taxonomy": ["Windows PC", "Xbox", "PlayStation", "Nintendo Switch"],
        },
        "ESRB": {
            "type": "string",
            "description": "ESRB age rating",
            "x-pydi-taxonomy": ["E", "E10+", "T", "M", "AO"],
        },
    },
}


# --- store CRUD -------------------------------------------------------------

def test_add_is_idempotent_and_refreshes_metadata():
    v = ValueVocabulary()
    v.add("platform", "Linux", added_by="a", note="first")
    v.add("platform", "Linux", added_by="b", note="second")  # same value again
    entries = v.list_field("platform")
    assert len(entries) == 1                 # no duplicate
    assert entries[0].note == "second"       # metadata refreshed


def test_update_renames_and_realias():
    v = ValueVocabulary()
    v.add("platform", "linux")
    v.update("platform", "linux", new_value="Linux", alias="lnx")
    e = v.list_field("platform")[0]
    assert e.value == "Linux" and e.alias == "lnx"


def test_update_preserves_untouched_provenance():
    # A partial update (rename only) must NOT wipe note/example/alias the
    # reviewer didn't touch (Breaker finding: default note='' was overwriting).
    v = ValueVocabulary()
    v.add("platform", "linux", alias="lnx", note="keep me", example="src-x")
    v.update("platform", "linux", new_value="Linux")  # omit note/example/alias
    e = v.list_field("platform")[0]
    assert e.value == "Linux"
    assert e.note == "keep me"       # preserved
    assert e.example == "src-x"      # preserved
    assert e.alias == "lnx"          # preserved


def test_update_can_explicitly_set_and_clear_fields():
    v = ValueVocabulary()
    v.add("platform", "Linux", note="old", example="ex")
    v.update("platform", "Linux", note="new")        # set note, leave example
    e = v.list_field("platform")[0]
    assert e.note == "new" and e.example == "ex"
    v.update("platform", "Linux", note="", example="")  # explicit clear
    e = v.list_field("platform")[0]
    assert e.note == "" and e.example == ""


def test_remove_returns_flag_and_clears_empty_field():
    v = ValueVocabulary()
    v.add("platform", "Linux")
    assert v.remove("platform", "Linux") is True
    assert v.list_field("platform") == []
    assert "platform" not in v.by_field       # empty field pruned
    assert v.remove("platform", "Linux") is False  # nothing left to remove


def test_add_empty_value_rejected():
    import pytest
    v = ValueVocabulary()
    with pytest.raises(ValueError):
        v.add("platform", "   ")


def test_roundtrip_serialization():
    v = ValueVocabulary()
    v.add("platform", "Linux", alias="linux", added_by="rev", note="n", example="x")
    again = ValueVocabulary.from_dict(v.to_dict())
    assert again.list_field("platform")[0] == v.list_field("platform")[0]


# --- merge (pure function) --------------------------------------------------

def test_merge_adds_value_and_alias_without_mutating_inputs():
    v = ValueVocabulary()
    v.add("platform", "Linux", alias="linux")
    merged = merge_vocab_into_schema(_SCHEMA, v)
    tax = merged["properties"]["platform"]["x-pydi-taxonomy"]
    assert tax == ["Windows PC", "Xbox", "PlayStation", "Nintendo Switch", "Linux"]
    assert merged["properties"]["platform"]["x-pydi-taxonomy-aliases"] == {"linux": "Linux"}
    # inputs untouched (pure)
    assert "Linux" not in _SCHEMA["properties"]["platform"]["x-pydi-taxonomy"]


def test_merge_is_noop_without_learned_values():
    assert merge_vocab_into_schema(_SCHEMA, ValueVocabulary()) is _SCHEMA


def test_merge_ignores_unknown_field():
    v = ValueVocabulary()
    v.by_field["not_a_field"] = [VocabEntry(value="x")]
    merged = merge_vocab_into_schema(_SCHEMA, v)
    assert "not_a_field" not in merged["properties"]


def test_merge_does_not_duplicate_existing_schema_value():
    v = ValueVocabulary()
    v.add("platform", "Xbox")  # already in the schema taxonomy
    tax = merge_vocab_into_schema(_SCHEMA, v)["properties"]["platform"]["x-pydi-taxonomy"]
    assert tax.count("Xbox") == 1


# --- end-to-end through the matcher ----------------------------------------

def _platform_grounds(schema, value: str) -> bool:
    """A platform synonym field 'console' carrying `value` grounds platform only
    if the value gate allows the value."""
    return "platform" in run_alignment([{"console": value}], schema).mapping.mapped()


def test_learned_value_recovers_but_lookalike_junk_stays_vetoed():
    # baseline: unlisted Linux vetoed; junk Steam vetoed; schema Xbox grounds.
    assert not _platform_grounds(_SCHEMA, "Linux")
    assert not _platform_grounds(_SCHEMA, "Steam")
    assert _platform_grounds(_SCHEMA, "Xbox")

    # teach Linux -> recovers; Steam (NOT taught) still vetoed.
    v = ValueVocabulary()
    v.add("platform", "Linux", added_by="reviewer")
    merged = merge_vocab_into_schema(_SCHEMA, v)
    assert _platform_grounds(merged, "Linux")
    assert not _platform_grounds(merged, "Steam")


def test_remove_re_vetoes_immediately():
    v = ValueVocabulary()
    v.add("platform", "Linux")
    assert _platform_grounds(merge_vocab_into_schema(_SCHEMA, v), "Linux")
    v.remove("platform", "Linux")
    assert not _platform_grounds(merge_vocab_into_schema(_SCHEMA, v), "Linux")


def test_learned_esrb_value_recovers():
    v = ValueVocabulary()
    v.add("ESRB", "RP", note="Rating Pending")
    merged = merge_vocab_into_schema(_SCHEMA, v)
    m = run_alignment([{"maturity": "RP"}], merged).mapping.mapped()
    assert m.get("ESRB") == "maturity"


# --- CRUD API endpoints -----------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path, raising=False)
    import app.jobs.runtime as rt
    monkeypatch.setattr(rt, "_queue", None, raising=False)
    return TestClient(app)


def _project_with_schema(client: TestClient) -> str:
    pid = client.post("/projects", json={"name": "vocab test"}).json()["id"]
    r = client.put(f"/projects/{pid}/alignment/target-schema", json={"target": _SCHEMA})
    assert r.status_code == 200, r.text
    return pid


def test_api_list_separates_builtin_from_learned(client):
    pid = _project_with_schema(client)
    body = client.get(f"/projects/{pid}/alignment/value-vocab").json()
    plat = next(f for f in body["fields"] if f["target"] == "platform")
    assert "Windows PC" in plat["builtin"]
    assert plat["learned"] == []            # nothing learned yet


def test_api_add_then_list_then_remove(client):
    pid = _project_with_schema(client)
    # add
    r = client.post(f"/projects/{pid}/alignment/value-vocab",
                    json={"action": "add", "target": "platform", "value": "Linux",
                          "alias": "linux", "added_by": "rev", "note": "valid pc"})
    assert r.status_code == 200, r.text
    plat = next(f for f in r.json()["vocab"] if f["target"] == "platform")
    assert [e["value"] for e in plat["learned"]] == ["Linux"]
    assert plat["learned"][0]["added_by"] == "rev"
    # remove
    r2 = client.post(f"/projects/{pid}/alignment/value-vocab",
                     json={"action": "remove", "target": "platform", "value": "Linux"})
    assert r2.status_code == 200, r2.text
    plat2 = next(f for f in r2.json()["vocab"] if f["target"] == "platform")
    assert plat2["learned"] == []


def test_api_rejects_unknown_field(client):
    pid = _project_with_schema(client)
    r = client.post(f"/projects/{pid}/alignment/value-vocab",
                    json={"action": "add", "target": "not_a_field", "value": "x"})
    assert r.status_code == 400
    assert "unknown golden field" in r.json()["detail"]


def test_api_update_renames_value(client):
    pid = _project_with_schema(client)
    client.post(f"/projects/{pid}/alignment/value-vocab",
                json={"action": "add", "target": "platform", "value": "linux"})
    r = client.post(f"/projects/{pid}/alignment/value-vocab",
                    json={"action": "update", "target": "platform", "value": "linux",
                          "new_value": "Linux"})
    assert r.status_code == 200, r.text
    plat = next(f for f in r.json()["vocab"] if f["target"] == "platform")
    assert [e["value"] for e in plat["learned"]] == ["Linux"]


def test_api_partial_update_preserves_note(client):
    # Breaker regression: a partial update over the API must not wipe the note.
    pid = _project_with_schema(client)
    client.post(f"/projects/{pid}/alignment/value-vocab",
                json={"action": "add", "target": "platform", "value": "Linux",
                      "note": "valid pc platform", "example": "console: Linux"})
    r = client.post(f"/projects/{pid}/alignment/value-vocab",
                    json={"action": "update", "target": "platform", "value": "Linux",
                          "alias": "linux"})  # only set alias; note/example omitted
    assert r.status_code == 200, r.text
    plat = next(f for f in r.json()["vocab"] if f["target"] == "platform")
    e = plat["learned"][0]
    assert e["alias"] == "linux"
    assert e["note"] == "valid pc platform"       # preserved
    assert e["example"] == "console: Linux"        # preserved


# --- alias CRUD API ---------------------------------------------------------

_ARRAY_SCHEMA = {
    "title": "game",
    "properties": {
        "platform": {
            "type": "array",
            "description": "the consoles the title ships on",
            "items": {
                "type": "string",
                "enum": ["Windows PC", "Xbox", "PlayStation", "Nintendo Switch"],
            },
        },
    },
}


def _project_with_array_schema(client: TestClient) -> str:
    pid = client.post("/projects", json={"name": "alias array test"}).json()["id"]
    r = client.put(f"/projects/{pid}/alignment/target-schema",
                   json={"target": _ARRAY_SCHEMA})
    assert r.status_code == 200, r.text
    return pid


def _plat(vocab: list[dict]) -> dict:
    return next(f for f in vocab if f["target"] == "platform")


def test_add_alias_endpoint_200_and_lists_in_get_vocab(client):
    pid = _project_with_schema(client)
    r = client.post(f"/projects/{pid}/alignment/value-vocab",
                    json={"action": "add_alias", "target": "platform",
                          "alias": "ps", "value": "PlayStation", "added_by": "rev"})
    assert r.status_code == 200, r.text
    assert r.json()["change"]["entry"]["alias"] == "ps"
    body = client.get(f"/projects/{pid}/alignment/value-vocab").json()
    aliases = _plat(body["fields"])["aliases"]
    assert [(a["alias"], a["value"]) for a in aliases] == [("ps", "PlayStation")]


def test_add_alias_canonical_not_accepted_422(client):
    pid = _project_with_schema(client)
    r = client.post(f"/projects/{pid}/alignment/value-vocab",
                    json={"action": "add_alias", "target": "platform",
                          "alias": "steamy", "value": "Steam"})
    assert r.status_code == 422, r.text
    assert "not an accepted value" in r.json()["detail"]


def test_add_alias_raw_equals_accepted_value_422(client):
    pid = _project_with_schema(client)
    r = client.post(f"/projects/{pid}/alignment/value-vocab",
                    json={"action": "add_alias", "target": "platform",
                          "alias": "Xbox", "value": "PlayStation"})
    assert r.status_code == 422, r.text
    assert "already an accepted value" in r.json()["detail"]


def test_add_alias_case_sensitive_membership(client):
    pid = _project_with_schema(client)
    # raw == accepted value 'Xbox' -> rejected by rule 2
    r = client.post(f"/projects/{pid}/alignment/value-vocab",
                    json={"action": "add_alias", "target": "platform",
                          "alias": "Xbox", "value": "Xbox"})
    assert r.status_code == 422, r.text
    # case-only difference 'xbox' is NOT equal under case-sensitive membership
    r2 = client.post(f"/projects/{pid}/alignment/value-vocab",
                     json={"action": "add_alias", "target": "platform",
                           "alias": "xbox", "value": "Xbox"})
    assert r2.status_code == 200, r2.text
    # and it folds so an incoming 'xbox' normalizes to 'Xbox'
    from app.json_alignment.project_store import effective_target_schema
    eff = effective_target_schema(pid)
    assert eff["properties"]["platform"]["x-pydi-taxonomy-aliases"]["xbox"] == "Xbox"


def test_add_alias_remap_422_and_same_pair_200(client):
    pid = _project_with_schema(client)
    first = client.post(f"/projects/{pid}/alignment/value-vocab",
                        json={"action": "add_alias", "target": "platform",
                              "alias": "ps", "value": "PlayStation"})
    assert first.status_code == 200, first.text
    # remap same raw to a different canonical -> 422
    remap = client.post(f"/projects/{pid}/alignment/value-vocab",
                        json={"action": "add_alias", "target": "platform",
                              "alias": "ps", "value": "Xbox"})
    assert remap.status_code == 422, remap.text
    assert "already maps to" in remap.json()["detail"]
    # re-adding the identical pair -> 200, no duplicate
    same = client.post(f"/projects/{pid}/alignment/value-vocab",
                       json={"action": "add_alias", "target": "platform",
                             "alias": "ps", "value": "PlayStation"})
    assert same.status_code == 200, same.text
    aliases = _plat(same.json()["vocab"])["aliases"]
    assert len(aliases) == 1


def test_alias_per_field_independence(client):
    pid = _project_with_schema(client)
    r1 = client.post(f"/projects/{pid}/alignment/value-vocab",
                     json={"action": "add_alias", "target": "platform",
                           "alias": "same", "value": "PlayStation"})
    assert r1.status_code == 200, r1.text
    r2 = client.post(f"/projects/{pid}/alignment/value-vocab",
                     json={"action": "add_alias", "target": "ESRB",
                           "alias": "same", "value": "M"})
    assert r2.status_code == 200, r2.text


def test_remove_alias_missing_404(client):
    pid = _project_with_schema(client)
    r = client.post(f"/projects/{pid}/alignment/value-vocab",
                    json={"action": "remove_alias", "target": "platform",
                          "alias": "nope"})
    assert r.status_code == 404, r.text
    assert "has no alias" in r.json()["detail"]


def test_remove_learned_value_cascades_aliases(client):
    pid = _project_with_schema(client)
    # learn a value, then point an alias at it
    client.post(f"/projects/{pid}/alignment/value-vocab",
                json={"action": "add", "target": "platform", "value": "Steam"})
    client.post(f"/projects/{pid}/alignment/value-vocab",
                json={"action": "add_alias", "target": "platform",
                      "alias": "steamy", "value": "Steam"})
    # remove the learned value -> cascade drops its alias, reported under entry
    r = client.post(f"/projects/{pid}/alignment/value-vocab",
                    json={"action": "remove", "target": "platform", "value": "Steam"})
    assert r.status_code == 200, r.text
    change = r.json()["change"]
    assert change["entry"]["removed"] == "Steam"
    assert change["entry"]["dropped_aliases"] == ["steamy"]
    assert "dropped_aliases" not in change            # NOT at top level
    # a later GET no longer lists the dropped alias
    body = client.get(f"/projects/{pid}/alignment/value-vocab").json()
    assert _plat(body["fields"])["aliases"] == []


# --- end-to-end through the matcher (recall + precision) --------------------

def _platform_grounds(schema, value) -> bool:
    return "platform" in run_alignment([{"console": value}], schema).mapping.mapped()


def test_aliased_value_to_LEARNED_target_conforms_end_to_end(client):  # noqa: N802
    pid = _project_with_schema(client)
    # canonical PlayStation is BUILTIN here; to make it a LEARNED target, teach a
    # learned value and alias a raw spelling to it.
    client.post(f"/projects/{pid}/alignment/value-vocab",
                json={"action": "add", "target": "platform", "value": "Linux"})
    client.post(f"/projects/{pid}/alignment/value-vocab",
                json={"action": "add_alias", "target": "platform",
                      "alias": "lnx", "value": "Linux"})
    from app.json_alignment.project_store import effective_target_schema
    eff = effective_target_schema(pid)
    assert _platform_grounds(eff, "lnx")          # alias -> learned value grounds
    assert not _platform_grounds(eff, "Steam")     # non-aliased junk stays vetoed


def test_aliased_value_to_BUILTIN_target_conforms_end_to_end(client):  # noqa: N802
    pid = _project_with_schema(client)
    r = client.post(f"/projects/{pid}/alignment/value-vocab",
                    json={"action": "add_alias", "target": "platform",
                          "alias": "Win", "value": "Windows PC"})
    assert r.status_code == 200, r.text
    from app.json_alignment.project_store import effective_target_schema
    eff = effective_target_schema(pid)
    # alias -> BUILTIN canonical (no backing VocabEntry) grounds end-to-end
    assert _platform_grounds(eff, "Win")
    assert not _platform_grounds(eff, "Steam")


def test_aliased_value_on_ARRAY_field_conforms_end_to_end(client):  # noqa: N802
    pid = _project_with_array_schema(client)
    r = client.post(f"/projects/{pid}/alignment/value-vocab",
                    json={"action": "add_alias", "target": "platform",
                          "alias": "PS5", "value": "PlayStation"})
    assert r.status_code == 200, r.text
    from app.json_alignment.project_store import effective_target_schema
    eff = effective_target_schema(pid)
    node = eff["properties"]["platform"]
    # alias present on the TOP-LEVEL node so extract_target picks it up
    assert node["x-pydi-taxonomy-aliases"] == {"PS5": "PlayStation"}
    assert "x-pydi-taxonomy-aliases" not in node["items"]
    # run_alignment on a source carrying PS5 normalizes to PlayStation (the
    # array split_list applies tgt.aliases per item) and conforms; a non-aliased
    # junk array value (Steam) fails the value gate and stays non-conformant.
    ps = run_alignment([{"platform": "PS5"}], eff)
    assert ps.records[0]["platform"] == ["PlayStation"]   # normalized
    assert ps.validation[0].ok                             # conforms
    junk = run_alignment([{"platform": "Steam"}], eff)
    assert not junk.validation[0].ok                       # stays vetoed
