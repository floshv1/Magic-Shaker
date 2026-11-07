from __future__ import annotations

from p9.model import build_graph, find_element, search


def test_graphe(export) -> None:
    g = build_graph(export)
    kinds = {}
    for _, d in g.nodes(data=True):
        kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
    assert kinds["step"] == 24 and kinds["component_state"] == 72 and kinds["persona_step_feedback"] == 54
    rels = [d["rel"] for *_, d in g.edges(data=True)]
    assert rels.count("expects_state") == 279
    assert rels.count("serves") == 27
    assert rels.count("then") == 24 - 4


def test_find_element_et_search(export) -> None:
    g = build_graph(export)
    hit = search(g, "clearer distance cue")
    assert hit and hit[0]["kind"] == "persona_step_feedback"
    el = find_element(g, hit[0]["id"])
    assert el and {e["rel"] for e in el["outgoing"]} == {"about_step"}
    assert find_element(g, "inconnu") is None
