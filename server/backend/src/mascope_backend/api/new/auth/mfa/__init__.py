"""
Second authentication factor (TOTP) for the interactive web session.

The package is split so that the pieces with different lifetimes stay apart:
``config`` and ``secrets`` hold what an operator controls, ``crypto`` holds seed
storage, ``pending`` holds the token bridging the two login steps, and
``service`` holds enrollment and verification. Only ``routes`` knows about HTTP.
"""
