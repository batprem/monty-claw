"""Filesystem mounts: expose a host directory inside the sandbox.

By default the sandbox sees no filesystem at all. `MountDir` maps one host
directory to a virtual path, with three modes:

- 'read-only'  : reads work, writes raise PermissionError
- 'read-write' : writes go through to the real host directory
- 'overlay'    : (default) reads fall through to the host; writes are kept
                 in memory per feed and *discarded* when the feed ends —
                 the sandbox thinks it wrote, the host dir never changes.

Run: uv run snippets/05_filesystem_mounts.py
"""

import tempfile
from pathlib import Path

from pydantic_monty import Monty, MontyRuntimeError, MountDir


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        host_dir = Path(tmp)
        (host_dir / 'notes.txt').write_text('monty was here\n')
        (host_dir / 'data.json').write_text('{"answer": 42}')

        with Monty() as pool:
            # --- overlay (default): writes are sandbox-local ---------------
            with MountDir(host_path=host_dir, virtual_path='/data') as mount:
                with pool.checkout() as session:
                    result = session.feed_run(
                        """
import json
from pathlib import Path

notes = Path('/data/notes.txt').read_text()
answer = json.loads(Path('/data/data.json').read_text())['answer']

# This write succeeds *inside* the sandbox...
Path('/data/output.txt').write_text('sandbox scribbles')
listing = sorted(p.name for p in Path('/data').iterdir())
{'notes': notes.strip(), 'answer': answer, 'sandbox_sees': listing}
""",
                        mount=mount,
                    )
                    print('overlay result:', result)
                # ...but the host directory is untouched.
                print('host really has:', sorted(p.name for p in host_dir.iterdir()))

            # --- read-only: writes are refused -----------------------------
            ro_mount = MountDir(host_path=host_dir, virtual_path='/data', mode='read-only')
            with ro_mount, pool.checkout() as session:
                try:
                    session.feed_run(
                        "open('/data/hack.txt', 'w').write('nope')",
                        mount=ro_mount,
                    )
                except MontyRuntimeError as e:
                    print('read-only mount:', e.display(format='type-msg'))

            # --- read-write, with a byte budget ----------------------------
            rw_mount = MountDir(
                host_path=host_dir,
                virtual_path='/data',
                mode='read-write',
                write_bytes_limit=1024,  # cap cumulative writes per feed
            )
            with rw_mount, pool.checkout() as session:
                session.feed_run(
                    "open('/data/report.txt', 'w').write('written for real')",
                    mount=rw_mount,
                )
            print('after read-write feed:', (host_dir / 'report.txt').read_text())

            # --- no mount at all: the sandbox is blind ---------------------
            with pool.checkout() as session:
                try:
                    session.feed_run("open('/etc/passwd').read()")
                except MontyRuntimeError as e:
                    print('no mount:', e.display(format='type-msg'))


if __name__ == '__main__':
    main()
