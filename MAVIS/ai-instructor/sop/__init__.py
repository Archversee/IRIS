"""SOP -> flight rules: an instructor uploads an aircraft SOP PDF, the local
LLM drafts machine-checkable rules into a local draft, the instructor reviews
and edits them and Saves them to Postgres as the aircraft's active rule set.
Saved rules are edited in place with a full history. See docs/sop-rules.md.

Runtime use (e.g. a future core/acs_eval.py):

    from sop.store import RuleStore
    rules = await RuleStore(cfg["database"]["url"]).active_rules("c172sp_g1000")
    # -> {"rule_set_id", "name", "filename", "updated_at", "rules": [enabled rules]} or None
"""
