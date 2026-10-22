"""Valid fastapi 0.115.6 code, including a keyword that only *looks* wrong.

``Query``/``Path``/``Body`` accept ``**extra`` (forwarded to the JSON schema), so
even a misspelled keyword is accepted at runtime and must not be reported.
"""

import fastapi
from fastapi import Depends, FastAPI, Query

app = FastAPI(title="demo", version="1.0")


def get_settings():
    return {"debug": False}


settings = Depends(get_settings, use_cache=False)
limit = Query(default=10, ge=1, le=100)
legacy = Query(defualt=None)
router = fastapi.APIRouter(prefix="/items")
app.include_router(router)
print(app, settings, limit, legacy, router)
