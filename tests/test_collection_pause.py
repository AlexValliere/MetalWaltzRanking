"""Collection pause policy is checked offline before credentials or network use."""

from contextlib import redirect_stderr
from io import StringIO
import os
import unittest
from unittest.mock import patch

from collector.__main__ import main
from collector.configuration import EXPECTED_BACKENDS, PAUSED_BACKENDS, STEAM_BACKENDS, load_servers


class CollectionPauseTests(unittest.TestCase):
    def test_paused_mobile_backends_remain_in_complete_publication_directory(self):
        servers = load_servers()
        self.assertEqual(PAUSED_BACKENDS, frozenset(("Bonn", "BANGKOK")))
        self.assertEqual(len(servers), 10)
        self.assertEqual({server["key"] for server in servers}, EXPECTED_BACKENDS)
        self.assertTrue(all(server["protocolProfile"] == "mobile"
                            for server in servers if server["key"] in PAUSED_BACKENDS))

    def test_default_and_explicit_all_collect_only_eight_active_backends(self):
        active = {"YaleAndroid", "YaleIOS", "JP", "KOREA", "JKMIX",
                  "SteamJP", "SteamBonn", "SteamUSAVA"}
        for arguments in (["collect"], ["collect", "--server", "all"]):
            with self.subTest(arguments=arguments), \
                    patch.dict("os.environ", {}, clear=True), \
                    patch("collector.__main__.run_collection", return_value=0) as runner:
                self.assertEqual(main(arguments), 0)
            self.assertEqual(runner.call_args.args[1], load_servers())
            self.assertEqual(runner.call_args.args[2], active)
            self.assertIsNone(runner.call_args.args[3])

    def test_explicit_paused_keys_and_aliases_fail_before_credentials_or_network(self):
        servers = load_servers()
        # Check canonicalization too, so a future alias cannot bypass the pause.
        next(server for server in servers if server["key"] == "Bonn")["aliases"].append("PausedAlias")
        environment_get = os.environ.get
        for key in ("Bonn", "BANGKOK", "PausedAlias"):
            credential_reads = []

            def read_environment(name, default=None):
                if name in ("MW_COLLECTOR_USERNAME", "MW_COLLECTOR_PASSWORD"):
                    credential_reads.append(name)
                    raise AssertionError("Paused collection must not read credentials")
                return environment_get(name, default)

            with self.subTest(key=key), \
                    patch("collector.__main__.load_servers", return_value=servers), \
                    patch("collector.__main__.load_masks") as masks, \
                    patch("collector.__main__.os.environ.get", side_effect=read_environment), \
                    patch("collector.__main__.run_collection") as runner, \
                    patch("collector.collect.socket.create_connection") as connector, \
                    redirect_stderr(StringIO()) as errors:
                with self.assertRaises(SystemExit) as caught:
                    main(["collect", "--server", key])
            self.assertEqual(caught.exception.code, 2)
            self.assertIn("temporarily paused", errors.getvalue())
            masks.assert_not_called()
            self.assertEqual(credential_reads, [])
            runner.assert_not_called()
            connector.assert_not_called()

    def test_steam_group_korea_and_active_aliases_still_collect(self):
        selections = {"steam": STEAM_BACKENDS, "SteamBonn": {"SteamBonn"},
                      "KOREA": {"KOREA"}, "MIXKJ": {"JKMIX"}, "SteamTEST": {"SteamJP"}}
        for alias, expected in selections.items():
            with self.subTest(alias=alias), \
                    patch.dict("os.environ", {}, clear=True), \
                    patch("collector.__main__.run_collection", return_value=0) as runner:
                self.assertEqual(main(["collect", "--server", alias]), 0)
            self.assertEqual(runner.call_args.args[2], expected)


if __name__ == "__main__":
    unittest.main()
