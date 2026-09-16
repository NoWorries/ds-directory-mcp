"""
Detect a handful of extra, best-effort metadata signals from a design
system's own crawled page text: accessibility conformance level, design
tokens format, implementation framework(s), dark-mode/theming support,
multi-brand/white-label support, governance model, and deprecation policy.

Same spirit as resources.py's RESOURCE_PATTERNS — pattern matching, not
authoritative claims — just applied to crawled page TEXT instead of URLs.
Deliberately lower-confidence/more niche than resources.py's github/npm/etc
links, which is why these are shown on a system's own detail page rather
than the summary matrix (see generate_systems.py) — worth knowing once
you're already looking at one system, not worth a column every visitor
scans past.
"""

import re

# (pattern, display label) — first match wins, ordered most-specific-first
# so e.g. "WCAG 2.2 AA" doesn't get shadowed by a looser "WCAG" match.
_A11Y_PATTERNS = [
    (re.compile(r"wcag\s*2\.2\s*aaa", re.IGNORECASE), "WCAG 2.2 AAA"),
    (re.compile(r"wcag\s*2\.2\s*aa\b", re.IGNORECASE), "WCAG 2.2 AA"),
    (re.compile(r"wcag\s*2\.1\s*aaa", re.IGNORECASE), "WCAG 2.1 AAA"),
    (re.compile(r"wcag\s*2\.1\s*aa\b", re.IGNORECASE), "WCAG 2.1 AA"),
    (re.compile(r"wcag\s*2\.0\s*aa\b", re.IGNORECASE), "WCAG 2.0 AA"),
    (re.compile(r"section\s*508", re.IGNORECASE), "Section 508"),
]

_TOKEN_FORMAT_PATTERNS = [
    (re.compile(r"style\s*dictionary", re.IGNORECASE), "Style Dictionary"),
    # DTCG (the W3C Design Tokens Community Group's actual token format spec)
    # checked as its own, more specific label before the looser "W3C Design
    # Tokens" phrase below — a system citing the spec by its common acronym
    # or its .tokens/.tokens.json file convention is making a stronger, more
    # concrete claim than one that just says "design tokens" in passing.
    (re.compile(r"\bDTCG\b|design\s*tokens?\s*community\s*group|\.tokens(?:\.json)?\b", re.IGNORECASE), "DTCG"),
    (re.compile(r"w3c\s*design\s*tokens|design\s*tokens?\s*format\s*module", re.IGNORECASE), "W3C Design Tokens"),
    (re.compile(r"figma\s*variables", re.IGNORECASE), "Figma Variables"),
    (re.compile(r"tokens\s*studio", re.IGNORECASE), "Tokens Studio"),
]

# Every match kept (a system can genuinely support more than one framework),
# unlike a11y/tokens above where only the strongest single match is useful.
_FRAMEWORK_PATTERNS = [
    (re.compile(r"\breact\b", re.IGNORECASE), "React"),
    (re.compile(r"\bvue(?:\.js)?\b", re.IGNORECASE), "Vue"),
    (re.compile(r"\bangular\b", re.IGNORECASE), "Angular"),
    (re.compile(r"web\s*components?\b", re.IGNORECASE), "Web Components"),
    (re.compile(r"\bswiftui\b", re.IGNORECASE), "SwiftUI"),
    (re.compile(r"jetpack\s*compose", re.IGNORECASE), "Jetpack Compose"),
    (re.compile(r"\bsvelte\b", re.IGNORECASE), "Svelte"),
]

_DARK_MODE_PATTERN = re.compile(r"dark\s*mode|dark\s*theme", re.IGNORECASE)
_MULTI_BRAND_PATTERN = re.compile(r"multi[- ]brand|white[- ]label(?:l)?ing", re.IGNORECASE)

# Whether a system documents WHO gets to decide things and HOW — a formal
# governance model (RFC/proposal process, steering committee, core-team vs.
# contributor tiers) is a genuinely different, checkable claim from just
# having a CONTRIBUTING.md (already tracked separately as the markdown_docs
# resource) — this is about decision-making structure, not just "how to
# submit a PR".
_GOVERNANCE_PATTERN = re.compile(
    r"governance\s*model|steering\s*committee|rfc\s*process|core\s*team\b|working\s*group",
    re.IGNORECASE,
)

# Whether "deprecated" is a documented, first-class state (a policy/process
# for retiring components/tokens) rather than just the word appearing once on
# a random page — the phrasing patterns below are deliberately about the
# PROCESS ("deprecation policy", "migration guide for deprecated X"), not a
# bare "deprecated" which is too common/noisy to mean anything on its own.
_DEPRECATION_POLICY_PATTERN = re.compile(
    r"deprecation\s*(policy|process|guidelines?|timeline)|deprecated\s*components?\s*(are|will\s*be)\s*removed",
    re.IGNORECASE,
)

# Remaining AI-affordance types from the State of AI in Design Systems survey
# (https://state-of-ai-in-design-systems.netlify.app/) not already covered by
# resources.py's URL-based detection (copilot_instructions/cursor_rules/
# registry) — these four are mentions in prose rather than a discoverable
# file at a fixed path, so text matching fits them better than a resource URL.
_CLI_SCAFFOLDING_PATTERN = re.compile(r"npx create-[\w-]+|npm create [\w@/-]+", re.IGNORECASE)
_CODEMOD_PATTERN = re.compile(r"\bcodemods?\b|jscodeshift|ts-morph", re.IGNORECASE)
# "code connect" alone is too generic (any two words), so this requires the
# Figma-specific framing right next to it.
_FIGMA_CODE_CONNECT_PATTERN = re.compile(r"figma\s*code\s*connect|figma['’]?s?\s*code\s*connect", re.IGNORECASE)
_PROMPT_LIBRARY_PATTERN = re.compile(r"prompt\s*library|library\s*of\s*prompts", re.IGNORECASE)

