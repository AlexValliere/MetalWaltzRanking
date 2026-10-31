"""Collection pause policy is checked offline before credentials or network use."""

from contextlib import redirect_stderr
from io import StringIO
import os
import unittest
from unittest.mock import patch

from collector.__main__ import main
from collector.configuration import EXPECTED_BACKENDS, PAUSED_BACKENDS, STEAM_BACKENDS, load_servers


class CollectionPauseTests(unittest.TestCase):
    def test_all_paused_backends_remain_in_complete_publication_directory(self):
        servers = load_servers()
        self.assertEqual(PAUSED_BACKENDS, EXPECTED_BACKENDS)
        self.assertEqual(len(servers), 10)
        self.assertEqual({server["key"] for server in servers}, EXPECTED_BACKENDS)
        self.assertTrue(all(not server["collectVehicles"] and not server["vehicleZeroRecovery"]
                            and not server["vehiclePartialResults"] for server in servers))

    def test_partial_pause_still_selects_only_active_backends(self):
        active = {"YaleAndroid", "YaleIOS", "JP", "KOREA", "JKMIX",
                  "SteamJP", "SteamBonn", "SteamUSAVA"}
        for arguments in (["collect"], ["collect", "--server", "all"]):
            with self.subTest(arguments=arguments), \
                    patch("collector.__main__.PAUSED_BACKENDS", frozenset(("Bonn", "BANGKOK"))), \
                    patch.dict("os.environ", {}, clear=True), \
                    patch("collector.__main__.run_collection", return_value=0) as runner:
                self.assertEqual(main(arguments), 0)
            self.assertEqual(runner.call_args.args[1], load_servers())
            self.assertEqual(runner.call_args.args[2], active)
            self.assertIsNone(runner.call_args.args[3])

    def test_all_selections_fail_before_credentials_or_network(self):
        servers = load_servers()
        # Check canonicalization too, so a future alias cannot bypass the pause.
        next(server for server in servers if server["key"] == "Bonn")["aliases"].append("PausedAlias")
        environment_get = os.environ.get
        selections = [["collect"], ["collect", "--server", "all"], ["collect", "--server", "steam"]]
        selections += [["collect", "--server", alias] for server in servers for alias in server["aliases"]]
        for arguments in selections:
            credential_reads = []

            def read_environment(name, default=None):
                if name in ("MW_COLLECTOR_USERNAME", "MW_COLLECTOR_PASSWORD"):
                    credential_reads.append(name)
                    raise AssertionError("Paused collection must not read credentials")
                return environment_get(name, default)

            with self.subTest(arguments=arguments), \
                    patch("collector.__main__.load_servers", return_value=servers), \
                    patch("collector.__main__.load_masks") as masks, \
                    patch("collector.__main__.os.environ.get", side_effect=read_environment), \
                    patch("collector.__main__.run_collection") as runner, \
                    patch("collector.collect.socket.create_connection") as connector, \
                    redirect_stderr(StringIO()) as errors:
                with self.assertRaises(SystemExit) as caught:
                    main(arguments)
            self.assertEqual(caught.exception.code, 2)
            self.assertIn("temporarily paused", errors.getvalue())
            masks.assert_not_called()
            self.assertEqual(credential_reads, [])
            runner.assert_not_called()
            connector.assert_not_called()

    def test_unpaused_groups_and_aliases_keep_their_canonical_selection(self):
        selections = {"steam": STEAM_BACKENDS, "SteamBonn": {"SteamBonn"},
                      "KOREA": {"KOREA"}, "MIXKJ": {"JKMIX"}, "SteamTEST": {"SteamJP"}}
        for alias, expected in selections.items():
            with self.subTest(alias=alias), \
                    patch("collector.__main__.PAUSED_BACKENDS", frozenset()), \
                    patch.dict("os.environ", {}, clear=True), \
                    patch("collector.__main__.run_collection", return_value=0) as runner:
                self.assertEqual(main(["collect", "--server", alias]), 0)
            self.assertEqual(runner.call_args.args[2], expected)


if __name__ == "__main__":
    unittest.main()
