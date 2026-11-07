import yaml

from premise_v2.envelope import AuthorRef
from premise_v2.reconcile import reconcile
from test_v2_pipeline import FakeGitHub, env, pipeline, r, stream_name


def ready(r, new_graph, stream_name, model=None):
    kwargs = {"model": model} if model else {}
    p = pipeline(r, new_graph, stream_name, **kwargs)
    p.workers = []
    p.staging.setup()
    p.load_decision_graph()
    return p


def test_switch_accounts_use_sender_id_and_reviewed_links(r, new_graph, stream_name, repo_copy):
    from premise_v2.entities import EntityCatalog
    from premise_v2.identity import account_of
    from premise_v2.normalize import IdentityResolver
    from premise_v2.registry import Ext

    path = repo_copy / "ext/identities.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["people"]["p_anika"]["switch"] = "participant-anika"
    doc["links"] = [{"account": "switch:participant-alias", "person": "p_anika", "status": "confirmed"}]
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    p = ready(r, new_graph, stream_name, repo_copy)
    message = env("Paylane now retries for 72 hours.").model_copy(update={
        "source": "switch", "entity_key": "switch:payments:m1", "native_id": "payments:m1",
        "author": AuthorRef(source_user_id="participant-alias")})
    assert account_of(message) == "switch:participant-alias"
    p.handle(message)
    p.identities.sync()
    assert p.identities.person_map()["switch:participant-alias"] == "p_anika"
    direct = message.model_copy(update={"author": AuthorRef(source_user_id="participant-anika")})
    assert IdentityResolver(Ext.load(repo_copy)).resolve(direct).person == "p_anika"
    assert EntityCatalog(p.decision, repo_copy).resolves("switch:participant-anika", "p_anika")


def test_fuzzy_proposal_does_not_merge_accounts_and_bridge_is_individual(r, new_graph, stream_name):
    p = ready(r, new_graph, stream_name)
    p.handle(env("Paylane now retries for 72 hours.", user="U01ANIKA"))
    alias = env("Paylane now retries for 72 hours.", key="slack:C2:2.0", user="UALIAS")
    alias.author = AuthorRef(source_user_id="UALIAS", display_name="Anika Ra")
    p.handle(alias)
    s = p.run(load_decision=False)
    assert s.drifts[0]["support"] == "corroborated"  # proposed link cannot merge the accounts
    link = next(x for x in p.identities.links() if x["account"] == "slack:UALIAS")
    assert link["status"] == "proposed" and link["score"] >= 92 and link["bridge"]
    assert len(s.identity_proposals) == 1 and s.identity_proposals[0]["bridge"]
    row = p.staging.proposal(s.identity_proposals[0]["key"])
    assert "Bridge warning" in row["body"] and row["kind"] == "identity"


def test_abbreviated_name_below_92_is_not_proposed(r, new_graph, stream_name):
    p = ready(r, new_graph, stream_name)
    alias = env("Paylane retries for 72 hours.", user="UALIAS")
    alias.author = AuthorRef(source_user_id="UALIAS", display_name="Anika R.")
    p.handle(alias)
    p.identities.sync()
    assert not p.identities.links()
    assert p.identities.person_map() == {}


def test_bridge_counts_authoritative_account_before_first_message(r, new_graph, stream_name):
    p = ready(r, new_graph, stream_name)
    alias = env("Profile updated", user="UOMAR_ALIAS")
    alias.author = AuthorRef(source_user_id="UOMAR_ALIAS", display_name="Omar Haddad")
    p.handle(alias)
    p.identities.sync()
    assert p.identities.links()[0]["bridge"] is True
    assert p.identities.person_map() == {}


