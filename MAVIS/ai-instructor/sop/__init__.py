"""SOP -> rules: instructor uploads an aircraft SOP PDF, the local LLM drafts
machine-checkable rules, the instructor verifies them, and the verified
ruleset is bound to an aircraft profile. See docs/sop-rules.md.

Runtime use (e.g. a future core/acs_eval.py):

    from sop.store import RuleStore
    rules = await RuleStore(cfg["database"]["url"]).active_rules("c172sp_g1000")
    # -> {"ruleset_id", "version", "filename", "rules": [approved rule dicts]} or None
"""
