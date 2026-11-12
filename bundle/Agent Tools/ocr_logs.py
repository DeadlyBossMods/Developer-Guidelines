#!/usr/bin/env python3
"""
ocr_logs.py — OCR all DBM log pull folders under known log roots and cache results.

For each folder containing screenshot images (PNG/JPG/JPEG), produces a "log.txt" file with
only the relevant DBM lines (ENCOUNTER_TIMELINE_EVENT_ADDED,
ENCOUNTER_TIMELINE_EVENT_STATE_CHANGED, ENCOUNTER_WARNING including severity,
DBM_SetStage, ENCOUNTER_START, and UTC/UF/IEEU debug evidence for deterministic
phase changes and warning disambiguation).
Folders with an existing log.txt are skipped unless --force is passed.

Usage:
    python3 "Agent Tools/ocr_logs.py"           # process new folders only
    python3 "Agent Tools/ocr_logs.py" --force   # re-OCR everything (overwrites log.txt)
    python3 "Agent Tools/ocr_logs.py" --folder "/path/to/pull" [more pull folders] [--force]
    python3 "Agent Tools/ocr_logs.py" --verify  # print a summary of all cached logs
    python3 "Agent Tools/ocr_logs.py" --normalize [--dry-run]  # post-review fixups for cached log.txt
    python3 "Agent Tools/ocr_logs.py" --audit-ocr  # refresh per-folder OCR issue reports
    python3 "Agent Tools/ocr_logs.py" --qa      # run enhanced QA checks against cached logs
"""

import subprocess
import os
import glob
import sys
import re
import math
import argparse
from collections import Counter, defaultdict
from dbm_binary_font import BinaryDecodeError, decode_image

LOG_ROOTS = (
    "12.0 Logs",
    "12.1 Logs",
)

RELEVANT_PATTERNS = (
    "ENCOUNTER_TIMELINE_EVENT_ADDED",
    "ENCOUNTER_TIMELINE_EVENT_STATE_CHANGED",
    "ENCOUNTER_WARNING",
    "DBM_SetStage",
    "ENCOUNTER_START",
    "Starting hardcoded timer for eventID",
    "Hardcoded timer terminated for eventID",
    "refreshed before expired",
    "UTC fired for",
    "UF fired for",
    "IEEU fired for",
)

LOG_FILENAME = "log.txt"
OCR_ISSUES_FILENAME = "ocr_issues.txt"
LOG_VARIANT_FILENAMES = ("log1.txt", "log2.txt", "log3.txt")
BINARY_CACHE_MARKER = "# DBM capture: binary-v1"
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")

# Strict 3-pass OCR consensus (persisted as log1/log2/log3).
# Note: pass quality can differ by psm; all three are still fresh OCR runs.
OCR_PASSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (LOG_VARIANT_FILENAMES[0], ("--psm", "6", "-l", "eng")),
    (LOG_VARIANT_FILENAMES[1], ("--psm", "4", "-l", "eng")),
    (LOG_VARIANT_FILENAMES[2], ("--psm", "3", "-l", "eng")),
)

NORMALIZED_PATTERNS = tuple(re.sub(r"[^A-Z0-9]", "", p.upper()) for p in RELEVANT_PATTERNS)

# Canonical spell-name overrides for IDs that are known/verified from module data.
# These are applied conservatively in post-processing for parsed timeline-added lines.
KNOWN_SPELL_ID_BY_CANON = {
    "defilementofthecrucible": "1298381",
}


def is_relevant_line(line: str) -> bool:
    line_without_color_markup = re.sub(r"\|c[0-9A-F]{8}|\|r", "", line, flags=re.IGNORECASE)
    normalized = re.sub(r"[^A-Z0-9]", "", line_without_color_markup.upper())
    return any(p in normalized for p in NORMALIZED_PATTERNS)


def normalize_ocr_line(line: str) -> str:
    """Normalize common OCR garbles in relevant DBM lines.

    Primary fix: spell ID prefixes where leading '1' is misread as '{', '(', '|', or 'l',
    e.g. "spellID: {255738" -> "spellID: 1255738".
    """
    # Normalize leading OCR junk before timestamps by stripping it.
    # We intentionally avoid force-prepending '1' here because it can overcorrect
    # clean values like '62.84' into '162.84'. Missing/extra digits are handled
    # later by neighbor-aware timestamp repair.
    line = re.sub(r"^(\s*)[^\d\s]+(?=\d)", r"\g<1>", line)

    # Normalize OCR timestamp separators like '4/57:' or '316,04:' -> '4.57:' / '316.04:'.
    line = re.sub(r"^(\s*\d+)[/,](\d+\s*[:;])", r"\1.\2", line)

    # Normalize split integer-part timestamp artifacts like '1.47.86:' -> '147.86:'.
    line = re.sub(r"^(\s*)(\d)\.(\d{2,3})\.(\d+\s*[:;])", r"\1\2\3.\4", line)

    # Normalize known typo variants in timer rounding label.
    line = re.sub(r"\b(?:Rounc(?:ed|ied)|Rounde[sa])\b", "Rounded", line, flags=re.IGNORECASE)
    line = re.sub(r"\(Rounded\s+([^\)]+)\)", r"(Rounded: \1)", line, flags=re.IGNORECASE)

    # Normalize malformed encounter timeline event headers to canonical tokens.
    line = re.sub(
        r"\bENCOUNTER[\W_]*TIMELINE[\W_]*EVENT[\W_]*ADDED\b",
        "ENCOUNTER_TIMELINE_EVENT_ADDED",
        line,
        flags=re.IGNORECASE,
    )
    line = re.sub(
        r"\bENCOUNTER[\W_]*TIMELINE[\W_]*EVENT[\W_]*STATE[\W_]*CHANGED\b",
        "ENCOUNTER_TIMELINE_EVENT_STATE_CHANGED",
        line,
        flags=re.IGNORECASE,
    )
    # Handle missing "STATE" OCR dropouts where intent is still unambiguous.
    line = re.sub(
        r"\bENCOUNTER[\W_]*TIMELINE[\W_]*EVENT[\W_]*CHANGED\b",
        "ENCOUNTER_TIMELINE_EVENT_STATE_CHANGED",
        line,
        flags=re.IGNORECASE,
    )

    # Normalize malformed event/field tokens.
    line = re.sub(r"\bevent[lI]D\b", "eventID", line, flags=re.IGNORECASE)
    line = re.sub(r"\btimer(?:lID|[lI]D)\b", "timerID", line, flags=re.IGNORECASE)
    line = re.sub(r"\bsp[eo][lI]{1,4}Name\b", "spellName", line, flags=re.IGNORECASE)

    # Normalize OCR letter/number confusion in state values.
    # A lone pipe is the recurring misread of the glyph for state 1.
    line = re.sub(r"(\bstate\s*:\s*)[oO]\b", r"\g<1>0", line, flags=re.IGNORECASE)
    line = re.sub(r"(\bstate\s*:\s*)\|\s*$", r"\g<1>1", line, flags=re.IGNORECASE)

    # Normalize malformed spellID field token (e.g. spelllD/spelD -> spellID).
    line = re.sub(r"\bsp[eo][lI]{1,4}D\b", "spellID", line, flags=re.IGNORECASE)

    # Focused to spell id-like fields to avoid collateral edits to other values.
    line = re.sub(r"(spell[^:]{0,8}:\s*)[\{\|\(lI]\s*(?=2)", r"\g<1>1", line, flags=re.IGNORECASE)

    # Normalize occasional leading garbage before numeric durations.
    line = re.sub(r"(duration:\s*)[\|\{\(]\s*(?=\d)", r"\g<1>", line, flags=re.IGNORECASE)

    # Normalize comma decimal separator in durations (e.g. 44,064 -> 44.064).
    line = re.sub(r"(duration:\s*\d+),(\d+)", r"\1.\2", line, flags=re.IGNORECASE)

    # Normalize noisy cast-count suffixes (e.g. '(1!)' -> '(1)').
    line = re.sub(r"\((\d+)!\)", r"(\1)", line)
    return line


