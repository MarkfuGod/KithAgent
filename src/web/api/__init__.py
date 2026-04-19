"""
Dashboard HTTP API handlers, split by feature area.

Each submodule owns a cohesive slice of the surface so `dashboard.py` stays
thin (just composition + static serving). Route registration lives in
`dashboard.create_app` — modules here only export handler functions.
"""
