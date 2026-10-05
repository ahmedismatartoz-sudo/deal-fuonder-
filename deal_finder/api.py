import os
import hmac
from contextlib import asynccontextmanager
from fastapi.responses import JSONResponse
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from .models import Listing
from .pricing import estimate
from .storage import Store
from .database import database_target

@asynccontextmanager
async def lifespan(app):
    if os.getenv('DEAL_FINDER_MODE') == 'production':
        from .serve import validate_runtime
        validate_runtime()
    yield

app = FastAPI(title='Deal Finder', version='0.3.0', lifespan=lifespan)


@app.post('/repairs/search-plan')
def repair_search_plan(request: dict):
    from .repair_research import search_plan
    try:
        return search_plan(request['vehicle'], request['part'])
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/repairs/estimate')
def repair_estimate(request: dict):
    from .repair_research import estimate_parts
    try:
        return estimate_parts(request)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

@app.middleware('http')
async def authenticate(request, call_next):
    if request.url.path != '/health':
        token = os.getenv('DEAL_FINDER_API_TOKEN')
        if os.getenv('DEAL_FINDER_MODE') == 'production' and (not token or len(token) < 32):
            return JSONResponse(status_code=503, content={'detail': 'Service configuration incomplete'})
        if token:
            auth = request.headers.get('authorization', '')
            if not hmac.compare_digest(auth.encode(), ('Bearer ' + token).encode()):
                return JSONResponse(status_code=401, content={'detail': 'Bearer authentication required'})
    return await call_next(request)


class ImportRequest(BaseModel):
    format: str = 'json'
    content: str

@app.get('/health')
def health():
    return {'status': 'ok', 'version': '0.3.0'}

@app.post('/imports')
def import_listings(request: ImportRequest):
    store = Store(database_target())
    try:
        return store.import_text(request.content, request.format)
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        store.close()

@app.post('/valuations')
def valuation(target: dict):
    store = Store(database_target())
    try:
        return estimate(Listing.parse(target), store.listings())
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        store.close()

from .queue import Queue
from .agents import registry, control_registry, PIPELINE_VERSION
from .agents.intake import IntakeAgent

class BatchRequest(BaseModel):
    batch_id: str
    records: list

class EvaluationRequest(BaseModel):
    model_version: str
    training_vehicle_ids: list[str]
    records: list[dict]

@app.get('/agents')
def agents():
    return {'pipeline_version': PIPELINE_VERSION, 'agents': registry(), 'controls': control_registry(),
            'intake': 'POST /batches', 'evaluation': 'POST /evaluations',
            'forecast_enabled': False}

@app.post('/batches', status_code=202)
def submit_batch(request: BatchRequest):
    queue = Queue(database_target())
    try:
        return IntakeAgent().execute(queue, request.batch_id, request.records)
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        queue.close()

@app.get('/batches/{batch_id}')
def batch_status(batch_id: str):
    queue = Queue(database_target())
    try:
        return queue.batch(batch_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail='Batch not found') from error
    finally:
        queue.close()

@app.get('/batches/{batch_id}/jobs')
def batch_jobs(batch_id: str, offset: int = 0, limit: int = 100):
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail='Invalid pagination')
    queue = Queue(database_target())
    try:
        queue.batch(batch_id)
        ids = [row[0] for row in queue.db.execute(
            'SELECT j.id FROM jobs j JOIN raw_records r ON r.id=j.raw_id WHERE r.batch_id=? ORDER BY j.id LIMIT ? OFFSET ?',
            (batch_id, limit, offset))]
        return {'batch_id': batch_id, 'offset': offset, 'jobs': [queue.result(x) for x in ids]}
    except KeyError as error:
        raise HTTPException(status_code=404, detail='Batch not found') from error
    finally:
        queue.close()

@app.post('/batches/{batch_id}/replay', status_code=202)
def replay_batch(batch_id: str):
    queue = Queue(database_target())
    try:
        return queue.replay_batch(batch_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail='Batch not found') from error
    finally:
        queue.close()

@app.get('/jobs/{job_id}')
def job_result(job_id: int):
    queue = Queue(database_target())
    try:
        return queue.result(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail='Job not found') from error
    finally:
        queue.close()

@app.post('/evaluations')
def evaluate_model(request: EvaluationRequest):
    queue = Queue(database_target())
    try:
        return queue.evaluate(request.model_version, request.training_vehicle_ids, request.records)
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        queue.close()

from .archive import Archive
from .collectors import sources
from .publication import PublicationAgent
from .market import Market


@app.get('/market/status')
def market_status():
    market = Market(database_target())
    try:
        return market.status()
    finally:
        market.close()


@app.post('/market/scan')
def market_scan(mode: str = 'incremental'):
    market = Market(database_target())
    queue = None
    try:
        queue = Queue(database_target())
        return market.scan(mode=mode, queue=queue)
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        if queue:
            queue.close()
        market.close()


