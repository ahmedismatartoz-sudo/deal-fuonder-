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
    if os.getenv('DEAL_FINDER_MODE') == 'production' and args.command != 'agents':
        if not (args.db or database_target()).startswith(('postgresql://', 'postgres://')):
            raise RuntimeError('Production workers require the shared PostgreSQL database')
    if args.command == 'agents':
        print(json.dumps(registry(), indent=2))
        return
    if args.command == 'migrate':
        print(json.dumps(migrate(args.db), indent=2))
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
            brightdata = None
            if os.getenv('DEAL_FINDER_BRIGHTDATA_CONFIG'):
                from .brightdata import BackgroundCollection
                try:
                    brightdata = BackgroundCollection(args.db, json.loads(os.environ['DEAL_FINDER_BRIGHTDATA_CONFIG']))
                except (ValueError, TypeError):
                    print(json.dumps({'brightdata': 'paused', 'reason': 'Invalid backend collection configuration'}), flush=True)
            stopping = False
            def stop(signum, frame):
                nonlocal stopping
                stopping = True
            old_handler = signal.signal(signal.SIGTERM, stop)
            try:
                while not stopping and (args.max_jobs == 0 or processed < args.max_jobs):
                    if brightdata:
                        progress = brightdata.step()
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
                    result = queue.work_one()
                    if result is None:
                        time.sleep(args.poll_seconds)
                        continue
                    print(json.dumps(result), flush=True)
                    processed += 1
            except KeyboardInterrupt:
                pass
            finally:
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