# The 11-category "techniques" taxonomy from the same survey (https://state-
# of-ai-in-design-systems.netlify.app/techniques) — ways a design system tries
# to keep a model from inventing components/tokens instead of using real
# ones. Five of the eleven are covered elsewhere already and reused rather
# than re-detected here (see generate_systems.py's render_ai_native() for how
# all eleven are assembled): curated-context/instruction-files come from the
# agent_instructions/copilot_instructions/cursor_rules resource URLs,
# registry-metadata from the registry resource, scaffolding from
# cli_scaffolding above, and design-code-mapping from figma_code_connect
# above. The five below are prose-only techniques with no discoverable file
# of their own, so text matching is the only way to catch them at all.
_VALIDATION_LOOP_PATTERN = re.compile(
    r"eslint[- ]plugin|stylelint|type[- ]check(?:ing|s)?\b.*(?:agent|ai|model)|"
    r"(?:agent|ai|model).*(?:lint|type[- ]check)|design\s*lint",
    re.IGNORECASE,
)
_PROHIBITION_PATTERN = re.compile(
    r"never\s+(invent|hardcode|fabricate)|do\s+not\s+(invent|hardcode|use\s+inline\s+styles)|"
    r"don'?t\s+(invent|hardcode)|no\s+raw\s+colou?r\s+values|do\s+not\s+guess",
    re.IGNORECASE,
)
_TOOL_GATING_PATTERN = re.compile(
    r"do\s+not\s+guess|never\s+guess|do\s+not\s+hallucinate|always\s+(query|call|use)\s+the\s+(mcp|cli|tool)|"
    r"before\s+(writing|generating)\s+(any\s+)?code,?\s+(call|query|check)",
    re.IGNORECASE,
)
_TOKEN_ENFORCEMENT_PATTERN = re.compile(
    r"no\s+raw\s+(hex|colou?r)\s+values|must\s+use\s+(design\s+)?tokens|token\s*-?\s*only|"
    r"enforce\s+(design\s+)?tokens|tokens?\s+instead\s+of\s+(raw|hard[- ]coded)\s+values",
    re.IGNORECASE,
)
# Either the ✅/❌ glyph pairing docs commonly use for do/don't code blocks, or
# the equivalent spelled out as words.
_EXEMPLARS_PATTERN = re.compile(
    r"✅.{0,200}❌|❌.{0,200}✅|\bdo\b\s*:.{0,200}\bdon'?t\b\s*:|correct\s*:.{0,300}incorrect\s*:",
    re.IGNORECASE | re.DOTALL,
)

# Cap how much crawled text gets scanned — this is a cheap keyword heuristic
# over whatever got indexed, not a reason to build a second full-text index;
# the first ~200k characters (a few dozen average pages) is plenty to catch
# a term a site actually uses anywhere in its own docs.
_MAX_SCAN_CHARS = 200_000


def detect_content_signals(pages: dict[str, str]) -> dict:
    """pages: {url: text} from a single crawl. Returns whatever of
    accessibility_conformance / tokens_format / frameworks / dark_mode /
    multi_brand / governance_model / deprecation_policy / cli_scaffolding /
    codemods / figma_code_connect / prompt_library was actually found — keys
    are simply absent when nothing matched, never a false "no" claim about
    something merely undetected."""
    combined = "\n".join(pages.values())[:_MAX_SCAN_CHARS]

    def first_match(patterns: list[tuple[re.Pattern, str]]) -> str | None:
        for pattern, label in patterns:
            if pattern.search(combined):
                return label
        return None

    signals: dict = {}

    a11y = first_match(_A11Y_PATTERNS)
    if a11y:
        signals["accessibility_conformance"] = a11y

    tokens_format = first_match(_TOKEN_FORMAT_PATTERNS)
    if tokens_format:
        signals["tokens_format"] = tokens_format

    frameworks = [label for pattern, label in _FRAMEWORK_PATTERNS if pattern.search(combined)]
    if frameworks:
        signals["frameworks"] = frameworks

    if _DARK_MODE_PATTERN.search(combined):
        signals["dark_mode"] = True
    if _MULTI_BRAND_PATTERN.search(combined):
        signals["multi_brand"] = True
    if _GOVERNANCE_PATTERN.search(combined):
        signals["governance_model"] = True
    if _DEPRECATION_POLICY_PATTERN.search(combined):
        signals["deprecation_policy"] = True
    if _CLI_SCAFFOLDING_PATTERN.search(combined):
        signals["cli_scaffolding"] = True
    if _CODEMOD_PATTERN.search(combined):
        signals["codemods"] = True
    if _FIGMA_CODE_CONNECT_PATTERN.search(combined):
        signals["figma_code_connect"] = True
    if _PROMPT_LIBRARY_PATTERN.search(combined):
        signals["prompt_library"] = True
    if _VALIDATION_LOOP_PATTERN.search(combined):
        signals["validation_loop"] = True
    if _PROHIBITION_PATTERN.search(combined):
        signals["prohibition"] = True
    if _TOOL_GATING_PATTERN.search(combined):
        signals["tool_gating"] = True
    if _TOKEN_ENFORCEMENT_PATTERN.search(combined):
        signals["token_enforcement"] = True
    if _EXEMPLARS_PATTERN.search(combined):
        signals["exemplars"] = True

    return signals
