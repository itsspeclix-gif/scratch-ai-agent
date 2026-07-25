from __future__ import annotations

import getpass


def main() -> None:
    try:
        import scratchattach as sa
    except ImportError as exc:
        raise SystemExit("scratchattach is not installed; run 'make install' first") from exc

    username = input("Scratch bot username: ").strip()
    password = getpass.getpass("Scratch bot password (hidden): ")
    session = sa.login(username, password)
    print("\nCopy the following entire value into the GitHub secret SCRATCH_SESSION_STRING:\n")
    print(session.session_string)
    print("\nTreat this value like a password. Do not commit or share it.")


if __name__ == "__main__":
    main()
