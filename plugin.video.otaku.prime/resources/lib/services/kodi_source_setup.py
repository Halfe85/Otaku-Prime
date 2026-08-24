# -*- coding: utf-8 -*-
"""Register Prime's generated folders as Kodi video file sources."""
from __future__ import annotations

import os
import shutil
import xml.etree.ElementTree as ET


class KodiSourceSetupService:
    """Preserve unrelated sources while adding Prime's two local roots.

    Kodi stores source paths in sources.xml, but scraper/content assignments in
    its existing video database. This service deliberately never opens that DB.
    """

    SOURCES = (("Otaku Prime Movies", "movies_root"),
               ("Otaku Prime TV Shows", "tv_series_root"))

    def __init__(self, kodi_profile, stream_library):
        self.path = os.path.join(kodi_profile, "sources.xml")
        self.stream_library = stream_library

    def ensure_sources(self):
        root = self._read()
        video = root.find("video")
        if video is None:
            video = ET.SubElement(root, "video")
        added = []
        for name, attribute in self.SOURCES:
            path = self._directory(getattr(self.stream_library, attribute))
            source = self._find(video, name, path)
            if source is None:
                source = ET.SubElement(video, "source")
                ET.SubElement(source, "name").text = name
                ET.SubElement(source, "path", {"pathversion": "1"}).text = path
                ET.SubElement(source, "allowsharing").text = "true"
                added.append(name)
        if added:
            self._write(root)
        return {"path": self.path, "added": added, "restart_required": bool(added)}

    def _read(self):
        if not os.path.exists(self.path):
            return ET.Element("sources")
        try:
            return ET.parse(self.path).getroot()
        except (ET.ParseError, OSError) as exc:
            raise RuntimeError("Kodi sources.xml could not be read: {}".format(exc))

    @staticmethod
    def _directory(path):
        return os.path.normpath(path) + os.sep

    @staticmethod
    def _find(video, name, path):
        target = os.path.normcase(os.path.normpath(path))
        for source in video.findall("source"):
            values = [item.text or "" for item in source.findall("path")]
            if (source.findtext("name") == name or any(
                    os.path.normcase(os.path.normpath(value)) == target
                    for value in values)):
                return source
        return None

    def _write(self, root):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        backup = self.path + ".otaku-prime.bak"
        if os.path.exists(self.path) and not os.path.exists(backup):
            shutil.copy2(self.path, backup)
        temporary = self.path + ".otaku-prime.tmp"
        tree = ET.ElementTree(root)
        try:
            ET.indent(tree, space="  ")
        except AttributeError:
            pass
        tree.write(temporary, encoding="utf-8", xml_declaration=True)
        os.replace(temporary, self.path)
