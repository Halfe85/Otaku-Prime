# -*- coding: utf-8 -*-
"""Remote identity validation and repair helpers.

Prime local IDs are authoritative and stable. Remote provider IDs are mutable
mappings that must be verified before they are trusted for catalogue writes.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from html import unescape
import re
import unicodedata


WATCHLIST_ID_PROVIDERS = ("anilist", "mal", "kitsu", "simkl")


class RemoteIdentityError(RuntimeError):
    pass


class RemoteIdentityAmbiguous(RemoteIdentityError):
    pass


class RemoteIdentityConflict(RemoteIdentityError):
    pass


def clean_remote_text(value):
    """Decode provider HTML entities without interpreting the text as markup."""
    if value in (None, ""):
        return value
    text = str(value)
    # A few provider records contain nested encoding such as ``&amp;#039;``.
    # Bound the loop so malformed input cannot keep this worker busy.
    for _ in range(3):
        decoded = unescape(text)
        if decoded == text:
            break
        text = decoded
    return text


def normalize_title(value):
    text = unicodedata.normalize("NFKD", str(clean_remote_text(value) or "")).casefold()
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.replace("&", " and ")
    # Python's Unicode-aware ``\w`` retains Japanese, Chinese and other title
    # scripts. The previous ASCII-only expression reduced titles such as
    # ``不死身な僕の日常 シーズン4`` to just ``4`` and caused false matches.
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).replace("_", " ")
    return " ".join(text.split())


def payload_titles(payload):
    values = []
    for key in ("en_title", "title", "name", "english_name", "romaji_name", "native_name"):
        value = (payload or {}).get(key)
        if value:
            values.append(clean_remote_text(value))
    for value in (payload or {}).get("alt_titles") or []:
        if isinstance(value, dict):
            value = value.get("name") or value.get("title")
        if value:
            values.append(clean_remote_text(value))
    return list(dict.fromkeys(values))


def item_titles(item):
    values = [item.get("english_name"), item.get("romaji_name"), item.get("native_name")]
    return [clean_remote_text(value) for value in values if value]


def _year(value):
    if value in (None, ""):
        return None
    match = re.search(r"(19|20)\d{2}", str(value))
    return int(match.group(0)) if match else None


def item_year(item):
    return _year(item.get("release_date") or item.get("year"))


def payload_year(payload):
    return _year((payload or {}).get("year") or (payload or {}).get("first_aired"))


def best_title_similarity(expected, actual):
    expected = [normalize_title(value) for value in expected if normalize_title(value)]
    actual = [normalize_title(value) for value in actual if normalize_title(value)]
    if not expected or not actual:
        return 0.0
    if set(expected) & set(actual):
        return 1.0
    return max(SequenceMatcher(None, left, right).ratio() for left in expected for right in actual)


def score_candidate(item, payload, ignore_provider=None):
    """Score a remote media record against Prime's canonical item.

    IDs are strong evidence, but never absolute authority. A stale ID can be
    repaired when title/year/type evidence clearly identifies a better record.
    """
    score = 0
    reasons = []
    matched_ids = 0
    conflicting_ids = 0
    ids = (payload or {}).get("ids") or {}

    for provider in WATCHLIST_ID_PROVIDERS:
        if provider == ignore_provider:
            continue
        known = item.get(provider + "_id")
        remote = ids.get(provider)
        if known in (None, "") or remote in (None, ""):
            continue
        if str(known) == str(remote):
            matched_ids += 1
            score += 150
            reasons.append("{} id matches".format(provider))
        else:
            conflicting_ids += 1
            score -= 140
            reasons.append("{} id differs".format(provider))

    similarity = best_title_similarity(item_titles(item), payload_titles(payload))
    if similarity >= 0.995:
        score += 120
        reasons.append("title exact")
    elif similarity >= 0.90:
        score += 90
        reasons.append("title very close")
    elif similarity >= 0.78:
        score += 55
        reasons.append("title close")
    elif similarity >= 0.65:
        score += 20
        reasons.append("title weak")
    elif item_titles(item) and payload_titles(payload):
        score -= 70
        reasons.append("title mismatch")

    expected_year = item_year(item)
    remote_year = payload_year(payload)
    if expected_year and remote_year:
        difference = abs(expected_year - remote_year)
        if difference == 0:
            score += 40
            reasons.append("year matches")
        elif difference == 1:
            score += 15
            reasons.append("year near")
        else:
            score -= 45
            reasons.append("year differs")

    expected_count = item.get("episode_count")
    remote_count = (payload or {}).get("total_episodes")
    if expected_count not in (None, "") and remote_count not in (None, ""):
        try:
            difference = abs(int(expected_count) - int(remote_count))
            if difference == 0:
                score += 35
                reasons.append("episode count matches")
            elif difference <= 1:
                score += 10
                reasons.append("episode count near")
            elif difference >= 5:
                score -= 15
                reasons.append("episode count differs")
        except (TypeError, ValueError):
            pass

    format_map = {
        "TV": "tv",
        "TV_SHORT": "tv",
        "MOVIE": "movie",
        "OVA": "ova",
        "ONA": "ona",
        "SPECIAL": "special",
        "MUSIC": "music video",
    }
    expected_type = format_map.get(str(item.get("media_format") or "").upper())
    remote_type = str((payload or {}).get("anime_type") or "").lower()
    if expected_type and remote_type:
        if expected_type == remote_type:
            score += 25
            reasons.append("media type matches")
        else:
            score -= 20
            reasons.append("media type differs")

    return {
        "score": score,
        "title_similarity": similarity,
        "matched_ids": matched_ids,
        "conflicting_ids": conflicting_ids,
        "reasons": reasons,
    }


def candidate_is_confident(result):
    if result["matched_ids"]:
        return result["score"] >= 80
    return result["score"] >= 120 and result["title_similarity"] >= 0.90


def choose_candidate(item, candidates, ignore_provider=None, minimum_margin=35):
    scored = []
    seen = set()
    for payload in candidates:
        ids = (payload or {}).get("ids") or {}
        remote_id = ids.get(ignore_provider or "simkl") or ids.get("simkl")
        key = str(remote_id or id(payload))
        if key in seen:
            continue
        seen.add(key)
        result = score_candidate(item, payload, ignore_provider=ignore_provider)
        scored.append((result["score"], result, payload))
    scored.sort(key=lambda value: value[0], reverse=True)
    if not scored or not candidate_is_confident(scored[0][1]):
        raise RemoteIdentityConflict("no remote candidate matches Prime confidently")
    if len(scored) > 1 and scored[0][0] - scored[1][0] < int(minimum_margin):
        raise RemoteIdentityAmbiguous(
            "remote identity lookup is ambiguous: scores {} and {}".format(
                scored[0][0], scored[1][0]
            )
        )
    return scored[0][2], scored[0][1]


def persist_watchlist_id_repair(store, local_id, provider, old_value, new_value, reason=None):
    """Replace a verified stale remote ID while preserving Prime's local ID."""
    provider = str(provider or "").lower()
    if provider not in WATCHLIST_ID_PROVIDERS:
        raise ValueError("unsupported watchlist provider identity")
    old_value = str(old_value) if old_value not in (None, "") else None
    new_value = str(new_value) if new_value not in (None, "") else None
    if not new_value or old_value == new_value:
        return False
    column = provider + "_id"
    with store._connection() as db:
        current = db.execute(
            "SELECT {} FROM watchlist_items WHERE local_id=?".format(column),
            (str(local_id),),
        ).fetchone()
        if not current:
            raise KeyError("watchlist item not found")
        current_value = current[column]
        if old_value is not None and current_value not in (None, old_value):
            raise RemoteIdentityConflict(
                "{} identity changed concurrently from {} to {}".format(
                    provider, old_value, current_value
                )
            )
        collision = db.execute(
            "SELECT local_id FROM watchlist_items WHERE {}=? AND local_id<>?".format(column),
            (new_value, str(local_id)),
        ).fetchone()
        if collision:
            raise RemoteIdentityConflict(
                "{} ID {} already belongs to Prime item {}".format(
                    provider, new_value, collision["local_id"]
                )
            )
        db.execute(
            "UPDATE watchlist_items SET {}=?,identity_resolution_status='REPAIRED',"
            "identity_resolution_error=?,identity_checked_at=CURRENT_TIMESTAMP,"
            "updated_at=CURRENT_TIMESTAMP WHERE local_id=?".format(column),
            (new_value, str(reason) if reason else None, str(local_id)),
        )
    return True