def _parse_timestamp_prefix(line: str) -> tuple[str, str, str] | None:
    """Parse leading timestamp token, e.g. '241.06:' or '241.06;' from a log line."""
    m = re.match(r"^(\s*)(\d+\.\d+)(\s*[:;])", line)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def _repair_timestamp_anomalies(lines: list[str]) -> tuple[list[str], int]:
    """Repair obvious OCR timestamp anomalies using immediate neighbors.

    Handles:
      - duplicated digit spikes (e.g. 2441.06 -> 241.06),
      - missing digit dips (e.g. 16.23 -> 168.23),
      - nearby digit confusions (e.g. 165.23 -> 168.23)

    It only acts when previous/next timestamps form a tight local context and the
    current value is a strong outlier from that context.
    """
    repaired = list(lines)
    replacements = 0

    # Guardrails to avoid touching normal timeline progression.
    neighbor_window = 8.0
    outlier_threshold = 2.5
    fit_tolerance = 1.5

    def _timestamp_candidates(int_part: str, frac_part: str) -> set[float]:
        candidates: set[float] = set()

        # 1) Drop one digit (handles duplicated digits like 2441 -> 241).
        if len(int_part) >= 3:
            for drop_idx in range(len(int_part)):
                cand_int = int_part[:drop_idx] + int_part[drop_idx + 1 :]
                if cand_int:
                    candidates.add(float(f"{cand_int}.{frac_part}"))

        # 2) Insert one digit (handles missing digit like 16 -> 168).
        for pos in range(len(int_part) + 1):
            for d in "0123456789":
                cand_int = int_part[:pos] + d + int_part[pos:]
                # Avoid pointless leading-zero variants unless original had them.
                if len(cand_int) > 1 and cand_int.startswith("0") and not int_part.startswith("0"):
                    continue
                candidates.add(float(f"{cand_int}.{frac_part}"))

        # 3) Substitute one digit (handles 5/8-like confusions: 165 -> 168).
        for pos, original in enumerate(int_part):
            for d in "0123456789":
                if d == original:
                    continue
                cand_int = int_part[:pos] + d + int_part[pos + 1 :]
                candidates.add(float(f"{cand_int}.{frac_part}"))

        return candidates

    for idx in range(1, len(repaired) - 1):
        prev_parts = _parse_timestamp_prefix(repaired[idx - 1])
        cur_parts = _parse_timestamp_prefix(repaired[idx])
        next_parts = _parse_timestamp_prefix(repaired[idx + 1])
        if not prev_parts or not cur_parts or not next_parts:
            continue

        prev_ts = float(prev_parts[1])
        cur_ts = float(cur_parts[1])
        next_ts = float(next_parts[1])
        local_min = min(prev_ts, next_ts)
        local_max = max(prev_ts, next_ts)
        midpoint = (prev_ts + next_ts) / 2.0

        # Only repair when neighbors are tightly clustered and current is a strong outlier.
        if abs(prev_ts - next_ts) > neighbor_window:
            continue
        if abs(cur_ts - midpoint) < outlier_threshold:
            continue

        # If already plausibly inside neighbor band, leave it alone.
        if (local_min - fit_tolerance) <= cur_ts <= (local_max + fit_tolerance):
            continue

        int_part, frac_part = cur_parts[1].split(".", 1)
        candidates = [
            c
            for c in _timestamp_candidates(int_part, frac_part)
            if (local_min - fit_tolerance) <= c <= (local_max + fit_tolerance)
        ]

        if not candidates:
            continue

        prev_2 = round(prev_ts, 2)
        next_2 = round(next_ts, 2)
        preferred = [c for c in candidates if round(c, 2) in {prev_2, next_2}]
        pool = preferred if preferred else candidates
        chosen = min(pool, key=lambda v: abs(v - midpoint))
        prefix, _, sep = cur_parts
        repaired[idx] = re.sub(
            r"^(\s*)\d+\.\d+(\s*[:;])",
            f"{prefix}{chosen:.2f}{sep}",
            repaired[idx],
            count=1,
        )
        replacements += 1

    return repaired, replacements


def _is_numeric_token(token: str) -> bool:
    return re.fullmatch(r"\d+(?:\.\d+)?", token) is not None


def _edit_distance_with_limit(a: str, b: str, max_edits: int) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > max_edits:
        return False
    i = j = 0
    edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > max_edits:
            return False
        if len(a) == len(b):
            i += 1
            j += 1
        elif len(a) > len(b):
            i += 1
        else:
            j += 1
    if i < len(a) or j < len(b):
        edits += 1
    return edits <= max_edits


def _edit_distance_leq_one(a: str, b: str) -> bool:
    return _edit_distance_with_limit(a, b, 1)


def _repair_spell_ids_by_name(lines: list[str]) -> tuple[list[str], int]:
    repaired = list(lines)
    replacements = 0
    counts_by_name: dict[str, Counter[str]] = defaultdict(Counter)

    parse_re = re.compile(
        r"spellID[:;]\s*[^\d]{0,4}(?P<spell_id>\d{4,8})[^\d]{0,3}.*?spellName:\s*(?P<spell_name>.*?)\s+and\s+duration:",
        re.IGNORECASE,
    )

    for line in repaired:
        m = parse_re.search(line)
        if not m:
            continue
        canon = _canonical_spell_name(m.group("spell_name"))
        counts_by_name[canon][m.group("spell_id")] += 1

    dominant_id_by_name: dict[str, str] = {}
    dominant_freq_by_name: dict[str, int] = {}
    for canon, cnt in counts_by_name.items():
        spell_id, freq = cnt.most_common(1)[0]
        if freq >= 2:
            dominant_id_by_name[canon] = spell_id
            dominant_freq_by_name[canon] = freq

    preferred_7digit_by_name: dict[str, str] = {}
    for canon, cnt in counts_by_name.items():
        seven_digit_ids = [sid for sid in cnt if sid.isdigit() and len(sid) == 7]
        if not seven_digit_ids:
            continue
        seven_digit_ids_sorted = sorted(seven_digit_ids)
        preferred_7digit_by_name[canon] = max(
            seven_digit_ids_sorted,
            key=lambda sid: (cnt[sid], sid),
        )

    for idx, line in enumerate(repaired):
        m = parse_re.search(line)
        if not m:
            continue
        canon = _canonical_spell_name(m.group("spell_name"))
        current_id = m.group("spell_id")

        known_id = KNOWN_SPELL_ID_BY_CANON.get(canon)
        if known_id and current_id != known_id:
            repaired[idx] = re.sub(
                r"(spellID[:;]\s*)\d+",
                rf"\g<1>{known_id}",
                line,
                count=1,
                flags=re.IGNORECASE,
            )
            replacements += 1
            continue

        dominant_id = dominant_id_by_name.get(canon)
        if not dominant_id or dominant_id == current_id:
            continue
        current_freq = counts_by_name[canon][current_id]
        dominant_freq = dominant_freq_by_name.get(canon, 0)
        # If both IDs are 7-digit, require stronger support before swapping.
        # This avoids flipping between plausible 7-digit OCR variants.
        if len(current_id) == 7 and len(dominant_id) == 7 and dominant_freq < current_freq + 3:
            continue
        if not _edit_distance_leq_one(current_id, dominant_id):
            continue
        repaired[idx] = re.sub(
            r"(spellID[:;]\s*)\d+",
            rf"\g<1>{dominant_id}",
            line,
            count=1,
            flags=re.IGNORECASE,
        )
        replacements += 1

    for idx, line in enumerate(repaired):
        m = parse_re.search(line)
        if not m:
            continue
        canon = _canonical_spell_name(m.group("spell_name"))
        current_id = m.group("spell_id")
        preferred_7 = preferred_7digit_by_name.get(canon)
        if not preferred_7 or current_id == preferred_7:
            continue
        # If a clean 7-digit spellID exists for the same spell name,
        # shorter numeric IDs are considered malformed OCR and upgraded.
        if len(current_id) >= 7:
            continue
        repaired[idx] = re.sub(
            r"(spellID[:;]\s*)\d+",
            rf"\g<1>{preferred_7}",
            line,
            count=1,
            flags=re.IGNORECASE,
        )
        replacements += 1

    return repaired, replacements


