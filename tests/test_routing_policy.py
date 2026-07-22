"""Policy loading + prefer_local_when ordering tests."""

import json
import os

import pytest

from anima.routing.policy import Candidate, PolicyError, RoutingPolicy


def doc_with_locals():
    return {
        "prefer_local_when": {"tiers": ["reflex"]},
        "tiers": {
            "reflex": {"candidates": [
                {"provider": "cloud1", "model": "c1", "base_url": "http://a"},
                {"provider": "local1", "model": "l1", "base_url": "http://b",
                 "local": True},
                {"provider": "cloud2", "model": "c2", "base_url": "http://c"},
                {"provider": "local2", "model": "l2", "base_url": "http://d",
                 "local": True},
            ]},
            "deep": {"candidates": [
                {"provider": "cloud1", "model": "c1", "base_url": "http://a"},
                {"provider": "local1", "model": "l1", "base_url": "http://b",
                 "local": True},
            ]},
        },
    }


class TestPreferLocalWhen:
    def test_local_moved_to_front_for_matching_tier(self):
        p = RoutingPolicy.from_dict(doc_with_locals())
        order = [c.provider for c in p.candidates_for("reflex")]
        # stable: locals first in original relative order, then remotes
        assert order == ["local1", "local2", "cloud1", "cloud2"]

    def test_non_matching_tier_keeps_declared_order(self):
        p = RoutingPolicy.from_dict(doc_with_locals())
        order = [c.provider for c in p.candidates_for("deep")]
        assert order == ["cloud1", "local1"]

    def test_always_rule(self):
        doc = doc_with_locals()
        doc["prefer_local_when"] = {"always": True}
        p = RoutingPolicy.from_dict(doc)
        assert [c.provider for c in p.candidates_for("deep")] == \
            ["local1", "cloud1"]

    def test_no_rule_keeps_order(self):
        doc = doc_with_locals()
        del doc["prefer_local_when"]
        p = RoutingPolicy.from_dict(doc)
        assert [c.provider for c in p.candidates_for("reflex")][0] == "cloud1"


class TestLoading:
    def test_from_file(self, tmp_path):
        path = tmp_path / "policy.json"
        path.write_text(json.dumps(doc_with_locals()))
        p = RoutingPolicy.from_file(str(path))
        assert set(p.tiers) == {"reflex", "deep"}

    def test_example_policy_file_loads(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        p = RoutingPolicy.from_file(
            os.path.join(here, "examples", "policy.example.json"))
        assert {"reflex", "standard", "deep", "verified_code"} <= set(p.tiers)
        std = p.candidates_for("standard")
        assert std[0].provider == "azure-anthropic-bradford"
        assert any(c.local and "8103" in c.base_url for c in std)
        assert any(c.provider == "ollama" for c in std)
        # reflex prefers local: first candidate must be local
        assert p.candidates_for("reflex")[0].local is True

    def test_defaults_flow_into_tiers(self):
        doc = doc_with_locals()
        doc["defaults"] = {"max_retries_same": 7, "backoff_base_s": 2.0}
        p = RoutingPolicy.from_dict(doc)
        assert p.tier("reflex").max_retries_same == 7
        assert p.tier("reflex").backoff_base_s == 2.0

    def test_tier_overrides_defaults(self):
        doc = doc_with_locals()
        doc["defaults"] = {"max_retries_same": 7}
        doc["tiers"]["deep"]["max_retries_same"] = 1
        p = RoutingPolicy.from_dict(doc)
        assert p.tier("deep").max_retries_same == 1
        assert p.tier("reflex").max_retries_same == 7


class TestValidation:
    def test_empty_tier_rejected(self):
        with pytest.raises(PolicyError):
            RoutingPolicy.from_dict({"tiers": {"x": {"candidates": []}}})

    def test_no_tiers_rejected(self):
        with pytest.raises(PolicyError):
            RoutingPolicy.from_dict({"tiers": {}})

    def test_unknown_tier_query(self):
        p = RoutingPolicy.from_dict(doc_with_locals())
        with pytest.raises(PolicyError, match="unknown tier"):
            p.candidates_for("nope")

    def test_min_content_chars_clamped_to_one(self):
        """Config can never lower the empty-reply bar below 1 char."""
        doc = doc_with_locals()
        doc["tiers"]["reflex"]["min_content_chars"] = 0
        p = RoutingPolicy.from_dict(doc)
        assert p.tier("reflex").min_content_chars == 1

    def test_candidate_id(self):
        c = Candidate(provider="p", model="m", base_url="http://x")
        assert c.id == "p/m"
