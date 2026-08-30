# -*- coding: utf-8 -*-
"""Native AniList franchise and episode placement for Prime watchlist items."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from resources.lib.logging_config import get_logger
from resources.lib.services.anilist_rate_limit import ANILIST_RATE_LIMITER
from resources.lib.services.mediator_helper_simkl import (
    MediatorMetadataPending,
    MediatorPlacementError,
)


MAX_PREQUEL_DEPTH = 64
SPECIAL_FORMATS = ("MOVIE", "OVA", "ONA", "SPECIAL", "MUSIC")
SEASON_FORMATS = ("TV", "TV_SHORT")
FRANCHISE_PARENT_RELATIONS = ("PARENT",)
FRANCHISE_BRIDGE_RELATIONS = ("SEQUEL",)
FRANCHISE_SECONDARY_RELATIONS = ("OTHER",)
LOGGER=get_logger(__name__)


class AniListMediatorClient:
    """Small cached AniList GraphQL client used only by native mediation."""

    API_URL = "https://graphql.anilist.co"

    def __init__(self, timeout=30, opener=None):
        self.timeout = int(timeout)
        self._open = opener or urlopen
        self._rate_limited = opener is None
        self._media_cache = {}
        self._schedule_cache = {}
        self._cast_cache = {}

    def _query(self, query, variables):
        request = Request(
            self.API_URL,
            data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Otaku-Prime/0.1.2 anilist-mediator",
            },
        )
        payload = None
        for attempt in range(2):
            try:
                if self._rate_limited:
                    ANILIST_RATE_LIMITER.wait()
                with self._open(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                if exc.code == 429 and attempt == 0:
                    time.sleep(ANILIST_RATE_LIMITER.retry_delay(exc))
                    continue
                raise MediatorPlacementError(
                    "AniList GraphQL returned HTTP {}".format(exc.code)
                ) from exc
            except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                if attempt == 0:
                    time.sleep(1)
                    continue
                raise MediatorPlacementError("AniList GraphQL failed: {}".format(exc)) from exc
        if not isinstance(payload, dict):
            raise MediatorPlacementError("AniList GraphQL returned an invalid response")
        if payload.get("errors"):
            messages = ", ".join(
                str(row.get("message") or "unknown GraphQL error")
                for row in payload.get("errors") or []
            )
            raise MediatorPlacementError("AniList GraphQL failed: {}".format(messages))
        return payload.get("data") or {}

    def media(self, anilist_id):
        key = str(anilist_id)
        if key not in self._media_cache:
            data = self._query(
                """query($id:Int!){Media(id:$id,type:ANIME){
                  id idMal format episodes status duration description(asHtml:false)
                  coverImage{extraLarge large medium}
                  genres isAdult tags{name rank isMediaSpoiler isGeneralSpoiler category}
                  title{english romaji native}
                  startDate{year month day} endDate{year month day}
                  nextAiringEpisode{episode airingAt}
                  relations{edges{relationType(version:2) node{
                    id idMal type format episodes status duration description(asHtml:false)
                    coverImage{extraLarge large medium}
                    genres isAdult tags{name rank isMediaSpoiler isGeneralSpoiler category}
                    title{english romaji native}
                    startDate{year month day} endDate{year month day}
                  }}}
                }}""",
                {"id": int(key)},
            )
            media = data.get("Media") or {}
            if not media.get("id"):
                raise MediatorPlacementError("AniList media {} was not found".format(key))
            self._media_cache[key] = media
        return self._media_cache[key]

    def schedule(self, anilist_id):
        key = str(anilist_id)
        if key in self._schedule_cache:
            return self._schedule_cache[key]
        page = 1
        rows = []
        while True:
            data = self._query(
                """query($id:Int!,$page:Int!){Media(id:$id,type:ANIME){
                  airingSchedule(page:$page,perPage:25){
                    pageInfo{hasNextPage}
                    nodes{episode airingAt}
                  }
                }}""",
                {"id": int(key), "page": page},
            )
            connection = ((data.get("Media") or {}).get("airingSchedule") or {})
            rows.extend(connection.get("nodes") or [])
            if not (connection.get("pageInfo") or {}).get("hasNextPage"):
                break
            page += 1
            if page > 100:
                raise MediatorPlacementError("AniList airing schedule exceeded its safety limit")
        self._schedule_cache[key] = rows
        return rows

    def cast(self, anilist_id):
        """Return character voice credits and standalone production staff."""
        key = str(anilist_id)
        if key in self._cast_cache:
            return self._cast_cache[key]
        result = []
        seen = set()
        errors=[]
        character_query="""query($id:Int!,$page:Int!){Media(id:$id,type:ANIME){
          characters(page:$page,perPage:25){pageInfo{hasNextPage} edges{
            node{id name{full} description(asHtml:false) image{large}}
            voiceActors(language:JAPANESE,sort:[RELEVANCE,ID]){id name{full}
              description(asHtml:false) dateOfBirth{year month day}
              dateOfDeath{year month day} age image{large}}
          }}}}"""
        staff_query="""query($id:Int!,$page:Int!){Media(id:$id,type:ANIME){
          staff(page:$page,perPage:25){pageInfo{hasNextPage} edges{role node{id
            name{full} description(asHtml:false) dateOfBirth{year month day}
            dateOfDeath{year month day} age image{large}}}}}}"""

        page=1
        while True:
            try:
                data=self._query(character_query,{"id":int(key),"page":page})
            except MediatorPlacementError as exc:
                errors.append("characters: {}".format(exc)); break
            connection=((data.get("Media") or {}).get("characters") or {})
            for edge in connection.get("edges") or []:
                character_node=(edge or {}).get("node") or {}
                character=(character_node.get("name") or {}).get("full")
                if not character: continue
                character_value={
                    "anilist_id":str(character_node.get("id")) if character_node.get("id") else None,
                    "name":str(character),"trivia":character_node.get("description"),
                    "image_url":(character_node.get("image") or {}).get("large"),
                }
                actors=(edge or {}).get("voiceActors") or []
                if not actors:
                    marker=(None,str(character))
                    if marker not in seen:
                        seen.add(marker); result.append({
                            "person_name":None,"character_name":str(character),"person":{},
                            "character":character_value,"credit_type":"voice_actor",
                            "language":"JAPANESE","source_provider":"anilist",
                            "sort_order":len(result)})
                for actor in actors:
                    person=((actor or {}).get("name") or {}).get("full")
                    if not person: continue
                    marker=(str(person),str(character))
                    if marker in seen: continue
                    seen.add(marker); result.append({
                        "person_name":str(person),"character_name":str(character),
                        "person":{"anilist_id":str(actor.get("id")) if actor.get("id") else None,
                                  "name":str(person),"trivia":actor.get("description"),
                                  "date_of_birth":_fuzzy_date_string(actor.get("dateOfBirth")),
                                  "date_of_death":_fuzzy_date_string(actor.get("dateOfDeath")),
                                  "age":actor.get("age"),
                                  "image_url":(actor.get("image") or {}).get("large")},
                        "character":character_value,"credit_type":"voice_actor",
                        "language":"JAPANESE","source_provider":"anilist",
                        "sort_order":len(result)})
            if not (connection.get("pageInfo") or {}).get("hasNextPage"): break
            page+=1
            if page>50:
                errors.append("characters: AniList character list exceeded its safety limit"); break

        page=1
        while True:
            try:
                data=self._query(staff_query,{"id":int(key),"page":page})
            except MediatorPlacementError as exc:
                errors.append("staff: {}".format(exc)); break
            connection=((data.get("Media") or {}).get("staff") or {})
            for edge in connection.get("edges") or []:
                person_node=(edge or {}).get("node") or {}
                person=(person_node.get("name") or {}).get("full")
                role=str((edge or {}).get("role") or "Staff")
                if not person: continue
                marker=("staff",str(person_node.get("id") or person),role)
                if marker in seen: continue
                seen.add(marker); result.append({
                    "person_name":str(person),"character_name":None,
                    "person":{"anilist_id":str(person_node.get("id")) if person_node.get("id") else None,
                              "name":str(person),"trivia":person_node.get("description"),
                              "date_of_birth":_fuzzy_date_string(person_node.get("dateOfBirth")),
                              "date_of_death":_fuzzy_date_string(person_node.get("dateOfDeath")),
                              "age":person_node.get("age"),
                              "image_url":(person_node.get("image") or {}).get("large")},
                    "character":{},"credit_type":role,"language":"",
                    "source_provider":"anilist","sort_order":len(result)})
            if not (connection.get("pageInfo") or {}).get("hasNextPage"): break
            page+=1
            if page>50:
                errors.append("staff: AniList staff list exceeded its safety limit"); break

        if errors and not result:
            raise MediatorPlacementError("; ".join(errors))
        if errors:
            LOGGER.warning("AniList %s enrichment is partial: %s",key,"; ".join(errors))
        self._cast_cache[key] = result
        return result


def _date_key(media):
    value = media.get("startDate") or {}
    return (
        int(value.get("year") or 9999),
        int(value.get("month") or 99),
        int(value.get("day") or 99),
        int(media.get("id") or 0),
    )


def _date_string(value):
    value = value or {}
    if not value.get("year"):
        return None
    return "{:04d}-{:02d}-{:02d}".format(
        int(value["year"]), int(value.get("month") or 1), int(value.get("day") or 1)
    )


def _fuzzy_date_string(value):
    """Preserve AniList's partial staff dates without inventing month/day values."""
    value=value or {}
    if not value.get("year"): return None
    result="{:04d}".format(int(value["year"]))
    if not value.get("month"): return result
    result+="-{:02d}".format(int(value["month"]))
    if not value.get("day"): return result
    return result+"-{:02d}".format(int(value["day"]))