def _repair_spell_names_by_id(lines: list[str]) -> tuple[list[str], int]:
    """Normalize spellName typos using dominant spellName per spellID within a log."""
    repaired = list(lines)
    replacements = 0

    parse_re = re.compile(
        r"spellID[:;]\s*[^\d]{0,4}(?P<spell_id>\d{4,8})[^\d]{0,3}.*?spellName:\s*(?P<spell_name>.*?)\s+and\s+duration:",
        re.IGNORECASE,
    )

    counts_by_id: dict[str, Counter[str]] = defaultdict(Counter)
    display_name_by_canon: dict[tuple[str, str], str] = {}

    for line in repaired:
        m = parse_re.search(line)
        if not m:
            continue
        sid = m.group("spell_id")
        raw_name = m.group("spell_name").strip()
        canon = _canonical_spell_name(raw_name)
        if not canon:
            continue
        counts_by_id[sid][canon] += 1
        display_name_by_canon.setdefault((sid, canon), raw_name)

    dominant_name_by_id: dict[str, str] = {}
    dominant_canon_by_id: dict[str, str] = {}
    for sid, cnt in counts_by_id.items():
        ranked = cnt.most_common(2)
        canon, freq = ranked[0]
        second_freq = ranked[1][1] if len(ranked) > 1 else 0
        # Precision-first guardrails:
        # - at least 3 observations for the winner
        # - at least +2 margin over next candidate
        if freq >= 3 and (freq - second_freq) >= 2:
            dominant_name_by_id[sid] = display_name_by_canon[(sid, canon)]
            dominant_canon_by_id[sid] = canon

    for idx, line in enumerate(repaired):
        m = parse_re.search(line)
        if not m:
            continue
        sid = m.group("spell_id")
        current_name = m.group("spell_name").strip()
        dominant_name = dominant_name_by_id.get(sid)
        if not dominant_name:
            continue
        current_canon = _canonical_spell_name(current_name)
        dominant_canon = dominant_canon_by_id.get(sid, _canonical_spell_name(dominant_name))
        if current_canon == dominant_canon:
            continue
        # Only fix near-typo variants, not semantically different names that
        # happen to share an ID in noisy data.
        if not _edit_distance_with_limit(current_canon, dominant_canon, 2):
            continue

        dominant_base, dominant_count = _split_spell_name_count(dominant_name)
        _current_base, current_count = _split_spell_name_count(current_name)
        chosen_count = current_count or dominant_count
        if chosen_count:
            target_name = f"{dominant_base} ({chosen_count})"
        else:
            target_name = dominant_base

        repaired[idx] = re.sub(
            r"(spellName:\s*)(.*?)(\s+and\s+duration:)",
            rf"\g<1>{target_name}\g<3>",
            line,
            count=1,
            flags=re.IGNORECASE,
        )
        replacements += 1

    return repaired, replacements


def _repair_spell_id_token_artifacts(lines: list[str]) -> tuple[list[str], int]:
    """Normalize obvious OCR artifacts in spellID tokens before semantic repair.

    Examples:
      - spellID: 129071! -> spellID: 1290711
      - spellID; | 292779 -> spellID: 292779
      - spellID; 1292779 -> spellID: 1292779
    """
    repaired = list(lines)
    replacements = 0

    token_re = re.compile(
        r"(spellID)\s*[:;]\s*(?P<token>.*?)\s+(with\s+spellName:)",
        re.IGNORECASE,
    )

    for idx, line in enumerate(repaired):
        m = token_re.search(line)
        if not m:
            continue

        raw_token = m.group("token")
        token = raw_token.strip()
        # Obvious substitutions inside spellID token.
        token = token.replace("!", "1")
        token = token.replace("l", "1").replace("I", "1")

        # Preserve likely leading 1 when OCR reads it as a pipe, e.g. '| 292779'.
        token = re.sub(r"^\|(?=\s*\d{5,7}\b)", "1", token)

        # Remove separators/noise often introduced by OCR.
        token = token.replace("|", "").replace(" ", "")

        digits = re.sub(r"\D", "", token)
        if not digits:
            continue

        # Keep this pass conservative: only rewrite to pure numeric token.
        new_line = token_re.sub(rf"\g<1>: {digits} \g<3>", line, count=1)
        if new_line != line:
            repaired[idx] = new_line
            replacements += 1

    return repaired, replacements


def _repair_durations_by_spell(lines: list[str]) -> tuple[list[str], int]:
    repaired = list(lines)
    replacements = 0

    parse_re = re.compile(
        r"spellName:\s*(?P<spell_name>.*?)\s+and\s+duration:\s*(?P<duration>[^\s]+)\s+\(Rounded:\s*(?P<rounded>[^\)]+)\)",
        re.IGNORECASE,
    )

    known_duration_by_name: dict[str, set[float]] = defaultdict(set)
    for line in repaired:
        m = parse_re.search(line)
        if not m:
            continue
        duration_token = m.group("duration").replace(",", ".")
        if _is_numeric_token(duration_token):
            known_duration_by_name[_canonical_spell_name(m.group("spell_name"))].add(float(duration_token))

    for idx, line in enumerate(repaired):
        m = parse_re.search(line)
        if not m:
            continue

        spell_name_canon = _canonical_spell_name(m.group("spell_name"))
        duration_token = m.group("duration").replace(",", ".")
        rounded_token = m.group("rounded").replace(",", ".")

        duration_is_num = _is_numeric_token(duration_token)
        rounded_is_num = _is_numeric_token(rounded_token)
        if duration_is_num and rounded_is_num:
            continue

        known = known_duration_by_name.get(spell_name_canon, set())

        duration_candidates: list[float] = []
        rounded_candidates: list[int] = []

        if duration_is_num:
            duration_candidates = [float(duration_token)]
        else:
            if re.fullmatch(r"[^\d]?\d+(?:\.\d+)?", duration_token):
                base = duration_token
                for d in "0123456789":
                    cand = re.sub(r"[^\d.]", d, base)
                    if _is_numeric_token(cand):
                        duration_candidates.append(float(cand))
            elif re.fullmatch(r"[^\d]", duration_token):
                for d in "0123456789":
                    duration_candidates.append(float(d))

        if rounded_is_num:
            rounded_candidates = [int(float(rounded_token))]
        else:
            if re.fullmatch(r"[^\d]?\d+", rounded_token):
                base = rounded_token
                for d in "0123456789":
                    cand = re.sub(r"[^\d]", d, base)
                    if re.fullmatch(r"\d+", cand):
                        rounded_candidates.append(int(cand))

        best_pair: tuple[float, int] | None = None
        best_score = -10**9

        if not duration_candidates and rounded_candidates:
            # Can't infer duration reliably from rounded-only without candidates.
            continue

        if duration_candidates and not rounded_candidates:
            rounded_candidates = [int(round(v)) for v in duration_candidates]

        for dv in duration_candidates:
            for rv in rounded_candidates:
                if int(round(dv)) != rv:
                    continue
                score = 0
                if known:
                    if dv in known:
                        score += 50
                    else:
                        score -= int(min(abs(dv - k) for k in known) * 10)
                # Prefer common timeline durations (helps when known set is sparse).
                if dv in {4.0, 6.0, 8.0, 12.0, 16.0, 20.0, 21.5, 32.0, 40.0, 41.0, 51.5, 52.0, 540.0}:
                    score += 5
                score -= int(abs(dv - rv) * 2)
                if score > best_score:
                    best_score = score
                    best_pair = (dv, rv)

        if best_pair is None:
            continue

        new_duration, new_rounded = best_pair
        if float(new_duration).is_integer():
            duration_str = str(int(new_duration))
        else:
            duration_str = f"{new_duration:.1f}"
        rounded_str = str(int(new_rounded))

        new_line = re.sub(
            r"(duration:\s*)[^\s]+(\s+\(Rounded:\s*)[^\)]+(\))",
            rf"\g<1>{duration_str}\g<2>{rounded_str}\g<3>",
            line,
            count=1,
            flags=re.IGNORECASE,
        )
        if new_line != line:
            repaired[idx] = new_line
            replacements += 1

    return repaired, replacements


def _repair_bootstrap_timestamps(lines: list[str]) -> tuple[list[str], int]:
    """Fix common OCR prefix error where initial 00.xx lines become 10.xx."""
    repaired = list(lines)
    replacements = 0

    first_ts_idx = next((i for i, line in enumerate(repaired) if _parse_timestamp_prefix(line)), None)
    if first_ts_idx is None:
        return repaired, 0

    block_indices: list[int] = []
    idx = first_ts_idx
    while idx < len(repaired):
        parts = _parse_timestamp_prefix(repaired[idx])
        if not parts:
            break
        ts = float(parts[1])
        if not (10.0 <= ts < 11.0):
            break
        if "ENCOUNTER_TIMELINE_EVENT_ADDED" not in repaired[idx] or "state: 0" not in repaired[idx]:
            break
        block_indices.append(idx)
        idx += 1

    if len(block_indices) < 4:
        return repaired, 0

    next_parts = _parse_timestamp_prefix(repaired[idx]) if idx < len(repaired) else None
    if not next_parts:
        return repaired, 0
    next_ts = float(next_parts[1])
    if not (0.0 <= next_ts < 10.0):
        return repaired, 0

    for bidx in block_indices:
        pfx, ts_str, sep = _parse_timestamp_prefix(repaired[bidx]) or ("", "", ":")
        old_ts = float(ts_str)
        new_ts = max(0.0, old_ts - 10.0)
        repaired[bidx] = re.sub(
            r"^(\s*)\d+\.\d+(\s*[:;])",
            f"{pfx}{new_ts:05.2f}{sep}",
            repaired[bidx],
            count=1,
        )
        replacements += 1

    return repaired, replacements


