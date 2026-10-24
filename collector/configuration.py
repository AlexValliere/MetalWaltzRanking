"""Reviewed public endpoints and fingerprinted native transport constants."""

import base64
import hashlib
from importlib.resources import files
import json


EXPECTED_LIBRARY = "fa43e196953cd594e54a01a2e315311618966b82744dc29e086ea0c3fed1bd43"
EXPECTED_COMMIT = "fee198aee089d57ab4184f4ab0cad5f3ccf67e35"
EXPECTED_STEAM_BINARY = "15e063b555565cd35e3b465cdd0a8dc005e26b552b2317c8cd56cd958e9c4724"
STEAM_BACKENDS = frozenset(("SteamJP", "SteamBonn", "SteamUSAVA"))
STEAM_ALIASES = {key: key for key in STEAM_BACKENDS} | {"SteamTEST": "SteamJP"}
EXPECTED_BACKENDS = frozenset(("Bonn", "YaleAndroid", "YaleIOS", "JP", "KOREA", "JKMIX", "BANGKOK")) | STEAM_BACKENDS


def load_masks(platform="mobile"):
    profiles = {"mobile": "protocol.json", "steam": "steam-protocol.json"}
    if platform not in profiles:
        raise ValueError("Unreviewed protocol profile")
    profile = json.loads(files("collector").joinpath("config/" + profiles[platform]).read_text(encoding="utf-8"))
    fingerprint = EXPECTED_LIBRARY if platform == "mobile" else EXPECTED_STEAM_BINARY
    if (profile["schema"] != "metal-waltz.native-transport.v1" or profile["librarySha256"] != fingerprint
            or platform == "mobile" and profile["gameDataCommit"] != EXPECTED_COMMIT):
        raise ValueError("Incompatible native profile")
    masks = {}
    for direction in ("c2g", "g2c"):
        item = profile["directions"][direction]
        mask = base64.b64decode(item["base64"], validate=True)
        if len(mask) != item["bytes"] or hashlib.sha256(mask).hexdigest() != item["sha256"]:
            raise ValueError("Invalid native profile fingerprint")
        masks[direction] = mask
    return masks


def load_servers():
    directory = json.loads(files("collector").joinpath("config/servers.json").read_text(encoding="utf-8"))
    steam = json.loads(files("collector").joinpath("config/steam-servers.json").read_text(encoding="utf-8"))
    groups = {}
    for item in directory["servers"] + steam["servers"]:
        platform = "steam" if item["key"] in STEAM_ALIASES else "mobile"
        canonical = STEAM_ALIASES.get(item["key"], item["key"])
        endpoint = ((platform, canonical) if platform == "steam" else
                    (platform, item["address"], tuple(sorted(item["ports"]))))
        if endpoint not in groups:
            groups[endpoint] = {"key": canonical, "address": item["address"], "ports": item["ports"],
                                "aliases": [], "serverNames": [], "protocolProfile": platform,
                                "collectVehicles": platform == "mobile",
                                "sessionSeconds": 10800 if platform == "steam" else 3600,
                                "batchRequestGapSeconds": 1.0 if platform == "steam" else 0.25}
        elif platform == "steam":
            groups[endpoint].setdefault("alternateRoutes", []).append(
                {"address": item["address"], "ports": item["ports"]})
        groups[endpoint]["aliases"].append(item["key"])
        groups[endpoint]["serverNames"].append(item["name"])
    servers = list(groups.values())
    if {item["key"] for item in servers} != EXPECTED_BACKENDS:
        raise ValueError("Unreviewed backend directory")
    return servers
