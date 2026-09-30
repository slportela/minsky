import json

from scripts.public_lockfiles import NPM_REGISTRY, fix_package_lock, fix_uv_lock

MIRROR = "https://mirror.example.internal"
BLAKE = "d1/53/" + "a" * 60


def test_uv_lock_urls_are_mapped_to_pypi():
    text = (
        f'source = {{ registry = "{MIRROR}/simple" }}\n'
        f'sdist = {{ url = "{MIRROR}/packages/packages/{BLAKE}/agate-1.9.1.tar.gz", hash = "sha256:x" }}\n'
    )
    fixed, unmapped = fix_uv_lock(text)
    assert 'registry = "https://pypi.org/simple"' in fixed
    assert f'"https://files.pythonhosted.org/packages/{BLAKE}/agate-1.9.1.tar.gz"' in fixed
    assert 'hash = "sha256:x"' in fixed and not unmapped


def test_uv_lock_already_public_is_unchanged():
    text = f'url = "https://files.pythonhosted.org/packages/{BLAKE}/x.whl"\nregistry = "https://pypi.org/simple"\n'
    assert fix_uv_lock(text) == (text, [])


def test_package_lock_resolved_is_mapped_to_npmjs_keeping_integrity():
    lock = {
        "packages": {
            "": {"name": "app"},
            "node_modules/@img/colour": {
                "version": "1.1.0",
                "integrity": "sha512-abc",
                "resolved": f"{MIRROR}/api/npm/remote/@img/colour/-/colour-1.1.0.tgz",
            },
            "node_modules/next": {"version": "15.5.0", "resolved": f"{NPM_REGISTRY}next/-/next-15.5.0.tgz"},
        }
    }
    fixed, unmapped = fix_package_lock(json.dumps(lock, indent=2) + "\n")
    packages = json.loads(fixed)["packages"]
    assert packages["node_modules/@img/colour"]["resolved"] == f"{NPM_REGISTRY}@img/colour/-/colour-1.1.0.tgz"
    assert packages["node_modules/@img/colour"]["integrity"] == "sha512-abc"
    assert packages["node_modules/next"]["resolved"] == f"{NPM_REGISTRY}next/-/next-15.5.0.tgz"
    assert not unmapped


def test_unknown_url_shape_is_reported_not_guessed():
    lock = {"packages": {"node_modules/left-pad": {"version": "1.0.0", "resolved": f"{MIRROR}/blob/abc123"}}}
    fixed, unmapped = fix_package_lock(json.dumps(lock))
    assert unmapped == [f"{MIRROR}/blob/abc123"]
    assert json.loads(fixed)["packages"]["node_modules/left-pad"]["resolved"] == f"{MIRROR}/blob/abc123"
