"""LLM prompt builders for AI-driven card authoring.

Single source of truth for the prompts consumed by ``CardAuthoringService``
(suggest card, generate name, reciprocal-relationship sync). Kept here — beside
``prompt_guidance.py`` — so the prompt text is diffable and testable apart from
the service orchestration (Phase 6b decision R2).

Each builder returns the exact string the service previously inlined; behaviour
is byte-for-byte preserved.
"""

from __future__ import annotations


def build_suggest_card_prompt(
    *,
    entity_name: str,
    card_type: str,
    template_frontmatter: str,
    template_body: str,
    related_cards_section: str,
    evidence: str,
    additional_context: str | None,
) -> str:
    """Prompt for drafting a full story card from narrative evidence."""
    additional = f"Additional context: {additional_context}" if additional_context else ""
    return f"""Create a story card for the entity "{entity_name}" (type: {card_type}).

The card MUST start with exactly ONE YAML frontmatter block (between --- delimiters) containing these fields:

{template_frontmatter}

Then use this body structure as a guide (only include sections where evidence supports them):
{template_body}

{related_cards_section}

Based on these narrative scenes where the entity appears:
{evidence}

{additional}

Instructions:
- Return ONLY the complete markdown card — one frontmatter block (between --- delimiters) followed by the body.
- Do NOT include two frontmatter blocks. There must be exactly one opening --- and one closing ---.
- The frontmatter MUST include all required fields (type, card_id, name, rp, triggers).
- All card_id references (in connected_locations, known_by, etc.) MUST use the proper type prefix (loc_, npc_, char_, item_, org_, etc.).
- Only populate body sections where the evidence supports it. Omit sections you have no information for rather than leaving them as empty placeholders.
- Keep the same heading structure (## and ###) as the template for sections you do include.
- You MUST be consistent with the related cards above. Do not invent facts that contradict established lore."""


def build_generate_name_prompt(
    *,
    count: int,
    card_type: str,
    tone_context: str,
    hints: str | None,
    existing_list: list[str],
) -> str:
    """Prompt for suggesting distinct names for a new card."""
    setting_line = f"Setting/tone context: {tone_context}" if tone_context else ""
    hints_line = f"Hints from the user: {hints}" if hints else ""
    existing_line = (
        f"Existing {card_type} names (avoid duplicates): {', '.join(existing_list)}"
        if existing_list
        else ""
    )
    return f"""Generate {count} creative name suggestions for a new "{card_type}" story card.

{setting_line}
{hints_line}
{existing_line}

Return a JSON object with a single key "names" containing an array of {count} name strings.
Names should be evocative, fit the card type, and be distinct from existing names.
Return ONLY the JSON object, no other text."""


def build_reciprocal_relationship_prompt(
    *,
    new_card_name: str,
    new_card_id: str,
    new_card_summary: str,
    new_card_rel: dict,
    target_name: str,
    target_summary: str,
) -> str:
    """Prompt for generating the inverse relationship entry on a target card."""
    return f"""A new character card was just created. Generate the RECIPROCAL relationship entry that should be added to an existing character's card.

NEW CHARACTER (just created):
{new_card_summary}

NEW CHARACTER'S RELATIONSHIP TO EXISTING CHARACTER:
- target: {new_card_rel.get("target", "")}
- role: {new_card_rel.get("role", "")}
- trust: {new_card_rel.get("trust", 0)}
- status: "{new_card_rel.get("status", "")}"

EXISTING CHARACTER (needs a reciprocal entry):
{target_summary}

Generate the reciprocal relationship entry FROM {target_name}'s perspective TOWARD {new_card_name}.
The trust score should reflect {target_name}'s feelings toward {new_card_name} (may differ from the reverse).
The role should be the inverse (e.g., if new char sees target as "older brother", target sees new char as "younger sister").

Also check: does the new card's content reveal anything that {target_name} DOESN'T KNOW about?
If so, include a "doesnt_know" array of brief descriptions.

Return ONLY a JSON object with these keys:
{{"target": "{new_card_id}", "role": "<inverse role>", "trust": <integer -50 to 50>, "status": "<brief current state from {target_name}'s perspective>", "doesnt_know": ["<thing {target_name} doesn't know>"] }}

If there are no knowledge gaps, set "doesnt_know" to an empty array [].
Return ONLY the JSON, no other text."""
