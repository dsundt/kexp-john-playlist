import json
from pathlib import Path

from kexp.backup import write_backup, prune


def test_write_backup_creates_parseable_file(tmp_path):
    path = write_backup(str(tmp_path), "snap123", ["u1", "u2", "u3"], now_str="20260723-101500")
    assert path.endswith("playlist-20260723-101500.json")
    data = json.loads(Path(path).read_text())
    assert data["snapshot_id"] == "snap123"
    assert data["uris"] == ["u1", "u2", "u3"]
    assert data["count"] == 3


def test_prune_keeps_newest_keep_by_filename_sort(tmp_path):
    names = [f"playlist-2026072{i}-000000.json" for i in range(10)]
    for i, n in enumerate(names):
        (tmp_path / n).write_text(json.dumps({"snapshot_id": f"s{i}", "uris": [], "count": 0}))

    deleted = prune(str(tmp_path), keep=4)

    assert deleted == 6
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == sorted(names)[-4:]
