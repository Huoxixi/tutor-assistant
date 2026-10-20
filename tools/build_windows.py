"""Build a redistributable Windows folder, license bundle and versioned ZIP."""
import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.runtime import VERSION

PUBLIC_DOCS = (
    'amap-setup.md', 'commute-workflow.md', 'windows-release.md',
    'user-journey-audit-20261020.md', 'images/authorization-preview.png',
)


def package_docs(bundle):
    """Ship only public help, removing stale internal reports on rebuilds."""
    sources = [(ROOT / 'docs' / name, name) for name in PUBLIC_DOCS]
    for source, _ in sources:
        if not source.is_file():
            raise FileNotFoundError('Public release documentation missing: ' + source.name)
    target = (bundle / 'docs').resolve()
    if target.parent != bundle.resolve():
        raise RuntimeError('Documentation directory must stay inside the release bundle')
    if target.exists():
        for previous in target.rglob('*'):
            if previous.is_file() and previous.relative_to(target).as_posix() not in PUBLIC_DOCS:
                if not previous.resolve().is_relative_to(target):
                    raise RuntimeError('Unexpected link in release documentation')
                previous.unlink()
    for source, name in sources:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def download(url, destination, expected=None):
    if expected and destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
        return
    request = urllib.request.Request(url, headers={'User-Agent': 'TutorAssistant-Build'})
    with urllib.request.urlopen(request, timeout=25) as response:
        data = response.read()
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError('Dependency source checksum mismatch')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def license_bundle(bundle):
    target = bundle / 'third_party_licenses'
    target.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / 'packaging/licenses/wechatauto-replica-APACHE-2.0.txt',
                    target / 'wechatauto-replica-APACHE-2.0.txt')
    inventory = []
    for dist in metadata.distributions():
        name = dist.metadata['Name']
        if not name:
            continue
        folder = target / (name.replace('/', '_') + '-' + dist.version)
        files = []
        for f in dist.files or []:
            # Copy notices only, never package data, caches or local settings.
            if any(word in Path(str(f)).name.lower() for word in ('license', 'copying', 'notice')):
                source = Path(dist.locate_file(f))
                if source.is_file() and source.stat().st_size < 3_000_000:
                    folder.mkdir(exist_ok=True)
                    dest = folder / str(f).replace('/', '__').replace('\\', '__')
                    shutil.copyfile(source, dest)
                    files.append(dest.name)
        inventory.append({'name': name, 'version': dist.version, 'license': dist.metadata.get('License-Expression') or dist.metadata.get('License'),
                          'source': dist.metadata.get('Home-page'), 'notices': files})
    (target / 'installed-components.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file(): shutil.copyfile(python_license, target / 'Python-LICENSE.txt')
    # The fixed upstream release has no bundled license text. Include its exact
    # source archive plus both published license versions and the GPLv3 terms.
    with urllib.request.urlopen('https://pypi.org/pypi/pywechat127/1.9.8/json', timeout=30) as response:
        release = json.load(response)
    source = next(row for row in release['urls'] if row['packagetype'] == 'sdist')
    archive = target / source['filename']
    download(source['url'], archive, source['digests']['sha256'])
    for name, url in {
        'LGPL-3.0.txt': 'https://raw.githubusercontent.com/gcc-mirror/gcc/master/COPYING3.LIB',
        'GPL-3.0.txt': 'https://raw.githubusercontent.com/gcc-mirror/gcc/master/COPYING3',
    }.items():
        download(url, target / name)
    import _soundfile_data
    shutil.copyfile(Path(_soundfile_data.__file__).parent / 'COPYING', target / 'LGPL-2.1.txt')
    (target / 'pywechat-source.json').write_text(json.dumps({
        'distribution': 'pywechat127', 'version': '1.9.8', 'url': source['url'],
        'sha256': source['digests']['sha256'], 'modified': False,
        'replacement_path': '_internal/pyweixin',
    }, indent=2), encoding='utf-8')
    sndfile = target / 'libsndfile-1.2.2.tar.gz'
    sndfile_url = 'https://codeload.github.com/libsndfile/libsndfile/tar.gz/refs/tags/1.2.2'
    download(sndfile_url, sndfile)
    (target / 'libsndfile-source.json').write_text(json.dumps({
        'version': '1.2.2', 'url': sndfile_url, 'sha256': hashlib.sha256(sndfile.read_bytes()).hexdigest(),
        'replacement_path': '_internal/_soundfile_data/libsndfile_x64.dll', 'modified': False,
    }, indent=2), encoding='utf-8')
    return [archive, sndfile]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-build', action='store_true')
    args = parser.parse_args()
    if os.name != 'nt' or sys.maxsize <= 2**32:
        raise SystemExit('Build on 64-bit Windows with 64-bit Python.')
    if not args.skip_build:
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', str(ROOT / 'dist'),
            '--workpath', str(ROOT / 'build/pyinstaller'), str(ROOT / 'packaging/TutorAssistant.spec')], check=True, cwd=ROOT)
    bundle = ROOT / 'dist/TutorAssistant'
    if not (bundle / 'TutorAssistant.exe').is_file(): raise RuntimeError('Executable missing')
    if not (bundle / '_internal/pyweixin/__init__.py').is_file():
        raise RuntimeError('Replaceable WeChat library source missing')
    for name in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copyfile(ROOT / name, bundle / name)
        shutil.copyfile(ROOT / name, bundle / '_internal' / name)
    package_docs(bundle)
    shutil.copyfile(ROOT / 'packaging/START_HERE.txt', bundle / 'START_HERE.txt')
    sources = license_bundle(bundle)
    bad = [str(p.relative_to(bundle)) for p in bundle.rglob('*') if p.is_file() and
           (p.suffix.lower() in {'.dpapi', '.sqlite3', '.sqlite', '.db', '.log'} or
            p.name in {'.env', 'desktop-session.json', 'desktop.lock'})]
    if bad: raise RuntimeError('Runtime/private files in bundle: ' + ','.join(bad))
    output = ROOT / 'dist' / ('TutorAssistant-v' + VERSION + '-windows-x64.zip')
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for p in sorted(bundle.rglob('*')):
            if p.is_file(): archive.write(p, 'TutorAssistant/' + p.relative_to(bundle).as_posix())
    # Release source attachment is byte-for-byte the PyPI source distribution.
    source_outputs = []
    for source in sources:
        source_output = ROOT / 'dist' / source.name
        shutil.copyfile(source, source_output)
        source_outputs.append(source_output)
    checksum = ROOT / 'dist/SHA256SUMS.txt'
    checksum.write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n'
                              for p in [output, *source_outputs]), encoding='ascii')
    print(json.dumps({'zip': output.name, 'size_mb': round(output.stat().st_size / 1024**2, 1),
                      'sources': [p.name for p in source_outputs], 'private_files': bad}))


if __name__ == '__main__': main()
