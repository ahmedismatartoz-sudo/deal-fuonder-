"""Production entrypoint requires configured PostgreSQL and bearer authentication."""
import os
from .database import database_target, validate_postgres_url
from .queue import Queue


def validate_runtime():
    target = database_target()
    if not target.startswith(('postgresql://', 'postgres://')):
        raise RuntimeError('Production requires DEAL_FINDER_DATABASE_URL')
    validate_postgres_url(target)
    if len(os.getenv('DEAL_FINDER_API_TOKEN', '')) < 32:
        raise RuntimeError('Production requires a bearer token of at least 32 characters')


def main():
    validate_runtime()
    queue = Queue()
    try:
        queue.db.execute('SELECT 1')
    finally:
        queue.close()
    os.environ['DEAL_FINDER_MODE'] = 'production'
    import uvicorn
    uvicorn.run('deal_finder.api:app', host='0.0.0.0', port=int(os.getenv('PORT', '8000')))

if __name__ == '__main__':
    main()