def _titles(media):
    values = media.get("title") or {}
    return {
        "english": values.get("english") or values.get("romaji") or values.get("native"),
        "romaji": values.get("romaji") or values.get("english") or values.get("native"),
    }


def _season_title(target, root_titles, season_number):
    """Return a useful catalogue title even before AniList publishes English metadata."""
    values=target.get("title") or {}
    english=values.get("english")
    if english:
        return english
    if season_number>1 and root_titles.get("english"):
        return "{} Season {}".format(root_titles["english"],season_number)
    return values.get("romaji") or values.get("native") or root_titles.get("english")


def _prequel_ids(media):
    result = []
    for edge in (media.get("relations") or {}).get("edges") or []:
        node = edge.get("node") or {}
        if (
            str(edge.get("relationType") or "").upper() == "PREQUEL"
            and str(node.get("type") or "ANIME").upper() == "ANIME"
            and node.get("id") not in (None, "")
        ):
            result.append(str(node["id"]))
    return list(dict.fromkeys(result))


def _anime_relation_nodes(media, relation_types):
    relation_types={str(value).upper() for value in relation_types}
    result=[]
    for edge in (media.get("relations") or {}).get("edges") or []:
        node=edge.get("node") or {}
        if (str(edge.get("relationType") or "").upper() not in relation_types or
                str(node.get("type") or "ANIME").upper()!="ANIME" or
                node.get("id") in (None,"")):
            continue
        result.append(node)
    return result


