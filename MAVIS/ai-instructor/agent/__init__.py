"""Agentic layer for FION (Phase 1: conversational tool-router).

The agent is a deterministic orchestrator that owns a registry of typed tools
and hands a student's utterance to the local LLM to pick which tool to call.
It never lets the LLM touch the sim directly - the LLM only returns a tool name
and arguments, which the registry validates before the handler runs. Every path
degrades gracefully to plain Q&A if the LLM is slow or offline.
"""
from .tools import Tool, ToolRegistry
from .router import IntentRouter, Decision

__all__ = ["Tool", "ToolRegistry", "IntentRouter", "Decision"]
