"""Verify the consumed Maiden catalog projection against its pinned source bytes.

This leaves Maiden's generated catalog and frozen baseline untouched. It independently
checks type/phase/name/default-model identities, native square selection and complete
Live2D exports using the same reviewed native readers and presentation resolver.
"""
from argparse import ArgumentParser
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


def main():
    parser = ArgumentParser()
    parser.add_argument('--maiden-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.maiden_root.resolve()
    sys.path[:0] = [str(root / 'tools'), str(root / 'bridge')]
    from catalog_sources import table, index, phase, appearance, capsule, strings, ui_resources
    from garage_sources import role_model, picture_tags
    from home_sources import HomeAssets, arranged_style
    from artwork_sources import ArtworkResolver, SteamSnapshot, PREFIX, android_artwork_reference
    from vehicle_sources import plain

    digest = lambda data: hashlib.sha256(data).hexdigest()
    load = lambda path: json.loads(path.read_text(encoding='utf-8'))
    data_root = root / 'gamefiles'
    compatibility = load(root / 'baseline/compatibility.json')['game_data']
    pins = load(root / 'baseline/artwork-sources.json')
    sums_bytes = (data_root / 'SHA256SUMS').read_bytes()
    if digest(sums_bytes) != compatibility['checksum_manifest_sha256']:
        raise ValueError('Android checksum manifest differs from the pin')
    checksums = {name: sha for sha, name in (line.split('  ', 1) for line in sums_bytes.decode().splitlines())}
    git_file = data_root / '.git'
    git_dir = (data_root / git_file.read_text().strip()[8:]).resolve() if git_file.is_file() else git_file
    actual = subprocess.run(['git', '--git-dir=' + str(git_dir), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
    if actual != compatibility['commit']:
        raise ValueError('Android source checkout differs from the pin')
    steam = SteamSnapshot(root / 'steam-gamefiles', pins['steam'], explicit=True)
    requests, consumed = {}, {}

    def source(relative):
        if relative.startswith(PREFIX):
            result = steam.read(relative[len(PREFIX):])
            name = PREFIX + 'RunningResources/' + relative[len(PREFIX):]
        else:
            name = None
            for layer in ('patch/_1.10', 'package/preinitial', 'Resources'):
                candidate = data_root / 'com.astrogame.shermange' / layer / relative
                if candidate.is_file():
                    result = candidate.read_bytes()
                    name = candidate.relative_to(data_root).as_posix()
                    if digest(result) != checksums.get(name):
                        raise ValueError('Android source checksum mismatch')
                    break
            if name is None:
                raise FileNotFoundError(relative)
        consumed[name] = digest(result)
        requests[relative] = name
        return result

    catalog_bytes = (root / 'renderer/catalog.json').read_bytes()
    catalog = json.loads(catalog_bytes)
    if catalog['dataCommit'] != actual:
        raise ValueError('Catalog data pin differs')
    text = index(table(source('config/StringTableInfo.dat'), strings))
    appearances = index(table(source('config/UnitAppearanceInfo.dat'), appearance))
    capsules = index(table(source('config/CapsuleTypeInfo.dat'), capsule))
    native_models = index(table(source('config/roleModelInfo.dat'), role_model))
    styles = index(table(source('config/ArrangedStyleInfo.dat'), arranged_style))
    images = ui_resources(source('config/UIStyle.dat'))
    verified_phases = set()
    for row in table(source('config/CapsulePhaseInfo.dat'), phase):
        if not 6001 <= row['id'] <= 9999 or capsules.get(row['id'], {}).get('deployTag') == 3:
            continue
        app = appearances.get(row['appearanceId'])
        if app is None:
            continue
        definition = catalog['units'][str(row['id'])]['phases'][str(row['phase'])]
        expected_name = text.get(app['stringId'], {}).get('text') or f"Maiden #{row['id']}"
        names = {plain(text[value]['text']) for sid in app['styles'] if sid in styles
                 for item in styles[sid]['items'] if item['mode'] == 'NICK_NAME'
                 for kind, value in item['numbers'] if kind == 5 and value in text}
        expected_maiden = next(iter(names)) if len(names) == 1 else None
        if (definition['displayName'] != expected_name or definition['maidenName'] != expected_maiden
                or definition['appearances'][0]['model'] != app['modelId']
                or definition['appearanceId'] != app['id']):
            raise ValueError('Catalog vehicle identity differs from native source')
        verified_phases.add((str(row['id']), str(row['phase'])))
    actual_phases = {(key, p) for key, unit in catalog['units'].items() for p in unit['phases']}
    if actual_phases != verified_phases:
        raise ValueError('Catalog vehicle phase coverage differs')
    artwork = ArtworkResolver(source, steam.identity, source_paths=requests,
        reviewed_android=android_artwork_reference(data_root, pins['android_reference'], sums_bytes))
    live_count = native_count = 0
    with TemporaryDirectory(prefix='ranking-source-proof-') as temporary:
        home = HomeAssets(source, Path(temporary) / 'home-art', images, text, artwork=artwork)
        for model_id, model in catalog['vehicleDetails']['models'].items():
            key = int(model_id)
            live_url = home.live_model(key)
            if live_url != model['live2d']:
                raise ValueError('Catalog Live2D mapping differs from native source')
            if live_url:
                live_count += 1
                continue
            tags = {tag for sid in native_models.get(key, {}).get('styles', []) if sid in styles
                    for tag in picture_tags(styles[sid], 'Head_Square')}
            if len(tags) != 1:
                raise ValueError('Ambiguous native square portrait')
            tag = next(iter(tags))
            filenames = images.get(tag, set())
            if len(filenames) != 1:
                raise ValueError('Ambiguous native portrait resource')
            filename = next(iter(filenames))
            selected = artwork.standalone('ui/' + filename, tag, limit=1024 * 1024)
            expected = artwork.reader(selected['platform'])('ui/' + filename)
            public_path = model['portrait']
            if not public_path or not public_path.startswith('./portraits/'):
                raise ValueError('Missing native square portrait')
            if (root / 'renderer/public' / public_path[2:]).read_bytes() != expected:
                raise ValueError('Generated native portrait differs from source bytes')
            native_count += 1
        for file in home.live_written:
            relative = file.relative_to(Path(temporary))
            if file.read_bytes() != (root / 'renderer/public' / relative).read_bytes():
                raise ValueError('Generated Live2D export differs from source bytes')
    report = {'schema': 'metal-waltz.vehicle-source-proof.v1', 'dataCommit': actual,
        'catalogSha256': digest(catalog_bytes), 'steam': steam.identity['steam'],
        'units': len(catalog['units']), 'phases': len(verified_phases),
        'nativeModelRecords': native_count, 'live2dModelRecords': live_count,
        'sources': dict(sorted(consumed.items()))}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, separators=(',', ':')) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key not in ('sources', 'steam')}))


if __name__ == '__main__':
    main()
