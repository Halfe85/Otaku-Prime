import http.client
import json
import os
import tempfile
import threading
import unittest
from urllib.parse import urlencode

from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.ui.renderer import read_static_asset,render_home
from resources.lib.users import UserStore
from resources.lib.web import create_server


class Alpha11WatchlistUITests(unittest.TestCase):
    def test_watchlist_view_uses_topbar_search_without_redundant_heading(self):
        page=render_home(
            {"username":"admin","role":"admin"},active_tab="watchlist-management",
            watchlist_accounts={})
        self.assertIn('id="prime-search"',page)
        self.assertIn('placeholder="Search"',page)
        self.assertNotIn('<h2>Watchlist Management</h2>',page)
        self.assertNotIn('Browse raw items fetched from connected watchlists.',page)
        self.assertNotIn('Prime watchlist item',page)

    def test_watchlist_pagination_floats_and_reports_in_the_bottom_bar(self):
        page=render_home(
            {"username":"admin","role":"admin"},active_tab="watchlist-management",
            watchlist_accounts={})
        watchlist_html=read_static_asset(
            "components/watchlist-management/watchlist-management.css")[1].decode("utf-8")
        container_css=read_static_asset(
            "components/main-container/main-container.css")[1].decode("utf-8")
        self.assertIn('class="bottombar-status" id="watchlist-page-status"',page)
        self.assertEqual(1,page.count('id="watchlist-page-status"'))
        self.assertIn(".watchlist-pagination { position:fixed",watchlist_html)
        self.assertIn("padding: 12px clamp(12px, 1.5vw, 22px) 14px",container_css)

    def test_bundled_provider_icons_are_served_as_png(self):
        for provider in ("anilist","mal","kitsu","simkl"):
            asset=read_static_asset(
                "components/watchlist-management/assets/{}.png".format(provider))
            self.assertIsNotNone(asset)
            self.assertEqual("image/png",asset[0])
            self.assertTrue(asset[1].startswith(b"\x89PNG"))

    def test_watchlist_state_polling_cannot_observe_its_own_dom_writes(self):
        asset=read_static_asset(
            "components/watchlist-management/watchlist-library-state.js")
        self.assertIsNotNone(asset)
        script=asset[1].decode("utf-8")
        self.assertNotIn("MutationObserver",script)
        self.assertIn('fetch("/api/watchlist/states"',script)
        self.assertIn('prime:watchlist-rendered',script)
        self.assertIn('prime:tabchange',script)

    def test_hidden_tabs_do_not_run_full_pollers(self):
        library=read_static_asset("components/library/library.js")[1].decode("utf-8")
        watchlist=read_static_asset(
            "components/watchlist-management/watchlist-management.js")[1].decode("utf-8")
        self.assertIn("function active()",library)
        self.assertIn("function active()",watchlist)
        self.assertIn("AbortController",library)
        self.assertIn("AbortController",watchlist)
        self.assertIn("window.setInterval(loadTiles, 10000)",library)

    def test_library_has_separate_character_and_staff_views(self):
        page=render_home(
            {"username":"admin","role":"admin"},active_tab="library",
            watchlist_accounts={})
        script=read_static_asset("components/library/library.js")[1].decode("utf-8")
        styles=read_static_asset("components/library/library.css")[1].decode("utf-8")
        self.assertIn('id="library-characters-panel"',page)
        self.assertIn('id="library-staff-panel"',page)
        self.assertIn('data-library-people-tab="characters"',page)
        self.assertIn('data-library-people-tab="staff"',page)
        self.assertNotIn('<h3>Actors</h3>',page)
        self.assertIn("function characterCard(character)",script)
        self.assertIn("function staffCard(person)",script)
        self.assertIn("function selectPeopleTab(tab)",script)
        self.assertIn(".library-people-grid",styles)

    def test_app_logs_preserve_scroll_and_offer_jump_to_newest(self):
        page=render_home(
            {"username":"admin","role":"admin"},active_tab="library",
            watchlist_accounts={})
        script=read_static_asset("components/app-logs/app-logs.js")[1].decode("utf-8")
        styles=read_static_asset("components/app-logs/app-logs.css")[1].decode("utf-8")
        self.assertIn('id="app-log-jump"',page)
        self.assertIn("var shouldFollow=atBottom()",script)
        self.assertIn("else setUnreadBelow(true)",script)
        self.assertNotIn("messages.scrollTop=messages.scrollHeight",script)
        self.assertIn(".app-log-jump",styles)

    def test_watchlist_ui_projection_excludes_background_bookkeeping(self):
        with tempfile.TemporaryDirectory() as directory:
            database=os.path.join(directory,"users.sqlite")
            items=WatchlistItemStore(database); items.initialize()
            items.replace_provider_snapshot("anilist",[{
                "provider_item_id":"1","ids":{"anilist":"1"},
                "english_name":"Series","episode_count":12,
                "list_status":"CURRENT","progress":1,"raw":{}}])
            items.finalize_merge()
            local_id=items.list_all()[0]["local_id"]
            items.mark_mediator_ready(local_id,True)

            row=items.list_ui_items()[0]
            state=items.list_ui_library_states()[0]

            self.assertEqual(local_id,row["local_id"])
            self.assertEqual("anilist",row["connected_providers"])
            self.assertNotIn("created_at",row)
            self.assertNotIn("identity_checked_at",row)
            self.assertEqual({
                "local_id","added_to_library","mediator_ready","mediator_status",
                "mediator_provider","simkl_reference_id","special_locator",
            },set(state))
            self.assertEqual(1,state["mediator_ready"])

    def test_progress_api_uses_the_watchlist_manager_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            database=os.path.join(directory,"users.sqlite")
            users=UserStore(database); users.initialize()
            items=WatchlistItemStore(database); items.initialize()
            items.replace_provider_snapshot("anilist",[{
                "provider_item_id":"1","ids":{"anilist":"1"},
                "english_name":"Series","episode_count":12,
                "list_status":"CURRENT","progress":1,"raw":{}}])
            items.finalize_merge(); local_id=items.list_all()[0]["local_id"]
            calls=[]
            def update(item_id,status=None,progress=None,source=None):
                calls.append((item_id,progress,source))
                current=items.list_all()[0]
                items.set_master_state(item_id,current["status"],progress)
                return {"changed":True,"item":items.list_all()[0]}
            try:
                server=create_server("127.0.0.1",0,users,on_watchlist_state_changed=update)
            except PermissionError:
                self.skipTest("sandbox does not permit local listener sockets")
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                connection=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=3)
                body=urlencode({"username":"admin","password":"admin"})
                connection.request("POST","/login",body,{"Content-Type":"application/x-www-form-urlencoded"})
                response=connection.getresponse(); cookie=response.getheader("Set-Cookie").split(";",1)[0]
                response.read(); connection.close()
                connection=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=3)
                payload=json.dumps({"progress":4})
                connection.request("POST","/api/watchlist/items/{}/progress".format(local_id),
                                   payload,{"Content-Type":"application/json","Cookie":cookie})
                response=connection.getresponse(); result=json.loads(response.read().decode("utf-8"))
                self.assertEqual(200,response.status)
                self.assertEqual(4,result["item"]["progress"])
                self.assertEqual([(local_id,4,"web-ui")],calls)
            finally:
                connection.close(); server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__=="__main__": unittest.main()
