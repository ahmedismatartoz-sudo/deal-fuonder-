"""Explicit local CLI. No background processes are started by importing the API."""
import argparse
import json
import time
import os
import signal
from .queue import Queue
from .agents import registry
from .database import database_target
from .migrate import migrate


def main():
    parser = argparse.ArgumentParser(description='Deal Finder agents and durable queue')
    parser.add_argument('--db', default=None, help='SQLite path; PostgreSQL URL should be configured via environment')
    sub = parser.add_subparsers(dest='command', required=True)
    submit = sub.add_parser('submit')
    submit.add_argument('file', help='JSON batch with batch_id and records')
    drain = sub.add_parser('drain')
    drain.add_argument('--limit', type=int, default=100)
    listen = sub.add_parser('run')
    listen.add_argument('--poll-seconds', type=float, default=2)
    listen.add_argument('--max-jobs', type=int, default=0, help='0 means keep running until interrupted')
    status = sub.add_parser('batch')
    status.add_argument('batch_id')
    replay = sub.add_parser('replay')
    replay.add_argument('batch_id')
    result = sub.add_parser('result')
    result.add_argument('job_id', type=int)
    collect = sub.add_parser('collect-file')
    collect.add_argument('file', help='Export with source, run_id, mode, scope and pages')
    collect.add_argument('--max-pages', type=int, default=100)
    collect.add_argument('--scan', action='store_true', help='Screen the whole archive after collection completes')
    scan = sub.add_parser('market-scan')
    scan.add_argument('--mode', choices=['initial', 'incremental', 'full'], default='incremental')
    sub.add_parser('market-status')
    sub.add_parser('market-candidates')
    first = sub.add_parser('first-archive-test')
    first.add_argument('--run-id', required=True)
    cycle = sub.add_parser('daily-cycle')
    cycle.add_argument('--config', default=None, help='Collection configuration JSON: Apify tasks and/or native autoscout24')
    cycle.add_argument('--mode', choices=['initial', 'incremental'], default='incremental')
    cycle.add_argument('--cycle-id', default=None, help='Stable cycle ID for resuming a previous daily collection')
    native = sub.add_parser('collect-autoscout24')
    native.add_argument('--config', default=None, help='Native AutoScout24 configuration JSON')
    native.add_argument('--mode', choices=['initial', 'incremental'], default='incremental')
    native.add_argument('--run-id', required=True, help='Reuse this ID to resume after interruptions')
    native.add_argument('--max-pages', type=int, default=100)
    native.add_argument('--scan', action='store_true')
    brightdata = sub.add_parser('collect-brightdata')
    brightdata.add_argument('--config', help='Reviewed Bright Data input configuration JSON')
    preview = sub.add_parser('publication-preview')
    preview.add_argument('batch_id')
    sub.add_parser('agents')
    sub.add_parser('migrate')
    sub.add_parser('check-db')
    args = parser.parse_args()
    if os.getenv('DEAL_FINDER_WORKER_PAUSED') == '1' and args.command in ('run', 'daily-cycle', 'market-scan'):
        print(json.dumps({'worker': 'paused', 'command': args.command,
                          'collection': False, 'analysis': False, 'photo_downloads': False}), flush=True)
        if args.command == 'run':
            import threading
            pause_stop = threading.Event()
            previous = signal.signal(signal.SIGTERM, lambda *args: pause_stop.set())
            try:
                while not pause_stop.wait(5):
                    pass
            except KeyboardInterrupt:
                pass
            finally:
                signal.signal(signal.SIGTERM, previous)
        return
    if os.getenv('DEAL_FINDER_MODE') == 'production' and args.command != 'agents':
        if not (args.db or database_target()).startswith(('postgresql://', 'postgres://')):
            raise RuntimeError('Production workers require the shared PostgreSQL database')
    if args.command == 'agents':
        print(json.dumps(registry(), indent=2))
        return
    if args.command == 'migrate':
        print(json.dumps(migrate(args.db), indent=2))
        return
    if args.command == 'first-archive-test':
        from .archive import Archive
        from .price_memory import PriceMemory
        from datetime import datetime, timezone
        archive = Archive(args.db)
        try:
            memory = PriceMemory(archive.db)
            now = datetime.now(timezone.utc)
            while memory.sync(now, limit=500):
                pass
            print(json.dumps(memory.first_test(args.run_id, now), indent=2))
        finally:
            archive.close()
        return
    if args.command == 'collect-brightdata':
        from .brightdata import cycle
        from .archive import Archive
        if args.config:
            with open(args.config) as handle:
                config = json.load(handle)
        else:
            config = json.loads(os.getenv('DEAL_FINDER_BRIGHTDATA_CONFIG', '{}'))
        archive = Archive(args.db)
        try:
            print(json.dumps(cycle(config, archive), indent=2))
        finally:
            archive.close()
        return
    if args.command == 'collect-autoscout24':
        from .autoscout24 import collect
        from .archive import Archive
        if args.config:
            with open(args.config) as handle:
                config = json.load(handle)
        else:
            config = json.loads(os.getenv('DEAL_FINDER_AUTOSCOUT24_CONFIG', '{}'))
        archive = Archive(args.db)
        try:
            output = collect(config, archive, run_id=args.run_id, mode=args.mode, max_pages=args.max_pages)
            if args.scan:
                if not output['complete']:
                    raise ValueError('Resume collection with the same run-id before screening')
                from .market import Market
                market, queue = Market(args.db), Queue(args.db)
                try:
                    output = {'collection': output, 'market': market.scan(mode=args.mode, queue=queue)}
                finally:
                    market.close()
                    queue.close()
            print(json.dumps(output, indent=2))
        finally:
            archive.close()
        return
    if args.command == 'collect-file':
        from .archive import Archive
        from .collectors import CollectionAgent, ExportConnector
        with open(args.file) as handle:
            export = json.load(handle)
        archive = Archive(args.db)
        try:
            output = CollectionAgent().execute(ExportConnector(export['source'], export['pages']),
                        archive, run_id=export['run_id'], mode=export['mode'],
                        scope=export['scope'], max_pages=args.max_pages)
            if args.scan:
                if not output['complete']:
                    raise ValueError('Finish collection before screening the initial base')
                from .market import Market
                market, queue = Market(args.db), Queue(args.db)
                try:
                    output = {'collection': output, 'market': market.scan(mode=export['mode'], queue=queue)}
                finally:
                    market.close()
                    queue.close()
            print(json.dumps(output, indent=2))
        finally:
            archive.close()
        return
    if args.command in ('market-scan', 'market-status', 'market-candidates', 'daily-cycle'):
        from .market import Market
        market, queue = Market(args.db), None
        try:
            if args.command == 'market-status':
                output = market.status()
            elif args.command == 'market-candidates':
                output = market.candidates()
            else:
                collections = None
                if args.command == 'daily-cycle':
                    from .apify import collect_tasks
                    if args.config:
                        with open(args.config) as handle:
                            config = json.load(handle)
                    else:
                        config = json.loads(os.getenv('DEAL_FINDER_COLLECTION_CONFIG', '{}'))
                    native_config = config.get('autoscout24') or json.loads(os.getenv('DEAL_FINDER_AUTOSCOUT24_CONFIG', '{}'))
                    from .bootstrap import bootstrap_config, bootstrap_status
                    bootstrap_spec = bootstrap_config()
                    if bootstrap_spec and not native_config:
                        native_config = bootstrap_spec['config']
                    if args.mode == 'incremental' and bootstrap_spec:
                        initial = bootstrap_status(market.archive, bootstrap_spec)
                        if not initial['complete']:
                            print(json.dumps({'waiting_for_initial_base': True,
                                              'run_id': bootstrap_spec['run_id'],
                                              'pages': initial['pages'], 'accepted': initial['accepted']}))
                            return
                        from .autoscout24 import validate_config
                        if validate_config(native_config) != bootstrap_spec['config']:
                            raise ValueError('Daily searches must match the configured initial base')
                    if set(config) - {'tasks', 'autoscout24'}:
                        raise ValueError('Unknown collection cycle configuration')
                    collections = []
                    if config.get('tasks'):
                        collections.extend(collect_tasks({'tasks': config['tasks']}, market.archive, mode=args.mode))
                    if native_config:
                        from .autoscout24 import collect, cycle_run_id
                        result = collect(native_config, market.archive, mode=args.mode,
                            run_id=cycle_run_id(native_config, args.mode, args.cycle_id), max_pages=10000)
                        collections.append(result)
                        if not result['complete']:
                            raise RuntimeError('Native collection incomplete; resume the same cycle before screening')
                    if not config.get('tasks') and not native_config:
                        raise ValueError('Configure Apify tasks or native AutoScout24 searches')
                queue = Queue(args.db)
                output = market.scan(mode=args.mode, queue=queue)
                if collections is not None:
                    output = {'collection': collections, 'market': output}
            print(json.dumps(output, indent=2))
        finally:
            if queue:
                queue.close()
            market.close()
        return
    queue = Queue(args.db)
    try:
        if args.command == 'check-db':
            queue.db.execute('SELECT 1')
            output = {'status': 'ready', 'backend': queue.db.dialect}
        elif args.command == 'publication-preview':
            from .archive import Archive
            from .publication import PublicationAgent
            archive = Archive(args.db)
            try:
                output = PublicationAgent().preview(queue, archive, args.batch_id)
            finally:
                archive.close()
        elif args.command == 'submit':
            with open(args.file) as handle:
                batch = json.load(handle)
            output = queue.submit(batch['batch_id'], batch['records'])
        elif args.command == 'drain':
            if not 1 <= args.limit <= 5000:
                parser.error('limit must be between 1 and 5000')
            output = []
            for _ in range(args.limit):
                result = queue.work_one()
                if result is None:
                    break
                output.append(result)
        elif args.command == 'run':
            if not 0.5 <= args.poll_seconds <= 30 or args.max_jobs < 0:
                parser.error('poll-seconds must be 0.5–30 and max-jobs nonnegative')
            processed = 0
            from .bootstrap import Bootstrap, bootstrap_config
            spec = bootstrap_config()
            bootstrap = Bootstrap(args.db, spec) if spec else None
            from .agent_runtime import BackgroundScreening
            screening = (BackgroundScreening(args.db)
                         if os.getenv('DEAL_FINDER_AGENT_SCHEDULER_ENABLED') == '1' else None)
            from .price_memory import BackgroundPriceMemory
            price_memory = BackgroundPriceMemory(args.db)
            from .photo_archive import BackgroundPhotoArchive
            photo_archive = BackgroundPhotoArchive(args.db)
            first_test_id = os.getenv('DEAL_FINDER_FIRST_ARCHIVE_TEST')
            brightdata = None
            from .brightdata import BackgroundArchiveRevalidation
            archive_revalidation = BackgroundArchiveRevalidation(args.db)
            campaign_raw = os.getenv('DEAL_FINDER_BRIGHTDATA_CAMPAIGN')
            if campaign_raw or os.getenv('DEAL_FINDER_BRIGHTDATA_CONFIG'):
                from .brightdata import BackgroundCollection
                try:
                    if campaign_raw:
                        from .brightdata_campaign import BackgroundCampaign
                        brightdata = BackgroundCampaign(args.db, json.loads(campaign_raw))
                    else:
                        brightdata = BackgroundCollection(args.db, json.loads(os.environ['DEAL_FINDER_BRIGHTDATA_CONFIG']))
                except (ValueError, TypeError):
                    print(json.dumps({'brightdata': 'paused', 'error_code': 'backend_configuration_invalid', 'reason': 'Invalid backend collection configuration'}), flush=True)
            stopping = False
            import threading
            analysis_stop = threading.Event()
            analysis_thread = None
            def analyze_archive():
                while not analysis_stop.is_set():
                    try:
                        progress = price_memory.step(first_test_id)
                        if progress:
                            print(json.dumps(progress), flush=True)
                        if analysis_stop.is_set():
                            break
                        progress = (photo_archive.step(first_test_id)
                                    if os.getenv('DEAL_FINDER_PHOTO_ARCHIVE_ENABLED') == '1' else None)
                        if progress:
                            print(json.dumps(progress), flush=True)
                    except Exception as error:
                        print(json.dumps({'archive_analysis':'retry_later','error_type':type(error).__name__}),flush=True)
                    analysis_stop.wait(2)
            def stop(signum, frame):
                nonlocal stopping
                stopping = True
                analysis_stop.set()
            old_handler = signal.signal(signal.SIGTERM, stop)
            try:
                if os.getenv('DEAL_FINDER_MODE') == 'production':
                    analysis_thread = threading.Thread(target=analyze_archive, name='archive-analysis', daemon=True)
                    analysis_thread.start()
                while not stopping and (args.max_jobs == 0 or processed < args.max_jobs):
                    progress = archive_revalidation.step()
                    if progress:
                        print(json.dumps(progress), flush=True)
                    if brightdata:
                        progress = brightdata.step()
                        if progress:
                            print(json.dumps(progress), flush=True)
                        if stopping:
                            break
                    if analysis_thread is None:
                        memory_progress = price_memory.step(first_test_id)
                        if memory_progress:
                            print(json.dumps(memory_progress), flush=True)
                        photo_progress = (photo_archive.step(first_test_id)
                                    if os.getenv('DEAL_FINDER_PHOTO_ARCHIVE_ENABLED') == '1' else None)
                        if photo_progress:
                            print(json.dumps(photo_progress), flush=True)
                    if screening and price_memory.ready and not first_test_id:
                        progress = screening.step()
                        if progress:
                            print(json.dumps(progress), flush=True)
                        if stopping:
                            break
                    if bootstrap:
                        progress = bootstrap.step()
                        if progress:
                            print(json.dumps(progress), flush=True)
                        if stopping:
                            break
                    worked = 0
                    drain_started = time.monotonic()
                    # Cheap enrichment plans should not wait one collection
                    # page each. Stop claiming when count/time/job budget ends.
                    while price_memory.ready and not stopping and worked < 10 and time.monotonic()-drain_started < 10:
                        if args.max_jobs and processed >= args.max_jobs:
                            break
                        result = (queue.work_one(batch_id='first-test-'+first_test_id)
                                  if first_test_id else queue.work_one())
                        if result is None:
                            break
                        print(json.dumps(result), flush=True)
                        processed += 1
                        worked += 1
                    if worked == 0:
                        time.sleep(args.poll_seconds)
                        continue
            except KeyboardInterrupt:
                pass
            finally:
                analysis_stop.set()
                if analysis_thread is not None:
                    analysis_thread.join(timeout=5)
                signal.signal(signal.SIGTERM, old_handler)
            output = {'processed': processed}
        elif args.command == 'batch':
            output = queue.batch(args.batch_id)
        elif args.command == 'replay':
            output = queue.replay_batch(args.batch_id)
        else:
            output = queue.result(args.job_id)
        print(json.dumps(output, indent=2))
    finally:
        queue.close()

if __name__ == '__main__':
    main()