def _repair_timestamp_prefix_outliers(lines: list[str]) -> tuple[list[str], int]:
    """Repair clustered leading-digit OCR mutations in timestamps.

    Targets patterns like:
      58.xx, 162.xx, 165.xx, 166.xx, 466.xx, 71.xx
    where removing one leading integer digit yields a locally coherent sequence.
    """
    repaired = list(lines)
    replacements = 0

    lookahead = 8
    drop_threshold = 70.0

    for idx in range(1, len(repaired) - 1):
        cur_parts = _parse_timestamp_prefix(repaired[idx])
        if not cur_parts:
            continue
        prev_parts = _parse_timestamp_prefix(repaired[idx - 1])
        if not prev_parts:
            continue

        cur_ts = float(cur_parts[1])
        prev_ts = float(prev_parts[1])

        int_part, frac_part = cur_parts[1].split(".", 1)
        if len(int_part) < 3:
            continue

        # Find nearest future timestamps in a small window.
        future_vals: list[float] = []
        for j in range(idx + 1, min(len(repaired), idx + 1 + lookahead)):
            p = _parse_timestamp_prefix(repaired[j])
            if p:
                future_vals.append(float(p[1]))
        if not future_vals:
            continue
        future_min = min(future_vals)

        # Only consider cases where current value is implausibly high vs nearby future values.
        if cur_ts - future_min < drop_threshold:
            continue

        best: float | None = None
        best_score = float("inf")

        for drop_idx in range(len(int_part)):
            cand_int = int_part[:drop_idx] + int_part[drop_idx + 1 :]
            if not cand_int:
                continue
            cand_ts = float(f"{cand_int}.{frac_part}")

            # Candidate should be plausible in local progression.
            if cand_ts < prev_ts - 2.0:
                continue
            if cand_ts > future_min + 25.0:
                continue

            score = abs(cand_ts - prev_ts) + abs(cand_ts - future_min)
            if score < best_score:
                best_score = score
                best = cand_ts

        if best is None:
            continue

        prefix, _, sep = cur_parts
        repaired[idx] = re.sub(
            r"^(\s*)\d+\.\d+(\s*[:;])",
            f"{prefix}{best:.2f}{sep}",
            repaired[idx],
            count=1,
        )
        replacements += 1

    return repaired, replacements


def _repair_missing_timestamp_prefixes(lines: list[str]) -> tuple[list[str], int]:
    """Restore timestamp prefixes on relevant OCR lines that lost them.

    Typical OCR corruption patterns:
      - ': ENCOUNTER_TIMELINE_EVENT_ADDED ...'
      - "': ENCOUNTER_TIMELINE_EVENT_ADDED ..."
      - ': Starting hardcoded timer for eventID ...'

    Strategy: restore a timestamp only when both immediate timestamp neighbors
    agree that the row belongs to the same batch. A missing timestamp is safer
    to leave for audit than to assign from an unrelated earlier event.
    """
    repaired = list(lines)
    replacements = 0

    missing_prefix_re = re.compile(
        r"^\s*['`\"”]?\s*(?:[:;]\s*)?(?P<body>(?:ENCOUNTER_TIMELINE_EVENT_ADDED|ENCOUNTER_TIMELINE_EVENT_STATE_CHANGED|Starting\s+hardcoded\s+timer\s+for\s+eventID|Hardcoded\s+timer\s+terminated\s+for\s+eventID|Timer\s+.*?refreshed\s+before\s+expired).*)$",
        re.IGNORECASE,
    )

    known_ts: list[float | None] = [None] * len(repaired)
    for idx, line in enumerate(repaired):
        parts = _parse_timestamp_prefix(line)
        if parts:
            known_ts[idx] = float(parts[1])

    for idx, line in enumerate(repaired):
        if _parse_timestamp_prefix(line):
            continue
        m = missing_prefix_re.match(line)
        if not m:
            continue

        prev_ts = None
        for j in range(idx - 1, -1, -1):
            if known_ts[j] is not None:
                prev_ts = known_ts[j]
                break

        next_ts = None
        for j in range(idx + 1, len(repaired)):
            if known_ts[j] is not None:
                next_ts = known_ts[j]
                break

        if prev_ts is None or next_ts is None or abs(prev_ts - next_ts) > 0.02:
            continue
        ts = prev_ts

        body = m.group("body").strip()
        repaired[idx] = f"{ts:.2f}: {body}"
        known_ts[idx] = ts
        replacements += 1

    return repaired, replacements


def _repair_hardcoded_timer_lines(lines: list[str]) -> tuple[list[str], int]:
    """Repair common OCR corruption on hardcoded timer lines.

    Handles:
      - missing timestamp prefix on "Starting hardcoded timer..." lines
        (e.g. ": Starting ..." or "Starting ...")
      - missing timestamp separator after numeric prefix
        (e.g. "220.02 Starting ..." -> "220.02: Starting ...")
      - trailing garbage after timer numeric token
        (e.g. "timer 54 f" -> "timer 54")
    """
    repaired = list(lines)
    replacements = 0

    added_re = re.compile(
        r"^(?P<time>\d+\.\d+)\s*[:;]\s*ENCOUNTER_TIMELINE_EVENT_ADDED:.*?eventID:\s*(?P<event_id>\d+)",
        re.IGNORECASE,
    )
    start_event_re = re.compile(
        r"Starting\s+hardcoded\s+timer\s+for\s+eventID\s*(?P<event_id>\d+)\s*:",
        re.IGNORECASE,
    )

    added_time_by_event_id: dict[int, float] = {}
    for line in repaired:
        m = added_re.search(line)
        if not m:
            continue
        event_id = int(m.group("event_id"))
        if event_id in added_time_by_event_id:
            continue
        added_time_by_event_id[event_id] = float(m.group("time"))

    for idx, line in enumerate(repaired):
        if "Starting hardcoded timer for eventID" not in line:
            continue

        updated = line

        # Ensure numeric timestamp has a ':' separator if OCR dropped it.
        updated = re.sub(
            r"^(\s*\d+\.\d+)\s+(Starting\s+hardcoded\s+timer\s+for\s+eventID)",
            r"\1: \2",
            updated,
            flags=re.IGNORECASE,
        )

        # Strip trailing OCR junk after timer numeric value.
        updated = re.sub(
            r"(Starting\s+hardcoded\s+timer\s+for\s+eventID\s*\d+\s*:\s*spellID\s*\d+\s*spellName\s*.*?\s+args\s+.*?\s+timer\s*\d+(?:\.\d+)?)(?:\s+[^\d\s].*)$",
            r"\1",
            updated,
            flags=re.IGNORECASE,
        )

        has_ts = _parse_timestamp_prefix(updated) is not None
        if not has_ts:
            em = start_event_re.search(updated)
            if em:
                event_id = int(em.group("event_id"))
                ts = added_time_by_event_id.get(event_id)
                if ts is not None:
                    start_idx = updated.lower().find("starting hardcoded timer for eventid")
                    suffix = updated[start_idx:] if start_idx >= 0 else updated.lstrip(" :;")
                    updated = f"{ts:.2f}: {suffix}"

        if updated != line:
            repaired[idx] = updated
            replacements += 1

    return repaired, replacements


def _postprocess_consensus_lines(lines: list[str]) -> tuple[list[str], int]:
    """Apply cross-line repairs that benefit from file-level consensus."""
    total = 0
    out = list(lines)

    out, n = _repair_spell_id_token_artifacts(out)
    total += n
    out, n = _repair_spell_ids_by_name(out)
    total += n
    out, n = _repair_spell_names_by_id(out)
    total += n
    out, n = _repair_durations_by_spell(out)
    total += n
    out, n = _repair_missing_timestamp_prefixes(out)
    total += n
    out, n = _repair_bootstrap_timestamps(out)
    total += n
    out, n = _repair_hardcoded_timer_lines(out)
    total += n
    out, n = _repair_timestamp_prefix_outliers(out)
    total += n
    out, n = _repair_timestamp_anomalies(out)
    total += n
    return out, total


