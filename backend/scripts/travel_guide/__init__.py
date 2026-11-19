"""The "Rebuilding Travel from scratch" guide, made from Travel's approved setup.

    uv run --with playwright python -m scripts.travel_guide

Part A walks through building Travel in the editor; Part B holds every value
to type, generated from ``app/seed/v1/travel.json``. Each save point of the
walkthrough is built and run through the setup check, and the last must be
the approved setup itself, so the guide cannot drift from it
(``tests/test_travel_guide.py``). The screenshots are of the editor, served
against the test database only.
"""