def _find_bottom_root(client, target):
    """Return the terminal PREQUEL root and its exact root-to-target path."""
    target_id = str(target["id"])
    frontier = [(target, [target], {target_id})]
    terminal_paths = []
    visited_nodes = 0
    while frontier:
        current, path, seen = frontier.pop(0)
        visited_nodes += 1
        if visited_nodes > MAX_PREQUEL_DEPTH * MAX_PREQUEL_DEPTH:
            raise MediatorPlacementError("AniList prequel graph exceeded its safety limit")
        candidates = []
        for candidate_id in _prequel_ids(current):
            if candidate_id in seen:
                continue
            candidates.append(client.media(candidate_id))
        if not candidates:
            terminal_paths.append(path)
            continue
        if len(path) >= MAX_PREQUEL_DEPTH:
            raise MediatorPlacementError("AniList prequel path exceeded its safety limit")
        for candidate in sorted(candidates, key=_date_key):
            candidate_id = str(candidate["id"])
            frontier.append((candidate, path + [candidate], seen | {candidate_id}))
    if not terminal_paths:
        raise MediatorPlacementError("AniList prequel graph has no terminal root")
    selected = sorted(
        terminal_paths,
        key=lambda path: (-len(path), _date_key(path[-1])),
    )[0]
    return selected[-1], list(reversed(selected))


