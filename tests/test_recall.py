import time

from anima.memory import recall


def test_recall_keyword_ranking(store):
    store.add_episode("Patched the fallback classifier in OpenClaw dist",
                      tags=["openclaw", "patch"])
    store.add_episode("Made espresso", tags=["home"])
    pack = recall(store, "fallback classifier patch")
    assert "fallback classifier" in pack
    assert "espresso" not in pack
    assert pack.startswith("## Recall:")


def test_recall_recency_weighting(store):
    now = time.time()
    # identical keyword relevance, different ages
    store.add_episode("gateway restart procedure noted", ts=now - 60 * 86400)
    store.add_episode("gateway restart procedure noted", ts=now - 1 * 86400)
    pack = recall(store, "gateway restart procedure", now=now)
    lines = [ln for ln in pack.splitlines()
             if ln.startswith("- ") and "gateway restart" in ln]
    assert len(lines) == 2
    # the newer episode must render first
    newer = time.strftime("%Y-%m-%d", time.localtime(now - 86400))
    assert newer in lines[0]


def test_recall_tag_filter(store):
    store.add_episode("deploy went fine", tags=["projA"])
    store.add_episode("deploy went fine", tags=["projB"])
    pack = recall(store, "deploy fine", tags=["projA"])
    assert pack.count("deploy went fine") == 1
    assert "#projA" in pack


def test_recall_actor_filter(store):
    store.add_episode("meeting about budget", actors=["Christopher"])
    store.add_episode("meeting about budget", actors=["Kieran"])
    pack = recall(store, "meeting budget", actors=["kieran"])
    assert pack.count("meeting about budget") == 1
    assert "Kieran" in pack


def test_recall_includes_beliefs_and_skills(store):
    e = store.add_episode("verified tunnel endpoint")
    store.add_belief("AiBox tunnel lives on port 8104", provenance=[e],
                     confidence=0.9)
    store.add_skill("tunnel-restart", description="restart the aibox tunnel",
                    recipe="systemctl --user restart aibox-tunnel")
    store.record_skill_outcome("tunnel-restart", success=True)
    pack = recall(store, "tunnel port restart")
    assert "### Beliefs" in pack
    assert "8104" in pack
    assert "### Relevant skills" in pack
    assert "tunnel-restart" in pack
    assert "100%" in pack


def test_recall_marks_stale_beliefs(store):
    now = time.time()
    store.add_belief("old service on port 9999", ts=now - 90 * 86400)
    store.flag_stale_beliefs(30, now=now)
    pack = recall(store, "service port 9999", now=now)
    assert "stale" in pack and "reverify" in pack


def test_recall_token_budget(store):
    for i in range(50):
        store.add_episode(f"budget test event number {i} " + "filler word " * 40)
    pack = recall(store, "budget test event", token_budget=200)
    assert len(pack) // 4 <= 260  # small overshoot slack for headers


def test_recall_empty(store):
    pack = recall(store, "nothing matches this query")
    assert "No relevant memories" in pack
