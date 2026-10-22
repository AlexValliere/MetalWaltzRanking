# Vehicle portraits

The ranking rows use actual vehicle types and generations returned by the native
public player detail response. The shared catalog resolves those two fields to
the native phase's default presentation model. Equipped costumes are not exposed
by that response; these portraits do not claim to show a player's selected skin.
The native public player view makes the same choice with an empty appearance-rule
list. A missing or unknown type/generation remains unavailable.

`site/assets/vehicles/catalog.json` contains public vehicle names, model mappings
and an exact PNG inventory. Images have content-addressed filenames, are shared
across every player and server, and are ordinary lazy-loaded images at runtime.
There is no Live2D runtime, model, texture atlas or account connection in the site.

## Sources and rendering

Gameplay identities come from the pinned Android gamefiles commit
`fee198aee089d57ab4184f4ab0cad5f3ccf67e35` (1.9.8.26, `_1.10` patch). Artwork uses
Maiden Waltz's reviewed resolver: compatible Steam presentation is pinned separately
to `30eb93b912b87d902eb00047985ab06298774e67`, build 25597619, with Android fallback.
Original source trees remain unchanged.

For ordinary models, the builder copies the exact native `Head_Square` PNG. For
Live2D models it renders a static first frame through Maiden Waltz's production
portrait adapter, original model/texture bundle, Cubism Framework and Core. Its
existing `portraitFocus` measures eye meshes on a separate Core instance to avoid
centering on a tank above or beside the character. The captured view is 256×256,
DPR 1, with the eye center placed at 50% horizontally and 40% vertically, a fixed
random seed and the same bounded 2–6 zoom. The original adapter crops transparent
borders after capture. No generative image model is used.

Each capture uses one isolated hidden build window, a temporary profile, no preload
or account bridge, and local artwork requests only. The builder verifies exactly
one rendered/captured frame and complete model/texture release before advancing.
Every generated PNG is decoded and checked for nontransparent content. Builds
reuse verified output bytes when the source and rendering recipe are unchanged.
Pixel equality across different GPUs or renderer versions is not guaranteed.

## Rebuild

Use a configured Maiden Waltz checkout with its pinned Android and Steam sources,
generated catalog, production portrait bundle, Node 24, Electron and Python virtual
environment already installed. From this repository:

```powershell
node scripts/build_vehicle_assets.cjs --maiden-root D:/AI/MaidenWaltz
node --test tests/vehicle-art.test.mjs
```

`--python <path>` selects the Python used for source verification. `--force`
recaptures the Live2D images. Normal GitHub refreshes use the committed static
assets and do not need either gamefile checkout or an installed rendering SDK.

Before using any generated Maiden catalog projection, the builder independently
rechecks all consumed source checksums, vehicle phase identities, names, default
models, native square selections and complete Live2D exports against the pinned
sources. This source proof does not rewrite Maiden Waltz's catalog or frozen
baseline. The records in `docs/research/vehicle-source-proof.json` and
`docs/research/vehicle-art-build.json` retain the consumed source hashes, source
pins, renderer/recipe fingerprints, focus coordinates and output inventory.

## Rights

Vehicle names and artwork are third-party Metal Waltz game content. This project
does not claim ownership or relicense them under its application-code license.
The Live2D SDK/Core retain their own licenses in the source Maiden Waltz checkout;
the ranking website publishes only the rendered PNGs. Source repositories:
[Android gamefiles](https://github.com/AlexValliere/MetalWaltzGamefiles),
[Steam gamefiles](https://github.com/AlexValliere/MetalWaltzSteamGamefiles), and
[Maiden Waltz](https://github.com/AlexValliere/MaidenWaltz).
