import json
import logging
from openai import OpenAI

import config
from app import database
from app.cost_tracker import track_usage

log = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def categorize(text):
    """Takes natural language input, returns categorization with 1-2 projects."""
    raw_projects = [dict(p) for p in database.get_projects()]
    raw_tags = [dict(t) for t in database.get_tags()]
    projects = [p["name"] for p in raw_projects]
    tags = [t["name"] for t in raw_tags]

    # Build project/tag context with descriptions for better AI accuracy
    proj_context = []
    for p in raw_projects:
        desc = p.get("description") or ""
        entry = p["name"] + (f" ({desc})" if desc else "")
        proj_context.append(entry)

    tag_context = []
    for t in raw_tags:
        desc = t.get("description") or ""
        entry = t["name"] + (f" ({desc})" if desc else "")
        tag_context.append(entry)

    # Split into a focused user message with the task, and system for rules
    system_prompt = (
        "You are a time tracking categorizer. Return ONLY valid JSON.\n"
        "Output format: {\"projects\": [1-2 names], \"tags\": [1-3 names], "
        "\"description\": \"cleaned up text\", \"multitask_warning\": false, "
        "\"new_project\": null, \"new_tags\": []}\n"
        "Rules:\n"
        "- Match the input against project/tag DESCRIPTIONS below. If a keyword from the input "
        "appears in a project description, that project is the match.\n"
        "- Use 'Without task' ONLY if zero project descriptions match.\n"
        "- Use 2 projects only for clear multitasking. Set multitask_warning=true if 3+ match.\n"
        "- Never use 'Break' as a project (that's handled separately).\n"
        "- new_project/new_tags: almost never needed. null/[] by default."
    )

    user_msg = (
        f"PROJECTS:\n"
        + "\n".join(f"- {p}" for p in proj_context if not p.startswith("Without task") and not p.startswith("Break"))
        + f"\n\nTAGS:\n"
        + "\n".join(f"- {t}" for t in tag_context)
        + f"\n\nCategorize this: \"{text}\""
    )

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            max_completion_tokens=2048,
        )

        usage = response.usage
        track_usage(
            model=config.OPENAI_MODEL,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
        )

        content = (response.choices[0].message.content or "").strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        result = json.loads(content)

        # Handle new project creation
        new_project = result.get("new_project")
        if new_project and isinstance(new_project, str) and new_project.strip():
            new_project = new_project.strip()
            database.add_project(new_project)
            projects.append(new_project)
        else:
            new_project = None

        # Normalize projects field
        ai_projects = result.get("projects", [])
        if not ai_projects:
            # Backward compat: single "project" field
            ai_projects = [result.get("project", "Without task")]
        # Validate and cap at 2
        ai_projects = [p for p in ai_projects if p in projects][:2]
        if not ai_projects:
            ai_projects = ["Without task"]

        # Handle new tags
        new_tags = result.get("new_tags", [])
        if isinstance(new_tags, list):
            for nt in new_tags:
                if isinstance(nt, str) and nt.strip() and nt.strip() not in tags:
                    database.add_tag(nt.strip())
                    tags.append(nt.strip())
        else:
            new_tags = []

        # Validate tags
        valid_tags = [t for t in result.get("tags", []) if t in tags]
        for nt in (new_tags or []):
            if isinstance(nt, str) and nt.strip() in tags and nt.strip() not in valid_tags:
                valid_tags.append(nt.strip())

        return {
            "project": ai_projects[0],
            "projects": ai_projects,
            "tags": valid_tags,
            "description": result.get("description", text),
            "multitask_warning": bool(result.get("multitask_warning", False)),
            "new_project": new_project,
            "new_tags": [t for t in (new_tags or []) if isinstance(t, str) and t.strip()],
        }

    except Exception as e:
        log.error("Categorization failed: %s", e)
        return {
            "project": "Without task",
            "projects": ["Without task"],
            "tags": [],
            "description": text,
            "multitask_warning": False,
            "new_project": None,
            "new_tags": [],
        }
