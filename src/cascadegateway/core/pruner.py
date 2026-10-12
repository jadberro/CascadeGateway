"""
CascadeGateway Universal Context Pruner
High-performance token compression middleware:
- Clamps verbose terminal outputs & compiler logs (>60 lines -> 45 lines)
- Deduplicates repetitive identical file buffers across conversational history
- Computes exact before/after token savings metrics
"""

import copy
import hashlib
import re
from typing import Any, Dict, List, Tuple

try:
    import tiktoken
    _TOKENIZER = tiktoken.get_encoding("cl100k_base")
except Exception:
    _TOKENIZER = None


def estimate_tokens(text: str) -> int:
    """Estimates tokens using tiktoken (cl100k_base) if available, falling back to 4 chars/token."""
    if not text:
        return 0
    if _TOKENIZER is not None:
        try:
            return len(_TOKENIZER.encode(text, disallowed_special=()))
        except Exception:
            pass
    return max(1, len(text) // 4)


def clamp_terminal_output(
    text: str,
    max_lines: int = 60,
    head_lines: int = 15,
    tail_lines: int = 30
) -> Tuple[str, int]:
    """
    Clamps terminal output, test results, or compiler logs exceeding max_lines.
    Preserves the first head_lines and last tail_lines while inserting a clean marker.
    """
    if not text or not isinstance(text, str):
        return text, 0

    lines = text.splitlines()
    total_lines = len(lines)
    if total_lines <= max_lines:
        return text, 0

    head = lines[:head_lines]
    tail = lines[-tail_lines:]
    omitted_count = total_lines - head_lines - tail_lines

    clamped_text = (
        "\n".join(head)
        + f"\n[... clipped by CascadeGateway: {omitted_count} lines omitted ...]\n"
        + "\n".join(tail)
    )
    return clamped_text, omitted_count


def fingerprint_content(text: str) -> str:
    """Returns an MD5 hex digest of normalized whitespace text."""
    normalized = re.sub(r"\s+", " ", text.strip())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def extract_code_blocks(text: str) -> List[Tuple[str, int, int]]:
    """Finds fenced code blocks (```...```) with at least 8 lines."""
    blocks = []
    pattern = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
    for match in pattern.finditer(text):
        content = match.group(1)
        if len(content.splitlines()) >= 8:
            blocks.append((content, match.start(), match.end()))
    return blocks


def deduplicate_file_buffers(
    messages: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Deduplicates repeated large file buffers (>8 lines) or fenced code blocks across conversational history.
    Replaces repeats with lightweight pointers referencing the original turn.
    """
    seen_fingerprints: Dict[str, int] = {}
    pruned_messages: List[Dict[str, Any]] = []
    dedup_count = 0

    for i, orig_msg in enumerate(messages):
        msg = dict(orig_msg)
        content = msg.get("content", "")

        if isinstance(content, str):
            # Check 1: Fenced code blocks inside message
            code_blocks = extract_code_blocks(content)
            if code_blocks:
                new_content = content
                # Replace from back to preserve indices
                for block_content, start, end in reversed(code_blocks):
                    fp = fingerprint_content(block_content)
                    if fp in seen_fingerprints:
                        first_turn = seen_fingerprints[fp]
                        replacement = f"```\n[File content identical to turn {first_turn}; omitted for token efficiency]\n```"
                        new_content = new_content[:start] + replacement + new_content[end:]
                        dedup_count += 1
                    else:
                        seen_fingerprints[fp] = i
                msg["content"] = new_content
            else:
                # Check 2: Raw multi-line buffer
                lines = content.splitlines()
                if len(lines) > 8:
                    fp = fingerprint_content(content)
                    if fp in seen_fingerprints:
                        first_turn = seen_fingerprints[fp]
                        msg["content"] = (
                            f"[File content identical to turn {first_turn}; omitted for token efficiency]"
                        )
                        dedup_count += 1
                    else:
                        seen_fingerprints[fp] = i
        elif isinstance(content, list):
            new_parts = []
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    part_text = part.get("text", "")
                    code_blocks = extract_code_blocks(part_text)
                    if code_blocks:
                        new_part_text = part_text
                        for block_content, start, end in reversed(code_blocks):
                            fp = fingerprint_content(block_content)
                            if fp in seen_fingerprints:
                                first_turn = seen_fingerprints[fp]
                                replacement = f"```\n[File content identical to turn {first_turn}; omitted for token efficiency]\n```"
                                new_part_text = new_part_text[:start] + replacement + new_part_text[end:]
                                dedup_count += 1
                            else:
                                seen_fingerprints[fp] = i
                        new_parts.append({**part, "text": new_part_text})
                        continue
                    lines = part_text.splitlines()
                    if len(lines) > 8:
                        fp = fingerprint_content(part_text)
                        if fp in seen_fingerprints:
                            first_turn = seen_fingerprints[fp]
                            new_parts.append({
                                **part,
                                "text": f"[File content identical to turn {first_turn}; omitted for token efficiency]"
                            })
                            dedup_count += 1
                            continue
                        else:
                            seen_fingerprints[fp] = i
                new_parts.append(part)
            msg["content"] = new_parts

        pruned_messages.append(msg)

    return pruned_messages, dedup_count


def prune_messages(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Executes full multi-stage context pruning:
    1. Clamps terminal and long log blocks in user / tool turns.
    2. Deduplicates repeated file buffers across conversation history.
    3. Calculates raw vs pruned token metrics.
    """
    if not messages:
        return {
            "pruned_messages": [],
            "raw_tokens": 0,
            "pruned_tokens": 0,
            "tokens_saved": 0,
            "clamped_lines": 0,
            "dedup_files": 0
        }

    # Stage 1: Calculate raw tokens & Deduplicate identical file/code buffers across turns
    raw_tokens = 0
    for msg in messages:
        c = msg.get("content", "")
        if isinstance(c, str):
            raw_tokens += estimate_tokens(c)
        elif isinstance(c, list):
            for p in c:
                if isinstance(p, dict) and "text" in p:
                    raw_tokens += estimate_tokens(p.get("text", ""))

    deduped_messages, dedup_files = deduplicate_file_buffers(messages)

    # Stage 2: Clamp terminal outputs (>60 lines)
    clamped_messages: List[Dict[str, Any]] = []
    total_clamped_lines = 0

    for msg in deduped_messages:
        msg_copy = copy.deepcopy(msg)
        content = msg_copy.get("content", "")

        if isinstance(content, str):
            clamped_content, omitted = clamp_terminal_output(content)
            total_clamped_lines += omitted
            msg_copy["content"] = clamped_content
        elif isinstance(content, list):
            new_parts = []
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    txt = part.get("text", "")
                    clamped_txt, omitted = clamp_terminal_output(txt)
                    total_clamped_lines += omitted
                    new_parts.append({**part, "text": clamped_txt})
                else:
                    new_parts.append(part)
            msg_copy["content"] = new_parts

        clamped_messages.append(msg_copy)

    pruned_messages = clamped_messages

    # Compute final pruned tokens
    pruned_tokens = 0
    for msg in pruned_messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            pruned_tokens += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    pruned_tokens += estimate_tokens(part.get("text", ""))

    tokens_saved = max(0, raw_tokens - pruned_tokens)

    return {
        "pruned_messages": pruned_messages,
        "raw_tokens": raw_tokens,
        "pruned_tokens": pruned_tokens,
        "tokens_saved": tokens_saved,
        "clamped_lines": total_clamped_lines,
        "dedup_files": dedup_files
    }