@app.get('/market/candidates')
def market_candidates(city: str | None = None, province: str | None = None,
                      offset: int = 0, limit: int = 100):
    market = Market(database_target())
    try:
        return market.candidates(city=city, province=province, offset=offset, limit=limit)
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        market.close()

@app.get('/collection/sources')
def collection_sources():
    return {'sources': sources()}


@app.get('/collection/quality')
def collection_quality(source: str | None = None):
    archive = Archive(database_target())
    try:
        return archive.quality(source)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        archive.close()


class AutoScout24Request(BaseModel):
    run_id: str
    mode: str = 'incremental'
    max_pages: int = 1


@app.post('/collection/autoscout24')
def collect_autoscout24(request: AutoScout24Request):
    """One bounded step; use the worker for bulk collection. Config is server-side."""
    import json
    from .autoscout24 import collect, CollectionBlocked
    if not 1 <= request.max_pages <= 5:
        raise HTTPException(status_code=422, detail='max_pages must be 1..5; use the worker for bulk collection')
    archive = Archive(database_target())
    try:
        config = json.loads(os.getenv('DEAL_FINDER_AUTOSCOUT24_CONFIG', '{}'))
        return collect(config, archive, run_id=request.run_id, mode=request.mode, max_pages=request.max_pages)
    except CollectionBlocked as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        archive.close()

@app.post('/collection/pages', status_code=202)
def receive_collection_page(page: dict):
    archive = Archive(database_target())
    try:
        return archive.ingest(page)
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        archive.close()

@app.get('/collection/runs/{source}/{run_id}')
def collection_status(source: str, run_id: str):
    archive = Archive(database_target())
    try:
        return archive.run_status(source, run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail='Collection run not found') from error
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        archive.close()

@app.get('/catalogue')
def catalogue(min_price: int = 1000, max_price: int = 50000, city: str | None = None,
              province: str | None = None, latitude: float | None = None,
              longitude: float | None = None, radius_km: float | None = None,
              offset: int = 0, limit: int = 100):
    archive = Archive(database_target())
    try:
        return archive.search(min_price=min_price, max_price=max_price, city=city, province=province,
                              latitude=latitude, longitude=longitude, radius_km=radius_km,
                              offset=offset, limit=limit)
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        archive.close()

@app.get('/catalogue/{source}/{source_id}/history')
def catalogue_history(source: str, source_id: str, offset: int = 0, limit: int = 100):
    archive = Archive(database_target())
    try:
        return {'items': archive.history(source, source_id, offset=offset, limit=limit)}
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        archive.close()


@app.get('/catalogue/{source}/{source_id}')
def catalogue_record(source: str, source_id: str):
    """Complete original record, even when price/specification gates exclude it."""
    archive = Archive(database_target())
    try:
        rows = archive.history(source, source_id, limit=1)
        if not rows:
            raise HTTPException(status_code=404, detail='Listing not found')
        return rows[0]
    finally:
        archive.close()

@app.post('/catalogue/{source}/{source_id}/promote', status_code=202)
def promote_listing(source: str, source_id: str, request: dict):
    archive = Archive(database_target())
    queue = None
    try:
        allowed = {'batch_id', 'identity_evidence', 'inspection', 'repair_quotes', 'operating_costs'}
        if set(request) - allowed:
            raise ValueError('Unexpected promotion fields')
        envelope = archive.normalized_envelope(source, source_id)
        envelope.update({k: v for k, v in request.items() if k != 'batch_id'})
        queue = Queue(database_target())
        return queue.submit(request['batch_id'], [envelope])
    except KeyError as error:
        raise HTTPException(status_code=422, detail='Listing or required field missing') from error
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        if queue:
            queue.close()
        archive.close()

@app.post('/publication/preview')
def publication_preview(request: dict):
    queue = Queue(database_target())
    archive = None
    try:
        archive = Archive(database_target())
        return PublicationAgent().preview(queue, archive, request['batch_id'])
    except KeyError as error:
        raise HTTPException(status_code=404, detail='Batch not found') from error
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        if archive:
            archive.close()
        queue.close()


@app.post('/vehicles/plate-lookup')
def lookup_vehicle_plate(request: dict):
    from .plate_lookup import PlateLookup
    lookup = PlateLookup()
    try:
        return lookup.lookup(request['plate'])
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        lookup.close()


@app.post('/vehicles/identity-plan')
def vehicle_identity_plan(request: dict):
    from .agents.photo_identity import plan
    try:
        return plan(request)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/vehicles/identify')
def identify_vehicle(request: dict):
    from datetime import datetime, timezone
    from .agents.photo_identity import execute
    try:
        return execute(request, datetime.now(timezone.utc))
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
