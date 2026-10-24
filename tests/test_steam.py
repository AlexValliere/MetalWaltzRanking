"""Steam's reviewed protocol profile is selected before any credential is sent."""

import unittest
from unittest.mock import patch

from collector.__main__ import main
from collector.collect import open_socket, scan_server
from collector.configuration import STEAM_BACKENDS, load_masks, load_servers
from collector.connection import CollectorConnection
from test_batch_pacing import Clock
from test_session import InventedGameSocket


class SteamTests(unittest.TestCase):
    def test_reviewed_routes_cannot_collide_with_mobile_keys(self):
        servers = load_servers()
        steam = [s for s in servers if s["protocolProfile"] == "steam"]
        self.assertEqual({s["key"] for s in steam}, STEAM_BACKENDS)
        self.assertTrue(all(s["serverNames"][0].startswith("Steam / ") for s in steam))
        self.assertTrue(all(s["sessionSeconds"] == 10800 for s in steam))
        self.assertTrue(all(s["sessionSeconds"] == 3600 for s in servers if s["protocolProfile"] == "mobile"))
        asia = next(s for s in steam if s["key"] == "SteamJP")
        self.assertEqual(asia["aliases"], ["SteamJP", "SteamTEST"])
        self.assertEqual(asia["alternateRoutes"], [{"address": "54.169.167.235", "ports": [37002, 37003]}])
        self.assertNotEqual(next(s for s in steam if s["key"] == "SteamJP")["address"],
                            next(s for s in servers if s["key"] == "JP")["address"])

    def test_alternate_route_is_only_tried_before_a_connection_exists(self):
        server = next(s for s in load_servers() if s["key"] == "SteamJP")
        peer = object()
        with patch("collector.collect.socket.create_connection", side_effect=[OSError(), OSError(), peer]) as connect:
            self.assertIs(open_socket(server), peer)
        self.assertEqual([c.args[0] for c in connect.call_args_list],
                         [("54.250.239.190", 37000), ("54.250.239.190", 37001), ("54.169.167.235", 37002)])

    def test_steam_session_uses_steam_masks_for_both_directions(self):
        server = next(s for s in load_servers() if s["key"] == "SteamBonn")
        steam_masks = load_masks("steam")
        self.assertNotEqual(steam_masks, load_masks())
        peer = InventedGameSocket(steam_masks)
        with patch("collector.collect.open_socket", return_value=peer):
            result = scan_server(server, ("invented-account", "invented-password"), load_masks())
        self.assertEqual(result["serverKey"], "SteamBonn")
        self.assertEqual(len(result["rows"]), 3)
        self.assertEqual(peer.sent[324], 1)
        self.assertEqual(peer.sent[20], 0)
        self.assertTrue(all(row["vehiclesStatus"] == "not_collected" and row["vehicles"] is None
                            for row in result["rows"]))
        self.assertTrue(peer.closed)

    def test_steam_uses_native_row_batches_and_one_second_shared_gap(self):
        server = next(s for s in load_servers() if s["key"] == "SteamUSAVA")
        peer, clock = InventedGameSocket(load_masks("steam")), Clock()
        connections = []

        def connect(*args, **kwargs):
            connection = CollectorConnection(*args, **kwargs, sleeper=clock.sleep)
            connections.append(connection)
            return connection

        with patch("collector.collect.open_socket", return_value=peer), \
                patch("collector.collect.CollectorConnection", side_effect=connect):
            result = scan_server(server, ("invented-account", "invented-password"), load_masks(),
                                 clock=clock.monotonic)
        self.assertEqual(len(result["rows"]), 3)
        # Even a short page needs a subsequent successful empty reply.
        self.assertEqual(peer.rank_requests, [(0, 200), (3, 200)])
        self.assertEqual(connections[0].batch_request_gap_seconds, 1.0)
        self.assertEqual(connections[0].session_seconds, 10800)
        self.assertEqual(clock.sleeps, [1.0, 1.0])
        self.assertEqual(peer.sent[20], 0)

    def test_unknown_profile_is_rejected_before_network(self):
        server = {**load_servers()[0], "protocolProfile": "unreviewed"}
        with patch("collector.collect.open_socket") as opener:
            with self.assertRaises(ValueError):
                scan_server(server, ("invented-account", "invented-password"), load_masks())
        opener.assert_not_called()

    def test_steam_cli_group_selects_only_steam_routes_and_reuses_credentials(self):
        with patch.dict("os.environ", {"MW_COLLECTOR_USERNAME": "invented-user",
                                          "MW_COLLECTOR_PASSWORD": "invented-password"}), \
                patch("collector.__main__.run_collection", return_value=0) as collect:
            self.assertEqual(main(["collect", "--output", "unused-output", "--server", "steam"]), 0)
        self.assertEqual(collect.call_args.args[2], STEAM_BACKENDS)
        self.assertEqual(collect.call_args.args[3], ("invented-user", "invented-password"))


if __name__ == "__main__":
    unittest.main()
