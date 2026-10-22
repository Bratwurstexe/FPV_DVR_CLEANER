"""Entry point for the frozen video engine shipped in release builds."""

from flugbild.bridge import main

if __name__ == "__main__":
    raise SystemExit(main())