def _write_log_lines(path: str, lines: list[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _flatten_pages(pages: list[tuple[str, list[str]]]) -> list[str]:
    out: list[str] = []
    for fname, relevant in pages:
        if not relevant:
            continue
        out.append(f"=== {fname} ===")
        out.extend(relevant)
    return out


def _line_quality_score(line: str) -> int:
    """Heuristic quality score for tie-breaking consensus lines."""
    score = 0
    score += len(re.findall(r"[A-Za-z0-9]", line))
    score += 2 * line.count("ENCOUNTER_TIMELINE_EVENT")
    score += line.count("eventID")
    score += line.count("spellID")
    score += line.count("spellName")
    score += line.count("duration:")
    score += line.count("Rounded:")
    score -= 2 * line.count("$")
    score -= 2 * line.count("&")
    score -= line.count("?")
    # Penalize likely truncated fragments.
    if line.rstrip().endswith(":") and "fired for" not in line:
        score -= 20
    if "ENCOUNTER_TIMELINE_EVENT_ADDED" in line and "eventID" not in line:
        score -= 20
    return score


def _choose_consensus_line(candidates: list[str]) -> str:
    counts = Counter(candidates)
    max_count = max(counts.values())
    top = [line for line, ct in counts.items() if ct == max_count]
    if len(top) == 1:
        return top[0]
    return max(top, key=_line_quality_score)


def _extract_spell_id_token(line: str) -> str | None:
    m = re.search(r"spellID\s*[:;]\s*([^\s]+)", line, flags=re.IGNORECASE)
    if not m:
        return None
    return m.group(1)


def _extract_timeline_event_id(line: str) -> str | None:
    """Return a timeline event's runtime ID when the OCR line contains one."""
    m = re.search(r"\beventID\s*:\s*(\d+)", line, flags=re.IGNORECASE)
    return m.group(1) if m else None


def _timeline_event_kind(line: str) -> str | None:
    if "ENCOUNTER_TIMELINE_EVENT_ADDED" in line:
        return "added"
    if "ENCOUNTER_TIMELINE_EVENT_STATE_CHANGED" in line:
        return "state"
    return None


def _normalize_spell_id_digits(token: str) -> str:
    token = token.strip()
    token = token.replace("!", "1")
    token = token.replace("l", "1").replace("I", "1")
    return re.sub(r"\D", "", token)


def _replace_spell_id_token(line: str, spell_id: str) -> str:
    return re.sub(
        r"(spellID\s*[:;]\s*)[^\s]+",
        rf"\g<1>{spell_id}",
        line,
        count=1,
        flags=re.IGNORECASE,
    )


def _prefer_spell_id_from_aligned_candidates(chosen_line: str, candidates: list[str]) -> str:
    """Prefer a clean 7-digit spellID from aligned OCR candidates at same index."""
    chosen_token = _extract_spell_id_token(chosen_line)
    if not chosen_token:
        return chosen_line

    if chosen_token.isdigit() and len(chosen_token) == 7:
        return chosen_line

    seven_digit_candidates: list[str] = []
    for cand in candidates:
        token = _extract_spell_id_token(cand)
        if not token:
            continue
        digits = _normalize_spell_id_digits(token)
        if len(digits) == 7:
            seven_digit_candidates.append(digits)

    if not seven_digit_candidates:
        return chosen_line

    counts = Counter(seven_digit_candidates)
    best_id, _ = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    return _replace_spell_id_token(chosen_line, best_id)


def _ocr_folder_pass(
    folder: str, tesseract_args: tuple[str, ...],
    binary_pages: dict[str, list[str]] | None = None,
) -> list[tuple[str, list[str]]]:
    """Run a single OCR pass and return relevant lines grouped by source PNG filename."""
    pngs = sorted(
        [f for f in os.listdir(folder) if f.lower().endswith(IMAGE_EXTENSIONS)],
        key=lambda x: int(re.sub(r"[^0-9]", "", x) or "0"),
    )
    pages: list[tuple[str, list[str]]] = []
    for fname in pngs:
        if binary_pages is not None and fname in binary_pages:
            pages.append((fname, binary_pages[fname]))
            continue
        path = os.path.join(folder, fname)
        result = subprocess.run(
            ["tesseract", path, "stdout", *tesseract_args],
            capture_output=True,
            text=True,
        )
        page_lines = result.stdout.split("\n")
        relevant: list[str] = []
        for line in page_lines:
            normalized_line = normalize_ocr_line(line)
            if is_relevant_line(normalized_line):
                relevant.append(normalized_line)
        pages.append((fname, relevant))
    return pages


def _consensus_pages(pass_pages: list[list[tuple[str, list[str]]]]) -> list[tuple[str, list[str]]]:
    """Build per-image consensus lines from multiple OCR pass outputs."""
    if not pass_pages:
        return []

    # Preserve deterministic image order from first pass.
    image_order = [fname for fname, _ in pass_pages[0]]

    pass_maps = []
    for pages in pass_pages:
        pass_maps.append({fname: lines for fname, lines in pages})

    consensus: list[tuple[str, list[str]]] = []
    for fname in image_order:
        aligned = [pmap.get(fname, []) for pmap in pass_maps]
        if not any(aligned):
            continue

        # Deterministic baseline = log1 (first pass).
        baseline = aligned[0]
        if not baseline:
            continue

        merged: list[str] = []
        for idx, base_line in enumerate(baseline):
            candidates = [lines[idx] for lines in aligned if idx < len(lines)]
            if not candidates:
                continue

            # Keep structural compatibility with baseline to avoid shifted/truncated substitutions.
            # OCR passes can lose a row, making same-index lines refer to different timeline
            # events. Never let an unrelated event ID replace the baseline's row.
            base_has_event = "eventID" in base_line
            base_has_spell = "spellID" in base_line
            base_event_id = _extract_timeline_event_id(base_line)
            base_event_kind = _timeline_event_kind(base_line)
            compatible = []
            for cand in candidates:
                if base_has_event and "eventID" not in cand:
                    continue
                if base_has_spell and "spellID" not in cand:
                    continue
                if base_event_id and _extract_timeline_event_id(cand) != base_event_id:
                    continue
                if base_event_kind and _timeline_event_kind(cand) != base_event_kind:
                    continue
                compatible.append(cand)

            # If a structured baseline event has no matching candidate, retain
            # it. OCR passes that lost a row are no longer aligned by index;
            # accepting their same-index row would corrupt this event's fields.
            pool = compatible if compatible else [base_line]
            counts = Counter(pool)
            base_count = counts.get(base_line, 0)
            majority_line, majority_count = counts.most_common(1)[0]

            # Conservative deterministic rule:
            # keep baseline unless another exact line has 2+ votes and strictly
            # more support than baseline at this index.
            if majority_line != base_line and majority_count >= 2 and majority_count > base_count:
                chosen = majority_line
            else:
                chosen = base_line

            chosen = _prefer_spell_id_from_aligned_candidates(chosen, pool)

            merged.append(chosen)
        consensus.append((fname, merged))
    return consensus


def _timeline_row_identity(line: str) -> tuple[str, str, str | None] | None:
    """Return a timestamp-independent identity for a deduplicable timeline row.

    Encounter timeline IDs are runtime IDs, so only use an identity to compare
    adjacent screenshots from the same visible stream. State changes also include
    their state because a single event legitimately progresses through multiple
    states.
    """
    kind = _timeline_event_kind(line)
    event_id = _extract_timeline_event_id(line)
    if not kind or not event_id:
        return None
    if kind == "state":
        state_match = re.search(r"\bstate\s*:\s*(\d+)", line, flags=re.IGNORECASE)
        if not state_match:
            return None
        return kind, event_id, state_match.group(1)
    return kind, event_id, None


def _deduplicate_adjacent_page_overlap(pages: list[tuple[str, list[str]]]) -> tuple[list[tuple[str, list[str]]], int]:
    """Remove only timeline rows repeated on the immediately preceding screenshot.

    OCR timestamp corruption must not make an otherwise identical event appear
    unique. Comparing the runtime event identity, rather than a timestamp or
    complete OCR text, safely removes ordinary screenshot boundary carryover
    while preserving separate pulls and legitimate state transitions.
    """
    output: list[tuple[str, list[str]]] = []
    previous_page_ids: set[tuple[str, str, str | None]] = set()
    removed = 0

    for fname, lines in pages:
        current_page_ids: set[tuple[str, str, str | None]] = set()
        deduplicated: list[str] = []
        for line in lines:
            if "ENCOUNTER_START" in line:
                # Never carry an overlap identity across an encounter boundary.
                previous_page_ids.clear()
            identity = _timeline_row_identity(line)
            if identity and identity in previous_page_ids:
                removed += 1
                continue
            deduplicated.append(line)
            if identity:
                current_page_ids.add(identity)
        output.append((fname, deduplicated))
        previous_page_ids = current_page_ids

    return output, removed


def ocr_folder(
    folder: str, write_pass_logs: bool = True, capture_format: str = "auto",
) -> list[str]:
    """OCR all PNGs in folder via 3-pass consensus and return corrected filtered lines.

    When write_pass_logs=True, intermediate pass logs are written as log1.txt/log2.txt/log3.txt.
    """
    binary_pages: dict[str, list[str]] = {}
    if capture_format != "text":
        for fname in sorted(os.listdir(folder)):
            if not fname.lower().endswith(IMAGE_EXTENSIONS):
                continue
            try:
                decoded = decode_image(os.path.join(folder, fname), required=capture_format == "binary")
            except BinaryDecodeError as error:
                raise BinaryDecodeError(f"{fname}: {error}") from error
            if decoded is not None:
                binary_pages[fname] = [line for line in decoded if is_relevant_line(line)]
    pass_outputs: list[list[tuple[str, list[str]]]] = []
    for variant_filename, tesseract_args in OCR_PASSES:
        pass_pages = _ocr_folder_pass(folder, tesseract_args, binary_pages)
        pass_outputs.append(pass_pages)
        if write_pass_logs:
            variant_lines = _flatten_pages(pass_pages)
            _write_log_lines(os.path.join(folder, variant_filename), variant_lines)

    consensus = _consensus_pages(pass_outputs)
    consensus = [(fname, binary_pages.get(fname, lines)) for fname, lines in consensus]
    consensus, _ = _deduplicate_adjacent_page_overlap(consensus)
    tagged = [(fname, [BINARY_CACHE_MARKER, *lines] if fname in binary_pages else lines)
              for fname, lines in consensus]
    lines_out, _ = _postprocess_capture_lines(_flatten_pages(tagged))
    return lines_out


def _postprocess_capture_lines(lines: list[str]) -> tuple[list[str], int]:
    """Keep verified binary sections out of English OCR repair heuristics."""
    output: list[str] = []
    pending: list[str] = []
    binary = False
    repairs = 0
    for line in lines:
        if line.startswith("=== "):
            binary = False
        if line == BINARY_CACHE_MARKER:
            corrected, count = _postprocess_consensus_lines(pending)
            output.extend(corrected)
            repairs += count
            pending.clear()
            binary = True
        if binary:
            output.append(line)
        else:
            pending.append(line)
    corrected, count = _postprocess_consensus_lines(pending)
    output.extend(corrected)
    return output, repairs + count


def audit_ocr_lines(lines: list[str]) -> list[str]:
    """Return focused OCR/source-quality issues that should be reviewed before routing.

    This deliberately reports rather than guesses. The next workflow stage should inspect
    only the named source screenshots, repair log.txt from the image evidence, and rerun
    this audit. Some reported timing mismatches can be real Blizzard timeline behavior.
    """
    issues: list[str] = []
    current_image = "unknown image"
    last_timestamp_by_image: dict[str, float] = {}
    latest_timestamp = 0.0
    additions: dict[int, dict[str, int | float | str | None]] = {}
    terminal_event_ids: set[int] = set()
    state_event_ids: set[int] = set()
    state_references: dict[int, str] = {}
    finished_events: list[tuple[int, float, str]] = []

    added_re = re.compile(
        r"eventID:\s*(?P<event_id>\d+).*?spellID:\s*(?P<spell_id>\d+).*?"
        r"spellName:\s*(?P<spell_name>.*?)\s+and\s+duration:\s*(?P<duration>\d+(?:\.\d+)?)\s+"
        r"\(Rounded:\s*(?P<rounded>\d+)\).*?state:\s*(?P<state>\d+)",
        re.IGNORECASE,
    )
    state_re = re.compile(
        r"eventID:\s*(?P<event_id>\d+)\s+with\s+state:\s*(?P<state>\d+)",
        re.IGNORECASE,
    )

    def line_ref(line_number: int) -> str:
        return f"{current_image}, log line {line_number}"

    for line_number, line in enumerate(lines, start=1):
        image_match = re.match(r"^===\s+(.*?)\s+===$", line)
        if image_match:
            current_image = image_match.group(1)
            continue

        timestamp_parts = _parse_timestamp_prefix(line)
        timestamp = float(timestamp_parts[1]) if timestamp_parts else None
        if timestamp is not None:
            latest_timestamp = max(latest_timestamp, timestamp)
            previous_timestamp = last_timestamp_by_image.get(current_image)
            if previous_timestamp is not None and timestamp + 0.5 < previous_timestamp:
                issues.append(
                    f"TIMESTAMP DROP [{line_ref(line_number)}]: {previous_timestamp:.2f} -> {timestamp:.2f}; inspect image."
                )
            last_timestamp_by_image[current_image] = timestamp

        if "ENCOUNTER_TIMELINE_EVENT_ADDED" in line:
            match = added_re.search(line)
            if not match:
                issues.append(f"MALFORMED ADDED [{line_ref(line_number)}]: {line}")
                continue
            event_id = int(match.group("event_id"))
            duration = float(match.group("duration"))
            rounded = int(match.group("rounded"))
            state = int(match.group("state"))
            if math.floor(duration + 0.5) != rounded:
                issues.append(
                    f"ROUNDING MISMATCH [{line_ref(line_number)}]: event {event_id}, duration {duration:g}, rounded {rounded}."
                )
            event_info: dict[str, int | float | str | None] = {
                "timestamp": timestamp,
                "duration": duration,
                "state": state,
                "spell_id": int(match.group("spell_id")),
                "spell_name": match.group("spell_name").strip(),
                "image": current_image,
                "line": line_number,
            }
            previous = additions.get(event_id)
            if previous and any(
                previous[key] != event_info[key]
                for key in ("duration", "state", "spell_id", "spell_name")
            ):
                issues.append(
                    f"CONFLICTING DUPLICATE [{line_ref(line_number)}]: event {event_id} differs from its earlier ADDED row."
                )
            elif previous:
                issues.append(
                    f"DUPLICATE ADDED [{line_ref(line_number)}]: event {event_id} repeats its earlier ADDED row; inspect the source image before treating it as an API resend."
                )
            elif not previous:
                additions[event_id] = event_info
        elif "ENCOUNTER_TIMELINE_EVENT_STATE_CHANGED" in line:
            match = state_re.search(line)
            if not match:
                issues.append(f"MALFORMED STATE [{line_ref(line_number)}]: {line}")
                continue
            event_id = int(match.group("event_id"))
            state = int(match.group("state"))
            state_event_ids.add(event_id)
            state_references.setdefault(event_id, line_ref(line_number))
            if state in (2, 3):
                terminal_event_ids.add(event_id)
            if state == 2 and timestamp is not None:
                finished_events.append((event_id, timestamp, line_ref(line_number)))

    for event_id in sorted(state_event_ids - additions.keys()):
        issues.append(
            f"STATE WITHOUT ADDED [{state_references[event_id]}]: event {event_id}; inspect this image and the preceding page boundary."
        )

    for event_id, finish_timestamp, reference in finished_events:
        addition = additions.get(event_id)
        if not addition or addition["timestamp"] is None:
            continue
        expected = float(addition["timestamp"]) + float(addition["duration"])
        # State 2 may land slightly late in normal API operation. Flag every
        # early finish, but only flag late finishes beyond the observed 1.5s variance.
        # Logs display centiseconds, so a half-centisecond tolerance prevents
        # binary float artifacts from marking equal displayed timestamps as early.
        if finish_timestamp + 0.005 < expected or finish_timestamp - expected > 1.5:
            issues.append(
                f"FINISH TIMING MISMATCH [{reference}]: event {event_id} expected near {expected:.2f}, finished {finish_timestamp:.2f}; inspect image, then treat as API behavior if confirmed."
            )

    for event_id, addition in sorted(additions.items()):
        if addition["state"] != 0 or event_id in terminal_event_ids or addition["timestamp"] is None:
            continue
        expected = float(addition["timestamp"]) + float(addition["duration"])
        if expected <= latest_timestamp + 0.5:
            issues.append(
                f"EXPECTED TERMINAL STATE MISSING [{addition['image']}, log line {addition['line']}]: event {event_id} expected near {expected:.2f}; inspect this image and the following page boundary."
            )

    return issues


def audit_ocr_folder(folder: str, write_report: bool = True) -> list[str]:
    """Audit one cached pull folder and optionally refresh its ocr_issues.txt report."""
    log_path = os.path.join(folder, LOG_FILENAME)
    if not os.path.exists(log_path):
        raise FileNotFoundError(f"No {LOG_FILENAME} in {folder}")
    with open(log_path, encoding="utf-8", errors="ignore") as f:
        lines = [line.rstrip("\n") for line in f]
    issues = audit_ocr_lines(lines)
    report_path = os.path.join(folder, OCR_ISSUES_FILENAME)
    if write_report:
        if issues:
            with open(report_path, "w", encoding="utf-8") as report:
                report.write("Review these issues against the named source screenshots before routing.\n")
                report.write('After repairing log.txt, rerun: python3 "Agent Tools/ocr_logs.py" --audit-ocr\n\n')
                report.write("\n".join(f"- {issue}" for issue in issues) + "\n")
        elif os.path.exists(report_path):
            os.remove(report_path)
    return issues


def audit_cached_ocr_logs() -> None:
    """Refresh focused OCR issue reports for every cached pull folder."""
    log_files: list[str] = []
    for root in LOG_ROOTS:
        log_files.extend(glob.glob(f"{root}/**/{LOG_FILENAME}", recursive=True))
    issue_folders = 0
    issue_count = 0
    for log_path in sorted(log_files):
        folder = os.path.dirname(log_path)
        issues = audit_ocr_folder(folder)
        if issues:
            issue_folders += 1
            issue_count += len(issues)
            print(f"OCR REVIEW: {folder} ({len(issues)} issue(s))")
            for issue in issues:
                print(f"  - {issue}")
    print(f"\nOCR audit: {issue_count} issue(s) in {issue_folders} folder(s); reports refreshed.")


def normalize_cached_logs(dry_run: bool = False) -> None:
    """Apply post-review normalization to existing cached log.txt files."""
    log_files: list[str] = []
    for root in LOG_ROOTS:
        log_files.extend(glob.glob(f"{root}/**/{LOG_FILENAME}", recursive=True))
    log_files = sorted(log_files)

    changed = 0
    total_line_changes = 0
    total_timestamp_repairs = 0
    for lf in log_files:
        with open(lf, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        new_lines: list[str] = []
        line_changes = 0
        binary = False
        for line in lines:
            stripped = line.rstrip("\n")
            if stripped.startswith("=== "):
                binary = False
            if stripped == BINARY_CACHE_MARKER:
                binary = True
            normalized = stripped if binary else normalize_ocr_line(stripped)
            if normalized != stripped:
                line_changes += 1
            new_lines.append(normalized + "\n")

        repaired_lines, post_repairs = _postprocess_capture_lines([l.rstrip("\n") for l in new_lines])
        if post_repairs:
            line_changes += post_repairs
            total_timestamp_repairs += post_repairs
            new_lines = [line + "\n" for line in repaired_lines]

        if line_changes > 0:
            changed += 1
            total_line_changes += line_changes
            if not dry_run:
                with open(lf, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
        if not dry_run:
            audit_ocr_folder(os.path.dirname(lf))

    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(
        f"{mode}: updated {changed} log file(s), {total_line_changes} line replacement(s) "
        f"(timestamp repairs: {total_timestamp_repairs})."
    )


def process_folders(folders: list[str], force: bool = False, capture_format: str = "auto") -> None:
    """OCR and audit only the requested pull folders."""
    skipped = 0
    processed = 0
    for folder in sorted(set(folders)):
        if not os.path.isdir(folder):
            print(f"SKIP: folder does not exist: {folder}")
            skipped += 1
            continue
        if not any(name.lower().endswith(IMAGE_EXTENSIONS) for name in os.listdir(folder)):
            print(f"SKIP: no screenshots found: {folder}")
            skipped += 1
            continue
        log_path = os.path.join(folder, LOG_FILENAME)
        if os.path.exists(log_path) and not force:
            issues = audit_ocr_folder(folder)
            print(f"AUDIT: {folder} ({len(issues)} OCR review issue(s); existing cache preserved)")
            skipped += 1
            continue
        print(f"OCR-ing: {folder} ...", end=" ", flush=True)
        lines = ocr_folder(folder, capture_format=capture_format)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        issues = audit_ocr_folder(folder)
        print(f"done ({len(lines)} lines, {len(issues)} OCR review issue(s))")
        processed += 1

    print(f"\nProcessed {processed} folder(s), skipped {skipped} (already cached).")


def process_all(force: bool = False, capture_format: str = "auto") -> None:
    image_dirs: set[str] = set()
    for root in LOG_ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, _, filenames in os.walk(root):
            if any(name.lower().endswith(IMAGE_EXTENSIONS) for name in filenames):
                image_dirs.add(dirpath)
    process_folders(sorted(image_dirs), force=force, capture_format=capture_format)


def _canonical_spell_name(name: str) -> str:
    name = re.sub(r"\(\d+\)", "", name)
    name = name.lower()
    name = re.sub(r"[^a-z0-9]", "", name)
    return name


def _split_spell_name_count(name: str) -> tuple[str, str | None]:
    m = re.search(r"\((\d+)\)\s*$", name)
    if not m:
        return name.strip(), None
    count = m.group(1)
    base = re.sub(r"\s*\(\d+\)\s*$", "", name).strip()
    return base, count


def qa_logs() -> None:
    """Run enhanced QA checks against cached log.txt files.

     Checks:
     1) Primary mismatch candidates: hardcoded starts by eventID where BOTH spellID and spellName differ.
     2) Likely cascade effects:
         - ambiguous-name ID mismatches that occur shortly after a primary mismatch.
         - refresh-before-expired collisions with nearest prior primary mismatch linkage.
    """
    log_files: list[str] = []
    for root in LOG_ROOTS:
        log_files.extend(glob.glob(f"{root}/**/{LOG_FILENAME}", recursive=True))
    log_files = sorted(log_files)

    if not log_files:
        print("No cached logs found.")
        return

    added_re = re.compile(
        r"^(?P<time>\d+\.\d+):\s*ENCOUNTER_TIMELINE_EVENT_ADDED:.*?eventID:\s*(?P<event_id>\d+).*?spellID:\s*(?P<spell_id>\d+).*?spellName:\s*(?P<spell_name>.*?)\s+and\s+duration:",
        re.IGNORECASE,
    )
    start_re = re.compile(
        r"^(?P<time>\d+\.\d+):\s*Starting hardcoded timer for eventID\s*(?P<event_id>\d+):\s*spellID\s*(?P<spell_id>\d+)\s*spellName\s*(?P<spell_name>.*?)\s+args\b",
        re.IGNORECASE,
    )
    refresh_re = re.compile(
        r"^(?P<time>\d+\.\d+):\s*Timer\s+.*?refreshed\s+before\s+expired\.\s+Remaining\s+time\s+is:\s*(?P<remaining>[0-9\.]+)",
        re.IGNORECASE,
    )

    # Name-equal ID mismatches are usually intentional aliases, except for a few
    # generic labels where OCR/log spell names are too coarse to disambiguate.
    # Track those separately as review-required.
    ambiguous_name_tokens = {"judgment"}

    total_primary_mismatches = 0
    total_ambiguous_primary_review = 0
    total_ambiguous_cascade = 0
    total_refreshes = 0
    total_refresh_cascade = 0
    total_timestamp_drops = 0
    total_missing_event_timestamps = 0

    for lf in log_files:
        with open(lf, encoding="utf-8", errors="ignore") as f:
            raw_lines = [line.rstrip("\n") for line in f]

        lines = [normalize_ocr_line(line) for line in raw_lines if line.strip()]
        if not lines:
            continue

        event_lines: list[dict[str, int | float | str]] = []
        missing_event_timestamps: list[dict[str, int | str]] = []
        for idx, line in enumerate(lines):
            is_event_line = (
                "ENCOUNTER_TIMELINE_EVENT_ADDED" in line
                or "ENCOUNTER_TIMELINE_EVENT_STATE_CHANGED" in line
            )
            if not is_event_line:
                continue
            ts_parts = _parse_timestamp_prefix(line)
            if not ts_parts:
                missing_event_timestamps.append(
                    {
                        "line_index": idx,
                        "line": line,
                    }
                )
                continue
            event_lines.append(
                {
                    "line_index": idx,
                    "time": float(ts_parts[1]),
                    "line": line,
                }
            )

        timestamp_drops: list[dict[str, int | float | str]] = []
        for prev, cur in zip(event_lines, event_lines[1:]):
            prev_time = float(prev["time"])
            cur_time = float(cur["time"])
            if cur_time + 0.5 < prev_time:
                timestamp_drops.append(
                    {
                        "prev_line_index": int(prev["line_index"]),
                        "prev_time": prev_time,
                        "cur_line_index": int(cur["line_index"]),
                        "cur_time": cur_time,
                        "drop": prev_time - cur_time,
                        "prev_line": str(prev["line"]),
                        "cur_line": str(cur["line"]),
                    }
                )

        added_by_event: dict[int, dict[str, str | float | int]] = {}
        starts: list[dict[str, str | float | int]] = []
        refreshes: list[dict[str, str | float | int]] = []

        for idx, line in enumerate(lines):
            m_added = added_re.search(line)
            if m_added:
                event_id = int(m_added.group("event_id"))
                added_by_event[event_id] = {
                    "line_index": idx,
                    "time": float(m_added.group("time")),
                    "spell_id": int(m_added.group("spell_id")),
                    "spell_name": m_added.group("spell_name").strip(),
                }
                continue

            m_start = start_re.search(line)
            if m_start:
                starts.append(
                    {
                        "line_index": idx,
                        "time": float(m_start.group("time")),
                        "event_id": int(m_start.group("event_id")),
                        "spell_id": int(m_start.group("spell_id")),
                        "spell_name": m_start.group("spell_name").strip(),
                    }
                )
                continue

            m_refresh = refresh_re.search(line)
            if m_refresh:
                refreshes.append(
                    {
                        "line_index": idx,
                        "time": float(m_refresh.group("time")),
                        "remaining": m_refresh.group("remaining"),
                        "line": line,
                    }
                )

        mismatches = []
        ambiguous_id_only = []
        for start in starts:
            event_id = int(start["event_id"])
            if event_id not in added_by_event:
                continue
            added = added_by_event[event_id]
            id_diff = start["spell_id"] != added["spell_id"]
            start_name_canon = _canonical_spell_name(str(start["spell_name"]))
            added_name_canon = _canonical_spell_name(str(added["spell_name"]))
            name_diff = start_name_canon != added_name_canon
            if id_diff and name_diff:
                mismatches.append(
                    {
                        "event_id": event_id,
                        "start_time": start["time"],
                        "added_time": added["time"],
                        "start_spell_id": start["spell_id"],
                        "start_spell_name": start["spell_name"],
                        "added_spell_id": added["spell_id"],
                        "added_spell_name": added["spell_name"],
                    }
                )
            elif id_diff and not name_diff and start_name_canon in ambiguous_name_tokens:
                ambiguous_id_only.append(
                    {
                        "event_id": event_id,
                        "start_time": start["time"],
                        "added_time": added["time"],
                        "start_spell_id": start["spell_id"],
                        "start_spell_name": start["spell_name"],
                        "added_spell_id": added["spell_id"],
                        "added_spell_name": added["spell_name"],
                    }
                )

        # Classify likely cascades using temporal proximity to nearest prior primary mismatch.
        # This helps separate root-cause misroutes from domino effects.
        mismatches.sort(key=lambda x: float(x["start_time"]))
        primary_times = [float(x["start_time"]) for x in mismatches]

        def find_nearest_prior_primary(ref_time: float, window: float) -> dict[str, str | float | int] | None:
            candidate = None
            for mm in mismatches:
                mm_time = float(mm["start_time"])
                if mm_time <= ref_time and (ref_time - mm_time) <= window:
                    candidate = mm
            return candidate

        ambiguous_primary_review: list[dict[str, str | float | int]] = []
        ambiguous_cascade: list[dict[str, str | float | int]] = []
        for entry in ambiguous_id_only:
            entry_time = float(entry["start_time"])
            root = find_nearest_prior_primary(entry_time, window=8.0)
            if root is not None:
                entry = dict(entry)
                entry["root_event_id"] = root["event_id"]
                entry["root_start_time"] = root["start_time"]
                ambiguous_cascade.append(entry)
            else:
                ambiguous_primary_review.append(entry)

        refresh_cascade: list[dict[str, str | float | int]] = []
        refresh_no_primary: list[dict[str, str | float | int]] = []
        for refresh in refreshes:
            refresh_time = float(refresh["time"])
            root = find_nearest_prior_primary(refresh_time, window=20.0)
            if root is not None:
                enrich = dict(refresh)
                enrich["root_event_id"] = root["event_id"]
                enrich["root_start_time"] = root["start_time"]
                refresh_cascade.append(enrich)
            else:
                refresh_no_primary.append(refresh)

        if (
            not mismatches
            and not ambiguous_primary_review
            and not ambiguous_cascade
            and not refreshes
            and not timestamp_drops
            and not missing_event_timestamps
        ):
            continue

        total_primary_mismatches += len(mismatches)
        total_ambiguous_primary_review += len(ambiguous_primary_review)
        total_ambiguous_cascade += len(ambiguous_cascade)
        total_refreshes += len(refreshes)
        total_refresh_cascade += len(refresh_cascade)
        total_timestamp_drops += len(timestamp_drops)
        total_missing_event_timestamps += len(missing_event_timestamps)

        print(f"\n=== QA: {os.path.dirname(lf)} ===")
        print(
            f"primary mismatches(ID+name): {len(mismatches)} | "
            f"ambiguous-name primary-review: {len(ambiguous_primary_review)} | "
            f"ambiguous-name likely-cascade: {len(ambiguous_cascade)} | "
            f"refresh-before-expired: {len(refreshes)} (likely-cascade: {len(refresh_cascade)}) | "
            f"timestamp drops: {len(timestamp_drops)} | "
            f"event lines missing parseable timestamp: {len(missing_event_timestamps)}"
        )

        if mismatches:
            print("-- primary mismatch candidates (ID and name both differ) --")
            for entry in mismatches:
                print(
                    "  "
                    + f"eventID {entry['event_id']} @start {entry['start_time']:.2f} "
                    + f"start({entry['start_spell_id']}, {entry['start_spell_name']}) "
                    + f"!= added({entry['added_spell_id']}, {entry['added_spell_name']}) @added {entry['added_time']:.2f}"
                )

        if ambiguous_primary_review:
            print("-- ambiguous-name ID mismatches (primary review-required) --")
            for entry in ambiguous_primary_review:
                print(
                    "  "
                    + f"eventID {entry['event_id']} @start {entry['start_time']:.2f} "
                    + f"start({entry['start_spell_id']}, {entry['start_spell_name']}) "
                    + f"!= added({entry['added_spell_id']}, {entry['added_spell_name']}) @added {entry['added_time']:.2f}"
                )

        if ambiguous_cascade:
            print("-- ambiguous-name ID mismatches (likely cascade from earlier primary mismatch) --")
            for entry in ambiguous_cascade:
                print(
                    "  "
                    + f"eventID {entry['event_id']} @start {entry['start_time']:.2f} "
                    + f"start({entry['start_spell_id']}, {entry['start_spell_name']}) "
                    + f"!= added({entry['added_spell_id']}, {entry['added_spell_name']}) @added {entry['added_time']:.2f}; "
                    + f"linked primary eventID {entry['root_event_id']} @ {entry['root_start_time']:.2f}"
                )

        if refreshes:
            print("-- refresh-before-expired review context --")
            for refresh in refreshes:
                idx = int(refresh["line_index"])
                start_idx = max(0, idx - 6)
                linked = ""
                linked_root = next((rc for rc in refresh_cascade if rc["line_index"] == refresh["line_index"]), None)
                if linked_root is not None:
                    linked = f" [likely-cascade from primary eventID {linked_root['root_event_id']} @ {float(linked_root['root_start_time']):.2f}]"
                print(f"  refresh @ {float(refresh['time']):.2f}, remaining={refresh['remaining']}{linked}")
                for cidx in range(start_idx, idx + 1):
                    prefix = "    >" if cidx == idx else "     "
                    print(f"{prefix} {lines[cidx]}")

        if timestamp_drops:
            print("-- non-monotonic event timestamp candidates --")
            for entry in timestamp_drops[:20]:
                print(
                    "  "
                    + f"line {int(entry['prev_line_index']) + 1} {float(entry['prev_time']):.2f} -> "
                    + f"line {int(entry['cur_line_index']) + 1} {float(entry['cur_time']):.2f} "
                    + f"(drop {float(entry['drop']):.2f})"
                )

        if missing_event_timestamps:
            print("-- event lines missing parseable timestamp prefix --")
            for entry in missing_event_timestamps[:20]:
                print("  " + f"line {int(entry['line_index']) + 1}: {entry['line']}")

    print("\n=== QA Summary ===")
    print(f"Total primary mismatches (ID+name): {total_primary_mismatches}")
    print(f"Total ambiguous-name primary-review mismatches: {total_ambiguous_primary_review}")
    print(f"Total ambiguous-name likely-cascade mismatches: {total_ambiguous_cascade}")
    print(f"Total refresh-before-expired events: {total_refreshes}")
    print(f"Total refresh likely-cascade events: {total_refresh_cascade}")
    print(f"Total non-monotonic event timestamp candidates: {total_timestamp_drops}")
    print(f"Total event lines missing parseable timestamp prefix: {total_missing_event_timestamps}")


def verify_all() -> None:
    log_files: list[str] = []
    for root in LOG_ROOTS:
        log_files.extend(glob.glob(f"{root}/**/log.txt", recursive=True))
    log_files = sorted(log_files)
    if not log_files:
        print("No cached logs found.")
        return
    print(f"{'Folder':<60} {'Lines':>6}")
    print("-" * 68)
    for lf in log_files:
        folder = os.path.dirname(lf)
        with open(lf, encoding="utf-8") as f:
            lines = [l for l in f.readlines() if l.strip()]
        print(f"{folder:<60} {len(lines):>6}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--normalize", action="store_true")
    parser.add_argument("--qa", action="store_true")
    parser.add_argument("--audit-ocr", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--folder", nargs="+")
    parser.add_argument("--capture-format", choices=("auto", "text", "binary"), default="auto",
                        help="auto detects Binary v1; use binary to reject unreadable/cropped captures")
    args = parser.parse_args()
    if args.normalize:
        normalize_cached_logs(dry_run=args.dry_run)
    elif args.audit_ocr:
        audit_cached_ocr_logs()
    elif args.qa:
        qa_logs()
    elif args.verify:
        verify_all()
    else:
        try:
            if args.folder:
                process_folders(args.folder, force=args.force, capture_format=args.capture_format)
            else:
                process_all(force=args.force, capture_format=args.capture_format)
        except (BinaryDecodeError, ModuleNotFoundError) as error:
            print(f"OCR failed: {error}", file=sys.stderr)
            sys.exit(1)
