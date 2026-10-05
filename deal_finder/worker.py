"""Explicit local CLI. No background processes are started by importing the API."""
import argparse
import json
import time
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
    sub.add_parser('agents')
    sub.add_parser('migrate')
    sub.add_parser('check-db')
    args = parser.parse_args()
    if args.command == 'agents':
        print(json.dumps(registry(), indent=2))
        return
    if args.command == 'migrate':
        print(json.dumps(migrate(args.db), indent=2))
        return
    queue = Queue(args.db)
    try:
        if args.command == 'check-db':
            queue.db.execute('SELECT 1')
            output = {'status': 'ready', 'backend': queue.db.dialect}
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
            try:
                while args.max_jobs == 0 or processed < args.max_jobs:
                    result = queue.work_one()
                    if result is None:
                        time.sleep(args.poll_seconds)
                        continue
                    print(json.dumps(result), flush=True)
                    processed += 1
            except KeyboardInterrupt:
                pass
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
