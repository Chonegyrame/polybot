"""BIG STOCK desk — a self-contained trading-desk module (stock notes,
futures journal, price alerts) that rides inside the Polymarket FastAPI
process but shares NOTHING with it.

Hard isolation rule: this package must not import from app.scheduler,
app.db, or app.services. It has its own SQLite store (desk/desk.db) and its
own router. The only Polymarket file that knows about it is app/api/main.py,
which mounts the static UI and includes desk.api.router.
"""
