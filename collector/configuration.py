"""Reviewed public endpoints and fingerprinted native transport constants."""

import base64
import hashlib
from importlib.resources import files
import json


EXPECTED_LIBRARY = "fa43e196953cd594e54a01a2e315311618966b82744dc29e086ea0c3fed1bd43"
EXPECTED_COMMIT = "fee198aee089d57ab4184f4ab0cad5f3ccf67e35"
EXPECTED_BACKENDS = frozenset(("Bonn", "YaleAndroid", "YaleIOS", "JP", "KOREA", "JKMIX", "BANGKOK"))


def load_masks():
    profile = json.loads(files("collector").joinpath("config/protocol.json").read_text(encoding="utf-8"))
    if profile["librarySha256"] != EXPECTED_LIBRARY or profile["gameDataCommit"] != EXPECTED_COMMIT:
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
    groups = {}
    for item in directory["servers"]:
        endpoint = item["address"], tuple(sorted(item["ports"]))
        if endpoint not in groups:
            groups[endpoint] = {"key": item["key"], "address": item["address"], "ports": item["ports"],
                                "aliases": [], "serverNames": []}
        groups[endpoint]["aliases"].append(item["key"])
        groups[endpoint]["serverNames"].append(item["name"])
    servers = list(groups.values())
    if {item["key"] for item in servers} != EXPECTED_BACKENDS:
        raise ValueError("Unreviewed backend directory")
    return servers
