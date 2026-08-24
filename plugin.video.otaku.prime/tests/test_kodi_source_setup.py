import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT=os.path.dirname(os.path.dirname(__file__)); sys.path.insert(0,ROOT)
from resources.lib.services.kodi_source_setup import KodiSourceSetupService
from resources.lib.services.stream_library import StreamLibraryService


class KodiSourceSetupTests(unittest.TestCase):
    def test_adds_both_sources_and_preserves_existing_source(self):
        with tempfile.TemporaryDirectory() as profile:
            with open(os.path.join(profile,"sources.xml"),"w",encoding="utf-8") as handle:
                handle.write("<sources><video><source><name>Existing</name>"
                             "<path>/media/existing/</path></source></video></sources>")
            library=StreamLibraryService(os.path.join(profile,"library")); library.initialize()
            setup=KodiSourceSetupService(profile,library)
            self.assertEqual(2,len(setup.ensure_sources()["added"]))
            self.assertTrue(os.path.exists(setup.path+".otaku-prime.bak"))
            self.assertEqual([],setup.ensure_sources()["added"])
            names=[node.findtext("name") for node in
                   ET.parse(setup.path).getroot().find("video").findall("source")]
            self.assertEqual(["Existing","Otaku Prime Movies","Otaku Prime TV Shows"],names)


if __name__=="__main__": unittest.main()
