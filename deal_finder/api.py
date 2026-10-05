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
from .agents import registry
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
    return {'pipeline_version': 'agents-v0.2', 'agents': registry(),
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
