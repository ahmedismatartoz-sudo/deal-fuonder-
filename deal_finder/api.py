import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from .models import Listing
from .pricing import estimate
from .storage import Store

app = FastAPI(title='Deal Finder', version='0.1.0')

class ImportRequest(BaseModel):
    format: str = 'json'
    content: str

@app.get('/health')
def health():
    return {'status': 'ok', 'version': '0.1.0'}

@app.post('/imports')
def import_listings(request: ImportRequest):
    store = Store(os.getenv('DEAL_FINDER_DB', 'deal-finder.db'))
    try:
        return store.import_text(request.content, request.format)
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        store.close()

@app.post('/valuations')
def valuation(target: dict):
    store = Store(os.getenv('DEAL_FINDER_DB', 'deal-finder.db'))
    try:
        return estimate(Listing.parse(target), store.listings())
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        store.close()
