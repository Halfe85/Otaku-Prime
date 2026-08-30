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

    def test_watchlist_titles_fall_back_and_include_anilist_alternatives(self):
        page=render_home(
            {"username":"admin","role":"admin"},active_tab="watchlist-management",
            watchlist_accounts={})
        script=read_static_asset(
            "components/watchlist-management/watchlist-management.js")[1].decode("utf-8")
        self.assertIn('id="series-modal-preferred"',page)
        self.assertIn('id="series-modal-alternatives"',page)
        self.assertIn("function alternativeTitles(entry)",script)
        self.assertIn("entry.english_name || entry.preferred_name || entry.romaji_name",script)
        self.assertIn(".concat(alternativeTitles(entry)",script)

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
        self.assertIn('portrait(character, "character", portraitStaff)',script)
        self.assertIn(".library-people-grid",styles)
        self.assertIn(".library-person-portrait.has-staff-portrait:hover",styles)
        self.assertIn(".library-portrait-layer.alternate",styles)

    def test_library_uses_posters_logos_and_full_width_banner_header(self):
        page=render_home(
            {"username":"admin","role":"admin"},active_tab="library",
            watchlist_accounts={})
        script=read_static_asset("components/library/library.js")[1].decode("utf-8")
        styles=read_static_asset("components/library/library.css")[1].decode("utf-8")

        self.assertIn('id="library-series-banner"',page)
        self.assertIn('id="library-series-logo"',page)
        self.assertNotIn('<span class="library-modal-kicker">Prime library</span>',page)
        self.assertIn("item.poster_url",script)
        self.assertIn("item.clearlogo_url",script)
        self.assertIn("library-tile-logo-wrap",script)
        self.assertIn("series.banner_url",script)
        self.assertIn("series.clearlogo_url",script)
        self.assertIn(".library-tile-poster",styles)
        self.assertIn("repeat(auto-fill,minmax(min(230px,100%),1fr))",styles)
        self.assertIn(".library-tile-logo-wrap",styles)
        self.assertIn(".library-series-hero",styles)

    def test_library_separates_tv_series_and_standalone_movies(self):
        page=render_home(
            {"username":"admin","role":"admin"},active_tab="library",
            watchlist_preferences={"mature":0})
        script=read_static_asset("components/library/library.js")[1].decode("utf-8")

        self.assertIn('data-library-kind="series"',page)
        self.assertIn('data-library-kind="movies"',page)
        self.assertIn('fetchJson("/api/library/movies")',script)
        self.assertIn('movie ? "movies/" : "series/"',script)
        self.assertIn('id="library-series-seasons-section"',page)

    def test_movies_and_tv_series_share_hidden_empty_tile_tracks(self):
        page=render_home(
            {"username":"admin","role":"admin"},active_tab="library",
            watchlist_accounts={})
        styles=read_static_asset("components/library/library.css")[1].decode("utf-8")
        people_styles=read_static_asset(
            "components/library/library-character-staff.css")[1].decode("utf-8")

        self.assertEqual(1,page.count('id="library-grid"'))
        self.assertIn('data-library-kind="series"',page)
        self.assertIn('data-library-kind="movies"',page)
        self.assertIn("repeat(auto-fill,minmax(min(230px,100%),1fr))",styles)
        self.assertIn("repeat(auto-fill,minmax(min(220px,100%),1fr))",styles)
        self.assertNotIn("repeat(auto-fit",styles)
        self.assertNotIn(".library-grid",people_styles)

    def test_library_groups_multipart_seasons_by_display_number(self):
        script=read_static_asset("components/library/library.js")[1].decode("utf-8")

        self.assertIn("function mergeSeasonParts(series)",script)
        self.assertIn('var key = "season:" + text(normalizedNumber',script)
        self.assertIn("var seasons = mergeSeasonParts(series);",script)
        self.assertIn('season.parts.length + " parts"',script)
        self.assertIn("episode._primeSeasonPart || season",script)

    def test_watchlist_has_binary_mature_switch_and_library_classification(self):
        page=render_home(
            {"username":"admin","role":"admin"},active_tab="watchlist",
            watchlist_accounts={},watchlist_preferences={"mature":1})
        script=read_static_asset(
            "components/main-container/main-container.js")[1].decode("utf-8")
        library=render_home(
            {"username":"admin","role":"admin"},active_tab="library",
            watchlist_accounts={})

        self.assertIn('id="mature-content-toggle"',page)
        self.assertIn('id="mature-content-value" class="preference-switch-value">mature=1',page)
        self.assertIn('type="checkbox" value="1" checked',page)
        self.assertIn('fetch("/api/preferences/mature"',script)
        self.assertIn('id="library-series-age-rating"',library)
        self.assertIn('id="library-series-genres"',library)
        self.assertIn('id="library-series-themes"',library)

    def test_hentai_artwork_blurs_with_mature_off_without_blurring_clearlogo(self):
        page=render_home(
            {"username":"admin","role":"admin"},active_tab="library",
            watchlist_accounts={},watchlist_preferences={"mature":0})
        script=read_static_asset("components/library/library.js")[1].decode("utf-8")
        styles=read_static_asset("components/library/library.css")[1].decode("utf-8")

        self.assertIn('data-mature="0"',page)
        self.assertIn('=== "hentai"',script)
        self.assertIn('prime:maturechange',script)
        self.assertIn('.library-tile.mature-artwork-blurred .library-tile-poster',styles)
        self.assertIn(
            '.library-series-hero.mature-artwork-blurred .library-hero-art img',styles)
        self.assertNotIn('.mature-artwork-blurred .library-tile-logo',styles)
        self.assertNotIn('.mature-artwork-blurred .library-series-logo',styles)

    def test_library_modal_footer_has_fixed_provider_availability_tile(self):
        page=render_home(
            {"username":"admin","role":"admin"},active_tab="library",
            watchlist_accounts={})
        script=read_static_asset("components/library/library.js")[1].decode("utf-8")
        styles=read_static_asset("components/library/library.css")[1].decode("utf-8")

        self.assertIn('id="library-series-providers"',page)
        for provider in ("anilist","mal","kitsu","simkl"):
            self.assertIn('data-library-provider="{}"'.format(provider),page)
        self.assertIn("function hasProviderIdentity(item, provider)",script)
        self.assertIn("renderProviderTile(series);",script)
        self.assertIn("renderProviderTile({});",script)
        self.assertIn("grid-template-columns:repeat(4,34px)",styles)
        self.assertIn(".library-provider-slot.unavailable img",styles)
        self.assertNotIn(".library-provider-slot { width:34px; height:34px; display:grid; place-items:center; border:",styles)

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
                "mediator_provider","anilist_id","mal_id","kitsu_id","simkl_id",
                "simkl_reference_id","special_locator","identity_resolution_status",
                "identity_resolution_error",
            },set(state))
            self.assertEqual(1,state["mediator_ready"])

    def test_provider_identity_poll_refreshes_modal_ids_and_exposes_simkl_references(self):
        manager=read_static_asset(
            "components/watchlist-management/watchlist-management.js")[1].decode("utf-8")
        states=read_static_asset(
            "components/watchlist-management/watchlist-library-state.js")[1].decode("utf-8")

        self.assertIn("function providerIdentity(provider, entry)",manager)
        self.assertIn("entry.simkl_reference_id",manager)
        self.assertIn('window.addEventListener("prime:watchlist-state"',manager)
        self.assertIn('window.dispatchEvent(new CustomEvent("prime:watchlist-state"',states)

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