def _find_franchise_root(client, relation_root):
    """Attach a watchlist root to its explicit parent franchise when possible.

    PREQUEL answers where the watchlist item sits in its own sequence.  It does
    not answer which Prime TV-show/franchise owns a movie, OVA, or spin-off.
    AniList exposes that second fact separately: normally through PARENT, and
    occasionally through a short special -> sequel -> OTHER bridge (for
    example BURN THE WITCH #0.8 -> BURN THE WITCH -> Bleach).

    OTHER is deliberately accepted only while walking a special-format bridge
    and only when it points backwards to one unambiguous TV root.  This avoids
    turning ordinary crossover relations between TV series into ownership.
    """
    root_format=str(relation_root.get("format") or "").upper()
    frontier=[relation_root]
    seen={str(relation_root["id"])}
    path=[relation_root]
    bridge_allowed=root_format in SPECIAL_FORMATS
    for _ in range(MAX_PREQUEL_DEPTH):
        if not frontier:
            break
        current=frontier.pop(0)
        if str(current.get("format") or "").upper() in SEASON_FORMATS:
            _unused_root,tv_path=_find_bottom_root(client,current)
            numbered=[node for node in tv_path
                      if str(node.get("format") or "").upper() in SEASON_FORMATS]
            anchor=numbered[0] if numbered else current
            return anchor,path+([anchor] if str(anchor["id"]) not in seen else []),True
        parents=_anime_relation_nodes(current,FRANCHISE_PARENT_RELATIONS)
        if parents:
            for parent in sorted((client.media(node["id"]) for node in parents),key=_date_key):
                parent_id=str(parent["id"])
                if parent_id not in seen:
                    seen.add(parent_id); frontier.append(parent); path.append(parent)

        if bridge_allowed:
            secondary=[]
            current_key=_date_key(current)
            for node in _anime_relation_nodes(current,FRANCHISE_SECONDARY_RELATIONS):
                candidate=client.media(node["id"])
                if (str(candidate.get("format") or "").upper() in SEASON_FORMATS and
                        _date_key(candidate)<current_key):
                    secondary.append(candidate)
            unique={str(node["id"]):node for node in secondary}
            if len(unique)==1:
                parent=next(iter(unique.values()))
                _unused_root,tv_path=_find_bottom_root(client,parent)
                numbered=[node for node in tv_path
                          if str(node.get("format") or "").upper() in SEASON_FORMATS]
                anchor=numbered[0] if numbered else parent
                return anchor,path+([anchor] if str(anchor["id"]) not in seen else []),True

            for node in _anime_relation_nodes(current,FRANCHISE_BRIDGE_RELATIONS):
                node_id=str(node["id"])
                if node_id in seen:
                    continue
                candidate=client.media(node_id)
                if str(candidate.get("format") or "").upper() not in (
                        SPECIAL_FORMATS+SEASON_FORMATS):
                    continue
                seen.add(node_id); frontier.append(candidate); path.append(candidate)
    return relation_root,path,False


def _season_number(target, path):
    media_format = str(target.get("format") or "").upper()
    if media_format in SPECIAL_FORMATS:
        return 0, "anilist_special_format"
    numbered = [
        node for node in path if str(node.get("format") or "").upper() in SEASON_FORMATS
    ]
    target_id = str(target["id"])
    for index, node in enumerate(numbered, 1):
        if str(node["id"]) == target_id:
            return index, "anilist_prequel_position"
    return max(1, len(numbered) + 1), "anilist_prequel_position"


def _episode_count(target, item, schedule):
    values = [target.get("episodes"), item.get("episode_count")]
    scheduled = [int(row["episode"]) for row in schedule if row.get("episode") not in (None, "")]
    if scheduled:
        values.append(max(scheduled))
    next_airing = target.get("nextAiringEpisode") or {}
    if next_airing.get("episode") not in (None, ""):
        values.append(int(next_airing["episode"]))
    for value in values:
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        if count > 0:
            return count
    raise MediatorMetadataPending(
        "AniList has no episode count for the requested watchlist item")


def _special_offset(target, path):
    target_id = str(target["id"])
    offset = 0
    for node in path:
        if str(node["id"]) == target_id:
            break
        if str(node.get("format") or "").upper() not in SPECIAL_FORMATS:
            continue
        try:
            count = int(node.get("episodes") or 0)
        except (TypeError, ValueError):
            count = 0
        offset += max(0, count)
    return offset


def _release_dates(target, schedule):
    dates = {}
    for row in schedule:
        try:
            episode = int(row["episode"])
            airing_at = int(row["airingAt"])
        except (KeyError, TypeError, ValueError):
            continue
        dates[episode] = datetime.fromtimestamp(airing_at, timezone.utc).date().isoformat()
    if 1 not in dates:
        first = _date_string(target.get("startDate"))
        if first:
            dates[1] = first
    return dates


def _year(media):
    try:
        return int((media.get("startDate") or {}).get("year"))
    except (TypeError, ValueError):
        return None


def _runtime(media):
    try:
        value = int(media.get("duration"))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _metadata_terms(root,target):
    genres=[]; themes=[]; seen_genres=set(); seen_themes=set()
    for media in (root or {},target or {}):
        for value in media.get("genres") or []:
            text=str(value or "").strip(); key=text.casefold()
            if text and key not in seen_genres:
                genres.append(text); seen_genres.add(key)
        # AniList exposes thematic descriptors as ranked Media tags.  Spoiler
        # tags never belong in Prime's public series metadata.
        for tag in media.get("tags") or []:
            if not isinstance(tag,dict) or tag.get("isMediaSpoiler") or tag.get("isGeneralSpoiler"):
                continue
            try: rank=int(tag.get("rank") or 0)
            except (TypeError,ValueError): rank=0
            if rank<40: continue
            text=str(tag.get("name") or "").strip(); key=text.casefold()
            if text and key not in seen_themes:
                themes.append(text); seen_themes.add(key)
    mature=bool((root or {}).get("isAdult") or (target or {}).get("isAdult"))
    return {"genres":genres,"themes":themes,
            "age_rating":"18+" if mature else None,"mature":mature}