def test_replayed_phase1_claims_backfill_accounts_without_extraction(r, new_graph, stream_name, repo_copy, monkeypatch):
    p = ready(r, new_graph, stream_name, repo_copy)
    messages = [env("Paylane now retries for 72 hours.", user="U01ANIKA"),
                env("Paylane now retries for 72 hours.", key="slack:C2:2.0", user="UALIAS")]
    for message in messages:
        p.handle(message)
    # Phase 1 stored person assignments and lacked source-account properties.
    p.staging.c.write("MATCH (c:Claim) SET c.person='legacy-person' REMOVE c.account")
    before = p.staging.c.read("MATCH (c:Claim) RETURN c.id AS id ORDER BY id")
    monkeypatch.setattr(p.extractor, "extract", lambda *a: __import__("pytest").fail("unchanged source was re-extracted"))
    for message in messages:
        p.handle(message)
    assert p.staging.c.read("MATCH (c:Claim) RETURN c.id AS id ORDER BY id") == before
    assert all(c["account"] and c["person"] is None for c in p.staging.c.read(
        "MATCH (c:Claim) RETURN c.account AS account, c.person AS person"))
    assert reconcile(p.staging, p.decision, repo_copy)[0][0].support == "corroborated"
    path = repo_copy / "ext/identities.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["links"] = [{"account": "slack:UALIAS", "person": "p_anika", "status": "confirmed"}]
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert reconcile(p.staging, p.decision, repo_copy)[0][0].support == "observed"


def test_changed_and_removed_mapping_recounts_without_reprocessing(r, new_graph, stream_name, repo_copy):
    p = ready(r, new_graph, stream_name, repo_copy)
    p.handle(env("Paylane now retries for 72 hours.", user="U01ANIKA"))
    p.handle(env("Paylane now retries for 72 hours.", key="slack:C2:2.0", user="UALIAS"))
    query = "MATCH (c:Claim) RETURN c.id AS id, c.account AS account, c.person AS person ORDER BY id"
    before = p.staging.c.read(query)
    assert all(c["account"].startswith("slack:") and c["person"] is None for c in before)
    assert reconcile(p.staging, p.decision, p.model_root)[0][0].support == "corroborated"
    path = repo_copy / "ext/identities.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["links"] = [{"account": "slack:UALIAS", "person": "p_anika", "status": "confirmed"}]
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert reconcile(p.staging, p.decision, p.model_root)[0][0].support == "observed"
    doc["links"] = []
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert reconcile(p.staging, p.decision, p.model_root)[0][0].support == "corroborated"
    assert p.staging.c.read(query) == before
    assert next(x for x in p.identities.links() if x["account"] == "slack:UALIAS")["status"] == "rejected"


def test_only_verified_exact_email_auto_links(r, new_graph, stream_name):
    p = ready(r, new_graph, stream_name)
    for n, verified in enumerate((False, True)):
        e = env("Paylane retries for 72 hours.", key=f"slack:C2:{n}.0", user=f"UEMAIL{n}")
        e.author = AuthorRef(source_user_id=f"UEMAIL{n}", email="ANIKA@kestrel.example", email_verified=verified)
        p.handle(e)
    p.identities.sync()
    assert p.identities.person_map() == {"slack:UEMAIL1": "p_anika"}


def test_identity_publication_is_idempotent_and_does_not_modify_model(r, new_graph, stream_name, repo_copy):
    p = ready(r, new_graph, stream_name, repo_copy)
    e = env("Paylane retries for 72 hours.", user="UALIAS")
    e.author = AuthorRef(source_user_id="UALIAS", display_name="Anika Ra")
    p.handle(e)
    old = (repo_copy / "ext/identities.yaml").read_bytes()
    gh = FakeGitHub()
    p.writer.github = gh
    p.run(load_decision=False)
    assert len(gh.prs) == 1
    assert "identity" in gh.prs[1]["body"]
    gh.calls.clear()
    p.run(load_decision=False)
    assert not gh.calls and len(gh.prs) == 1
    assert (repo_copy / "ext/identities.yaml").read_bytes() == old


def test_ambiguous_model_mapping_fails_before_changing_links(r, new_graph, stream_name, repo_copy):
    import pytest
    p = ready(r, new_graph, stream_name, repo_copy)
    p.handle(env("Paylane retries for 72 hours.", user="U01ANIKA"))
    p.identities.sync()
    before = p.identities.person_map()
    path = repo_copy / "ext/identities.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["people"]["p_jun"]["slack"] = "U01ANIKA"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="multiple people"):
        p.identities.sync()
    assert p.identities.person_map() == before
