"""Build the command for the trusted Python framework executable on macOS."""

from __future__ import annotations

import json
import sys
import sysconfig
from pathlib import Path


_SYSTEM_RUNTIME = r'''
(allow file-read* file-map-executable
    (literal "/usr/lib/dyld")
    (regex #"^/usr/lib/(system/)?[^/]+[.]dylib$")
    (regex #"^/System/Library/dyld/dyld_shared_cache_[^/]+$")
    (regex #"^/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld/dyld_shared_cache_[^/]+$")
    (literal "/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation"))
'''


def sandbox_command(reply_fd: int, message_fd: int) -> list[str]:
    """Return the Seatbelt command. The caller supplies an empty environment."""
    base = Path(sys.base_prefix).resolve(strict=True)
    python = (base / "Resources/Python.app/Contents/MacOS/Python").resolve(strict=True)
    framework = (base / "Python").resolve(strict=True)
    stdlib = Path(sysconfig.get_path("stdlib")).resolve(strict=True)
    child = Path(__file__).with_name("_child.py").resolve(strict=True)

    if not (
        python.is_file()
        and framework.is_file()
        and child.is_file()
        and stdlib.is_dir()
        and base in python.parents
        and base in framework.parents
        and base in stdlib.parents
    ):
        raise ValueError("Unsupported Python framework layout")

    def quote(path: Path) -> str:
        return json.dumps(str(path), ensure_ascii=False)

    profile = f'''
(version 1)
(deny default)
(deny network*)
(deny process-fork)
(allow process-exec (literal {quote(python)}))
(allow file-read* (literal "/"))
(allow file-read* file-map-executable
    (literal {quote(python)})
    (literal {quote(framework)})
    (subpath {quote(stdlib)}))
(deny file-read* file-map-executable (subpath {quote(stdlib / 'site-packages')}))
(allow file-read* (literal {quote(child)}))
{_SYSTEM_RUNTIME}
'''
    return [
        "/usr/bin/sandbox-exec", "-p", profile, str(python), "-IBS", str(child),
        str(reply_fd), str(message_fd),
    ]