class AniListMediatorHelper:
    provider = "anilist"

    def __init__(self, client=None):
        self.client = client or AniListMediatorClient()

    def franchise_identity(self, anilist_id):
        """Return canonical Prime franchise identity without resolving episodes."""
        target=self.client.media(anilist_id)
        relation_root,relation_path=_find_bottom_root(self.client,target)
        root,franchise_path,has_tv_franchise=_find_franchise_root(
            self.client,relation_root)
        titles=_titles(root)
        return {
            "name":titles["english"],"romaji_name":titles["romaji"],
            "anilist_id":str(root["id"]),
            "mal_id":str(root["idMal"]) if root.get("idMal") not in (None,"") else None,
            "source_format":str(root.get("format") or "").upper() or None,
            "publish_year":_year(root),"source":"anilist_franchise_relation",
            "relation_path":[str(node["id"]) for node in relation_path],
            "franchise_relation_path":[str(node["id"]) for node in franchise_path],
            "library_type":("movie" if str(target.get("format") or "").upper()=="MOVIE"
                            and not has_tv_franchise else "series"),
        }

    def resolve(self, item, client=None):
        value = item.get("anilist_id")
        if value in (None, ""):
            raise MediatorPlacementError("watchlist item has no AniList ID")
        target = self.client.media(value)
        if str(target.get("id")) != str(value):
            raise MediatorPlacementError("AniList returned a different media identity")
        relation_root, path = _find_bottom_root(self.client, target)
        root, franchise_path, has_tv_franchise = _find_franchise_root(
            self.client, relation_root)
        season_number, number_source = _season_number(target, path)
        schedule = self.client.schedule(value)
        root_titles = _titles(root)
        target_titles = _titles(target)
        root_cast=self.client.cast(root["id"])
        target_cast=(root_cast if str(root["id"])==str(value)
                     else self.client.cast(value))
        root_format = str(root.get("format") or "").upper() or None
        season_release_date=_date_string(target.get("startDate")) or item.get("release_date")
        placement = {
            "provider_path": self.provider,
            "provider_id": str(value),
            "library_type": ("movie" if str(target.get("format") or "").upper()=="MOVIE"
                             and not has_tv_franchise else "series"),
            "tv_show": {
                "name": root_titles["english"] or target_titles["english"],
                "romaji_name": root_titles["romaji"] or target_titles["romaji"],
                "simkl_id": None,
                "tvdb_id": None,
                "anilist_id": str(root["id"]),
                "mal_id": str(root["idMal"]) if root.get("idMal") not in (None,"") else None,
                "source_format": root_format,
                "source": "anilist_franchise_relation",
                "publish_year": _year(root) or _year(target),
                "overview": target.get("description") or root.get("description"),
                "runtime_minutes": _runtime(target) or _runtime(root),
                "air_status": target.get("status") or root.get("status"),
                "cast": root_cast or target_cast or None,
                "cast_source":"anilist",
                **_metadata_terms(root,target),
            },
            "season": {
                "number": season_number,
                "number_source": number_source,
                "name": _season_title(target,root_titles,season_number),
                "romaji_name": target_titles["romaji"],
                "media_type": str(target.get("format") or "").lower(),
                "first_episode": None,
                "last_episode": None,
                "release_date": season_release_date,
                "release_status": target.get("status"),
                "cast":target_cast or None,
                "cast_source":"anilist",
            },
            "episodes": [],
            "relation_path": [str(node["id"]) for node in path],
            "franchise_relation_path": [str(node["id"]) for node in franchise_path],
        }
        try:
            count = _episode_count(target, item, schedule)
        except MediatorMetadataPending as exc:
            raise MediatorMetadataPending(str(exc),placement=placement) from exc
        offset = _special_offset(target, path) if season_number == 0 else 0
        dates = _release_dates(target, schedule)
        episodes = []
        for source_number in range(1, count + 1):
            episodes.append({
                "source_episode_number": source_number,
                "episode_number": offset + source_number,
                "season_number": season_number,
                "simkl_id": None,
                "mal_id": None,
                "title": None,
                "overview": None,
                "runtime_minutes": _runtime(target),
                "release_date": dates.get(source_number),
            })
        numbers = [row["episode_number"] for row in episodes]
        placement["season"].update({
            "first_episode":numbers[0],"last_episode":numbers[-1]})
        placement["episodes"]=episodes
        return placement
