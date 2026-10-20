"""Publication must not inherit private reports from a developer's docs folder."""
from pathlib import Path

import pytest

from tools import build_windows


def public_sources(root):
    for name in build_windows.PUBLIC_DOCS:
        path=root/'docs'/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'public help')


def test_release_docs_exclude_private_reports_and_remove_stale_copies(tmp_path,monkeypatch):
    root=tmp_path/'project';public_sources(root)
    (root/'docs/private-review.md').write_text('private source details',encoding='utf-8')
    bundle=tmp_path/'dist/Release'
    (bundle/'docs').mkdir(parents=True)
    (bundle/'docs/old-private-review.md').write_text('private stale details',encoding='utf-8')
    (bundle/'app-resource.bin').write_bytes(b'unchanged executable resources')
    monkeypatch.setattr(build_windows,'ROOT',root)
    build_windows.package_docs(bundle)
    assert {p.relative_to(bundle/'docs').as_posix() for p in (bundle/'docs').rglob('*') if p.is_file()}==set(build_windows.PUBLIC_DOCS)
    assert (bundle/'app-resource.bin').read_bytes()==b'unchanged executable resources'
    assert (root/'docs/private-review.md').exists()


def test_missing_public_source_does_not_mutate_existing_bundle(tmp_path,monkeypatch):
    bundle=tmp_path/'Release';(bundle/'docs').mkdir(parents=True)
    old=bundle/'docs/old-private-review.md';old.write_text('stale',encoding='utf-8')
    monkeypatch.setattr(build_windows,'ROOT',tmp_path/'missing')
    with pytest.raises(FileNotFoundError):build_windows.package_docs(bundle)
    assert old.read_text(encoding='utf-8')=='stale'
